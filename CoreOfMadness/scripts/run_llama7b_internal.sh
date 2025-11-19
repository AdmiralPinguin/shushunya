#!/usr/bin/env bash
set -euo pipefail
CORE="/media/acab/LMS/Shushunya/CoreOfMadness"
LCPP="$CORE/llama.cpp"
MODEL="$LCPP/models/7b/llama-2-7b-chat.Q4_K_M.gguf"
BIN="$( [ -x "$LCPP/build/bin/server" ] && echo "$LCPP/build/bin/server" || echo "$LCPP/build/bin/llama-server" )"
fuser -k 18021/tcp 2>/dev/null || true
exec "$BIN" -m "$MODEL" --host 127.0.0.1 --port 18021 -c 2048 -t "$(nproc)" -ngl 0
