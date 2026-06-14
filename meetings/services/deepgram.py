"""
Deepgram transcription — sends the recording URL to Deepgram and returns a
transcript with speaker diarization ("Speaker 1", "Speaker 2", ...).
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger("mitaxy")

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
TIMEOUT = 600  # transcription of a long meeting can take a while


class DeepgramError(Exception):
    pass


def transcribe_url(media_url: str) -> dict:
    """
    Transcribe a remote audio/video URL.

    Returns: {"full_text": str, "segments": [{"speaker","text","start","end"}, ...]}
    """
    key = settings.DEEPGRAM_API_KEY
    if not key:
        raise DeepgramError("DEEPGRAM_API_KEY is not configured")

    params = {
        "model": settings.DEEPGRAM_MODEL or "nova-2",
        "diarize": "true",
        "punctuate": "true",
        "smart_format": "true",
        "utterances": "true",
        "paragraphs": "true",
    }
    resp = requests.post(
        DEEPGRAM_URL,
        params=params,
        json={"url": media_url},
        headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise DeepgramError(f"Deepgram failed ({resp.status_code}): {resp.text[:400]}")

    data = resp.json()
    return _parse(data)


def _parse(data: dict) -> dict:
    """Turn Deepgram's response into our transcript shape."""
    segments = []

    # Preferred: utterances (already grouped per speaker turn)
    utterances = data.get("results", {}).get("utterances") or []
    if utterances:
        for u in utterances:
            spk = u.get("speaker")
            segments.append({
                "speaker": f"Speaker {spk + 1}" if isinstance(spk, int) else "Speaker",
                "text": (u.get("transcript") or "").strip(),
                "start": round(u.get("start", 0.0), 2),
                "end": round(u.get("end", 0.0), 2),
            })
    else:
        # Fallback: stitch word-level diarization into speaker turns.
        try:
            alt = data["results"]["channels"][0]["alternatives"][0]
            words = alt.get("words") or []
        except (KeyError, IndexError):
            words = []
        current = None
        for w in words:
            spk = w.get("speaker", 0)
            token = w.get("punctuated_word") or w.get("word") or ""
            if current is None or current["_spk"] != spk:
                if current:
                    segments.append(_finalize(current))
                current = {"_spk": spk, "words": [token], "start": w.get("start", 0.0), "end": w.get("end", 0.0)}
            else:
                current["words"].append(token)
                current["end"] = w.get("end", current["end"])
        if current:
            segments.append(_finalize(current))

    full_text = "\n".join(f"{s['speaker']}: {s['text']}" for s in segments if s["text"])
    if not full_text:
        # Last resort: the flat transcript string.
        try:
            full_text = data["results"]["channels"][0]["alternatives"][0].get("transcript", "")
        except (KeyError, IndexError):
            full_text = ""
    return {"full_text": full_text, "segments": segments}


def _finalize(current):
    spk = current["_spk"]
    return {
        "speaker": f"Speaker {spk + 1}" if isinstance(spk, int) else "Speaker",
        "text": " ".join(current["words"]).strip(),
        "start": round(current["start"], 2),
        "end": round(current["end"], 2),
    }
