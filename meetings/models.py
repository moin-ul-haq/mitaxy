import secrets

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Platform(models.TextChoices):
    ZOOM = "zoom", "Zoom"
    MEET = "meet", "Google Meet"
    TEAMS = "teams", "Microsoft Teams"
    OTHER = "other", "Other"


class MeetingStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    JOINING = "joining", "Joining"
    WAITING = "waiting", "In waiting room"
    RECORDING = "recording", "Recording"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


# Status -> CSS modifier used for badges (see static/css/app.css)
STATUS_BADGE = {
    MeetingStatus.SCHEDULED: "is-scheduled",
    MeetingStatus.JOINING: "is-active",
    MeetingStatus.WAITING: "is-waiting",
    MeetingStatus.RECORDING: "is-active",
    MeetingStatus.PROCESSING: "is-processing",
    MeetingStatus.COMPLETED: "is-completed",
    MeetingStatus.FAILED: "is-failed",
    MeetingStatus.CANCELLED: "is-muted",
}

TERMINAL_STATUSES = {MeetingStatus.COMPLETED, MeetingStatus.FAILED, MeetingStatus.CANCELLED}

# What the dashboard shows under the badge while a meeting is in flight.
STATUS_HINT = {
    MeetingStatus.SCHEDULED: "Bot will join at the scheduled time",
    MeetingStatus.JOINING: "Bot is connecting to the call",
    MeetingStatus.WAITING: "Waiting for the host to admit the bot",
    MeetingStatus.RECORDING: "In the call and recording",
    MeetingStatus.PROCESSING: "Transcribing and writing your notes",
}


class Meeting(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meetings")
    title = models.CharField(max_length=200, blank=True)
    meeting_url = models.URLField(max_length=1000)
    platform = models.CharField(max_length=12, choices=Platform.choices, default=Platform.OTHER)
    bot_name = models.CharField(max_length=80, blank=True, help_text="Name the bot shows as in the call")
    notes_email = models.EmailField()
    scheduled_at = models.DateTimeField()
    deployed_now = models.BooleanField(default=False, help_text="Bot was deployed immediately")
    # Voice agent: the bot answers when participants address it by its display name.
    voice_agent_enabled = models.BooleanField(default=False)

    status = models.CharField(max_length=16, choices=MeetingStatus.choices, default=MeetingStatus.SCHEDULED)
    # When the current status was entered — drives the joining/waiting-room timeouts.
    status_changed_at = models.DateTimeField(default=timezone.now)
    recall_bot_id = models.CharField(max_length=128, blank=True)
    # Written straight from Recall's API (no form validation) — keep generous.
    bot_status_detail = models.CharField(max_length=200, blank=True)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-scheduled_at", "-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["user", "-scheduled_at"]),
            models.Index(fields=["recall_bot_id"]),
        ]

    def __str__(self):
        return f"{self.display_title} ({self.get_status_display()})"

    @property
    def display_title(self):
        return self.title.strip() or f"{self.get_platform_display()} meeting"

    @property
    def badge_class(self):
        return STATUS_BADGE.get(self.status, "is-muted")

    @property
    def is_terminal(self):
        return self.status in TERMINAL_STATUSES

    @property
    def is_completed(self):
        return self.status == MeetingStatus.COMPLETED

    @property
    def can_deploy_now(self):
        """Only a still-waiting scheduled bot can be fast-forwarded into the call."""
        return self.status == MeetingStatus.SCHEDULED

    @property
    def can_cancel(self):
        """Anything that hasn't finished (or already failed) can be called off."""
        return self.status in {
            MeetingStatus.SCHEDULED,
            MeetingStatus.JOINING,
            MeetingStatus.WAITING,
        }

    @property
    def status_hint(self):
        return STATUS_HINT.get(self.status, "")

    def set_status(self, new_status, *, error="", extra_fields=None):
        """Transition to `new_status`, stamping status_changed_at only on change."""
        fields = {"status", "updated_at"}
        if new_status != self.status:
            self.status = new_status
            self.status_changed_at = timezone.now()
            fields.add("status_changed_at")
        if error:
            self.error_message = error
            fields.add("error_message")
        if new_status == MeetingStatus.COMPLETED and not self.completed_at:
            self.completed_at = timezone.now()
            fields.add("completed_at")
        for f in extra_fields or ():
            fields.add(f)
        self.save(update_fields=list(fields))

    def log_event(self, code, message):
        """Append to the user-visible bot activity timeline (deduped by code)."""
        last = self.events.order_by("-created_at").first()
        if last and last.code == code:
            return last
        return MeetingEvent.objects.create(meeting=self, code=code, message=message)

    def get_absolute_url(self):
        return reverse("meetings:detail", args=[self.pk])

    @staticmethod
    def detect_platform(url: str) -> str:
        u = (url or "").lower()
        if "zoom." in u or "zoom.us" in u:
            return Platform.ZOOM
        if "meet.google" in u or "meet.google.com" in u:
            return Platform.MEET
        if "teams.microsoft" in u or "teams.live" in u:
            return Platform.TEAMS
        return Platform.OTHER


