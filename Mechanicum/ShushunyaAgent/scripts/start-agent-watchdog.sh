#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/agent-watchdog.pid"
LOG_FILE="$RUNTIME_DIR/agent-watchdog.log"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "ShushunyaAgent watchdog is already running with PID $(cat "$PID_FILE")"
  exit 0
fi

mkdir -p "$RUNTIME_DIR"
cd "$ROOT"

if [[ -f "$RUNTIME_DIR/agent-watchdog.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$RUNTIME_DIR/agent-watchdog.env"
  set +a
fi

setsid "$ROOT/scripts/watch-agent-api.sh" >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"

echo "ShushunyaAgent watchdog started: PID $(cat "$PID_FILE")"
echo "Log: $LOG_FILE"
