#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/ReSave}"
PYTHON_BIN="${PYTHON_BIN:-python}"
BRANCH="${BRANCH:-main}"
BOT_API_VERSION="10.3"
BOT_API_TAG="bot-api-10.3-resave.1"
BOT_API_BIN="${BOT_API_BIN:-$HOME/.local/bin/telegram-bot-api}"
BOT_API_URL="https://github.com/ReNothingg/ReSave/releases/download/$BOT_API_TAG/telegram-bot-api-linux-amd64"
BOT_API_SHA256="30e63cd08dc3bd0c28fcf7fd465f3a68550ec6c39203313526ef071622faa26c"
LOCK_DIR="$APP_DIR/.run_alwaysdata_local_bot_api.lock"
LOG_DIR="$APP_DIR/logs"

log() {
  printf '[deploy] %s\n' "$*"
}

cd "$APP_DIR"
log "Updating $BRANCH"
git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"

log "Updating Python dependencies"
"$PYTHON_BIN" -m pip install --user --upgrade -r requirements.txt

installed_version="$($BOT_API_BIN --version 2>&1 || true)"
if [[ "$installed_version" != *"$BOT_API_VERSION"* ]]; then
  log "Installing telegram-bot-api $BOT_API_VERSION"
  mkdir -p "$(dirname "$BOT_API_BIN")"
  download_path="$BOT_API_BIN.download"
  curl --fail --location --retry 3 --output "$download_path" "$BOT_API_URL"
  printf '%s  %s\n' "$BOT_API_SHA256" "$download_path" | sha256sum --check --status
  chmod 755 "$download_path"
  mv "$download_path" "$BOT_API_BIN"
fi

if [ -f "$LOCK_DIR/pid" ]; then
  previous_pid="$(sed -n '1p' "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [[ "$previous_pid" =~ ^[0-9]+$ ]] && kill -0 "$previous_pid" 2>/dev/null; then
    log "Stopping previous service pid=$previous_pid"
    kill "$previous_pid"
    for _ in {1..120}; do
      kill -0 "$previous_pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$previous_pid" 2>/dev/null; then
      log "Previous service did not stop cleanly; refusing to start a second instance"
      exit 1
    fi
  fi
fi

mkdir -p "$LOG_DIR"
rm -rf "$LOCK_DIR"
log "Starting ReSave in background"
nohup env APP_DIR="$APP_DIR" PYTHON_BIN="$PYTHON_BIN" BOT_API_BIN="$BOT_API_BIN" \
  bash scripts/run_alwaysdata_local_bot_api.sh >>"$LOG_DIR/service.log" 2>&1 </dev/null &
service_pid=$!

ready=false
for _ in {1..60}; do
  if ! kill -0 "$service_pid" 2>/dev/null; then
    break
  fi
  if curl --silent --max-time 2 "http://127.0.0.1:8081/" >/dev/null \
    && ps -o args= --ppid "$service_pid" | grep -q '[m]ain.py'; then
    ready=true
    break
  fi
  sleep 1
done

if [ "$ready" != "true" ]; then
  log "Bot API or ReSave bot failed to become ready"
  kill "$service_pid" 2>/dev/null || true
  tail -n 100 "$LOG_DIR/service.log" || true
  exit 1
fi

log "ReSave is running: pid=$service_pid, $($BOT_API_BIN --version 2>&1 || true)"
tail -n 20 "$LOG_DIR/service.log" || true