class Recording(models.Model):
    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name="recording")
    # Recall presigned URLs can be very long (well over 1500 chars) — use TEXT.
    media_url = models.TextField(blank=True)
    s3_url = models.TextField(blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Recording for meeting #{self.meeting_id}"

    @property
    def duration_display(self):
        if not self.duration_seconds:
            return "—"
        m, s = divmod(int(self.duration_seconds), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m}m"
        return f"{m}m {s}s"


class Transcript(models.Model):
    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name="transcript")
    full_text = models.TextField(blank=True)
    # List of {"speaker": "Speaker 1", "text": "...", "start": 0.0, "end": 4.2}
    speaker_segments = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Transcript for meeting #{self.meeting_id}"


class MeetingNotes(models.Model):
    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name="notes")
    summary = models.TextField(blank=True)
    action_items = models.JSONField(default=list, blank=True)   # list[str]
    key_decisions = models.JSONField(default=list, blank=True)  # list[str]
    follow_ups = models.JSONField(default=list, blank=True)     # list[str]
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notes for meeting #{self.meeting_id}"


class MeetingEvent(models.Model):
    """User-visible activity timeline: what the bot is doing right now."""

    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="events")
    code = models.CharField(max_length=64)
    message = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["meeting", "-created_at"])]

    def __str__(self):
        return f"[{self.meeting_id}] {self.code}: {self.message}"


def _share_token():
    return secrets.token_urlsafe(24)


class ShareVisibility(models.TextChoices):
    LINK = "link", "Anyone with the link"
    RESTRICTED = "restricted", "Only specific people"


class MeetingShare(models.Model):
    """Public share of a meeting's summary, controlled by the owner."""

    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name="share")
    token = models.CharField(max_length=64, unique=True, default=_share_token)
    visibility = models.CharField(
        max_length=12, choices=ShareVisibility.choices, default=ShareVisibility.LINK
    )
    # Lowercased emails allowed to view when visibility == restricted.
    allowed_emails = models.JSONField(default=list, blank=True)
    include_transcript = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Share for meeting #{self.meeting_id} ({self.get_visibility_display()})"

    @property
    def url(self):
        return f"{settings.SITE_URL}{reverse('meetings:shared', args=[self.token])}"

    @property
    def is_restricted(self):
        return self.visibility == ShareVisibility.RESTRICTED

    def allows_email(self, email):
        return (email or "").strip().lower() in [e.lower() for e in self.allowed_emails]

    def regenerate_token(self):
        self.token = _share_token()
        self.save(update_fields=["token", "updated_at"])


class EmailKind(models.TextChoices):
    WELCOME = "welcome", "Welcome"
    LOGIN_ALERT = "login_alert", "Login alert"
    MEETING_NOTES = "meeting_notes", "Meeting notes"
    PASSWORD_RESET = "password_reset", "Password reset"
    SHARE_INVITE = "share_invite", "Share invite"
    SHARE_ACCESS = "share_access", "Share access link"


class EmailLog(models.Model):
    meeting = models.ForeignKey(Meeting, on_delete=models.SET_NULL, null=True, blank=True, related_name="emails")
    recipient = models.EmailField()
    kind = models.CharField(max_length=20, choices=EmailKind.choices, default=EmailKind.MEETING_NOTES)
    status = models.CharField(max_length=12, default="sent")  # sent | failed
    error = models.TextField(blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.get_kind_display()} -> {self.recipient} ({self.status})"


class ContactMessage(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField()
    subject = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    handled = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} <{self.email}>"
