import datetime
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import ContactForm, ScheduleForm, ShareForm
from .models import (
    EmailKind,
    EmailLog,
    Meeting,
    MeetingEvent,
    MeetingShare,
    MeetingStatus,
    ShareVisibility,
)
from .services import recall
from .services.mailer import send_html_email

logger = logging.getLogger("mitaxy")

SHARE_ACCESS_SALT = "mitaxy.share.access"
SHARE_ACCESS_MAX_AGE = 60 * 60 * 24 * 7  # personal access links live 7 days

ACTIVE_STATUSES = [
    MeetingStatus.SCHEDULED,
    MeetingStatus.JOINING,
    MeetingStatus.WAITING,
    MeetingStatus.RECORDING,
    MeetingStatus.PROCESSING,
]

DASHBOARD_TABS = {
    "all": None,
    "upcoming": [MeetingStatus.SCHEDULED],
    "live": [
        MeetingStatus.JOINING,
        MeetingStatus.WAITING,
        MeetingStatus.RECORDING,
        MeetingStatus.PROCESSING,
    ],
    "done": [MeetingStatus.COMPLETED],
}


def _utc_iso(dt):
    """ISO-8601 UTC string for Recall's join_at."""
    return dt.astimezone(datetime.timezone.utc).isoformat()


def landing(request):
    if request.user.is_authenticated:
        return redirect("meetings:dashboard")
    return render(request, "landing.html")


@login_required
def dashboard(request):
    base = Meeting.objects.filter(user=request.user)

    # One aggregate query for all the stat cards (instead of 4 COUNTs).
    agg = base.aggregate(
        total=Count("id"),
        upcoming=Count("id", filter=Q(status=MeetingStatus.SCHEDULED)),
        live=Count(
            "id",
            filter=Q(status__in=[
                MeetingStatus.JOINING,
                MeetingStatus.WAITING,
                MeetingStatus.RECORDING,
                MeetingStatus.PROCESSING,
            ]),
        ),
        completed=Count("id", filter=Q(status=MeetingStatus.COMPLETED)),
    )

    tab = request.GET.get("tab", "all")
    if tab not in DASHBOARD_TABS:
        tab = "all"
    meetings = base
    if DASHBOARD_TABS[tab]:
        meetings = meetings.filter(status__in=DASHBOARD_TABS[tab])

    query = (request.GET.get("q") or "").strip()
    if query:
        meetings = meetings.filter(Q(title__icontains=query) | Q(meeting_url__icontains=query))

    paginator = Paginator(meetings, 12)
    page = paginator.get_page(request.GET.get("page"))

    has_active = base.filter(status__in=ACTIVE_STATUSES).exists()
    show_onboarding = not request.user.onboarding_completed

    return render(request, "meetings/dashboard.html", {
        "meetings": page.object_list,
        "page": page,
        "counts": agg,
        "has_active": has_active,
        "tab": tab,
        "query": query,
        "show_onboarding": show_onboarding,
        "is_new_user": agg["total"] == 0,
    })


@login_required
def schedule(request):
    if request.method == "POST":
        form = ScheduleForm(request.POST)
        if form.is_valid():
            meeting = form.save(commit=False, user=request.user)
            try:
                if form.deploy_now:
                    # Meeting is happening now — bot joins immediately.
                    bot = recall.create_bot(meeting.meeting_url, None, bot_name=meeting.bot_name)
                else:
                    bot = recall.create_bot(
                        meeting.meeting_url,
                        _utc_iso(meeting.scheduled_at),
                        bot_name=meeting.bot_name,
                    )
                meeting.recall_bot_id = bot.get("id", "")
                if not meeting.recall_bot_id:
                    raise recall.RecallError("Recall did not return a bot id")
                if form.deploy_now:
                    meeting.deployed_now = True
                    meeting.status = MeetingStatus.JOINING
                else:
                    meeting.status = MeetingStatus.SCHEDULED
                meeting.save()
                if form.deploy_now:
                    meeting.log_event("deploying", "Bot deployed — connecting to the call now")
                    messages.success(
                        request,
                        f"The notetaker is joining “{meeting.display_title}” right now.",
                    )
                else:
                    meeting.log_event(
                        "scheduled",
                        f"Bot scheduled for {timezone.localtime(meeting.scheduled_at).strftime('%b %d, %Y at %I:%M %p')}",
                    )
                    messages.success(
                        request,
                        f"Done! A Mitaxy notetaker will join “{meeting.display_title}” and email your notes afterward.",
                    )
                return redirect(meeting.get_absolute_url())
            except recall.RecallError as exc:
                logger.error("create_bot failed: %s", exc)
                messages.error(
                    request,
                    "We couldn't dispatch the bot for that link. Double-check the meeting URL and try again.",
                )
    else:
        initial = {"notes_email": request.user.email}
        if request.GET.get("now") == "1":
            initial["start_mode"] = ScheduleForm.START_NOW
        form = ScheduleForm(initial=initial)
    return render(request, "meetings/schedule.html", {"form": form})


