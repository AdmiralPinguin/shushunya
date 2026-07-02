#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/ShushunyaAgent/bin/python"
cd "$ROOT"

if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export SHUSHUNYA_AGENT_SEARXNG_URL="${SHUSHUNYA_AGENT_SEARXNG_URL:-http://127.0.0.1:8888}"
export SHUSHUNYA_AGENT_SEARCH_PROVIDERS="${SHUSHUNYA_AGENT_SEARCH_PROVIDERS:-searxng,marginalia,wikipedia,brave}"
exec "$PYTHON" -m shushunya_agent.agent_runner "$@"
