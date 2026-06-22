#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_URL="http://${SHUSHUNYA_AGENT_HOST:-127.0.0.1}:${SHUSHUNYA_AGENT_PORT:-8095}"
INTERVAL_SEC="${SHUSHUNYA_AGENT_WATCH_INTERVAL_SEC:-15}"
MAX_FAILURES="${SHUSHUNYA_AGENT_WATCH_MAX_FAILURES:-2}"
ONCE="${SHUSHUNYA_AGENT_WATCH_ONCE:-0}"
TASK_SUPERVISOR="${SHUSHUNYA_AGENT_WATCH_TASK_SUPERVISOR:-0}"
TASK_INTERVAL_SEC="${SHUSHUNYA_AGENT_TASK_WATCH_INTERVAL_SEC:-900}"
PYTHON="$ROOT/ShushunyaAgent/bin/python"

auth_args=()
if [[ -n "${SHUSHUNYA_AGENT_API_KEY:-}" ]]; then
  auth_args=(-H "Authorization: Bearer $SHUSHUNYA_AGENT_API_KEY")
fi

failures=0
last_task_watch=0

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
    now="$(date +%s)"
    if [[ "$TASK_SUPERVISOR" == "1" || "$TASK_SUPERVISOR" == "true" ]]; then
      if (( now - last_task_watch >= TASK_INTERVAL_SEC )); then
        last_task_watch="$now"
        if [[ -x "$PYTHON" ]]; then
          PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -m shushunya_agent.task_watchdog --once >&2 || true
        else
          echo "watch-agent-api: task supervisor skipped, missing python: $PYTHON" >&2
        fi
      fi
    fi
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
