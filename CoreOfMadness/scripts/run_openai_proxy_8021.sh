#!/usr/bin/env bash
set -euo pipefail
CORE="/media/acab/LMS/Shushunya/CoreOfMadness"
export LLAMA_URL="http://127.0.0.1:18021"
fuser -k 8021/tcp 2>/dev/null || true
exec uvicorn proxy.openai_llamacpp_proxy:app --host 0.0.0.0 --port 8021
