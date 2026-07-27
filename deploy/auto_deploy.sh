#!/usr/bin/env bash
# Mitaxy auto-deploy: pulls new commits from GitHub main and rolls them out.
# Runs every 2 minutes via mitaxy-deploy.timer (see deploy/README.md).
#
# SAFETY: touches ONLY the mitaxy services. Never nginx, never other apps.
set -euo pipefail

APP_DIR="/root/mitaxy"
VENV="$APP_DIR/venv/bin"
LOG="/var/log/mitaxy-deploy.log"
LOCK="/run/mitaxy-deploy.lock"
BRANCH="main"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# Never run two deploys at once.
exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

cd "$APP_DIR"

git fetch origin "$BRANCH" --quiet 2>>"$LOG" || { log "fetch failed"; exit 0; }

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

[ "$LOCAL" = "$REMOTE" ] && exit 0   # nothing new

log "deploying $LOCAL -> $REMOTE"

REQ_BEFORE=$(md5sum requirements.txt | cut -d' ' -f1)

# .env, media/, staticfiles/ are untracked/gitignored — reset never touches them.
git reset --hard "origin/$BRANCH" >> "$LOG" 2>&1

REQ_AFTER=$(md5sum requirements.txt | cut -d' ' -f1)
if [ "$REQ_BEFORE" != "$REQ_AFTER" ]; then
  log "requirements changed — installing"
  "$VENV/pip" install -q -r requirements.txt >> "$LOG" 2>&1 || log "pip install FAILED"
fi

"$VENV/python" manage.py migrate --noinput >> "$LOG" 2>&1 || { log "migrate FAILED — aborting restart"; exit 1; }
"$VENV/python" manage.py collectstatic --noinput >> "$LOG" 2>&1 || log "collectstatic FAILED"

# Restart ONLY mitaxy's own services (shared box — other apps must not be touched).
systemctl restart mitaxy mitaxy-celery mitaxy-celerybeat >> "$LOG" 2>&1
# Voice agent restarts only if installed & running (no-op before first install).
systemctl try-restart mitaxy-voice >> "$LOG" 2>&1 || true

sleep 3
if systemctl is-active --quiet mitaxy; then
  log "deploy OK at $(git rev-parse --short HEAD)"
else
  log "WARNING: mitaxy service not active after deploy — check journalctl -u mitaxy"
fi
