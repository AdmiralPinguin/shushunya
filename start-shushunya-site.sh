#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SITE_DIR="$ROOT/ShushunyaSite"
RUNTIME_DIR="$ROOT/runtime/shushunya-site"
PID_FILE="$RUNTIME_DIR/site.pid"
LOG_FILE="$RUNTIME_DIR/site.log"
HOST="${SHUSHUNYA_SITE_HOST:-127.0.0.1}"
PORT="${SHUSHUNYA_SITE_PORT:-8094}"

mkdir -p "$RUNTIME_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Shushunya site already running with PID $(cat "$PID_FILE")"
  exit 0
fi

rm -f "$PID_FILE"
setsid python3 -m http.server "$PORT" --bind "$HOST" --directory "$SITE_DIR" >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"

echo "Shushunya site started: PID $(cat "$PID_FILE"), http://$HOST:$PORT"
echo "Log: $LOG_FILE"
