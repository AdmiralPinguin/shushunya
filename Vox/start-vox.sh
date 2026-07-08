#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$ROOT/runtime"
PID_FILE="$ROOT/runtime/vox.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Vox already running with PID $(cat "$PID_FILE")"
  exit 0
fi
setsid nohup python3 "$ROOT/vox_service.py" > "$ROOT/runtime/vox.log" 2>&1 < /dev/null &
echo $! > "$PID_FILE"
sleep 1
echo "Vox started: PID $(cat "$PID_FILE")"
