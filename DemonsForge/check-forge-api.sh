#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${FORGE_BASE_URL:-http://127.0.0.1:8110}"
PYTHON_BIN="${PYTHON_BIN:-$(dirname "$0")/DemonsForge/bin/python}"

curl_json() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  if [ -n "$body" ]; then
    curl -fsS -X "$method" "$BASE_URL$path" \
      -H 'content-type: application/json' \
      -d "$body"
  else
    curl -fsS -X "$method" "$BASE_URL$path"
  fi
}

curl_json GET /health | "$PYTHON_BIN" -m json.tool >/dev/null
curl_json GET /forge/capabilities | "$PYTHON_BIN" -m json.tool >/dev/null
curl_json GET /forge/models | "$PYTHON_BIN" -m json.tool >/dev/null
curl_json GET /forge/loras | "$PYTHON_BIN" -m json.tool >/dev/null
curl_json GET /forge/embeddings | "$PYTHON_BIN" -m json.tool >/dev/null
curl_json GET /forge/schedulers | "$PYTHON_BIN" -m json.tool >/dev/null
curl_json GET /forge/queue | "$PYTHON_BIN" -m json.tool >/dev/null
curl_json GET /forge/schema/job | "$PYTHON_BIN" -m json.tool >/dev/null
curl_json GET /forge/memory/status | "$PYTHON_BIN" -m json.tool >/dev/null
curl_json POST /forge/plan '{"request":"SDXL 512x512 steps 1 smoke portrait"}' | "$PYTHON_BIN" -m json.tool >/dev/null

echo "forge api smoke ok: $BASE_URL"
