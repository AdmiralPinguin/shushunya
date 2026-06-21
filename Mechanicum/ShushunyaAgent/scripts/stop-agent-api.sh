#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/runtime/agent-api.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "ShushunyaAgent API is not running"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "Stopped ShushunyaAgent API PID $PID"
else
  echo "ShushunyaAgent API PID $PID is not alive"
fi

rm -f "$PID_FILE"
