#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$ROOT/../.." && pwd)"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/heartbeat.pid"
LOG_FILE="$RUNTIME_DIR/heartbeat.log"

mkdir -p "$RUNTIME_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Administratum heartbeat is already running with PID $(cat "$PID_FILE")"
  exit 0
fi

PYTHONPATH="$PROJECT_ROOT" setsid "$PROJECT_ROOT/DemonsForge/DemonsForge/bin/python" \
  -m EyeOfTerror.Administratum.heartbeat \
  --interval-sec "${ADMINISTRATUM_HEARTBEAT_INTERVAL_SEC:-60}" \
  >"$LOG_FILE" 2>&1 </dev/null &

echo "$!" > "$PID_FILE"
echo "Administratum heartbeat started: PID $(cat "$PID_FILE")"
echo "Log: $LOG_FILE"
