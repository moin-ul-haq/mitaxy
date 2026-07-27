"""
Recall.ai client — dispatches a server-side bot into a Zoom / Google Meet call,
then exposes the recording so we can transcribe it.

Docs: https://docs.recall.ai  (API v1, region-scoped host)
"""
import logging

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("mitaxy")

TIMEOUT = 30

# Shared session: connection pooling + automatic retries on transient failures.
# Retries only cover idempotent GET/DELETE — a POST (create_bot) is never
# auto-retried, so we can't accidentally dispatch two bots into one call.
_session = requests.Session()
_retry = Retry(
    total=3,
    backoff_factor=1.5,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET", "DELETE"]),
    raise_on_status=False,
)
_session.mount("https://", HTTPAdapter(max_retries=_retry, pool_maxsize=20))


class RecallError(Exception):
    pass


def _base_url():
    region = settings.RECALL_REGION or "us-east-1"
    return f"https://{region}.recall.ai/api/v1"


def _headers():
    key = settings.RECALL_API_KEY
    if not key:
        raise RecallError("RECALL_API_KEY is not configured")
    return {"Authorization": f"Token {key}", "Content-Type": "application/json"}


def create_bot(meeting_url: str, join_at_utc_iso: str = None, bot_name: str = None,
               voice_ws_url: str = None) -> dict:
    """
    Dispatch a bot into `meeting_url`. If `join_at_utc_iso` is given (ISO-8601, UTC)
    the bot joins at that time; if omitted, it joins immediately.

    If `voice_ws_url` is given, the bot is configured as a voice agent: it streams
    mixed call audio to that websocket (16kHz mono S16LE PCM, base64 in JSON
    events) and is armed for `output_audio` pushes (via a silent
    automatic_audio_output clip, per Recall's docs).

    Returns the created bot dict (contains 'id').
    """
    bot_name = bot_name or settings.RECALL_BOT_NAME
    url = f"{_base_url()}/bot/"

    # Modern payload (asks Recall to retain a mixed audio/video recording).
    payload = {
        "meeting_url": meeting_url,
        "bot_name": bot_name,
        "recording_config": {"video_mixed_layout": "speaker_view"},
    }
    if voice_ws_url:
        from .voice_assets import SILENT_MP3_B64

        payload["recording_config"]["audio_mixed_raw"] = {}
        payload["recording_config"]["realtime_endpoints"] = [{
            "type": "websocket",
            "url": voice_ws_url,
            "events": ["audio_mixed_raw.data"],
        }]
        # Required to unlock the output_audio endpoint; the clip is ~0.4s of
        # silence so participants hear nothing on join.
        payload["automatic_audio_output"] = {
            "in_call_recording": {"data": {"kind": "mp3", "b64_data": SILENT_MP3_B64}}
        }
    if join_at_utc_iso:
        payload["join_at"] = join_at_utc_iso
    try:
        resp = _session.post(url, json=payload, headers=_headers(), timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise RecallError(f"create_bot network error: {exc}")

    # Some Recall accounts/plans reject an explicit recording_config — retry minimal.
    # Only for plain notetaker bots: a voice bot must keep its config, so we surface
    # the validation error instead of silently degrading.
    if resp.status_code in (400, 422) and not voice_ws_url:
        logger.warning("Recall create_bot 4xx with recording_config (%s); retrying minimal", resp.text[:200])
        payload.pop("recording_config", None)
        try:
            resp = _session.post(url, json=payload, headers=_headers(), timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise RecallError(f"create_bot network error: {exc}")

    if resp.status_code not in (200, 201):
        raise RecallError(f"create_bot failed ({resp.status_code}): {resp.text[:400]}")
    return resp.json()


def output_audio(bot_id: str, mp3_b64: str) -> bool:
    """Play an MP3 clip through the bot into the call (voice-agent replies).
    Returns True on success; logs and returns False on failure (never raises)."""
    if not bot_id:
        return False
    try:
        resp = _session.post(
            f"{_base_url()}/bot/{bot_id}/output_audio/",
            json={"kind": "mp3", "b64_data": mp3_b64},
            headers=_headers(),
            timeout=TIMEOUT,
        )
        if resp.status_code in (200, 201, 202, 204):
            return True
        logger.error("output_audio failed (%s): %s", resp.status_code, resp.text[:300])
        return False
    except requests.RequestException:
        logger.exception("output_audio network error for bot %s", bot_id)
        return False


def get_bot(bot_id: str) -> dict:
    try:
        resp = _session.get(f"{_base_url()}/bot/{bot_id}/", headers=_headers(), timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise RecallError(f"get_bot network error: {exc}")
    if resp.status_code != 200:
        raise RecallError(f"get_bot failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def delete_bot(bot_id: str) -> bool:
    """Best-effort cancellation of a scheduled bot. Never raises."""
    if not bot_id:
        return False
    try:
        resp = _session.delete(f"{_base_url()}/bot/{bot_id}/", headers=_headers(), timeout=TIMEOUT)
        return resp.status_code in (200, 202, 204, 404)
    except requests.RequestException:
        logger.warning("delete_bot network error for %s", bot_id)
        return False


def latest_status_code(bot: dict) -> str:
    """Most recent lifecycle status code reported by Recall."""
    changes = bot.get("status_changes") or []
    if changes:
        last = changes[-1]
        return (last.get("code") or "").lower()
    status = bot.get("status")
    if isinstance(status, dict):
        return (status.get("code") or "").lower()
    return (status or "").lower()


# Status codes that mean "the call is over, recording should be ready soon".
DONE_CODES = {"done", "call_ended", "media_expired", "analysis_done"}
FATAL_CODES = {
    "fatal", "error", "bot_rejected", "bot_removed", "call_not_found",
    "meeting_not_found", "timeout", "recording_permission_denied",
}
IN_CALL_CODES = {"in_call_recording", "recording_permission_allowed", "in_call_not_recording"}
WAITING_ROOM_CODES = {"in_waiting_room"}
JOINING_CODES = {"joining_call", "ready", "scheduled"}

# User-facing copy for the bot activity timeline, keyed by Recall status code.
FRIENDLY_STATUS = {
    "ready": "Bot is ready and standing by",
    "scheduled": "Bot is scheduled and standing by",
    "joining_call": "Bot is connecting to the meeting",
    "in_waiting_room": "Bot is in the waiting room — waiting for the host to admit it",
    "in_call_not_recording": "Bot joined the call — waiting for permission to record",
    "recording_permission_allowed": "Recording permission granted",
    "in_call_recording": "Bot is in the call and recording",
    "call_ended": "The call ended — preparing the recording",
    "done": "Recording finished",
    "analysis_done": "Recording finished",
    "bot_rejected": "The host declined to admit the bot",
    "bot_removed": "The bot was removed from the call by the host",
    "recording_permission_denied": "The host denied recording permission",
    "call_not_found": "The meeting could not be found — check the link",
    "meeting_not_found": "The meeting could not be found — check the link",
    "timeout": "The bot timed out trying to join",
    "fatal": "The bot hit an unexpected error",
    "error": "The bot hit an unexpected error",
}

# Human explanation used in Meeting.error_message when a bot fails.
FATAL_REASON = {
    "bot_rejected": "The host declined to admit the bot into the call.",
    "bot_removed": "The bot was removed from the call by the host.",
    "recording_permission_denied": "The host denied recording permission, so nothing could be captured.",
    "call_not_found": "The meeting could not be found. Double-check the meeting link.",
    "meeting_not_found": "The meeting could not be found. Double-check the meeting link.",
    "timeout": "The bot timed out while trying to join the meeting.",
}


def friendly_status(code: str) -> str:
    return FRIENDLY_STATUS.get(code, f"Bot status: {code}" if code else "Waiting for the bot")


def fatal_reason(code: str) -> str:
    return FATAL_REASON.get(code, f"The bot could not record this meeting (status: {code}).")


def _walk_urls(obj, found):
    """Recursively collect (key, url) pairs for any http(s) string values."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v.startswith("http"):
                found.append((k.lower(), v))
            else:
                _walk_urls(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _walk_urls(item, found)


def extract_media_url(bot: dict) -> str | None:
    """
    Find the best downloadable media URL in a bot payload, resilient to the
    several shapes Recall has used across API versions.
    """
    found = []
    _walk_urls(bot, found)
    if not found:
        return None

    def score(pair):
        key, url = pair
        s = 0
        if "download" in key:
            s += 5
        if "video" in key or url.endswith(".mp4"):
            s += 3
        if "audio" in key or url.endswith((".m4a", ".mp3", ".wav")):
            s += 4  # audio is lighter + perfect for Deepgram
        if "mixed" in key:
            s += 2
        return s

    found.sort(key=score, reverse=True)
    best_key, best_url = found[0]
    if score(found[0]) == 0:
        return None  # nothing that looks like media
    logger.info("Recall media url chosen from key '%s'", best_key)
    return best_url


def extract_duration_seconds(bot: dict):
    """Best-effort duration in seconds."""
    for key in ("duration", "duration_seconds", "recording_duration"):
        val = bot.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return int(val)
    recordings = bot.get("recordings") or []
    for rec in recordings:
        for key in ("duration", "duration_seconds"):
            val = rec.get(key) if isinstance(rec, dict) else None
            if isinstance(val, (int, float)) and val > 0:
                return int(val)
    return None
