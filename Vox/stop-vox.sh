#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/runtime/vox.pid"
if [ -f "$PID_FILE" ]; then
  kill "$(cat "$PID_FILE")" 2>/dev/null && echo "Stopped Vox PID $(cat "$PID_FILE")" || echo "Vox was not running"
  rm -f "$PID_FILE"
fi
