#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/ReSave}"
BOT_API_BIN="${BOT_API_BIN:-$HOME/.local/bin/telegram-bot-api}"
BOT_API_HOST="${BOT_API_HOST:-127.0.0.1}"
BOT_API_PORT="${BOT_API_PORT:-8081}"
BOT_API_DIR="${BOT_API_DIR:-$APP_DIR/.telegram-bot-api}"
BOT_API_TEMP_DIR="${BOT_API_TEMP_DIR:-$APP_DIR/temp_downloads/bot-api}"
LOG_DIR="${LOG_DIR:-$APP_DIR/logs}"
PYTHON_BIN="${PYTHON_BIN:-python}"
BOT_API_PID=""
BOT_PID=""

export PATH="$HOME/.local/bin:$HOME/ffmpeg/ffmpeg-7.0.2-amd64-static:$PATH"
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
export MAX_CONCURRENT_DOWNLOADS="${MAX_CONCURRENT_DOWNLOADS:-1}"
export MAX_DOWNLOADS_PER_USER="${MAX_DOWNLOADS_PER_USER:-1}"
export INLINE_DOWNLOAD_ENABLED="${INLINE_DOWNLOAD_ENABLED:-false}"

cd "$APP_DIR"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  . ".env"
  set +a
fi

if [ ! -x "$BOT_API_BIN" ]; then
  echo "telegram-bot-api binary not found or not executable: $BOT_API_BIN" >&2
  echo "Build or install it first, then set BOT_API_BIN if needed." >&2
  exit 1
fi

if [ -z "${TELEGRAM_API_ID:-}" ] || [ -z "${TELEGRAM_API_HASH:-}" ]; then
  echo "TELEGRAM_API_ID and TELEGRAM_API_HASH are required for local telegram-bot-api." >&2
  exit 1
fi

mkdir -p "$BOT_API_DIR" "$BOT_API_TEMP_DIR" "$LOG_DIR"

export BOT_API_BASE_URL="http://$BOT_API_HOST:$BOT_API_PORT"
export BOT_API_IS_LOCAL="true"

"$BOT_API_BIN" \
  --api-id="$TELEGRAM_API_ID" \
  --api-hash="$TELEGRAM_API_HASH" \
  --local \
  --http-ip-address="$BOT_API_HOST" \
  --http-port="$BOT_API_PORT" \
  --dir="$BOT_API_DIR" \
  --temp-dir="$BOT_API_TEMP_DIR" \
  --verbosity="${BOT_API_VERBOSITY:-2}" \
  >"$LOG_DIR/telegram-bot-api.log" 2>&1 &
BOT_API_PID=$!

cleanup() {
  if [ -n "$BOT_API_PID" ]; then
    kill "$BOT_API_PID" 2>/dev/null || true
    wait "$BOT_API_PID" 2>/dev/null || true
  fi
  if [ -n "$BOT_PID" ]; then
    kill "$BOT_PID" 2>/dev/null || true
    wait "$BOT_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT HUP INT TERM

sleep 2

"$PYTHON_BIN" main.py &
BOT_PID=$!

wait -n "$BOT_API_PID" "$BOT_PID"
