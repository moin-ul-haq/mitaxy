# Mitaxy deployment

## How CI/CD works (pull-based)

Every 2 minutes, `mitaxy-deploy.timer` runs `auto_deploy.sh` on the server:

1. `git fetch origin main` in `/root/mitaxy`.
2. If GitHub has new commits → `git reset --hard origin/main`.
3. `pip install -r requirements.txt` (only when requirements.txt changed).
4. `manage.py migrate` + `collectstatic`.
5. Restart **only** `mitaxy`, `mitaxy-celery`, `mitaxy-celerybeat`.

So: **push to `main` on GitHub → live within ~2 minutes.** Log: `/var/log/mitaxy-deploy.log`.

`.env`, `media/`, `staticfiles/`, and the venv are untracked, so deploys never
touch secrets or uploaded data. The other apps on this box are never restarted.

## One-time install (already done)

```bash
cp /root/mitaxy/deploy/mitaxy-deploy.service /etc/systemd/system/
cp /root/mitaxy/deploy/mitaxy-deploy.timer /etc/systemd/system/
chmod +x /root/mitaxy/deploy/auto_deploy.sh
systemctl daemon-reload
systemctl enable --now mitaxy-deploy.timer
```

## Manual deploy / rollback

```bash
/root/mitaxy/deploy/auto_deploy.sh        # deploy now (no wait)
cd /root/mitaxy && git reset --hard <sha> # roll back code
systemctl restart mitaxy mitaxy-celery mitaxy-celerybeat
```
(A rollback sticks until a NEW commit lands on main, because the script only
acts when local HEAD ≠ origin/main. To pin a rollback, stop the timer:
`systemctl stop mitaxy-deploy.timer`.)

## Env vars the app reads (server `/root/mitaxy/.env`)

Required: `DJANGO_SECRET_KEY`, `DATABASE_URL`, `RECALL_API_KEY`, `RECALL_REGION`,
`DEEPGRAM_API_KEY`, `GROQ_API_KEY`, `RESEND_API_KEY`, `RESEND_FROM`,
`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `SITE_URL`.

Optional:
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` — enables "Continue with Google"
  (redirect URI to whitelist: `https://mitaxy.moinit.dev/accounts/google/callback/`).
- `MITAXY_JOIN_TIMEOUT_MIN` (30), `MITAXY_WAITING_TIMEOUT_MIN` (45),
  `MITAXY_STALE_AFTER_MIN` (360) — bot lifecycle timeouts.
- `CACHE_URL` (default `redis://127.0.0.1:6379/7`).

**After editing `.env`, restart all three services** — celery reads it at boot:
`systemctl restart mitaxy mitaxy-celery mitaxy-celerybeat`
