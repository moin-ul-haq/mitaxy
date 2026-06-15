# Mitaxy — System Architecture & Reference

> **Mitaxy** is an AI meeting-assistant SaaS. A user pastes a Zoom or Google Meet link and a time; a
> server-side bot joins the call on their behalf, records and transcribes it, generates structured AI
> notes (summary, action items, decisions, follow-ups), emails them, and stores everything in a
> searchable dashboard. **Tagline:** *Never sit through a meeting again.*

This document is the single source of truth for what the system is, how it's built, where every piece
lives, why each technology was chosen, and how to operate it.

---

## 1. At a glance

| | |
|---|---|
| **Live URL** | http://212.47.72.184/ (HTTP — no domain/SSL yet) |
| **Admin** | http://212.47.72.184/admin/ (`admin@mitaxy.local`) |
| **Source repo** | https://github.com/moin-ul-haq/mitaxy (public) |
| **Server** | `root@212.47.72.184` — Ubuntu 24.04, 4 vCPU, 8 GB RAM, 72 GB disk |
| **App directory** | `/root/mitaxy` |
| **Local dev copy** | `/Users/nadirprofile/Desktop/mitaxy` |
| **Framework** | Django 5.1.4 (Python 3.12) |
| **Process model** | Gunicorn (web) + Celery worker + Celery beat, all under systemd |
| **Data** | PostgreSQL 16 (app data) + Redis (Celery broker/results) |

The server is **shared** with 4 other Django apps (`apkmanager`, `firestick`, `firestick3`,
`firestickpackagess`). Mitaxy is fully isolated from them — its own database, its own Redis DB indexes,
its own systemd units, its own nginx server block, and its own socket. Nothing Mitaxy does touches them.

---

## 2. High-level architecture

```
                                  ┌──────────────────────────────────────────────┐
   Browser (user)                 │                  THE SERVER                   │
   ───────────────                │              212.47.72.184 (Ubuntu)           │
        │ HTTP                    │                                              │
        ▼                         │   ┌────────┐   unix socket   ┌────────────┐  │
   ┌─────────┐   :80   ┌──────────┼──▶│ nginx  │───────────────▶ │  Gunicorn  │  │
   │ visitor │────────▶│  nginx   │   │(reverse│  /root/mitaxy/  │  (Django)  │  │
   └─────────┘         │ :80/:443 │   │ proxy) │   mitaxy.sock   │ mitaxy.svc │  │
                       └──────────┘   └────────┘                 └─────┬──────┘  │
                         serves                                        │         │
                       /static, /media                                 │ ORM     │
                                                                       ▼         │
                                                         ┌──────────────────────┐│
                                                         │   PostgreSQL 16      ││
                                                         │   db: mitaxy         ││
                                                         └──────────────────────┘│
                                                                       ▲         │
                       ┌───────────────────────────────────┐          │         │
                       │  Celery worker  (mitaxy-celery)    │──────────┘         │
                       │  Celery beat    (mitaxy-celerybeat)│                    │
                       └───────────────┬───────────────────┘                    │
                                       │ broker/results                         │
                                       ▼                                        │
                                 ┌───────────┐                                  │
                                 │   Redis   │  db 5 = broker, db 6 = results   │
                                 └───────────┘                                  │
                       └────────────────────────────────────────────────────────┘
                                       │
              external API calls (HTTPS, outbound)
                                       ▼
   ┌───────────┐   ┌────────────┐   ┌────────┐   ┌────────┐
   │ Recall.ai │   │  Deepgram  │   │  Groq  │   │ Resend │
   │ (the bot) │   │(transcribe)│   │ (notes)│   │ (email)│
   └───────────┘   └────────────┘   └────────┘   └────────┘
```

**Two planes of execution:**
- **Web plane** (Gunicorn/Django): everything the user clicks — auth, dashboard, scheduling, pages.
  Fast, synchronous request/response.
