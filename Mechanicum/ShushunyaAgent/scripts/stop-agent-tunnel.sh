#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/agent-cloudflared.pid"
URL_FILE="$RUNTIME_DIR/agent-public-url.txt"

if [[ ! -f "$PID_FILE" ]]; then
  echo "Agent tunnel is not running"
  rm -f "$URL_FILE"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in {1..50}; do
    if ! kill -0 "$PID" 2>/dev/null; then
      echo "Stopped agent tunnel PID $PID"
      rm -f "$PID_FILE" "$URL_FILE"
      exit 0
    fi
    sleep 0.1
  done
  kill -KILL "$PID" 2>/dev/null || true
  echo "Force-stopped agent tunnel PID $PID"
else
  echo "Agent tunnel PID $PID is not alive"
fi

rm -f "$PID_FILE" "$URL_FILE"
