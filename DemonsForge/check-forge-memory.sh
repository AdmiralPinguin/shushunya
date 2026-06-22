#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

BASE_URL="${FORGE_BASE_URL:-http://127.0.0.1:8110}"
QUERY="${1:-DemonsForge CPU GPU memory}"

PYTHON="$PWD/DemonsForge/bin/python"
QUERY_ENCODED="$("$PYTHON" -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$QUERY")"

request() {
  local path="$1"
  curl -fsS "$BASE_URL$path" | "$PYTHON" -m json.tool
}

echo "Forge memory status:"
request "/forge/memory/status"

echo
echo "Forge memory policy:"
request "/forge/memory/policy"

echo
echo "Archive gateway through Forge:"
request "/forge/memory/gateway"

echo
echo "DemonsForge memory catalog:"
request "/forge/memory/catalog?create=true"

echo
echo "DemonsForge memory search:"
request "/forge/memory/search?q=$QUERY_ENCODED&layers=focus,wiki,vector,graph&limit=5"

echo
echo "Recent DemonsForge memory events:"
request "/forge/memory/events?limit=10"

echo
echo "Local Forge proposal audit:"
request "/forge/memory/proposals?limit=10"