- **Worker plane** (Celery): everything that takes time or happens later — driving the bot through its
  lifecycle, transcription, AI notes, sending the notes email. Decoupled from the web request so the UI
  is never blocked.

---

## 3. The end-to-end pipeline (the heart of the system)

```
1. SCHEDULE        User submits the schedule form (Zoom/Meet link, time, bot name, notes email).
   (web)           → meetings.views.schedule
                   → recall.create_bot(url, join_at, bot_name)  ← Recall schedules a bot for that time
                   → Meeting row saved (status=scheduled, recall_bot_id stored)

2. POLL            Celery beat fires meetings.tasks.poll_pending_meetings EVERY 60s.
   (worker)        For each non-terminal meeting it calls recall.get_bot() and maps the bot's
                   latest status code → our status (joining → recording → …).
                   This is the reliable engine — no Recall webhook configuration required.

3. CALL ENDS       When the bot's status code is "done"/"call_ended", the poll flips the meeting to
   (worker)        PROCESSING (exactly once) and enqueues meetings.tasks.process_meeting.

4. PROCESS         process_meeting(meeting_id) runs the full post-call pipeline:
   (worker)          a. recall.get_bot() → recall.extract_media_url() → Recording row
                     b. deepgram.transcribe_url(media_url) → Transcript row (speaker-labelled)
                     c. ai.generate_notes(transcript) → MeetingNotes row (summary/actions/…)
                     d. mailer.send_html_email(...) → notes email via Resend → EmailLog row
                     e. status = COMPLETED
                   If the recording isn't ready yet, it retries (60s). On error it retries (120s,
                   up to 10x) then marks FAILED.

5. VIEW            Dashboard auto-refreshes; the meeting card flips to "Completed".
   (web)           Detail page shows summary, action items, decisions, follow-ups, recording link,
                   and the full speaker-labelled transcript. The notes email is in the user's inbox.
```

**"Deploy now"** short-circuits step 1's schedule: `meetings.views.deploy_now` cancels the scheduled
Recall bot (`recall.delete_bot`, returns HTTP 204) and creates a fresh bot with **no** `join_at`, so it
joins the call immediately. The poll then picks it up from step 2 as normal.

**Status lifecycle:** `scheduled → joining → recording → processing → completed` (or `failed`, or
`cancelled`). Mapping from Recall status codes lives in `meetings/services/recall.py`
(`JOINING_CODES`, `IN_CALL_CODES`, `DONE_CODES`, `FATAL_CODES`).

---

## 4. Technology stack — what and why

