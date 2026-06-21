#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/runtime/palatine-console.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "No Palatine Console PID file found."
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "Stopped Palatine Console PID $PID"
else
  echo "Palatine Console process $PID is not running."
fi

rm -f "$PID_FILE"
