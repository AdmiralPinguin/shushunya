#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-9379}"
BASE_URL="${BASE_URL:-http://127.0.0.1:$PORT}"
MODEL="${MODEL:-gemma4-12b,gpu,2048}"

echo "Models:"
curl -fsS "$BASE_URL/v1/models"
echo

echo "Chat test:"
curl -fsS "$BASE_URL/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d "{
    \"model\": \"$MODEL\",
    \"messages\": [
      {\"role\": \"user\", \"content\": \"Ответь по-русски одной короткой фразой: LiteRT сервер работает?\"}
    ],
    \"max_tokens\": 64,
    \"temperature\": 0
  }"
echo
