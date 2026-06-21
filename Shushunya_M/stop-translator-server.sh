#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/runtime/translator-server.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "No translator server PID file found."
  exit 0
fi

pid="$(cat "$PID_FILE")"
if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "Stopped translator server PID $pid"
else
  echo "Translator server process $pid is not running."
fi

rm -f "$PID_FILE"
