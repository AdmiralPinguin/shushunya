#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/runtime/litert-lm.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "LiteRT-LM server is not running"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "Stopped LiteRT-LM server PID $PID"
else
  echo "LiteRT-LM PID file was stale: $PID"
fi
rm -f "$PID_FILE"
