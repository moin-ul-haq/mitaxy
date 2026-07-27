from django import forms
from django.conf import settings
from django.core.validators import validate_email
from django.utils import timezone

from .models import ContactMessage, Meeting, MeetingShare, Platform, ShareVisibility

_INPUT = {"class": "field__input"}

MAX_SHARE_EMAILS = 50


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def format_value(self, value):
        value = super().format_value(value)
        # <input type=datetime-local> wants "YYYY-MM-DDTHH:MM"
        if value and " " in value:
            value = value.replace(" ", "T")[:16]
        return value


class ScheduleForm(forms.ModelForm):
    START_LATER = "later"
    START_NOW = "now"

    start_mode = forms.ChoiceField(
        choices=[(START_LATER, "Schedule for later"), (START_NOW, "Deploy right now")],
        initial=START_LATER,
        widget=forms.RadioSelect,
        label="When should the bot join?",
    )
    title = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={**_INPUT, "placeholder": "e.g. Client kickoff call (optional)"}),
    )
    meeting_url = forms.URLField(
        label="Meeting link",
        max_length=1000,
        widget=forms.URLInput(attrs={**_INPUT, "placeholder": "Paste your Zoom, Google Meet or Teams link"}),
    )
    bot_name = forms.CharField(
        label="Bot display name",
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs={**_INPUT, "placeholder": "Name shown in the call (e.g. Mitaxy Notetaker)"}),
        help_text="This is the name participants see when the bot joins.",
    )
    scheduled_at = forms.DateTimeField(
        label="Date & time",
        required=False,  # only needed when scheduling for later
        widget=DateTimeLocalInput(attrs={**_INPUT}),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
        help_text="Your local time. The bot joins the call at this moment.",
    )
    notes_email = forms.EmailField(
        label="Send notes to",
        widget=forms.EmailInput(attrs={**_INPUT, "placeholder": "you@company.com"}),
    )
    voice_agent_enabled = forms.BooleanField(
        required=False,
        label="Voice agent (beta)",
        help_text="The bot answers out loud when someone calls it by its display name.",
    )

    class Meta:
        model = Meeting
        fields = ["title", "meeting_url", "bot_name", "scheduled_at", "notes_email", "voice_agent_enabled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get("bot_name"):
            self.fields["bot_name"].initial = settings.RECALL_BOT_NAME

    def clean_meeting_url(self):
        url = self.cleaned_data["meeting_url"].strip()
        platform = Meeting.detect_platform(url)
        if platform not in (Platform.ZOOM, Platform.MEET, Platform.TEAMS):
            raise forms.ValidationError(
                "Please paste a valid Zoom, Google Meet or Microsoft Teams link."
            )
        self._platform = platform
        return url

    def clean_bot_name(self):
        name = (self.cleaned_data.get("bot_name") or "").strip()
        return name or settings.RECALL_BOT_NAME

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("start_mode") or self.START_LATER
        when = cleaned.get("scheduled_at")

        if mode == self.START_NOW:
            # Meeting is happening now — the bot joins immediately, no date needed.
            cleaned["scheduled_at"] = timezone.now()
            return cleaned

        if when is None:
            self.add_error("scheduled_at", "Pick a date & time, or choose “Deploy right now”.")
            return cleaned
        if when <= timezone.now():
            self.add_error(
                "scheduled_at",
                "That time is in the past. Pick a future time, or choose “Deploy right now” "
                "if the meeting is already happening.",
            )
        elif (when - timezone.now()).total_seconds() < 60:
            # Recall needs a little lead time to dispatch the bot.
            self.add_error("scheduled_at", "Schedule at least 1 minute from now — or deploy right now.")
        return cleaned

    @property
    def deploy_now(self):
        return self.cleaned_data.get("start_mode") == self.START_NOW

    def save(self, commit=True, user=None):
        meeting = super().save(commit=False)
        meeting.platform = getattr(self, "_platform", Meeting.detect_platform(meeting.meeting_url))
        if user is not None:
            meeting.user = user
        if commit:
            meeting.save()
        return meeting


class ShareForm(forms.Form):
    """Owner-facing controls for sharing one meeting's summary."""

    visibility = forms.ChoiceField(choices=ShareVisibility.choices, initial=ShareVisibility.LINK)
    emails = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "field__input", "rows": 2,
            "placeholder": "teammate@company.com, boss@company.com",
        }),
        help_text="Only these people will be able to open the link.",
    )
    include_transcript = forms.BooleanField(required=False)

    def clean_emails(self):
        raw = self.cleaned_data.get("emails") or ""
        emails, seen = [], set()
        for chunk in raw.replace("\n", ",").replace(";", ",").split(","):
            email = chunk.strip().lower()
            if not email or email in seen:
                continue
            try:
                validate_email(email)
            except forms.ValidationError:
                raise forms.ValidationError(f"“{email}” doesn't look like a valid email address.")
            seen.add(email)
            emails.append(email)
        if len(emails) > MAX_SHARE_EMAILS:
            raise forms.ValidationError(f"You can share with at most {MAX_SHARE_EMAILS} people.")
        return emails

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("visibility") == ShareVisibility.RESTRICTED and not cleaned.get("emails"):
            self.add_error("emails", "Add at least one email address, or switch to “Anyone with the link”.")
        return cleaned


class ContactForm(forms.ModelForm):
    name = forms.CharField(
        max_length=120,
        widget=forms.TextInput(attrs={**_INPUT, "placeholder": "Your name"}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={**_INPUT, "placeholder": "you@company.com"}),
    )
    subject = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={**_INPUT, "placeholder": "What's this about? (optional)"}),
    )
    message = forms.CharField(
        widget=forms.Textarea(attrs={**_INPUT, "rows": 5, "placeholder": "How can we help?"}),
    )

    class Meta:
        model = ContactMessage
        fields = ["name", "email", "subject", "message"]
