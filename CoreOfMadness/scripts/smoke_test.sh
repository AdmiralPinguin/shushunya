#!/usr/bin/env bash
set -euo pipefail
BASE="http://127.0.0.1:8014"
echo "# list models"
curl -s "$BASE/v1/models" | jq .
echo "# chat"
curl -s -X POST "$BASE/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"TheBloke/gpt-neox-20B-GPTQ",
    "messages":[
      {"role":"system","content":"Ты — Шушуня. Кратко."},
      {"role":"user","content":"Назови себя."}
    ],
    "max_tokens":64,
    "temperature":0.7
  }' | jq .
