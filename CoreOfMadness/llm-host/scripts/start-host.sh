#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="$(cd "$ROOT/.." && pwd)"
MODEL="${MODEL:-$MODELS_DIR/gemma-4-12b-it-UD-Q5_K_XL.gguf}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
CTX_SIZE="${CTX_SIZE:-2048}"
GPU_LAYERS="${GPU_LAYERS:-999}"
PARALLEL="${PARALLEL:-1}"
REASONING="${REASONING:-off}"
PID_FILE="$ROOT/runtime/llama-server.pid"
LOG_FILE="$ROOT/runtime/llama-server.log"

if [ ! -f "$MODEL" ]; then
  echo "Model not found: $MODEL" >&2
  exit 1
fi

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "llama-server is already running with PID $(cat "$PID_FILE")"
  exit 0
fi

mkdir -p "$ROOT/runtime"

export LD_LIBRARY_PATH="$ROOT/llama.cpp:${LD_LIBRARY_PATH:-}"

setsid "$ROOT/llama.cpp/llama-server" \
  --model "$MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --ctx-size "$CTX_SIZE" \
  --n-gpu-layers "$GPU_LAYERS" \
  --parallel "$PARALLEL" \
  --reasoning "$REASONING" \
  --flash-attn auto \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  >"$LOG_FILE" 2>&1 </dev/null &

echo "$!" > "$PID_FILE"
echo "llama-server started: PID $(cat "$PID_FILE"), http://127.0.0.1:$PORT"
echo "Log: $LOG_FILE"
