"""
Django email backend that delivers through the Resend HTTP API.

This lets Django's built-in flows (e.g. password reset) send mail via Resend.
Rich product emails (login alert, welcome, meeting notes) use
meetings.services.mailer directly, but they share the same transport.
"""
import logging

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger("mitaxy")

RESEND_ENDPOINT = "https://api.resend.com/emails"


class ResendEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        api_key = settings.RESEND_API_KEY
        if not api_key:
            if not self.fail_silently:
                logger.warning("RESEND_API_KEY not set; skipping %d email(s).", len(email_messages))
            return 0

        sent = 0
        for message in email_messages:
            try:
                html = None
                text = message.body
                # If this is an EmailMultiAlternatives with an HTML part, use it.
                for content, mimetype in getattr(message, "alternatives", []) or []:
                    if mimetype == "text/html":
                        html = content
                payload = {
                    "from": message.from_email or settings.RESEND_FROM,
                    "to": list(message.to),
                    "subject": message.subject,
                }
                if html:
                    payload["html"] = html
                    if text:
                        payload["text"] = text
                else:
                    payload["text"] = text
                if message.cc:
                    payload["cc"] = list(message.cc)
                if message.bcc:
                    payload["bcc"] = list(message.bcc)

                resp = requests.post(
                    RESEND_ENDPOINT,
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=20,
                )
                if resp.status_code in (200, 201):
                    sent += 1
                else:
                    logger.error("Resend send failed (%s): %s", resp.status_code, resp.text[:300])
                    if not self.fail_silently:
                        raise RuntimeError(f"Resend error {resp.status_code}: {resp.text[:200]}")
            except Exception:
                logger.exception("Resend email send raised")
                if not self.fail_silently:
                    raise
        return sent