| Layer | Technology | Why it's used |
|-------|-----------|---------------|
| Web framework | **Django 5.1.4** | Batteries-included: ORM, auth, admin, templating, forms, migrations. One framework covers the whole web plane. |
| WSGI server | **Gunicorn 23** | Production Python app server; runs Django behind nginx over a unix socket. 3 workers. |
| Reverse proxy | **nginx** | TLS termination point (future), static/media file serving, routes the IP host to Mitaxy's socket. Already fronts the other apps. |
| Task queue | **Celery 5.4** | Runs the slow pipeline (transcription, AI, email) off the request thread, plus the scheduled poll. |
| Broker / cache | **Redis** | Celery message broker (DB 5) and result backend (DB 6). Lightweight, already a Celery default. |
| Database | **PostgreSQL 16** | Robust concurrent writes (the worker and web plane both write). Chosen over SQLite because Celery + web concurrency needs real row locking. |
| Meeting bot | **Recall.ai** (region `us-west-2`) | Deploys a *server-side* bot into Zoom/Google Meet — no user device, no SDK in the browser. Handles the hard part (joining calls, recording). |
| Transcription | **Deepgram** (`nova-2`) | Speech-to-text with **speaker diarization** ("Speaker 1/2"). Accepts a remote URL directly, so we don't download large media. |
| AI notes | **Groq** (`llama-3.3-70b-versatile`) | Fast, **free-tier** LLM with an OpenAI-compatible API + JSON mode. (The brief specified Claude; swapped to Groq because only a Groq key was provided and "free" was required. Isolated in one module so it's swappable.) |
| Email | **Resend** | Simple HTTP email API. Wired in as a Django email backend so password-reset and product emails share one transport. |
| Static files | **WhiteNoise** | Compressed, hashed static-file serving from Django/Gunicorn (collected into `staticfiles/`, also aliased by nginx). |
| Config | **python-dotenv** + **dj-database-url** | Loads `.env`; parses `DATABASE_URL`. Keeps all secrets/config out of code. |
| HTTP client | **requests** | All outbound calls to Recall/Deepgram/Groq/Resend. |
| UI | **Vanilla HTML + CSS + JS** | No frontend framework (per requirement). A small hand-built design system (white + teal). |

---

## 5. Repository structure — every file, what it does

```
mitaxy/
├── manage.py                     Django CLI entrypoint
├── requirements.txt              Pinned Python dependencies
├── .env.example                  Template for environment config (no real secrets)
├── .gitignore                    Excludes .env, venv, staticfiles, media, *.sock, db, beat schedule
├── README.md                     Quick start + deploy notes
├── SYSTEM.md                     ← this document
│
├── config/                       ░░ Django PROJECT (not an app) ░░
│   ├── settings.py               All configuration: apps, DB, Celery, integrations, security, logging
│   ├── urls.py                   Root URL router → admin, meetings, accounts
│   ├── wsgi.py                   WSGI entrypoint (Gunicorn loads config.wsgi:application)
│   ├── asgi.py                   ASGI entrypoint (unused, present for completeness)
│   ├── celery.py                 Celery app factory (config_from_object + autodiscover_tasks)
│   └── __init__.py               Imports the Celery app so it loads with Django
│
├── accounts/                     ░░ APP: users, auth, email transport ░░
│   ├── models.py                 Custom User (email-based, no username) + full_name
│   ├── managers.py               UserManager.create_user / create_superuser keyed on email
│   ├── backends.py               EmailBackend — authenticate by email (case-insensitive)
│   ├── forms.py                  RegisterForm, EmailLoginForm
│   ├── views.py                  Register, Login, Logout, full password-reset flow
│   ├── urls.py                   /accounts/* routes
│   ├── signals.py                user_logged_in → send styled "new sign-in" email
│   ├── email_backend.py          ResendEmailBackend — Django email backend that POSTs to Resend
│   ├── admin.py                  User admin (email-based UserAdmin)
│   └── migrations/               Schema history (0001…)
│
├── meetings/                     ░░ APP: the product itself ░░
│   ├── models.py                 Meeting, Recording, Transcript, MeetingNotes, EmailLog, ContactMessage
│   ├── forms.py                  ScheduleForm (validates Zoom/Meet + future time), ContactForm
│   ├── views.py                  landing, dashboard, schedule, detail, deploy_now, about, contact,
│   │                             statuses (JSON for live refresh), recall_webhook
│   ├── urls.py                   All app routes (see §8)
│   ├── tasks.py                  Celery: poll_pending_meetings (beat) + process_meeting (pipeline)
│   ├── context_processors.py     Injects BRAND_NAME / BRAND_TAGLINE into every template
│   ├── admin.py                  Admin for Meeting (+inlines), EmailLog, ContactMessage
│   ├── migrations/               Schema history (0001…0004)
│   └── services/                 ░ external integrations + helpers (one module per concern) ░
│       ├── recall.py             Recall.ai client: create/get/delete bot, media-URL extraction
│       ├── deepgram.py           Deepgram client: transcribe a URL → speaker segments
│       ├── ai.py                 Groq client: transcript → structured JSON notes
│       └── mailer.py             Render an email template + send via the Resend backend
│
├── templates/
│   ├── base.html                 Shared layout: nav, flash messages, footer, asset includes
│   ├── landing.html              Marketing home: hero, steps, features, FAQ (#faq), CTA
│   ├── partials/
│   │   ├── logo.svg              Inline teal wordmark/logo
│   │   └── field.html            Reusable form-field renderer (label + input + errors)
│   ├── accounts/                 login, register, password_reset (+ done/confirm/complete), subject
│   ├── meetings/                 dashboard, schedule, detail
│   ├── pages/                    about, contact
│   └── emails/                   base_email + welcome, login_alert, meeting_notes, password_reset_email
│
└── static/
    ├── css/app.css               The entire design system (white + teal) + responsive breakpoints
    ├── js/app.js                 Flash dismiss, datetime-min guard, dashboard live status polling
    └── img/favicon.svg           Favicon
```

---

## 6. Data model (PostgreSQL tables)

All app tables, their key fields, and relationships. (Django also creates standard `auth_*`,
`django_admin_log`, `django_session`, `django_migrations` tables.)

```
accounts.User (custom; AUTH_USER_MODEL)
  id, email (unique, USERNAME_FIELD), full_name, password, is_staff, is_superuser,
  is_active, date_joined, last_login
        │ 1
        │
        │ N
meetings.Meeting
  id, user→User, title, meeting_url (URLField 1000), platform (zoom/meet/teams/other),
  bot_name, notes_email, scheduled_at, deployed_now (bool),
  status (scheduled/joining/recording/processing/completed/failed/cancelled),
  recall_bot_id, bot_status_detail (200), error_message (text),
  created_at, updated_at, completed_at
   │ 1↔1            │ 1↔1            │ 1↔1            │ 1↔N
   ▼                ▼                ▼                ▼
Recording        Transcript       MeetingNotes     EmailLog
  meeting (O2O)    meeting (O2O)    meeting (O2O)    meeting→ (nullable)
  media_url (TEXT) full_text (text) summary (text)   recipient, kind, status,
  s3_url (TEXT)    speaker_segments action_items[]   error, sent_at
  duration_seconds   (JSON)         key_decisions[]
                                    follow_ups[]

meetings.ContactMessage  (standalone — from the public contact form)
  id, name, email, subject, message, created_at, handled
```

**Field-design notes (lessons baked in):**
- `Recording.media_url` / `s3_url` are **`TextField`**, not `URLField`. Recall's presigned download URLs
  exceed 1500 chars and overflowed a `varchar(1500)` column — this is *the* bug that once froze
  processing. Unbounded text fixes it.
- `bot_status_detail` is written **straight from Recall's API with no form validation**, so it's a
  generous `varchar(200)` **and** truncated in code (`code[:200]`) — defensive against the same class.
- JSON list fields (`action_items`, etc.) use `default=list` (a callable, safe default).

---

## 7. Django apps in depth

### 7.1 `config` (the project)
- **`settings.py`** reads everything from `.env` via helper functions (`env`, `env_bool`, `env_list`).
  Defines `AUTH_USER_MODEL = "accounts.User"`, the custom `EmailBackend`, WhiteNoise storage, the
  Postgres `DATABASE_URL`, all Celery settings (including the 60-second beat schedule), and the
  integration keys (`RECALL_*`, `DEEPGRAM_*`, `GROQ_*`, `RESEND_*`). `TIME_ZONE = Asia/Karachi`.
- **`celery.py`** builds the Celery app, pulls `CELERY_*` settings from Django, and autodiscovers
  `tasks.py` in each app. `__init__.py` imports it so `celery -A config` works.

### 7.2 `accounts` (identity + email transport)
- **Email-first auth:** `User` has no `username`; `email` is the login identifier (`USERNAME_FIELD`).
  `EmailBackend` authenticates case-insensitively and runs a dummy hash on miss (timing safety).
- **Web auth is session-based** (Django's `LoginView`/`LogoutView` with a custom email form). Simpler
  and safer than JWT for a server-rendered dashboard.
- **Login alert:** `signals.py` listens for `user_logged_in` and sends a styled "new sign-in" email
  (IP, device, time). Failures are swallowed so they can never block login.
- **Password reset:** Django's built-in reset flow, themed with our templates, emails through Resend.
- **`email_backend.ResendEmailBackend`:** a real Django email backend (`EMAIL_BACKEND`) that POSTs to
  `https://api.resend.com/emails`. This makes *all* Django mail (including password reset) go through
  Resend, while product emails use the same transport via `mailer.py`.

### 7.3 `meetings` (the product)
- **Views** map 1:1 to the URLs in §8. Notable: `schedule` calls Recall synchronously so the user gets
  immediate success/failure; `deploy_now` is a CSRF-protected POST; `recall_webhook` is an optional,
  CSRF-exempt endpoint that just nudges the poll (the poll is the real engine).
- **`tasks.py`** is the worker brain — see §3 and §9.
- **`services/`** isolates every external dependency behind a small, defensively-written module, so a
  vendor's API-shape change (or a swap, e.g. Groq→Claude) touches exactly one file.

---

## 8. URL map

| Path | View | Auth | Purpose |
|------|------|------|---------|
| `/` | `meetings.landing` | public | Marketing home (redirects to dashboard if logged in) |
| `/about/` | `meetings.about` | public | About page |
| `/contact/` | `meetings.contact` | public | Contact form (saves `ContactMessage`) |
| `/dashboard/` | `meetings.dashboard` | login | Meeting list + stats |
| `/schedule/` | `meetings.schedule` | login | Schedule form → creates Recall bot |
| `/meetings/<id>/` | `meetings.detail` | login | Notes, transcript, recording, status |
| `/meetings/<id>/deploy-now/` | `meetings.deploy_now` | login (POST) | Make the bot join immediately |
| `/api/statuses/` | `meetings.statuses` | login | JSON for dashboard live-refresh |
| `/api/webhooks/recall/` | `meetings.recall_webhook` | public (POST) | Optional Recall webhook → nudges poll |
| `/accounts/register/` | `accounts.RegisterView` | public | Sign up (auto-login + welcome email) |
| `/accounts/login/` | `accounts.LoginView` | public | Sign in (fires login-alert email) |
| `/accounts/logout/` | `accounts.LogoutView` | login | Sign out |
| `/accounts/password-reset/…` | password reset flow | public | 4-step reset via emailed link |
| `/admin/` | Django admin | staff | Manage users, meetings, contact messages |

---

## 9. Background processing (Celery)

- **Worker:** `mitaxy-celery.service` → `celery -A config worker -l info --concurrency=2`.
- **Scheduler:** `mitaxy-celerybeat.service` → `celery -A config beat …` with one periodic task.
- **Broker/results:** Redis DB 5 / DB 6 (`redis://127.0.0.1:6379/5` and `/6`).

**`poll_pending_meetings`** (every 60s): selects meetings in `scheduled/joining/recording` with a bot
id, calls `recall.get_bot()` for each, and advances status. On a terminal Recall code it flips to
`PROCESSING` **once** and enqueues `process_meeting`. A 6-hour timeout guard fails bots that never
produce a recording. Because a meeting in `PROCESSING` is outside the poll's filter, it can't be
double-enqueued.

**`process_meeting`** (bound task, `max_retries=10`): runs Recording → Deepgram → Groq → email →
`COMPLETED`. If the recording isn't ready it `self.retry(countdown=60)`; on any error it
`self.retry(countdown=120)` and, when retries are exhausted (`MaxRetriesExceededError`), marks the
meeting `FAILED` with the error message. Celery's `Retry` exception is caught and re-raised separately
so a retry is never mistaken for a failure.

---

## 10. Email system

```
Product emails:  view/task → mailer.send_html_email(to, subject, template, ctx)
                            → renders templates/emails/<template>.html with brand context
                            → Django EmailMultiAlternatives (html + text)
                            → EMAIL_BACKEND = ResendEmailBackend → POST api.resend.com/emails

Django emails:   password reset → Django's flow → same ResendEmailBackend
```

Templates all extend `emails/base_email.html` (teal header, table-based layout, inline styles for client
compatibility): **welcome**, **login_alert**, **meeting_notes**, **password_reset_email**.

> **Resend constraint:** on the free tier without a verified domain, Resend only delivers to the Resend
> account owner's email (currently `moinmail001@gmail.com`), sending from `onboarding@resend.dev`.
> `RESEND_FROM` is env-driven — verify a domain in Resend and change it to send to anyone.

---

## 11. Frontend / UI

- **Design system** (`static/css/app.css`): white canvas + **teal** (`#0F766E`) accent with slate
  neutrals — deliberately *not* the typical purple "AI" look. CSS variables drive colors, radius,
  shadows. Components: nav, buttons, cards, forms, status badges, hero, steps, features, FAQ accordion,
  meeting rows, detail/notes grid, transcript, footer.
- **Templates** all extend `base.html` for a uniform shell. `partials/field.html` renders every form
  field consistently.
- **JavaScript** (`static/js/app.js`, no framework): dismiss flash messages, prevent past datetimes,
  and **live-poll `/api/statuses/`** on the dashboard so in-flight meeting badges update without a manual
  refresh.
- **Responsive:** breakpoints at 820 / 720 / 640 / 400 px (grids collapse to one column, nav stacks,
  hero CTAs go full-width, meeting rows reflow). Verified live in the CSSOM.

---

## 12. Server infrastructure

| Component | Where / what |
|-----------|--------------|
| App code | `/root/mitaxy` |
| Virtualenv | `/root/mitaxy/venv` (Python 3.12) |
| Secrets | `/root/mitaxy/.env` (chmod 600, **never** committed) |
| Gunicorn socket | `/root/mitaxy/mitaxy.sock` (root:www-data) |
| Static (collected) | `/root/mitaxy/staticfiles/` (served by nginx + WhiteNoise) |
| Media | `/root/mitaxy/media/` |
| systemd: web | `mitaxy.service` → Gunicorn, 3 workers, unix socket |
| systemd: worker | `mitaxy-celery.service` → Celery worker, concurrency 2 |
| systemd: scheduler | `mitaxy-celerybeat.service` → Celery beat (60s poll) |
| nginx site | `/etc/nginx/sites-available/mitaxy` → `server_name 212.47.72.184`, proxies socket, serves `/static/` + `/media/` |
| PostgreSQL | role + db both `mitaxy`, `127.0.0.1:5432` |
| Redis | `127.0.0.1:6379`, DB 5 (broker) / DB 6 (results) |

**Coexistence with other apps:** the box also runs `apkmanager`, `firestick`, `firestick3`,
`firestickpackagess` (Django/Gunicorn/nginx/Certbot on their own domains, SQLite). Mitaxy shares nothing
with them: distinct DB, distinct Redis indexes, distinct service names, distinct socket, and an
IP-matched nginx server block (so domain-based vhosts are unaffected). nginx is always reloaded
gracefully after `nginx -t` passes.

---

## 13. Configuration (environment variables)

All config lives in `/root/mitaxy/.env` (see `.env.example` for the template):

| Variable | Purpose |
|----------|---------|
| `DJANGO_SECRET_KEY` | Django cryptographic secret |
| `DJANGO_DEBUG` | `False` in production |
| `DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` / `SITE_URL` | Host & CSRF config, base URL for email links |
| `TIME_ZONE` | `Asia/Karachi` |
| `DATABASE_URL` | `postgres://mitaxy:***@127.0.0.1:5432/mitaxy` |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis DB 5 / DB 6 |
| `RECALL_API_KEY` / `RECALL_REGION` / `RECALL_BOT_NAME` | Meeting bot (region `us-west-2`) |
| `DEEPGRAM_API_KEY` / `DEEPGRAM_MODEL` | Transcription (`nova-2`) |
| `GROQ_API_KEY` / `GROQ_MODEL` | AI notes (`llama-3.3-70b-versatile`) |
| `RESEND_API_KEY` / `RESEND_FROM` | Email |

---

## 14. Deployment & Git workflow

**Code lives in two places:** local dev (`/Users/nadirprofile/Desktop/mitaxy`) and the server
(`/root/mitaxy`, which is also the Git working tree with the GitHub remote configured).

**Deploy a change:**
```bash
# 1. From local: push code to the server (excludes secrets/venv/artifacts)
rsync -az --exclude venv --exclude __pycache__ --exclude .env \
      --exclude staticfiles --exclude media \
      /Users/nadirprofile/Desktop/mitaxy/ root@212.47.72.184:/root/mitaxy/

# 2. On the server: apply DB + static changes and restart
cd /root/mitaxy
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput
systemctl restart mitaxy mitaxy-celery mitaxy-celerybeat
```

**Push to GitHub** (remote already configured in `/root/mitaxy/.git/config` with the PAT):
```bash
cd /root/mitaxy && git add -A && git commit -m "..." && git push
```
> `git config --global safe.directory /root/mitaxy` is set (the dir is owned by uid 502 from rsync,
> which would otherwise trip Git's dubious-ownership check).

---

## 15. Operations & troubleshooting

```bash
# Service health
systemctl status mitaxy mitaxy-celery mitaxy-celerybeat

# Logs
journalctl -u mitaxy.service -f             # web/gunicorn (HTTP errors, 500s)
journalctl -u mitaxy-celery.service -f      # the pipeline (transcription, AI, email)
journalctl -u mitaxy-celerybeat.service -f  # the 60s poll scheduler

# Re-run a stuck/failed meeting
cd /root/mitaxy && ./venv/bin/python manage.py shell -c \
  "from meetings.tasks import process_meeting; process_meeting.delay(<id>)"

# DB / Redis quick checks
sudo -u postgres psql -d mitaxy -c "\dt"
redis-cli -n 5 ping
```

**Common issues:**
- *Card stuck on "Processing":* check `mitaxy-celery` logs for an exception in `process_meeting`
  (historically a `varchar` overflow on the recording URL — now fixed with `TextField`).
- *Notes email not received:* expected unless the recipient is the Resend account owner — verify a
  domain in Resend and set `RESEND_FROM` (see §10).
- *Schedule returns 500:* check the web log; ensure Recall key/region are valid (region is `us-west-2`).

---

## 16. Security posture & known limitations

- **HTTP only** — no domain/SSL yet. Point a domain at the server and run `certbot --nginx` (as the
  other apps do) to add free Let's Encrypt SSL; then tighten cookie/SSL settings.
- **Secrets** live only in `/root/mitaxy/.env` (chmod 600) and are gitignored — verified absent from the
  public GitHub repo. The GitHub PAT is stored in `/root/mitaxy/.git/config` (plaintext) to enable
  server-side pushes; rotate it if the server is ever shared.
- **Public repo** — `github.com/moin-ul-haq/mitaxy` is public and the docs reference the server IP/infra
  (no credentials). Make it private if that's a concern.
- **Recall cost** — real bot deployments into live calls are billed by Recall (the rest of the stack is
  on free tiers).
- **AI provider** — Groq is used instead of the brief's Claude (no Anthropic key was provided); swap by
  editing only `meetings/services/ai.py`.

---

## 17. Change history (notable fixes)

| Date | Change |
|------|--------|
| 2026-06-15 | Initial build & deploy; full pipeline live |
| 2026-06-15 | Added editable per-meeting **bot display name**, **Deploy now**, About/Contact/FAQ pages, production footer, responsive breakpoints |
| 2026-06-15 | **Fix:** schedule 500 — Django 5.1 removed `timezone.utc` → use `datetime.timezone.utc` |
| 2026-06-15 | **Fix:** processing stuck forever — Recall presigned URL overflowed `varchar(1500)` → `media_url`/`s3_url` now `TextField`; hardened `bot_status_detail` |
| 2026-06-15 | Initialized Git in `/root/mitaxy`, pushed to `github.com/moin-ul-haq/mitaxy` |
```
