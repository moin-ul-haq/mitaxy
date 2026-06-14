# Mitaxy — AI Meeting Assistant

Mitaxy sends a bot into your **Zoom** or **Google Meet** call, records & transcribes it,
generates structured AI notes (summary, action items, decisions, follow-ups), and emails
them to you. Every meeting is stored in a searchable dashboard.

> **Never sit through a meeting again.**

## Pipeline

```
Schedule (web form)
   └─> Recall.ai bot joins the call at the scheduled time
        └─> Celery poll detects the call ended
             └─> Deepgram transcribes the recording (speaker labels)
                  └─> Groq (Llama 3.3) writes structured notes
                       └─> Resend emails a formatted HTML summary
                            └─> Dashboard + detail page updated
```

## Stack

- **Django 5.1** + Gunicorn — web + REST + auth (custom email-based user)
- **Celery + Redis** — scheduled bot lifecycle & post-call processing
- **PostgreSQL** — data
- **Recall.ai** — server-side meeting bot (Zoom / Google Meet)
- **Deepgram** (`nova-2`) — transcription with diarization
- **Groq** (`llama-3.3-70b-versatile`) — AI notes (swappable to Claude)
- **Resend** — transactional email (login alert, welcome, notes, password reset)
- Vanilla HTML/CSS/JS — white + teal design system, no frontend framework

## Local development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in keys; leave DATABASE_URL unset to use SQLite
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
# In another terminal (needs Redis running):
celery -A config worker -l info
celery -A config beat -l info
```

## Environment

See `.env.example`. Key variables:

| Var | Purpose |
|-----|---------|
| `DJANGO_SECRET_KEY` | Django secret |
| `DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` / `SITE_URL` | host config |
| `DATABASE_URL` | Postgres DSN |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis (DB 5 / 6) |
| `RECALL_API_KEY` / `RECALL_REGION` | meeting bot |
| `DEEPGRAM_API_KEY` | transcription |
| `GROQ_API_KEY` / `GROQ_MODEL` | AI notes |
| `RESEND_API_KEY` / `RESEND_FROM` | email |

## Production (this deployment)

- Runs on `212.47.72.184` over HTTP via nginx (`server_name 212.47.72.184`).
- App dir: `/root/mitaxy`, venv at `/root/mitaxy/venv`.
- systemd: `mitaxy.service` (gunicorn unix socket), `mitaxy-celery.service`, `mitaxy-celerybeat.service`.
- Isolated from the 4 existing apps: own DB, own Redis DB indexes, own socket/service names.

### Notes / follow-ups

- **Email recipients:** Resend's free tier only delivers from a *verified domain*. With the
  default `onboarding@resend.dev` sender, mail only reaches your own Resend account email.
  Verify a domain in Resend and set `RESEND_FROM` to send to anyone.
- **HTTPS:** currently HTTP (no domain). Point a domain at the server and run
  `certbot --nginx` to add SSL (matches the other apps).
- **Instant processing:** set a Recall webhook to `http://212.47.72.184/api/webhooks/recall/`
  to skip the 60s poll delay (poll works fine without it).
