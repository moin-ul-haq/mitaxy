"""
Google OAuth 2.0 sign-in — implemented directly on top of `requests`, no extra
dependency. Enabled only when GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are set
in the environment; the buttons hide themselves otherwise.

Flow: /accounts/google/start/ -> Google consent -> /accounts/google/callback/
"""
import logging
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings

logger = logging.getLogger("mitaxy")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
TIMEOUT = 20

STATE_SESSION_KEY = "google_oauth_state"


class GoogleOAuthError(Exception):
    pass


def enabled():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def redirect_uri():
    return f"{settings.SITE_URL}/accounts/google/callback/"


def build_auth_url(request):
    """Start the flow: stash a CSRF state in the session, return Google's URL."""
    state = secrets.token_urlsafe(24)
    request.session[STATE_SESSION_KEY] = state
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(request, code, state):
    """Validate state, swap the code for tokens, and return Google's userinfo."""
    expected = request.session.pop(STATE_SESSION_KEY, None)
    if not expected or state != expected:
        raise GoogleOAuthError("OAuth state mismatch")

    try:
        resp = requests.post(TOKEN_URL, data={
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(),
        }, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise GoogleOAuthError(f"token exchange network error: {exc}")
    if resp.status_code != 200:
        raise GoogleOAuthError(f"token exchange failed ({resp.status_code}): {resp.text[:300]}")

    access_token = resp.json().get("access_token")
    if not access_token:
        raise GoogleOAuthError("no access_token in Google response")

    try:
        info = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GoogleOAuthError(f"userinfo network error: {exc}")
    if info.status_code != 200:
        raise GoogleOAuthError(f"userinfo failed ({info.status_code}): {info.text[:300]}")

    data = info.json()
    email = (data.get("email") or "").strip().lower()
    if not email:
        raise GoogleOAuthError("Google did not return an email address")
    if data.get("email_verified") is False:
        raise GoogleOAuthError("Google email is not verified")
    return {
        "email": email,
        "full_name": (data.get("name") or "").strip(),
    }