@login_required
def detail(request, pk):
    meeting = get_object_or_404(
        Meeting.objects.select_related("user").prefetch_related("events"),
        pk=pk, user=request.user,
    )
    share = getattr(meeting, "share", None)
    share_form_initial = {
        "visibility": share.visibility if share else ShareVisibility.LINK,
        "emails": ", ".join(share.allowed_emails) if share else "",
        "include_transcript": share.include_transcript if share else False,
    }
    return render(request, "meetings/detail.html", {
        "meeting": meeting,
        "notes": getattr(meeting, "notes", None),
        "transcript": getattr(meeting, "transcript", None),
        "recording": getattr(meeting, "recording", None),
        "events": meeting.events.all(),
        "share": share,
        "share_form_initial": share_form_initial,
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
        meeting.set_status(
            MeetingStatus.JOINING,
            extra_fields=["recall_bot_id", "scheduled_at", "deployed_now"],
        )
        meeting.log_event("deploying", "Bot deployed — connecting to the call now")
        messages.success(request, f"Deploying the bot into “{meeting.display_title}” now. It will join momentarily.")
    except recall.RecallError as exc:
        logger.error("deploy_now failed: %s", exc)
        messages.error(request, "We couldn't deploy the bot right now. Please check the meeting link and try again.")
    return redirect(meeting.get_absolute_url())


@login_required
@require_POST
def cancel(request, pk):
    """Call off a bot that hasn't finished (scheduled, joining, or waiting)."""
    meeting = get_object_or_404(Meeting, pk=pk, user=request.user)
    if not meeting.can_cancel:
        messages.info(request, "This meeting can't be cancelled anymore.")
        return redirect(meeting.get_absolute_url())

    recall.delete_bot(meeting.recall_bot_id)
    meeting.set_status(MeetingStatus.CANCELLED)
    meeting.log_event("cancelled", "Cancelled — the bot will not join this meeting")
    messages.success(request, f"“{meeting.display_title}” was cancelled. The bot won't join.")
    return redirect("meetings:dashboard")


# ---------------------------------------------------------------------------
# Sharing
# ---------------------------------------------------------------------------
def _personal_share_links(share, emails):
    """Signed per-recipient access links for a restricted share."""
    signer = TimestampSigner(salt=SHARE_ACCESS_SALT)
    links = {}
    for email in emails:
        key = signer.sign_object({"share": share.pk, "email": email})
        links[email] = f"{share.url}?key={key}"
    return links


@login_required
@require_POST
def share_update(request, pk):
    """Create/update the share settings for one meeting (owner only)."""
    meeting = get_object_or_404(Meeting, pk=pk, user=request.user)
    if not meeting.is_completed:
        messages.info(request, "You can share a meeting once its notes are ready.")
        return redirect(meeting.get_absolute_url())

    action = request.POST.get("action", "save")
    share = getattr(meeting, "share", None)

    if action == "disable":
        if share:
            share.is_active = False
            share.save(update_fields=["is_active", "updated_at"])
        messages.success(request, "Sharing is turned off. The link no longer works.")
        return redirect(meeting.get_absolute_url())

    if action == "regenerate":
        if share:
            share.regenerate_token()
            share.is_active = True
            share.save(update_fields=["is_active", "updated_at"])
            messages.success(request, "A fresh link was generated. Old links no longer work.")
        return redirect(meeting.get_absolute_url())

    form = ShareForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect(meeting.get_absolute_url())

    visibility = form.cleaned_data["visibility"]
    emails = form.cleaned_data["emails"]
    if share is None:
        share = MeetingShare(meeting=meeting)
    share.visibility = visibility
    share.allowed_emails = emails if visibility == ShareVisibility.RESTRICTED else []
    share.include_transcript = form.cleaned_data["include_transcript"]
    share.is_active = True
    share.save()

    if visibility == ShareVisibility.RESTRICTED and action == "save_invite" and emails:
        from .tasks import send_share_invites

        links = _personal_share_links(share, emails)
        send_share_invites.delay(share.pk, emails, links)
        messages.success(
            request,
            f"Sharing is on. We emailed a personal access link to {len(emails)} "
            f"{'person' if len(emails) == 1 else 'people'}.",
        )
    else:
        messages.success(request, "Sharing settings saved. Copy the link and send it to anyone who needs it.")
    return redirect(meeting.get_absolute_url())


def shared(request, token):
    """Public share page. 'link' mode renders directly; 'restricted' mode needs
    a personal signed key (from the invite email) or a verified session."""
    share = (
        MeetingShare.objects.select_related("meeting", "meeting__user")
        .filter(token=token, is_active=True)
        .first()
    )
    if share is None:
        return render(request, "meetings/shared_gone.html", status=404)

    meeting = share.meeting
    session_key = f"share_access:{share.pk}"

    if share.is_restricted:
        signer = TimestampSigner(salt=SHARE_ACCESS_SALT)

        # 1) Arriving with a personal key from the invite email?
        key = request.GET.get("key")
        if key:
            try:
                data = signer.unsign_object(key, max_age=SHARE_ACCESS_MAX_AGE)
                if data.get("share") == share.pk and share.allows_email(data.get("email")):
                    request.session[session_key] = data["email"]
                    return redirect("meetings:shared", token=share.token)
            except (BadSignature, SignatureExpired):
                pass
            messages.error(request, "That access link is invalid or has expired. Request a new one below.")
            return redirect("meetings:shared", token=share.token)

        # 2) Already verified in this browser session?
        viewer_email = request.session.get(session_key)
        if not viewer_email or not share.allows_email(viewer_email):
            # 3) Ask who they are; email them a personal link if they're allowed.
            if request.method == "POST":
                email = (request.POST.get("email") or "").strip().lower()
                # Always answer the same way — don't leak who's on the list.
                if share.allows_email(email):
                    link = _personal_share_links(share, [email])[email]
                    ok = send_html_email(
                        to=email,
                        subject=f"Your access link — {meeting.display_title}",
                        template="share_access",
                        context={"meeting": meeting, "share": share,
                                 "owner": meeting.user, "access_url": link},
                    )
                    EmailLog.objects.create(
                        meeting=meeting, recipient=email, kind=EmailKind.SHARE_ACCESS,
                        status="sent" if ok else "failed",
                        error="" if ok else "Resend send failed (see server logs).",
                    )
                return render(request, "meetings/shared_gate.html", {
                    "share": share, "meeting": meeting, "sent": True, "email": email,
                })
            return render(request, "meetings/shared_gate.html", {"share": share, "meeting": meeting})

    return render(request, "meetings/shared.html", {
        "share": share,
        "meeting": meeting,
        "notes": getattr(meeting, "notes", None),
        "transcript": getattr(meeting, "transcript", None) if share.include_transcript else None,
        "recording": getattr(meeting, "recording", None),
        "viewer_email": request.session.get(session_key, ""),
    })


# ---------------------------------------------------------------------------
# Marketing / misc
# ---------------------------------------------------------------------------
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
    meetings = Meeting.objects.filter(user=request.user).prefetch_related(
        Prefetch("events", queryset=MeetingEvent.objects.order_by("-created_at"))
    )
    data = {}
    for m in meetings:
        events = list(m.events.all()[:1])
        data[str(m.pk)] = {
            "status": m.status,
            "label": m.get_status_display(),
            "badge": m.badge_class,
            "terminal": m.is_terminal,
            "hint": events[0].message if events else m.status_hint,
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
        from .tasks import advance_meeting

        meeting = Meeting.objects.filter(recall_bot_id=bot_id).only("pk").first()
        if meeting:
            advance_meeting.delay(meeting.pk)
        logger.info("recall webhook received for bot %s", bot_id)
    return JsonResponse({"ok": True})
