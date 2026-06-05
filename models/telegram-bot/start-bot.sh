#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/runtime/telegram-bot.pid"
LOG_FILE="$ROOT/runtime/telegram-bot.log"

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "Set TELEGRAM_BOT_TOKEN before starting the bot." >&2
  exit 1
fi

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Telegram bot is already running with PID $(cat "$PID_FILE")"
  exit 0
fi

mkdir -p "$ROOT/runtime"

setsid python3 "$ROOT/bot.py" >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"

echo "Telegram bot started: PID $(cat "$PID_FILE")"
echo "Log: $LOG_FILE"
