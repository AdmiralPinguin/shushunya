#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/runtime/telegram-bot.pid"
LOG_FILE="$ROOT/runtime/telegram-bot.log"
ENV_FILE="$ROOT/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "Set TELEGRAM_BOT_TOKEN before starting the bot." >&2
  exit 1
fi

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Telegram bot is already running with PID $(cat "$PID_FILE")"
  exit 0
fi

ps -eo pid=,args= | awk -v script="$ROOT/bot.py" '$0 ~ "python3 " script && $0 !~ /awk/ {print $1}' | while read -r stale_pid; do
  if [ -n "$stale_pid" ] && [ "$stale_pid" != "$$" ]; then
    kill "$stale_pid" 2>/dev/null || true
  fi
done

mkdir -p "$ROOT/runtime"

setsid python3 "$ROOT/bot.py" >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"

echo "Telegram bot started: PID $(cat "$PID_FILE")"
echo "Log: $LOG_FILE"
