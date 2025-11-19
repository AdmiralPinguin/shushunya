#!/usr/bin/env bash
set -euo pipefail
cd /media/acab/LMS/Shushunya/CoreOfMadness
source coremad/bin/activate

export LLAMA_URL="http://127.0.0.1:18020"
exec uvicorn proxy.openai_llamacpp_proxy:app --host 0.0.0.0 --port 8020
