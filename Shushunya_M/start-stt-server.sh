#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/stt-server.pid"
LOG_FILE="$RUNTIME_DIR/stt-server.log"

mkdir -p "$RUNTIME_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "STT server already running with PID $(cat "$PID_FILE")"
  exit 0
fi

setsid python3 "$ROOT/stt-server.py" >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"
echo "STT server started: PID $(cat "$PID_FILE"), http://127.0.0.1:${STT_PORT:-8093}"
echo "Log: $LOG_FILE"
