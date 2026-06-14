import datetime
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import ContactForm, ScheduleForm
from .models import Meeting, MeetingStatus
from .services import recall

logger = logging.getLogger("mitaxy")


def _utc_iso(dt):
    """ISO-8601 UTC string for Recall's join_at."""
    return dt.astimezone(datetime.timezone.utc).isoformat()


def landing(request):
    if request.user.is_authenticated:
        return redirect("meetings:dashboard")
    return render(request, "landing.html")


@login_required
def dashboard(request):
    meetings = Meeting.objects.filter(user=request.user)
    counts = {
        "total": meetings.count(),
        "completed": meetings.filter(status=MeetingStatus.COMPLETED).count(),
        "upcoming": meetings.filter(
            status__in=[MeetingStatus.SCHEDULED, MeetingStatus.JOINING, MeetingStatus.RECORDING]
        ).count(),
    }
    has_active = meetings.exclude(
        status__in=[MeetingStatus.COMPLETED, MeetingStatus.FAILED, MeetingStatus.CANCELLED]
    ).exists()
    return render(request, "meetings/dashboard.html", {
        "meetings": meetings,
        "counts": counts,
        "has_active": has_active,
    })


@login_required
def schedule(request):
    if request.method == "POST":
        form = ScheduleForm(request.POST)
        if form.is_valid():
            meeting = form.save(commit=False, user=request.user)
            # Dispatch the Recall bot for the scheduled time (UTC).
            try:
                bot = recall.create_bot(
                    meeting.meeting_url,
                    _utc_iso(meeting.scheduled_at),
                    bot_name=meeting.bot_name,
                )
                meeting.recall_bot_id = bot.get("id", "")
                if not meeting.recall_bot_id:
                    raise recall.RecallError("Recall did not return a bot id")
                meeting.status = MeetingStatus.SCHEDULED
                meeting.save()
                messages.success(
                    request,
                    f"Done! A Mitaxy notetaker will join “{meeting.display_title}” and email your notes afterward.",
                )
                return redirect("meetings:dashboard")
            except recall.RecallError as exc:
                logger.error("create_bot failed: %s", exc)
                messages.error(
                    request,
                    "We couldn't schedule the bot for that link. Double-check the meeting URL and try again.",
                )
    else:
        form = ScheduleForm(initial={"notes_email": request.user.email})
    return render(request, "meetings/schedule.html", {"form": form})


@login_required
def detail(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk, user=request.user)
    return render(request, "meetings/detail.html", {
        "meeting": meeting,
        "notes": getattr(meeting, "notes", None),
        "transcript": getattr(meeting, "transcript", None),
        "recording": getattr(meeting, "recording", None),
    })


@login_required
@require_POST
def deploy_now(request, pk):
    """Fast-forward a scheduled bot so it joins the call immediately."""
    meeting = get_object_or_404(Meeting, pk=pk, user=request.user)
    if not meeting.can_deploy_now:
        messages.info(request, "This meeting can't be deployed now — it's already in progress or finished.")
        return redirect("meetings:dashboard")

    # Cancel the existing scheduled bot, then dispatch a fresh one with no join_at.
    recall.delete_bot(meeting.recall_bot_id)
    try:
        bot = recall.create_bot(meeting.meeting_url, None, bot_name=meeting.bot_name)
        new_id = bot.get("id", "")
        if not new_id:
            raise recall.RecallError("Recall did not return a bot id")
        meeting.recall_bot_id = new_id
        meeting.scheduled_at = timezone.now()
        meeting.deployed_now = True
        meeting.status = MeetingStatus.JOINING
        meeting.save(update_fields=["recall_bot_id", "scheduled_at", "deployed_now", "status", "updated_at"])
        messages.success(request, f"Deploying the bot into “{meeting.display_title}” now. It will join momentarily.")
    except recall.RecallError as exc:
        logger.error("deploy_now failed: %s", exc)
        messages.error(request, "We couldn't deploy the bot right now. Please check the meeting link and try again.")
    return redirect("meetings:dashboard")


def about(request):
    return render(request, "pages/about.html")


def contact(request):
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Thanks for reaching out! We'll get back to you soon.")
            return redirect("meetings:contact")
    else:
        initial = {"email": request.user.email} if request.user.is_authenticated else {}
        form = ContactForm(initial=initial)
    return render(request, "pages/contact.html", {"form": form})


@login_required
def statuses(request):
    """Lightweight JSON for the dashboard's live status refresh."""
    meetings = Meeting.objects.filter(user=request.user)
    data = {
        str(m.pk): {
            "status": m.status,
            "label": m.get_status_display(),
            "badge": m.badge_class,
            "terminal": m.is_terminal,
        }
        for m in meetings
    }
    return JsonResponse({"meetings": data})


@csrf_exempt
@require_POST
def recall_webhook(request):
    """
    Optional Recall webhook receiver. Configuring this in the Recall dashboard
    makes processing instant; without it, the 60s poll handles everything.
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

    bot_id = (
        payload.get("data", {}).get("bot_id")
        or payload.get("bot_id")
        or payload.get("data", {}).get("bot", {}).get("id")
    )
    if bot_id:
        from .tasks import poll_pending_meetings
        # Re-poll promptly; the poll task is idempotent.
        poll_pending_meetings.delay()
        logger.info("recall webhook received for bot %s", bot_id)
    return JsonResponse({"ok": True})
