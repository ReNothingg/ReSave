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
FFMPEG_TAG="ffmpeg-20260826-resave.1"
FFMPEG_BIN="${FFMPEG_BIN:-$HOME/.local/bin/ffmpeg}"
FFMPEG_URL="https://github.com/ReNothingg/ReSave/releases/download/$FFMPEG_TAG/ffmpeg-linux-amd64"
FFMPEG_SHA256="233dfb130636a69f88b3a187af1eab91e1652689fdc2613f9da9d37f8aef752f"
ALWAYSDATA_SERVICE_ID="${ALWAYSDATA_SERVICE_ID:-23340}"
ALWAYSDATA_ACCOUNT="${ALWAYSDATA_ACCOUNT:-renothingg}"

log() {
  printf '[deploy] %s\n' "$*"
}

cd "$APP_DIR"
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . .env
  set +a
fi

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

if ! printf '%s  %s\n' "$FFMPEG_SHA256" "$FFMPEG_BIN" | sha256sum --check --status 2>/dev/null; then
  log "Installing ReSave ffmpeg build"
  mkdir -p "$(dirname "$FFMPEG_BIN")"
  download_path="$FFMPEG_BIN.download"
  curl --fail --location --retry 3 --output "$download_path" "$FFMPEG_URL"
  printf '%s  %s\n' "$FFMPEG_SHA256" "$download_path" | sha256sum --check --status
  chmod 755 "$download_path"
  mv "$download_path" "$FFMPEG_BIN"
fi

if [ -z "${ALWAYSDATA_API_TOKEN:-}" ]; then
  log "ALWAYSDATA_API_TOKEN is missing in .env; cannot restart Service $ALWAYSDATA_SERVICE_ID"
  exit 2
fi

log "Restarting alwaysdata Service $ALWAYSDATA_SERVICE_ID"
curl --fail-with-body --silent --show-error \
  --header "alwaysdata-synchronous: true" \
  --basic --user "$ALWAYSDATA_API_TOKEN account=$ALWAYSDATA_ACCOUNT:" \
  --request POST \
  "https://api.alwaysdata.com/v1/service/$ALWAYSDATA_SERVICE_ID/restart/" \
  >/dev/null

log "Update installed and restart accepted by alwaysdata"
