"""Stats for the custom admin index dashboard."""
from datetime import timedelta

from django import template
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone

from meetings.models import ContactMessage, EmailLog, Meeting, MeetingStatus

register = template.Library()


@register.simple_tag
def admin_dashboard_stats():
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    User = get_user_model()

    meetings = Meeting.objects.aggregate(
        total=Count("id"),
        this_week=Count("id", filter=Q(created_at__gte=week_ago)),
        live=Count("id", filter=Q(status__in=[
            MeetingStatus.JOINING, MeetingStatus.WAITING,
            MeetingStatus.RECORDING, MeetingStatus.PROCESSING,
        ])),
        scheduled=Count("id", filter=Q(status=MeetingStatus.SCHEDULED)),
        completed=Count("id", filter=Q(status=MeetingStatus.COMPLETED)),
        failed=Count("id", filter=Q(status=MeetingStatus.FAILED)),
    )
    users = User.objects.aggregate(
        total=Count("id"),
        this_week=Count("id", filter=Q(date_joined__gte=week_ago)),
    )
    emails = EmailLog.objects.aggregate(
        total=Count("id"),
        failed=Count("id", filter=Q(status="failed")),
    )
    unhandled_contacts = ContactMessage.objects.filter(handled=False).count()

    return {
        "meetings": meetings,
        "users": users,
        "emails": emails,
        "unhandled_contacts": unhandled_contacts,
    }
