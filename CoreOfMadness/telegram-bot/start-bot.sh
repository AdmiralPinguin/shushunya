#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="$ROOT/runtime"
PID_FILE="$RUNTIME/telegram-bot.pid"
LOG_FILE="$RUNTIME/telegram-bot.log"
LOCK_FILE="$RUNTIME/start-bot.lock"
ENV_FILE="$ROOT/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] || { echo "Set TELEGRAM_BOT_TOKEN before starting the bot." >&2; exit 1; }

STOP_SECS="${TELEGRAM_BOT_STOP_TIMEOUT_SEC:-45}"
LOCK_SECS="${TELEGRAM_BOT_START_LOCK_TIMEOUT_SEC:-60}"
[[ "$STOP_SECS" =~ ^[1-9][0-9]*$ && "$LOCK_SECS" =~ ^[1-9][0-9]*$ ]] || {
  echo "Telegram bot lock/stop timeouts must be positive integers." >&2; exit 1;
}
command -v flock >/dev/null 2>&1 || { echo "flock is required." >&2; exit 1; }
mkdir -p "$RUNTIME"

is_ours() {
  local p="${1:-}" args=()
  [[ "$p" =~ ^[1-9][0-9]*$ ]] && kill -0 "$p" 2>/dev/null && [ -r "/proc/$p/cmdline" ] || return 1
  mapfile -d '' -t args < "/proc/$p/cmdline" || return 1
  [ "${#args[@]}" -ge 2 ] || return 1
  case "${args[0]##*/}" in python|python[0-9]*) ;; *) return 1 ;; esac
  [ "${args[1]}" = "$ROOT/bot.py" ]
}

own_pids() {
  local proc p
  for proc in /proc/[0-9]*; do
    p="${proc##*/}"
    is_ours "$p" && printf '%s\n' "$p"
  done
  return 0
}

stop_one() {
  local p="$1" i
  kill -TERM "$p" 2>/dev/null || { is_ours "$p" && return 1 || return 0; }
  for ((i=0; i<STOP_SECS*5; i++)); do
    is_ours "$p" || return 0
    sleep 0.2
  done
  echo "Telegram bot PID $p did not stop in ${STOP_SECS}s; refusing a second poller." >&2
  return 1
}

(
  flock -w "$LOCK_SECS" 9 || { echo "Timed out waiting for Telegram bot start lock." >&2; exit 1; }
  tracked=""
  if [ -f "$PID_FILE" ] && read -r p < "$PID_FILE" && is_ours "${p:-}"; then
    tracked="$p"
  else
    rm -f "$PID_FILE"
  fi
  for p in $(own_pids); do
    [ "$p" = "$tracked" ] || stop_one "$p" || exit 1
  done
  if [ -n "$tracked" ] && is_ours "$tracked"; then
    echo "Telegram bot is already running with PID $tracked"
    exit 0
  fi
  rm -f "$PID_FILE"
  setsid python3 "$ROOT/bot.py" >"$LOG_FILE" 2>&1 </dev/null 9>&- &
  p="$!"
  printf '%s\n' "$p" > "$PID_FILE"
  sleep 0.2
  if ! is_ours "$p"; then
    rm -f "$PID_FILE"
    echo "Telegram bot exited during startup. See $LOG_FILE" >&2
    exit 1
  fi
  echo "Telegram bot started: PID $p"
  echo "Log: $LOG_FILE"
) 9>"$LOCK_FILE"
