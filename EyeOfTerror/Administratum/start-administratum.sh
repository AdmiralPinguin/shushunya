#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$ROOT/../.." && pwd)"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/ashur-kai.pid"
LOG_FILE="$RUNTIME_DIR/ashur-kai.log"

mkdir -p "$RUNTIME_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "AshurKai is already running with PID $(cat "$PID_FILE")"
  exit 0
fi

PYTHONPATH="$PROJECT_ROOT" setsid "$PROJECT_ROOT/DemonsForge/DemonsForge/bin/python" \
  -m EyeOfTerror.Administratum.ashur_kai_service \
  --host "${ADMINISTRATUM_HOST:-127.0.0.1}" \
  --port "${ADMINISTRATUM_PORT:-7300}" \
  >"$LOG_FILE" 2>&1 </dev/null &

echo "$!" > "$PID_FILE"
echo "AshurKai started: PID $(cat "$PID_FILE"), http://${ADMINISTRATUM_HOST:-127.0.0.1}:${ADMINISTRATUM_PORT:-7300}"
echo "Log: $LOG_FILE"
