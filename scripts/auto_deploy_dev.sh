#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/var/www/html"
BRANCH="dev"
LOG_FILE="/var/log/wavepool-auto-deploy.log"
LOCK_FILE="/tmp/wavepool-auto-deploy.lock"

mkdir -p "$(dirname "$LOG_FILE")"

# Prevent overlapping runs from cron.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date -Iseconds) [INFO] Deploy already running; exiting." >> "$LOG_FILE"
  exit 0
fi

cd "$REPO_DIR"

echo "$(date -Iseconds) [INFO] Checking for updates on $BRANCH" >> "$LOG_FILE"

git fetch origin "$BRANCH" >> "$LOG_FILE" 2>&1

local_sha="$(git rev-parse HEAD)"
remote_sha="$(git rev-parse "origin/$BRANCH")"

if [ "$local_sha" = "$remote_sha" ]; then
  echo "$(date -Iseconds) [INFO] No new commits." >> "$LOG_FILE"
  exit 0
fi

stashed=0
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  git stash push -u -m "auto-deploy-$(date +%F-%T)" >> "$LOG_FILE" 2>&1
  stashed=1
fi

git pull --ff-only origin "$BRANCH" >> "$LOG_FILE" 2>&1

echo "$(date -Iseconds) [INFO] Updated to $(git rev-parse --short HEAD)" >> "$LOG_FILE"

# If you run a service, uncomment and set the correct unit.
# systemctl restart wavepool.service >> "$LOG_FILE" 2>&1

if [ "$stashed" -eq 1 ]; then
  if git stash pop >> "$LOG_FILE" 2>&1; then
    echo "$(date -Iseconds) [INFO] Restored stashed local changes." >> "$LOG_FILE"
  else
    echo "$(date -Iseconds) [WARN] Could not auto-pop stash; leaving stash for manual review." >> "$LOG_FILE"
  fi
fi
