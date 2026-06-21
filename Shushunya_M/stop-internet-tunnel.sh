#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/runtime/cloudflared.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "No tunnel PID file found."
  exit 0
fi

pid="$(cat "$PID_FILE")"
if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "Stopped tunnel PID $pid"
else
  echo "Tunnel process $pid is not running."
fi

rm -f "$PID_FILE"
