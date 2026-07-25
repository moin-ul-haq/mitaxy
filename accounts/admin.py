from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Count

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("-date_joined",)
    list_display = ("email", "full_name", "auth_provider", "meeting_count",
                    "onboarding_completed", "is_staff", "is_active", "date_joined")
    search_fields = ("email", "full_name")
    list_filter = ("auth_provider", "is_staff", "is_superuser", "is_active", "onboarding_completed")
    readonly_fields = ("last_login", "date_joined")
    list_per_page = 40
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("full_name", "auth_provider", "onboarding_completed")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "full_name", "password1", "password2"),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_meetings=Count("meetings"))

    @admin.display(description="Meetings", ordering="_meetings")
    def meeting_count(self, obj):
        return obj._meetings
