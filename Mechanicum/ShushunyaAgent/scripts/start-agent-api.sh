#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/agent-api.pid"
LOG_FILE="$RUNTIME_DIR/agent-api.log"
PYTHON="$ROOT/ShushunyaAgent/bin/python"
BASE_URL="http://${SHUSHUNYA_AGENT_HOST:-127.0.0.1}:${SHUSHUNYA_AGENT_PORT:-8095}"
HEALTH_PATH="${SHUSHUNYA_AGENT_START_CHECK_PATH:-/health}"
AUTH_ARGS=()
if [[ -n "${SHUSHUNYA_AGENT_API_KEY:-}" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer $SHUSHUNYA_AGENT_API_KEY")
fi

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "ShushunyaAgent API is already running with PID $(cat "$PID_FILE")"
  if curl -fsS "${AUTH_ARGS[@]}" "$BASE_URL$HEALTH_PATH" >/dev/null 2>&1; then
    exit 0
  fi
  echo "Existing ShushunyaAgent API PID is alive but health check failed: $BASE_URL$HEALTH_PATH" >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR"
cd "$ROOT"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing agent Python environment: $PYTHON" >&2
  echo "Create it at $ROOT/ShushunyaAgent before starting the API." >&2
  exit 1
fi

export SHUSHUNYA_AGENT_SEARXNG_URL="${SHUSHUNYA_AGENT_SEARXNG_URL:-http://127.0.0.1:8888}"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
setsid "$PYTHON" -m shushunya_agent.server >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"

echo "ShushunyaAgent API started: PID $(cat "$PID_FILE"), $BASE_URL"
echo "Log: $LOG_FILE"

for _ in {1..30}; do
  if curl -fsS "${AUTH_ARGS[@]}" "$BASE_URL$HEALTH_PATH" >/dev/null 2>&1; then
    exit 0
  fi
  sleep 0.2
done

echo "ShushunyaAgent API did not become healthy in time" >&2
tail -40 "$LOG_FILE" >&2 || true
exit 1
