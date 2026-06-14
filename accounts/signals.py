"""Send a styled security alert email whenever a user logs in."""
import logging

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone

from meetings.services.mailer import send_html_email

logger = logging.getLogger("mitaxy")


def _client_ip(request):
    if request is None:
        return "unknown"
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


@receiver(user_logged_in)
def send_login_alert(sender, request, user, **kwargs):
    try:
        send_html_email(
            to=user.email,
            subject="New sign-in to your Mitaxy account",
            template="login_alert",
            context={
                "user": user,
                "ip_address": _client_ip(request),
                "user_agent": (request.META.get("HTTP_USER_AGENT", "Unknown device") if request else "Unknown device"),
                "login_time": timezone.localtime().strftime("%b %d, %Y at %I:%M %p %Z"),
            },
        )
    except Exception:
        # Never let an email failure block login.
        logger.exception("login alert email failed")
