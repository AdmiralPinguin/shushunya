#!/usr/bin/env bash
set -euo pipefail
source /media/acab/LMS/Shushunya/CoreOfMadness/coremad/bin/activate
export LLAMA_URL="http://127.0.0.1:18020"
fuser -k 8020/tcp >/dev/null 2>&1 || true
exec uvicorn proxy.openai_llamacpp_proxy:app --host 0.0.0.0 --port 8020
