"""
Celery tasks that drive a meeting from "scheduled" to "completed":

  poll_pending_meetings  (beat, every 60s) -> advances each bot's status,
                          and when the call is over, kicks off process_meeting.
  process_meeting        -> recording -> Deepgram -> Groq notes -> email.

The poll is the reliable engine (no Recall webhook configuration required);
the optional webhook endpoint just makes it snappier.
"""
import logging
from datetime import timedelta

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError, Retry
from django.utils import timezone

from .models import (
    EmailKind,
    EmailLog,
    Meeting,
    MeetingNotes,
    MeetingStatus,
    Recording,
    Transcript,
)
from .services import ai, deepgram, recall
from .services.mailer import send_html_email

logger = logging.getLogger("mitaxy")

# If a meeting is this far past its start with no recording, give up.
STALE_AFTER = timedelta(hours=6)


@shared_task(ignore_result=True)
def poll_pending_meetings():
    """Advance every in-flight meeting based on its Recall bot status."""
    active = Meeting.objects.filter(
        status__in=[
            MeetingStatus.SCHEDULED,
            MeetingStatus.JOINING,
            MeetingStatus.RECORDING,
        ]
    ).exclude(recall_bot_id="")

    for meeting in active:
        try:
            _advance(meeting)
        except recall.RecallError as exc:
            logger.warning("poll: recall error for meeting %s: %s", meeting.pk, exc)
        except Exception:
            logger.exception("poll: unexpected error for meeting %s", meeting.pk)


def _advance(meeting: Meeting):
    bot = recall.get_bot(meeting.recall_bot_id)
    code = recall.latest_status_code(bot)
    meeting.bot_status_detail = (code or "")[:200]

    if code in recall.FATAL_CODES:
        meeting.status = MeetingStatus.FAILED
        meeting.error_message = f"The bot could not record this meeting (status: {code})."
        meeting.save(update_fields=["status", "error_message", "bot_status_detail", "updated_at"])
        return

    if code in recall.DONE_CODES:
        # Call finished — hand off to processing exactly once.
        meeting.status = MeetingStatus.PROCESSING
        meeting.save(update_fields=["status", "bot_status_detail", "updated_at"])
        process_meeting.delay(meeting.pk)
        return

    if code in recall.IN_CALL_CODES:
        new_status = MeetingStatus.RECORDING
    elif code in recall.JOINING_CODES:
        new_status = MeetingStatus.JOINING
    else:
        new_status = meeting.status

    # Timeout guard for bots that never get anywhere.
    if timezone.now() - meeting.scheduled_at > STALE_AFTER:
        meeting.status = MeetingStatus.FAILED
        meeting.error_message = "Timed out waiting for the meeting recording."
        meeting.save(update_fields=["status", "error_message", "bot_status_detail", "updated_at"])
        return

    meeting.status = new_status
    meeting.save(update_fields=["status", "bot_status_detail", "updated_at"])


@shared_task(bind=True, max_retries=10, default_retry_delay=60)
def process_meeting(self, meeting_id: int):
    """Full post-call pipeline for one meeting."""
    try:
        meeting = Meeting.objects.get(pk=meeting_id)
    except Meeting.DoesNotExist:
        return

    if meeting.status == MeetingStatus.COMPLETED:
        return

    try:
        bot = recall.get_bot(meeting.recall_bot_id)
        media_url = recall.extract_media_url(bot)

        if not media_url:
            # Recording may still be rendering on Recall's side — retry shortly.
            logger.info("meeting %s: media not ready yet, retrying", meeting_id)
            raise self.retry(countdown=60)

        # 1) Recording
        Recording.objects.update_or_create(
            meeting=meeting,
            defaults={
                "media_url": media_url,
                "duration_seconds": recall.extract_duration_seconds(bot),
            },
        )

        # 2) Transcription (Deepgram, with speaker labels)
        result = deepgram.transcribe_url(media_url)
        Transcript.objects.update_or_create(
            meeting=meeting,
            defaults={
                "full_text": result["full_text"],
                "speaker_segments": result["segments"],
            },
        )

        # 3) AI notes (Groq)
        notes_data = ai.generate_notes(result["full_text"])
        MeetingNotes.objects.update_or_create(
            meeting=meeting,
            defaults={
                "summary": notes_data["summary"],
                "action_items": notes_data["action_items"],
                "key_decisions": notes_data["key_decisions"],
                "follow_ups": notes_data["follow_ups"],
            },
        )

        # 4) Email the notes
        _email_notes(meeting)

        # 5) Done
        meeting.status = MeetingStatus.COMPLETED
        meeting.completed_at = timezone.now()
        meeting.error_message = ""
        meeting.save(update_fields=["status", "completed_at", "error_message", "updated_at"])
        logger.info("meeting %s processed successfully", meeting_id)

    except Retry:  # raised by self.retry() — let Celery reschedule
        raise
    except Exception as exc:
        logger.exception("process_meeting failed for %s", meeting_id)
        try:
            raise self.retry(exc=exc, countdown=120)
        except MaxRetriesExceededError:
            meeting.status = MeetingStatus.FAILED
            meeting.error_message = f"Processing failed: {exc}"[:500]
            meeting.save(update_fields=["status", "error_message", "updated_at"])


def _email_notes(meeting: Meeting):
    from django.conf import settings

    notes = getattr(meeting, "notes", None)
    recording = getattr(meeting, "recording", None)
    detail_url = f"{settings.SITE_URL}{meeting.get_absolute_url()}"
    ok = send_html_email(
        to=meeting.notes_email,
        subject=f"Your notes for “{meeting.display_title}” are ready",
        template="meeting_notes",
        context={
            "meeting": meeting,
            "notes": notes,
            "recording": recording,
            "detail_url": detail_url,
        },
    )
    EmailLog.objects.create(
        meeting=meeting,
        recipient=meeting.notes_email,
        kind=EmailKind.MEETING_NOTES,
        status="sent" if ok else "failed",
        error="" if ok else "Resend send failed (see server logs).",
    )
