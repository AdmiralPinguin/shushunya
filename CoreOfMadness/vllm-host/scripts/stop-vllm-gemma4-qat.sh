#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/runtime/vllm-gemma4-qat.pid"

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    echo "Stopped vLLM Gemma4 QAT server PID $PID"
  fi
  rm -f "$PID_FILE"
fi

ps -eo pid=,args= | awk -v root="$ROOT/venv/bin/vllm" '$0 ~ root && $0 !~ /awk/ {print $1}' | while read -r stale_pid; do
  if [ -n "$stale_pid" ]; then
    kill "$stale_pid" 2>/dev/null || true
    echo "Stopped stale vLLM process PID $stale_pid"
  fi
done
