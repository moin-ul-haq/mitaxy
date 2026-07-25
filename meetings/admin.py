from django.contrib import admin, messages
from django.utils.html import format_html

from .models import (
    ContactMessage,
    EmailLog,
    Meeting,
    MeetingEvent,
    MeetingNotes,
    MeetingShare,
    MeetingStatus,
    Recording,
    Transcript,
)
from .services import recall


class RecordingInline(admin.StackedInline):
    model = Recording
    extra = 0


class TranscriptInline(admin.StackedInline):
    model = Transcript
    extra = 0
    fields = ("full_text",)


class MeetingNotesInline(admin.StackedInline):
    model = MeetingNotes
    extra = 0


class MeetingShareInline(admin.StackedInline):
    model = MeetingShare
    extra = 0
    readonly_fields = ("token", "share_link", "created_at", "updated_at")
    fields = ("is_active", "visibility", "allowed_emails", "include_transcript",
              "token", "share_link", "created_at", "updated_at")

    @admin.display(description="Share link")
    def share_link(self, obj):
        if not obj or not obj.pk:
            return "—"
        return format_html('<a href="{}" target="_blank">{}</a>', obj.url, obj.url)


class MeetingEventInline(admin.TabularInline):
    model = MeetingEvent
    extra = 0
    readonly_fields = ("code", "message", "created_at")
    can_delete = False
    ordering = ("-created_at",)

    def has_add_permission(self, request, obj=None):
        return False


class EmailLogInline(admin.TabularInline):
    model = EmailLog
    extra = 0
    readonly_fields = ("recipient", "kind", "status", "error", "sent_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("display_title", "user", "platform", "status_badge", "scheduled_at",
                    "deployed_now", "created_at")
    list_filter = ("status", "platform", "deployed_now", ("scheduled_at", admin.DateFieldListFilter))
    search_fields = ("title", "meeting_url", "user__email", "recall_bot_id", "notes_email")
    readonly_fields = ("recall_bot_id", "bot_status_detail", "status_changed_at",
                       "created_at", "updated_at", "completed_at", "error_message")
    date_hierarchy = "scheduled_at"
    list_select_related = ("user",)
    list_per_page = 40
    actions = ("cancel_bots", "repoll_status")
    inlines = [MeetingEventInline, RecordingInline, TranscriptInline,
               MeetingNotesInline, MeetingShareInline, EmailLogInline]

    fieldsets = (
        ("Meeting", {"fields": ("user", "title", "meeting_url", "platform", "bot_name",
                                "notes_email", "scheduled_at", "deployed_now")}),
        ("Bot state", {"fields": ("status", "status_changed_at", "recall_bot_id",
                                  "bot_status_detail", "error_message")}),
        ("Timestamps", {"fields": ("created_at", "updated_at", "completed_at"),
                        "classes": ("collapse",)}),
    )

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        colors = {
            "scheduled": "#1d4ed8", "joining": "#0d9488", "waiting": "#b45309",
            "recording": "#0d9488", "processing": "#b45309",
            "completed": "#047857", "failed": "#b91c1c", "cancelled": "#64748b",
        }
        color = colors.get(obj.status, "#64748b")
        return format_html(
            '<span style="padding:2px 10px;border-radius:999px;font-weight:600;'
            'font-size:11px;background:{}18;color:{};">{}</span>',
            color, color, obj.get_status_display(),
        )

    @admin.action(description="Cancel selected bots (delete from Recall)")
    def cancel_bots(self, request, queryset):
        done = 0
        for meeting in queryset:
            if meeting.can_cancel:
                recall.delete_bot(meeting.recall_bot_id)
                meeting.set_status(MeetingStatus.CANCELLED)
                meeting.log_event("cancelled", "Cancelled by an administrator")
                done += 1
        self.message_user(request, f"Cancelled {done} meeting(s).", messages.SUCCESS)

    @admin.action(description="Re-poll bot status now")
    def repoll_status(self, request, queryset):
        from .tasks import advance_meeting

        count = 0
        for meeting in queryset.exclude(recall_bot_id=""):
            advance_meeting.delay(meeting.pk)
            count += 1
        self.message_user(request, f"Queued a status refresh for {count} meeting(s).", messages.SUCCESS)


@admin.register(MeetingShare)
class MeetingShareAdmin(admin.ModelAdmin):
    list_display = ("meeting", "visibility", "is_active", "include_transcript",
                    "invited", "created_at")
    list_filter = ("visibility", "is_active", "include_transcript")
    search_fields = ("meeting__title", "meeting__user__email", "token")
    readonly_fields = ("token", "created_at", "updated_at")
    list_select_related = ("meeting",)

    @admin.display(description="People invited")
    def invited(self, obj):
        return len(obj.allowed_emails or [])


@admin.register(MeetingEvent)
class MeetingEventAdmin(admin.ModelAdmin):
    list_display = ("meeting", "code", "message", "created_at")
    list_filter = ("code",)
    search_fields = ("meeting__title", "meeting__user__email", "message")
    readonly_fields = ("meeting", "code", "message", "created_at")
    list_select_related = ("meeting",)

    def has_add_permission(self, request):
        return False


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ("recipient", "kind", "status_badge", "meeting", "sent_at")
    list_filter = ("kind", "status")
    search_fields = ("recipient", "meeting__title")
    readonly_fields = ("meeting", "recipient", "kind", "status", "error", "sent_at")
    list_select_related = ("meeting",)
    date_hierarchy = "sent_at"

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        color = "#047857" if obj.status == "sent" else "#b91c1c"
        return format_html(
            '<span style="padding:2px 10px;border-radius:999px;font-weight:600;'
            'font-size:11px;background:{}18;color:{};">{}</span>',
            color, color, obj.status,
        )

    def has_add_permission(self, request):
        return False


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "subject", "handled", "created_at")
    list_filter = ("handled", ("created_at", admin.DateFieldListFilter))
    search_fields = ("name", "email", "subject", "message")
    list_editable = ("handled",)
    actions = ("mark_handled",)
    date_hierarchy = "created_at"

    @admin.action(description="Mark selected as handled")
    def mark_handled(self, request, queryset):
        updated = queryset.update(handled=True)
        self.message_user(request, f"Marked {updated} message(s) as handled.", messages.SUCCESS)
