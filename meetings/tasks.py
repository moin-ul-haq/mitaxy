"""
Celery tasks that drive a meeting from "scheduled" to "completed":

  poll_pending_meetings  (beat, every 60s) -> fans out one advance_meeting task
                          per in-flight bot, so many bots progress in parallel.
  advance_meeting        -> reads the bot's Recall status, updates our status,
                          logs user-visible activity events, applies timeouts.
  process_meeting        -> recording -> Deepgram -> Groq notes -> email.

The poll is the reliable engine (no Recall webhook configuration required);
the optional webhook endpoint just makes it snappier.
"""
import logging
from datetime import timedelta

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError, Retry
from django.conf import settings
from django.core.cache import cache
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

ACTIVE_STATUSES = [
    MeetingStatus.SCHEDULED,
    MeetingStatus.JOINING,
    MeetingStatus.WAITING,
    MeetingStatus.RECORDING,
]


def _minutes(setting_name, default):
    try:
        return timedelta(minutes=int(getattr(settings, setting_name, default)))
    except (TypeError, ValueError):
        return timedelta(minutes=default)


@shared_task(ignore_result=True)
def poll_pending_meetings():
    """Fan out one advance task per in-flight meeting (parallel, non-blocking)."""
    ids = (
        Meeting.objects.filter(status__in=ACTIVE_STATUSES)
        .exclude(recall_bot_id="")
        .values_list("pk", flat=True)
    )
    for pk in ids:
        advance_meeting.delay(pk)


@shared_task(ignore_result=True)
def advance_meeting(meeting_id: int):
    """Advance a single meeting based on its Recall bot status."""
    # Redis lock: a slow Recall call must not overlap with the next beat tick.
    lock_key = f"mitaxy:advance:{meeting_id}"
    if not cache.add(lock_key, 1, timeout=55):
        return
    try:
        meeting = Meeting.objects.filter(pk=meeting_id, status__in=ACTIVE_STATUSES).first()
        if meeting is None or not meeting.recall_bot_id:
            return
        try:
            _advance(meeting)
        except recall.RecallError as exc:
            # Transient API trouble: log it, keep the meeting in play — the next
            # poll retries. The stale-guard below still bounds how long we wait.
            logger.warning("poll: recall error for meeting %s: %s", meeting.pk, exc)
            _apply_timeouts(meeting)
        except Exception:
            logger.exception("poll: unexpected error for meeting %s", meeting.pk)
    finally:
        cache.delete(lock_key)


def _advance(meeting: Meeting):
    bot = recall.get_bot(meeting.recall_bot_id)
    code = recall.latest_status_code(bot)
    meeting.bot_status_detail = (code or "")[:200]

    if code:
        meeting.log_event(code, recall.friendly_status(code))

    if code in recall.FATAL_CODES:
        reason = recall.fatal_reason(code)
        meeting.set_status(MeetingStatus.FAILED, error=reason, extra_fields=["bot_status_detail"])
        meeting.log_event("failed", reason)
        return

    if code in recall.DONE_CODES:
        # Call finished — hand off to processing exactly once.
        meeting.set_status(MeetingStatus.PROCESSING, extra_fields=["bot_status_detail"])
        meeting.log_event("processing", "Preparing the recording for transcription")
        process_meeting.delay(meeting.pk)
        return

    if code in recall.IN_CALL_CODES:
        new_status = MeetingStatus.RECORDING
    elif code in recall.WAITING_ROOM_CODES:
        new_status = MeetingStatus.WAITING
    elif code in recall.JOINING_CODES:
        # Before the scheduled time the bot is parked; that's still "scheduled".
        if meeting.status == MeetingStatus.SCHEDULED and meeting.scheduled_at > timezone.now():
            new_status = MeetingStatus.SCHEDULED
        else:
            new_status = MeetingStatus.JOINING
    else:
        new_status = meeting.status

    meeting.set_status(new_status, extra_fields=["bot_status_detail"])
    _apply_timeouts(meeting)


