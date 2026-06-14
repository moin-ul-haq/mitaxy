from django import forms
from django.conf import settings
from django.utils import timezone

from .models import ContactMessage, Meeting, Platform

_INPUT = {"class": "field__input"}


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def format_value(self, value):
        value = super().format_value(value)
        # <input type=datetime-local> wants "YYYY-MM-DDTHH:MM"
        if value and " " in value:
            value = value.replace(" ", "T")[:16]
        return value


class ScheduleForm(forms.ModelForm):
    title = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={**_INPUT, "placeholder": "e.g. Client kickoff call (optional)"}),
    )
    meeting_url = forms.URLField(
        label="Meeting link",
        max_length=1000,
        widget=forms.URLInput(attrs={**_INPUT, "placeholder": "Paste your Zoom or Google Meet link"}),
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
        widget=DateTimeLocalInput(attrs={**_INPUT}),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
    )
    notes_email = forms.EmailField(
        label="Send notes to",
        widget=forms.EmailInput(attrs={**_INPUT, "placeholder": "you@company.com"}),
    )

    class Meta:
        model = Meeting
        fields = ["title", "meeting_url", "bot_name", "scheduled_at", "notes_email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get("bot_name"):
            self.fields["bot_name"].initial = settings.RECALL_BOT_NAME

    def clean_meeting_url(self):
        url = self.cleaned_data["meeting_url"].strip()
        platform = Meeting.detect_platform(url)
        if platform not in (Platform.ZOOM, Platform.MEET, Platform.TEAMS):
            raise forms.ValidationError(
                "Please paste a valid Zoom or Google Meet link."
            )
        self._platform = platform
        return url

    def clean_scheduled_at(self):
        when = self.cleaned_data["scheduled_at"]
        if when <= timezone.now():
            raise forms.ValidationError("Pick a time in the future.")
        # Recall needs a little lead time to dispatch the bot.
        if (when - timezone.now()).total_seconds() < 60:
            raise forms.ValidationError("Schedule at least 1 minute from now.")
        return when

    def clean_bot_name(self):
        name = (self.cleaned_data.get("bot_name") or "").strip()
        return name or settings.RECALL_BOT_NAME

    def save(self, commit=True, user=None):
        meeting = super().save(commit=False)
        meeting.platform = getattr(self, "_platform", Meeting.detect_platform(meeting.meeting_url))
        if user is not None:
            meeting.user = user
        if commit:
            meeting.save()
        return meeting


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
