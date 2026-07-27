"""
Mitaxy in-meeting voice agent.

One asyncio process (systemd: mitaxy-voice.service) that serves websocket
connections FROM Recall.ai bots. For each voice-enabled meeting:

    Recall bot ──(mixed call audio, 16kHz mono S16LE PCM, b64 JSON)──▶ this service
        │                                                                │
        │                                       Deepgram live STT ◀──────┘
        │                                                │ transcripts
        │                                  wake word? ("<bot display name>")
        │                                                │ question
        │                                        Groq LLM (short answer)
        │                                                │ text
        │                                       Deepgram Aura TTS (mp3)
        └──◀────────(Recall output_audio: bot speaks the mp3)────────────┘

The wake word is the bot's *display name* for that meeting (Meeting.bot_name):
if the bot joined as "Ali", saying "Ali, ..." makes it answer.

URL shape (configured on bot creation, see meetings/services/recall.py):
    wss://<site>/voice-ws/<signed-meeting-id>/
The path token is Django-signed so only bots we created can connect.
"""
import asyncio
import base64
import json
import logging
import os
import re
import sys
import time
from urllib.parse import urlparse

# --- Django bootstrap (gives us settings + ORM) -----------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

import requests  # noqa: E402
import websockets  # noqa: E402
from django.conf import settings  # noqa: E402

from meetings.models import Meeting  # noqa: E402
from meetings.services import recall  # noqa: E402
from meetings.services.voice_ws import parse_ws_token  # noqa: E402

logger = logging.getLogger("mitaxy.voice")
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)

HOST = os.environ.get("VOICE_HOST", "127.0.0.1")
PORT = int(os.environ.get("VOICE_PORT", "8791"))
MAX_SESSIONS = int(os.environ.get("VOICE_MAX_SESSIONS", "8"))

DG_LIVE_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?encoding=linear16&sample_rate=16000&channels=1"
    "&model=nova-2&smart_format=true&punctuate=true&interim_results=false"
)
DG_SPEAK_URL = "https://api.deepgram.com/v1/speak?model={model}&encoding=mp3"
TTS_MODEL = os.environ.get("VOICE_TTS_MODEL", "aura-asteria-en")

ANSWER_COOLDOWN_SEC = 3.0
AWAIT_QUESTION_SEC = 9.0       # after a bare "Ali?" wait this long for the question
CONTEXT_MAX_CHARS = 3500       # rolling transcript given to the LLM
MAX_ANSWER_CHARS = 420         # keep spoken replies short

SYSTEM_PROMPT = (
    "You are {bot_name}, a helpful AI voice assistant sitting in a live meeting"
    "{title_part}. You hear the conversation and answer when addressed by name. "
    "Reply in ONE or TWO short spoken-style sentences (no lists, no markdown, no "
    "emojis — this will be read aloud). Base answers on the meeting transcript "
    "context when relevant; otherwise answer from general knowledge. If you "
    "genuinely can't help, say so briefly."
)


# --------------------------------------------------------------------------
# Blocking helpers (run in threads via asyncio.to_thread)
# --------------------------------------------------------------------------
def _db_load_meeting(meeting_id: int):
    return (
        Meeting.objects.filter(pk=meeting_id, voice_agent_enabled=True)
        .only("id", "title", "bot_name", "recall_bot_id", "platform")
        .first()
    )

def _db_log_event(meeting_id: int, code: str, message: str):
    try:
        meeting = Meeting.objects.get(pk=meeting_id)
        meeting.log_event(code, message)
    except Exception:
        logger.exception("event log failed for meeting %s", meeting_id)


def _groq_answer(bot_name: str, title: str, context: str, question: str) -> str:
    title_part = f' titled "{title}"' if title else ""
    payload = {
        "model": settings.GROQ_MODEL or "llama-3.3-70b-versatile",
        "temperature": 0.4,
        "max_tokens": 220,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.format(bot_name=bot_name, title_part=title_part)},
            {"role": "user", "content": (
                f"Recent meeting transcript (may be partial):\n\"\"\"\n{context}\n\"\"\"\n\n"
                f"Someone just addressed you: \"{question}\"\n\nYour spoken reply:"
            )},
        ],
    }
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
        timeout=30,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    return text[:MAX_ANSWER_CHARS]


