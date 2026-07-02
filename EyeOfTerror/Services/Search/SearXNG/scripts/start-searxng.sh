#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="/media/shushunya/SHUSHUNYA/shushunya"
AGENT_VENV="$PROJECT_ROOT/EyeOfTerror/Warmaster/MobileGateway/ShushunyaAgent/ShushunyaAgent"
SETTINGS="$ROOT/config/settings.yml"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/searxng.pid"
LOG_FILE="$RUNTIME_DIR/searxng.log"
HOST="${SEARXNG_BIND_ADDRESS:-127.0.0.1}"
PORT="${SEARXNG_PORT:-8888}"

if [[ ! -f "$SETTINGS" ]]; then
  echo "Missing SearXNG settings: $SETTINGS" >&2
  echo "Create it from README instructions before starting." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "SearXNG already running with PID $(cat "$PID_FILE")"
  exit 0
fi

cd "$PROJECT_ROOT"
PATH="$AGENT_VENV/bin:$PATH" \
SEARXNG_SETTINGS_PATH="$SETTINGS" \
SEARXNG_BIND_ADDRESS="$HOST" \
SEARXNG_PORT="$PORT" \
setsid "$AGENT_VENV/bin/searxng-run" >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"

BASE_URL="http://$HOST:$PORT"
for _ in {1..80}; do
  if curl -fsS -H "X-Real-IP: 127.0.0.1" "$BASE_URL/search?q=OpenAI&format=json" >/dev/null 2>&1; then
    echo "SearXNG started: PID $(cat "$PID_FILE"), $BASE_URL"
    exit 0
  fi
  sleep 0.25
done

echo "SearXNG did not become healthy in time" >&2
tail -80 "$LOG_FILE" >&2 || true
exit 1
