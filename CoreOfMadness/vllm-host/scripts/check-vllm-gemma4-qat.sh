#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8081}"
MODEL="${SERVED_MODEL_NAME:-google/gemma-4-12B-it-qat-w4a16-ct}"

curl -fsS "http://127.0.0.1:$PORT/v1/models" >/dev/null
curl -fsS "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H "Content-Type: application/json" \
  --data "$(python3 - <<PY
import json
print(json.dumps({
    "model": "$MODEL",
    "messages": [
        {"role": "user", "content": "Верни строго JSON: {\"ok\":true,\"engine\":\"vllm\",\"model\":\"gemma4_qat\"}"}
    ],
    "temperature": 0,
    "max_tokens": 64,
    "chat_template_kwargs": {"enable_thinking": False},
}))
PY
)"
