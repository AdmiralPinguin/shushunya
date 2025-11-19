#!/usr/bin/env bash
set -euo pipefail
ROOT="/media/acab/LMS/Shushunya/CoreOfMadness"
LLC="$ROOT/llama.cpp"
BIN="$LLC/build/bin/server"
MODEL="$LLC/models/20b/gpt-neox-20b-q4_k_m.gguf"

HOST="127.0.0.1"
PORT="18020"
CTX="512"
THREADS="$(nproc)"
NGL="0"   # <<< ключевой момент, можешь поиграться: 0–10

export CUDA_VISIBLE_DEVICES=0
exec "$BIN" \
  -m "$MODEL" \
  --host "$HOST" --port "$PORT" \
  -c "$CTX" -t "$THREADS" -ngl "$NGL" \
  --parallel 1
