#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8080}"
BASE_URL="${BASE_URL:-http://127.0.0.1:$PORT}"

echo "Health:"
curl -fsS "$BASE_URL/health"
echo

echo "Models:"
curl -fsS "$BASE_URL/v1/models"
echo

echo "Chat test:"
curl -fsS "$BASE_URL/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gemma-4-12b-it-UD-Q5_K_XL",
    "messages": [
      {"role": "user", "content": "Ответь по-русски одной короткой фразой: сервер работает?"}
    ],
    "max_tokens": 128,
    "temperature": 0
  }'
echo
