from django.contrib import admin

from .models import ContactMessage, EmailLog, Meeting, MeetingNotes, Recording, Transcript


class RecordingInline(admin.StackedInline):
    model = Recording
    extra = 0


class TranscriptInline(admin.StackedInline):
    model = Transcript
    extra = 0


class MeetingNotesInline(admin.StackedInline):
    model = MeetingNotes
    extra = 0


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("display_title", "user", "platform", "status", "scheduled_at", "created_at")
    list_filter = ("status", "platform")
    search_fields = ("title", "meeting_url", "user__email", "recall_bot_id")
    readonly_fields = ("recall_bot_id", "bot_status_detail", "created_at", "updated_at", "completed_at")
    date_hierarchy = "scheduled_at"
    inlines = [RecordingInline, TranscriptInline, MeetingNotesInline]


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ("recipient", "kind", "status", "meeting", "sent_at")
    list_filter = ("kind", "status")
    search_fields = ("recipient",)


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "subject", "handled", "created_at")
    list_filter = ("handled",)
    search_fields = ("name", "email", "subject", "message")
    list_editable = ("handled",)
