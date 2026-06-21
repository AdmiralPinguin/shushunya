#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="$(cd "$ROOT/.." && pwd)"
MODEL="${MODEL:-$MODELS_DIR/gemma-4-12b-it-UD-Q5_K_XL.gguf}"
MMPROJ="${MMPROJ:-$MODELS_DIR/vision/mmproj-google-gemma-4-12B-it-BF16.gguf}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"
CTX_SIZE="${CTX_SIZE:-8192}"
GPU_LAYERS="${GPU_LAYERS:-999}"
PARALLEL="${PARALLEL:-1}"
REASONING="${REASONING:-off}"
CACHE_TYPE_K="${CACHE_TYPE_K:-q4_0}"
CACHE_TYPE_V="${CACHE_TYPE_V:-q4_0}"
EMBEDDINGS="${EMBEDDINGS:-1}"
POOLING="${POOLING:-mean}"
PID_FILE="$ROOT/runtime/llama-server.pid"
LOG_FILE="$ROOT/runtime/llama-server.log"

if [ ! -f "$MODEL" ]; then
  echo "Model not found: $MODEL" >&2
  exit 1
fi

MMPROJ_ARGS=()
if [ -f "$MMPROJ" ]; then
  MMPROJ_ARGS=(--mmproj "$MMPROJ")
fi

EMBEDDING_ARGS=()
case "${EMBEDDINGS,,}" in
  1|true|yes|on)
    EMBEDDING_ARGS=(--embeddings --pooling "$POOLING")
    ;;
esac

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    echo "llama-server is already running with PID $PID"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

mkdir -p "$ROOT/runtime"

export LD_LIBRARY_PATH="$ROOT/llama.cpp:${LD_LIBRARY_PATH:-}"

setsid "$ROOT/llama.cpp/llama-server" \
  --model "$MODEL" \
  "${MMPROJ_ARGS[@]}" \
  "${EMBEDDING_ARGS[@]}" \
  --host "$HOST" \
  --port "$PORT" \
  --ctx-size "$CTX_SIZE" \
  --n-gpu-layers "$GPU_LAYERS" \
  --parallel "$PARALLEL" \
  --reasoning "$REASONING" \
  --flash-attn auto \
  --cache-type-k "$CACHE_TYPE_K" \
  --cache-type-v "$CACHE_TYPE_V" \
  >"$LOG_FILE" 2>&1 </dev/null &

echo "$!" > "$PID_FILE"
echo "llama-server started: PID $(cat "$PID_FILE"), http://127.0.0.1:$PORT"
echo "Log: $LOG_FILE"
