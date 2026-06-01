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
BOT_API_STARTUP_TIMEOUT="${BOT_API_STARTUP_TIMEOUT:-15}"
RESTART_ON_FAILURE="${RESTART_ON_FAILURE:-true}"
RESTART_DELAY="${RESTART_DELAY:-5}"
LOCK_DIR="${LOCK_DIR:-$APP_DIR/.run_alwaysdata_local_bot_api.lock}"
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
BOT_API_LOG_FILE="$LOG_DIR/telegram-bot-api.log"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" >"$LOCK_DIR/pid"
    return 0
  fi

  local existing_pid=""
  if [ -f "$LOCK_DIR/pid" ]; then
    existing_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  fi

  if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
    log "Another ReSave service wrapper is already running: pid=$existing_pid"
    exit 1
  fi

  log "Removing stale service lock"
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
  printf '%s\n' "$$" >"$LOCK_DIR/pid"
}

is_running() {
  [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null
}

bot_api_accepts_connections() {
  { exec 3<>"/dev/tcp/$BOT_API_HOST/$BOT_API_PORT"; } 2>/dev/null || return 1
  exec 3<&-
  exec 3>&-
  return 0
}

cleanup_children() {
  if is_running "$BOT_PID"; then
    kill "$BOT_PID" 2>/dev/null || true
    wait "$BOT_PID" 2>/dev/null || true
  fi
  if is_running "$BOT_API_PID"; then
    kill "$BOT_API_PID" 2>/dev/null || true
    wait "$BOT_API_PID" 2>/dev/null || true
  fi
  BOT_PID=""
  BOT_API_PID=""
}

cleanup() {
  cleanup_children
  if [ -f "$LOCK_DIR/pid" ] && [ "$(cat "$LOCK_DIR/pid" 2>/dev/null || true)" = "$$" ]; then
    rm -rf "$LOCK_DIR"
  fi
}
trap cleanup EXIT HUP INT TERM

acquire_lock

start_bot_api() {
  log "Starting telegram-bot-api at $BOT_API_BASE_URL"
  "$BOT_API_BIN" \
    --api-id="$TELEGRAM_API_ID" \
    --api-hash="$TELEGRAM_API_HASH" \
    --local \
    --http-ip-address="$BOT_API_HOST" \
    --http-port="$BOT_API_PORT" \
    --dir="$BOT_API_DIR" \
    --temp-dir="$BOT_API_TEMP_DIR" \
    --verbosity="${BOT_API_VERBOSITY:-1}" \
    >"$BOT_API_LOG_FILE" 2>&1 &
  BOT_API_PID=$!
}

wait_for_bot_api() {
  local elapsed=0
  while [ "$elapsed" -lt "$BOT_API_STARTUP_TIMEOUT" ]; do
    if bot_api_accepts_connections; then
      log "telegram-bot-api is accepting connections"
      return 0
    fi

    if ! is_running "$BOT_API_PID"; then
      log "telegram-bot-api exited during startup"
      tail -n 80 "$BOT_API_LOG_FILE" >&2 || true
      return 1
    fi

    sleep 1
    elapsed=$((elapsed + 1))
  done

  log "telegram-bot-api did not open $BOT_API_HOST:$BOT_API_PORT in ${BOT_API_STARTUP_TIMEOUT}s"
  tail -n 80 "$BOT_API_LOG_FILE" >&2 || true
  return 1
}

start_bot() {
  log "Starting ReSave bot"
  "$PYTHON_BIN" main.py &
  BOT_PID=$!
}

monitor_children() {
  while true; do
    if ! is_running "$BOT_API_PID"; then
      wait "$BOT_API_PID" 2>/dev/null
      local status=$?
      log "telegram-bot-api exited with status $status"
      return "$status"
    fi

    if ! is_running "$BOT_PID"; then
      wait "$BOT_PID" 2>/dev/null
      local status=$?
      log "ReSave bot exited with status $status"
      return "$status"
    fi

    sleep 2
  done
}

while true; do
  start_bot_api
  set +e
  wait_for_bot_api
  status=$?
  set -e
  if [ "$status" -ne 0 ]; then
    cleanup_children
    if [ "$RESTART_ON_FAILURE" != "true" ]; then
      exit "$status"
    fi
    log "Restarting service pair in ${RESTART_DELAY}s"
    sleep "$RESTART_DELAY"
    continue
  fi

  start_bot

  set +e
  monitor_children
  status=$?
  set -e

  cleanup_children

  if [ "$RESTART_ON_FAILURE" != "true" ]; then
    exit "$status"
  fi

  log "Restarting service pair in ${RESTART_DELAY}s"
  sleep "$RESTART_DELAY"
done
