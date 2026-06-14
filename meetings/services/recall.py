"""
Recall.ai client — dispatches a server-side bot into a Zoom / Google Meet call,
then exposes the recording so we can transcribe it.

Docs: https://docs.recall.ai  (API v1, region-scoped host)
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger("mitaxy")

TIMEOUT = 30


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


def create_bot(meeting_url: str, join_at_utc_iso: str = None, bot_name: str = None) -> dict:
    """
    Dispatch a bot into `meeting_url`. If `join_at_utc_iso` is given (ISO-8601, UTC)
    the bot joins at that time; if omitted, it joins immediately.
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
    if join_at_utc_iso:
        payload["join_at"] = join_at_utc_iso
    resp = requests.post(url, json=payload, headers=_headers(), timeout=TIMEOUT)

    # Some Recall accounts/plans reject an explicit recording_config — retry minimal.
    if resp.status_code in (400, 422):
        logger.warning("Recall create_bot 4xx with recording_config (%s); retrying minimal", resp.text[:200])
        payload.pop("recording_config", None)
        resp = requests.post(url, json=payload, headers=_headers(), timeout=TIMEOUT)

    if resp.status_code not in (200, 201):
        raise RecallError(f"create_bot failed ({resp.status_code}): {resp.text[:400]}")
    return resp.json()


def get_bot(bot_id: str) -> dict:
    resp = requests.get(f"{_base_url()}/bot/{bot_id}/", headers=_headers(), timeout=TIMEOUT)
    if resp.status_code != 200:
        raise RecallError(f"get_bot failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def delete_bot(bot_id: str) -> bool:
    """Best-effort cancellation of a scheduled bot. Never raises."""
    if not bot_id:
        return False
    try:
        resp = requests.delete(f"{_base_url()}/bot/{bot_id}/", headers=_headers(), timeout=TIMEOUT)
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
FATAL_CODES = {"fatal", "error", "bot_rejected", "call_not_found", "meeting_not_found", "timeout"}
IN_CALL_CODES = {"in_call_recording", "recording_permission_allowed", "in_call_not_recording"}
JOINING_CODES = {"joining_call", "ready", "in_waiting_room"}


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
