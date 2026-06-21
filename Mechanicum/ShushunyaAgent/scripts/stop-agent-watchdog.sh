#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/runtime/agent-watchdog.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "ShushunyaAgent watchdog is not running"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in {1..50}; do
    if ! kill -0 "$PID" 2>/dev/null; then
      echo "Stopped ShushunyaAgent watchdog PID $PID"
      rm -f "$PID_FILE"
      exit 0
    fi
    sleep 0.1
  done
  kill -KILL "$PID" 2>/dev/null || true
  echo "Force-stopped ShushunyaAgent watchdog PID $PID"
else
  echo "ShushunyaAgent watchdog PID $PID is not alive"
fi

rm -f "$PID_FILE"
