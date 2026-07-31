# Mitaxy — AI Meeting Assistant

**Live at → [https://mitaxy.moinit.dev](https://mitaxy.moinit.dev)**

Mitaxy sends an AI notetaker bot into your **Zoom**, **Google Meet** or **Microsoft Teams**
call. It records and transcribes the meeting (with speaker labels), writes structured AI
notes — summary, action items, key decisions, follow-ups — and emails them to you. Every
meeting lives in a clean dashboard where you can watch the bot's live status, read the full
transcript, and share the notes with anyone.

> **Never sit through a meeting again.**

## Features

- **Schedule or deploy instantly** — pick a time, or send the bot into a meeting that's
  already running ("Deploy right now", no date needed)
- **Live bot status timeline** — connecting → waiting room → recording → processing →
  done, updated in real time on the dashboard
- **AI notes by email** — summary, action items, decisions, follow-ups + full
  speaker-labelled transcript
- **Voice agent (beta)** — the bot answers out loud in the call when someone says its
  display name (joined as *Ali*? just say *"Ali, …"*). Female/male voice selectable
- **Sharing** — share any meeting's notes via a public link, or restrict to specific
  people (each gets a personal signed access link by email), optional transcript
- **Google sign-in** + email/password auth, onboarding tour for new users
- **Branded admin panel** with a live stats dashboard
- SEO-ready marketing site (sitemap, structured data, Open Graph)

## Pipeline

```
Schedule (web form)
   └─> Recall.ai bot joins the call (at the scheduled time, or instantly)
        ├─> [voice agent] mixed call audio streams to voice/agent.py over websocket
        │      └─> Deepgram live STT -> wake word -> Groq answer -> Aura TTS -> bot speaks
        └─> Celery poll drives the bot lifecycle & detects the call ended
             └─> Deepgram transcribes the recording (speaker labels)
                  └─> Groq (Llama 3.3) writes structured notes
                       └─> Resend emails a formatted HTML summary
                            └─> Dashboard + detail page updated (sharable)
```

## Stack

- **Django 5.1** + Gunicorn — web, auth (email-based custom user + Google OAuth)
- **Celery + Redis** — bot lifecycle engine & post-call processing (+ Redis cache for locks)
- **PostgreSQL** — data
- **Recall.ai** — server-side meeting bot (Zoom / Meet / Teams) + realtime audio + output audio
- **Deepgram** — `nova-2` transcription with diarization, live STT and Aura TTS for the voice agent
- **Groq** (`llama-3.3-70b-versatile`) — AI notes & voice-agent answers
- **Resend** — transactional email (notes, welcome, login alert, password reset, share invites)
- **Sentry** — error tracking (web + workers)
- Vanilla HTML/CSS/JS — teal design system, dark-sidebar app shell, no frontend framework

## Local development

```bash
python3 -m venv venv && source venv/bin/activate   # Python 3.12
pip install -r requirements.txt
cp .env.example .env          # fill in keys; leave DATABASE_URL unset to use SQLite
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
# In other terminals (needs Redis running):
celery -A config worker -l info
celery -A config beat -l info
python voice/agent.py         # voice agent websocket service (optional)
```

## Environment

See `.env.example` and `deploy/README.md` for the full list. Key variables:

| Var | Purpose |
|-----|---------|
| `DJANGO_SECRET_KEY` / `DJANGO_ALLOWED_HOSTS` / `SITE_URL` | core config |
| `DATABASE_URL` | Postgres DSN |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` / `CACHE_URL` | Redis (DB 5 / 6 / 7) |
| `RECALL_API_KEY` / `RECALL_REGION` | meeting bot |
| `DEEPGRAM_API_KEY` | transcription + voice agent STT/TTS |
| `GROQ_API_KEY` / `GROQ_MODEL` | AI notes + voice answers |
| `RESEND_API_KEY` / `RESEND_FROM` | email |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | "Continue with Google" (optional) |
| `SENTRY_DSN` | error tracking (optional) |
| `MITAXY_JOIN_TIMEOUT_MIN` / `MITAXY_WAITING_TIMEOUT_MIN` | bot lifecycle timeouts |

## Deployment & CI/CD

Push to `main` → the production server auto-deploys within ~2 minutes
(`deploy/auto_deploy.sh` via a systemd timer: pull → pip → migrate → collectstatic →
restart app services). See **[deploy/README.md](deploy/README.md)** for operations and
**[SYSTEM.md](SYSTEM.md)** for the full architecture reference.

Services in production: `mitaxy` (gunicorn), `mitaxy-celery`, `mitaxy-celerybeat`,
`mitaxy-voice` (voice agent), behind nginx with HTTPS (Let's Encrypt).
