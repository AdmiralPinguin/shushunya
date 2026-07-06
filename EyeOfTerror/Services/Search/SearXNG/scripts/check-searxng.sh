#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${SHUSHUNYA_SEARCH_SEARXNG_URL:-http://127.0.0.1:8888}"

curl -fsS "$BASE_URL/search?q=OpenAI&format=json" \
  | python3 -c 'import json,sys; data=json.load(sys.stdin); print({"query": data.get("query"), "results": len(data.get("results", []))})'
