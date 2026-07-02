#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/media/shushunya/SHUSHUNYA/shushunya"
AGENT_ROOT="$PROJECT_ROOT/EyeOfTerror/Warmaster/MobileGateway/ShushunyaAgent"
BASE_URL="http://${SHUSHUNYA_AGENT_HOST:-127.0.0.1}:${SHUSHUNYA_AGENT_PORT:-8095}"

auth_args=()
if [[ -n "${SHUSHUNYA_AGENT_API_KEY:-}" ]]; then
  auth_args=(-H "Authorization: Bearer $SHUSHUNYA_AGENT_API_KEY")
fi

echo "ShushunyaAgent API state:"
if curl -fsS "${auth_args[@]}" "$BASE_URL/state"; then
  echo
else
  echo "skip: API is not running or /state is unavailable"
fi
echo

echo "Offline sandbox self-test:"
cd "$AGENT_ROOT"
export SHUSHUNYA_AGENT_SELF_TEST_OFFLINE=1
export SHUSHUNYA_AGENT_SEARXNG_URL="${SHUSHUNYA_AGENT_SEARXNG_URL:-http://127.0.0.1:8888}"
export SHUSHUNYA_AGENT_SEARCH_PROVIDERS="${SHUSHUNYA_AGENT_SEARCH_PROVIDERS:-searxng,marginalia,wikipedia,brave}"
./scripts/self-test.sh
