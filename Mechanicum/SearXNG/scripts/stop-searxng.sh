#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/runtime/searxng.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "SearXNG is not running"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "Stopped SearXNG PID $PID"
else
  echo "SearXNG PID $PID is not alive"
fi

rm -f "$PID_FILE"
