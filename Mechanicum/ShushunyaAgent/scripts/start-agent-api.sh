#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/agent-api.pid"
LOG_FILE="$RUNTIME_DIR/agent-api.log"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "ShushunyaAgent API is already running with PID $(cat "$PID_FILE")"
  exit 0
fi

mkdir -p "$RUNTIME_DIR"
cd "$ROOT"

setsid python3 -m shushunya_agent.server >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"

echo "ShushunyaAgent API started: PID $(cat "$PID_FILE"), http://${SHUSHUNYA_AGENT_HOST:-127.0.0.1}:${SHUSHUNYA_AGENT_PORT:-8095}"
echo "Log: $LOG_FILE"

BASE_URL="http://${SHUSHUNYA_AGENT_HOST:-127.0.0.1}:${SHUSHUNYA_AGENT_PORT:-8095}"
for _ in {1..30}; do
  if curl -fsS "$BASE_URL/health" >/dev/null 2>&1; then
    exit 0
  fi
  sleep 0.2
done

echo "ShushunyaAgent API did not become healthy in time" >&2
tail -40 "$LOG_FILE" >&2 || true
exit 1
