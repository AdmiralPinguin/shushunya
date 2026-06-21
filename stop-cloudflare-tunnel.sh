#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/runtime/cloudflare/shushunya-core.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "No Cloudflare tunnel PID file found."
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "Stopped Cloudflare tunnel PID $PID"
else
  echo "Cloudflare tunnel process $PID is not running."
fi

rm -f "$PID_FILE"
