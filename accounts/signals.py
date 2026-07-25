"""Send a styled security alert email whenever a user logs in."""
import logging

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone

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
    # Hand the email off to Celery so the Resend round-trip never blocks the
    # login response. Enqueue is non-blocking; failure to enqueue must never
    # break login.
    from meetings.tasks import send_account_email

    try:
        send_account_email.delay(
            user.pk,
            "New sign-in to your Mitaxy account",
            "login_alert",
            {
                "ip_address": _client_ip(request),
                "user_agent": (request.META.get("HTTP_USER_AGENT", "Unknown device") if request else "Unknown device"),
                "login_time": timezone.localtime().strftime("%b %d, %Y at %I:%M %p %Z"),
            },
        )
    except Exception:
        # Never let an email/broker failure block login.
        logger.exception("login alert enqueue failed")
