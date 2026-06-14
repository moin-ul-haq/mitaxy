"""Render + send branded HTML emails (via the Resend-backed Django mailer)."""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger("mitaxy")


def send_html_email(*, to, subject, template, context=None):
    """
    Render `templates/emails/<template>.html` with branding context and send it.

    Returns True on success, False on failure (never raises to callers).
    """
    ctx = {
        "brand_name": settings.BRAND_NAME,
        "brand_tagline": settings.BRAND_TAGLINE,
        "site_url": settings.SITE_URL,
        "subject": subject,
    }
    ctx.update(context or {})

    recipients = [to] if isinstance(to, str) else list(to)
    try:
        html_body = render_to_string(f"emails/{template}.html", ctx)
        text_body = strip_tags(html_body)
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.RESEND_FROM,
            to=recipients,
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        logger.info("Email '%s' sent to %s", template, ", ".join(recipients))
        return True
    except Exception:
        logger.exception("Failed to send email '%s' to %s", template, recipients)
        return False
