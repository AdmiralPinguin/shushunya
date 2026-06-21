#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_URL="http://${SHUSHUNYA_AGENT_HOST:-127.0.0.1}:${SHUSHUNYA_AGENT_PORT:-8095}"
INTERVAL_SEC="${SHUSHUNYA_AGENT_WATCH_INTERVAL_SEC:-15}"
MAX_FAILURES="${SHUSHUNYA_AGENT_WATCH_MAX_FAILURES:-2}"
ONCE="${SHUSHUNYA_AGENT_WATCH_ONCE:-0}"

auth_args=()
if [[ -n "${SHUSHUNYA_AGENT_API_KEY:-}" ]]; then
  auth_args=(-H "Authorization: Bearer $SHUSHUNYA_AGENT_API_KEY")
fi

failures=0

restart_api() {
  echo "watch-agent-api: restarting ShushunyaAgent API" >&2
  "$ROOT/scripts/stop-agent-api.sh" >&2 || true
  SHUSHUNYA_AGENT_START_CHECK_PATH="${SHUSHUNYA_AGENT_START_CHECK_PATH:-/state}" \
    "$ROOT/scripts/start-agent-api.sh" >&2
}

while true; do
  if curl -fsS "${auth_args[@]}" "$BASE_URL/state" >/dev/null; then
    if [[ "$failures" != "0" ]]; then
      echo "watch-agent-api: API recovered after $failures failed checks" >&2
    fi
    failures=0
  else
    failures=$((failures + 1))
    echo "watch-agent-api: /state failed ($failures/$MAX_FAILURES)" >&2
    if (( failures >= MAX_FAILURES )); then
      restart_api
      failures=0
    fi
  fi

  if [[ "$ONCE" == "1" || "$ONCE" == "true" ]]; then
    exit 0
  fi
  sleep "$INTERVAL_SEC"
done
