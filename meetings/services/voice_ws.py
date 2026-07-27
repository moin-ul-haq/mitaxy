"""Signed websocket URLs for the voice agent (shared by web + voice service)."""
from django.conf import settings
from django.core import signing

VOICE_SIGNING_SALT = "mitaxy.voice.ws"


def make_ws_token(meeting_id: int) -> str:
    return signing.Signer(salt=VOICE_SIGNING_SALT).sign(str(meeting_id))


def parse_ws_token(token: str):
    try:
        return int(signing.Signer(salt=VOICE_SIGNING_SALT).unsign(token))
    except (signing.BadSignature, ValueError):
        return None


def voice_ws_url(meeting_id: int) -> str:
    """wss:// URL Recall's bot streams call audio to."""
    base = settings.SITE_URL.replace("https://", "wss://").replace("http://", "ws://")
    return f"{base}/voice-ws/{make_ws_token(meeting_id)}/"
