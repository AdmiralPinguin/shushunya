#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/runtime/telegram-bot.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "No PID file found."
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "Stopped Telegram bot PID $PID"
else
  echo "Process $PID is not running."
fi

sleep 1

ps -eo pid=,args= | awk -v script="$ROOT/bot.py" '$0 ~ "python3 " script && $0 !~ /awk/ {print $1}' | while read -r stale_pid; do
  if [ -n "$stale_pid" ] && [ "$stale_pid" != "$$" ]; then
    kill -9 "$stale_pid" 2>/dev/null || true
    echo "Stopped stale Telegram bot PID $stale_pid"
  fi
done

rm -f "$PID_FILE"
