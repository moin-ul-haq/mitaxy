"""
AI note generation via Groq (OpenAI-compatible API, free tier).

Takes a transcript and returns structured notes:
{summary, action_items[], key_decisions[], follow_ups[]}.

Swappable to Anthropic Claude later by changing this module only.
"""
import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger("mitaxy")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT = 120
MAX_TRANSCRIPT_CHARS = 90_000  # keep well within the model context window

SYSTEM_PROMPT = (
    "You are an expert meeting-notes assistant. You are given a raw meeting "
    "transcript with speaker labels. Produce concise, accurate, professional "
    "notes. Only use information present in the transcript — never invent "
    "details, names, dates, or owners. Respond with STRICT JSON only."
)

USER_TEMPLATE = """Here is the meeting transcript:

\"\"\"
{transcript}
\"\"\"

Return a JSON object with exactly these keys:
- "summary": a 3-5 sentence paragraph summarizing what was discussed and decided.
- "action_items": an array of short strings. Each item should name the owner if stated (e.g. "Ali: send the report by Friday"). Empty array if none.
- "key_decisions": an array of short strings describing concrete decisions made. Empty array if none.
- "follow_ups": an array of short strings for open questions or next steps to revisit. Empty array if none.

Respond with ONLY the JSON object, no markdown, no commentary."""


class AIError(Exception):
    pass


def generate_notes(transcript_text: str) -> dict:
    key = settings.GROQ_API_KEY
    if not key:
        raise AIError("GROQ_API_KEY is not configured")

    text = (transcript_text or "").strip()
    if not text:
        return {"summary": "No speech was detected in this meeting.",
                "action_items": [], "key_decisions": [], "follow_ups": []}
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[:MAX_TRANSCRIPT_CHARS] + "\n...[transcript truncated]..."

    payload = {
        "model": settings.GROQ_MODEL or "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(transcript=text)},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        GROQ_URL,
        json=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise AIError(f"Groq failed ({resp.status_code}): {resp.text[:400]}")

    try:
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise AIError(f"Could not parse Groq response: {exc}")

    return _normalize(data)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        # split on newlines/bullets if the model returned a blob
        parts = [p.strip(" -•\t") for p in value.splitlines() if p.strip(" -•\t")]
        return parts or ([value.strip()] if value.strip() else [])
    return [str(value)]


def _normalize(data: dict) -> dict:
    return {
        "summary": str(data.get("summary", "")).strip(),
        "action_items": _as_list(data.get("action_items")),
        "key_decisions": _as_list(data.get("key_decisions")),
        "follow_ups": _as_list(data.get("follow_ups")),
    }