def _aura_tts(text: str) -> bytes:
    resp = requests.post(
        DG_SPEAK_URL.format(model=TTS_MODEL),
        json={"text": text},
        headers={"Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
                 "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


# --------------------------------------------------------------------------
# Per-meeting session
# --------------------------------------------------------------------------
class VoiceSession:
    def __init__(self, ws, meeting):
        self.ws = ws
        self.meeting = meeting
        self.bot_name = (meeting.bot_name or settings.RECALL_BOT_NAME).strip()
        self.wake_words = self._wake_words(self.bot_name)
        self.dg = None                      # Deepgram websocket
        self.transcript = []                # [(t, text)] final segments
        self.awaiting_until = 0.0           # "Ali?" said — waiting for the question
        self.speaking_until = 0.0           # suppress wake detection while bot talks
        self.last_answer_at = 0.0
        self.last_audio_at = time.monotonic()
        self.closed = False

    @staticmethod
    def _wake_words(bot_name):
        words = {bot_name.lower()}
        first = bot_name.split()[0].lower() if bot_name.split() else ""
        if len(first) >= 3:
            words.add(first)
        return words

    @property
    def wake_re(self):
        alts = "|".join(re.escape(w) for w in sorted(self.wake_words, key=len, reverse=True))
        return re.compile(rf"\b(?:hey\s+|ok\s+|okay\s+)?({alts})\b[\s,.!?:]*", re.IGNORECASE)

    # ---------------- transcript / wake handling ----------------
    def context_text(self):
        text = " ".join(t for _, t in self.transcript)
        return text[-CONTEXT_MAX_CHARS:]

    async def on_final_transcript(self, text):
        text = (text or "").strip()
        if not text:
            return
        now = time.monotonic()
        self.transcript.append((now, text))
        if len(self.transcript) > 400:
            del self.transcript[:100]

        if now < self.speaking_until:      # our own TTS coming back through the mic mix
            return
        if now - self.last_answer_at < ANSWER_COOLDOWN_SEC:
            return

        m = self.wake_re.search(text)
        if m:
            question = text[m.end():].strip()
            if len(question.split()) >= 2:
                await self.answer(question)
            else:
                # bare "Ali?" — arm and wait for the actual question
                self.awaiting_until = now + AWAIT_QUESTION_SEC
        elif now < self.awaiting_until:
            self.awaiting_until = 0.0
            await self.answer(text)

    async def answer(self, question):
        self.awaiting_until = 0.0
        self.last_answer_at = time.monotonic()
        logger.info("meeting %s: answering %r", self.meeting.id, question[:120])
        try:
            reply = await asyncio.to_thread(
                _groq_answer, self.bot_name, self.meeting.title, self.context_text(), question
            )
            mp3 = await asyncio.to_thread(_aura_tts, reply)
            est_secs = max(2.0, min(45.0, len(reply) / 13.0))
            self.speaking_until = time.monotonic() + est_secs + 1.5
            ok = await asyncio.to_thread(
                recall.output_audio, self.meeting.recall_bot_id, base64.b64encode(mp3).decode()
            )
            if ok:
                await asyncio.to_thread(
                    _db_log_event, self.meeting.id, "voice_answered",
                    f"Voice agent answered: “{question[:120]}”",
                )
            else:
                logger.error("meeting %s: output_audio push failed", self.meeting.id)
        except Exception:
            logger.exception("meeting %s: answer pipeline failed", self.meeting.id)

    # ---------------- deepgram plumbing ----------------
    async def run(self):
        await asyncio.to_thread(
            _db_log_event, self.meeting.id, "voice_connected",
            f"Voice agent live — say “{self.bot_name}” to talk to it",
        )
        try:
            await asyncio.gather(self._pump_recall(), self._keepalive())
        finally:
            self.closed = True
            if self.dg:
                try:
                    await self.dg.close()
                except Exception:
                    pass
            await asyncio.to_thread(
                _db_log_event, self.meeting.id, "voice_ended", "Voice agent disconnected"
            )

    async def _ensure_deepgram(self):
        if self.dg is not None:
            return
        self.dg = await websockets.connect(
            DG_LIVE_URL,
            additional_headers={"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"},
            max_size=2 ** 22,
        )
        asyncio.create_task(self._pump_deepgram())

    async def _pump_deepgram(self):
        dg = self.dg
        try:
            async for raw in dg:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "Results":
                    alt = (msg.get("channel") or {}).get("alternatives") or [{}]
                    if msg.get("is_final"):
                        await self.on_final_transcript(alt[0].get("transcript", ""))
        except websockets.ConnectionClosed:
            pass
        finally:
            if not self.closed and self.dg is dg:
                self.dg = None      # next audio frame reconnects

    async def _pump_recall(self):
        async for raw in self.ws:
            if isinstance(raw, bytes):
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("event") not in ("audio_mixed_raw.data", "audio_separate_raw.data"):
                continue
            buf = ((msg.get("data") or {}).get("data") or {}).get("buffer")
            if not buf:
                continue
            try:
                pcm = base64.b64decode(buf)
            except Exception:
                continue
            self.last_audio_at = time.monotonic()
            try:
                await self._ensure_deepgram()
                await self.dg.send(pcm)
            except Exception:
                self.dg = None      # transient — retried on the next frame

    async def _keepalive(self):
        """Deepgram closes idle streams; ping it through silent stretches."""
        while not self.closed:
            await asyncio.sleep(6)
            if self.dg and time.monotonic() - self.last_audio_at > 5:
                try:
                    await self.dg.send(json.dumps({"type": "KeepAlive"}))
                except Exception:
                    self.dg = None


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------
_sessions = 0

async def handler(ws):
    global _sessions
    path = urlparse(ws.request.path).path
    m = re.fullmatch(r"/voice-ws/([^/]+)/?", path)
    meeting_id = parse_ws_token(m.group(1)) if m else None
    if meeting_id is None:
        logger.warning("rejected ws connect with bad path %r", path[:120])
        await ws.close(code=4403, reason="bad token")
        return

    meeting = await asyncio.to_thread(_db_load_meeting, meeting_id)
    if meeting is None:
        await ws.close(code=4404, reason="unknown meeting")
        return
    if _sessions >= MAX_SESSIONS:
        logger.error("session cap reached (%s) — refusing meeting %s", MAX_SESSIONS, meeting_id)
        await ws.close(code=4429, reason="busy")
        return

    _sessions += 1
    logger.info("voice session start: meeting %s (bot %r, %s active)",
                meeting_id, meeting.bot_name, _sessions)
    try:
        await VoiceSession(ws, meeting).run()
    except websockets.ConnectionClosed:
        pass
    except Exception:
        logger.exception("voice session crashed for meeting %s", meeting_id)
    finally:
        _sessions -= 1
        logger.info("voice session end: meeting %s (%s active)", meeting_id, _sessions)


async def main():
    logger.info("mitaxy-voice listening on %s:%s (max %s sessions, tts=%s)",
                HOST, PORT, MAX_SESSIONS, TTS_MODEL)
    async with websockets.serve(handler, HOST, PORT, max_size=2 ** 22, ping_interval=20):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