def _apply_timeouts(meeting: Meeting):
    """Fail bots that will clearly never produce a recording — with a clear reason.

    - WAITING:   nobody admitted the bot from the waiting room.
    - JOINING:   the meeting never actually started (host never showed up).
    - any:       hard ceiling so nothing lingers forever.
    """
    now = timezone.now()
    in_status_for = now - meeting.status_changed_at

    waiting_timeout = _minutes("MITAXY_WAITING_TIMEOUT_MIN", 45)
    join_timeout = _minutes("MITAXY_JOIN_TIMEOUT_MIN", 30)
    stale_after = _minutes("MITAXY_STALE_AFTER_MIN", 360)

    if meeting.status == MeetingStatus.WAITING and in_status_for > waiting_timeout:
        reason = (
            "The bot waited in the waiting room but nobody admitted it "
            f"within {int(waiting_timeout.total_seconds() // 60)} minutes."
        )
    elif meeting.status == MeetingStatus.JOINING and in_status_for > join_timeout:
        reason = (
            "The meeting never started — the bot kept trying to join for "
            f"{int(join_timeout.total_seconds() // 60)} minutes."
        )
    elif (
        meeting.status in (MeetingStatus.SCHEDULED, MeetingStatus.JOINING, MeetingStatus.WAITING)
        and meeting.scheduled_at < now - stale_after
    ):
        reason = "Timed out waiting for the meeting recording."
    else:
        return

    recall.delete_bot(meeting.recall_bot_id)  # best-effort cleanup
    meeting.set_status(MeetingStatus.FAILED, error=reason)
    meeting.log_event("failed", reason)


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
        meeting.log_event("transcribing", "Transcribing the audio")
        result = deepgram.transcribe_url(media_url)
        Transcript.objects.update_or_create(
            meeting=meeting,
            defaults={
                "full_text": result["full_text"],
                "speaker_segments": result["segments"],
            },
        )

        # 3) AI notes (Groq)
        meeting.log_event("summarizing", "Writing your meeting notes")
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
        meeting.log_event("emailing", "Emailing your notes")
        _email_notes(meeting)

        # 5) Done
        meeting.set_status(MeetingStatus.COMPLETED, error="")
        meeting.log_event("completed", "Notes ready — emailed and available on your dashboard")
        logger.info("meeting %s processed successfully", meeting_id)

    except Retry:  # raised by self.retry() — let Celery reschedule
        raise
    except Exception as exc:
        logger.exception("process_meeting failed for %s", meeting_id)
        try:
            raise self.retry(exc=exc, countdown=120)
        except MaxRetriesExceededError:
            reason = f"Processing failed: {exc}"[:500]
            meeting.set_status(MeetingStatus.FAILED, error=reason)
            meeting.log_event("failed", "Processing failed — our team has been notified")


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


@shared_task(ignore_result=True)
def send_account_email(user_id, subject, template, extra_context=None):
    """Render + send a branded account email (login alert, welcome) off the
    request thread, so the Resend HTTP round-trip never blocks the login/
    register response. Best-effort: a missing user or send failure is logged,
    not raised."""
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        logger.warning("send_account_email: user %s no longer exists", user_id)
        return
    ctx = {"user": user}
    ctx.update(extra_context or {})
    send_html_email(to=user.email, subject=subject, template=template, context=ctx)


@shared_task(ignore_result=True)
def send_share_invites(share_id: int, emails: list, personal_links: dict):
    """Email each invited person their personal access link to a shared summary."""
    from .models import MeetingShare

    share = (
        MeetingShare.objects.select_related("meeting", "meeting__user")
        .filter(pk=share_id, is_active=True)
        .first()
    )
    if share is None:
        return
    meeting = share.meeting
    owner = meeting.user
    for email in emails:
        link = personal_links.get(email) or share.url
        ok = send_html_email(
            to=email,
            subject=f"{owner.display_name} shared meeting notes with you",
            template="share_invite",
            context={"meeting": meeting, "share": share, "owner": owner,
                     "access_url": link, "recipient": email},
        )
        EmailLog.objects.create(
            meeting=meeting,
            recipient=email,
            kind=EmailKind.SHARE_INVITE,
            status="sent" if ok else "failed",
            error="" if ok else "Resend send failed (see server logs).",
        )
