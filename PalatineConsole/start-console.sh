#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/runtime/palatine-console.pid"
LOG_FILE="$ROOT/runtime/palatine-console.log"
PORT="${PALATINE_PORT:-57257}"

mkdir -p "$ROOT/runtime"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Palatine Console already running: http://127.0.0.1:$PORT"
  xdg-open "http://127.0.0.1:$PORT" >/dev/null 2>&1 || true
  exit 0
fi

rm -f "$PID_FILE"
setsid python3 "$ROOT/control_server.py" --open >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"

echo "Palatine Console started: PID $(cat "$PID_FILE"), http://127.0.0.1:$PORT"
echo "Log: $LOG_FILE"
