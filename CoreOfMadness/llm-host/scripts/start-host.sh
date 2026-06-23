#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="$(cd "$ROOT/.." && pwd)"
MODEL="${MODEL:-$MODELS_DIR/gemma-4-12b-it-UD-Q5_K_XL.gguf}"
MMPROJ="${MMPROJ:-$MODELS_DIR/vision/mmproj-google-gemma-4-12B-it-BF16.gguf}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"
CTX_SIZE="${CTX_SIZE:-32768}"
GPU_LAYERS="${GPU_LAYERS:-999}"
PARALLEL="${PARALLEL:-1}"
REASONING="${REASONING:-off}"
CACHE_TYPE_K="${CACHE_TYPE_K:-q4_0}"
CACHE_TYPE_V="${CACHE_TYPE_V:-q4_0}"
EMBEDDINGS="${EMBEDDINGS:-1}"
POOLING="${POOLING:-mean}"
PROMPT_CACHE="${PROMPT_CACHE:-0}"
CACHE_REUSE="${CACHE_REUSE:-0}"
SLOT_PROMPT_SIMILARITY="${SLOT_PROMPT_SIMILARITY:-0.0}"
CACHE_IDLE_SLOTS="${CACHE_IDLE_SLOTS:-0}"
CACHE_RAM="${CACHE_RAM:-0}"
SLOT_SAVE_PATH="${SLOT_SAVE_PATH:-$ROOT/runtime/slots}"
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

CACHE_ARGS=(--cache-ram "$CACHE_RAM" --cache-reuse "$CACHE_REUSE" --slot-prompt-similarity "$SLOT_PROMPT_SIMILARITY")
case "${PROMPT_CACHE,,}" in
  1|true|yes|on)
    CACHE_ARGS=(--cache-prompt "${CACHE_ARGS[@]}")
    ;;
  *)
    CACHE_ARGS=(--no-cache-prompt "${CACHE_ARGS[@]}")
    ;;
esac
case "${CACHE_IDLE_SLOTS,,}" in
  1|true|yes|on)
    CACHE_ARGS+=(--cache-idle-slots)
    ;;
  *)
    CACHE_ARGS+=(--no-cache-idle-slots)
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
mkdir -p "$SLOT_SAVE_PATH"

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
  "${CACHE_ARGS[@]}" \
  --slot-save-path "$SLOT_SAVE_PATH" \
  >"$LOG_FILE" 2>&1 </dev/null &

echo "$!" > "$PID_FILE"
echo "llama-server started: PID $(cat "$PID_FILE"), http://127.0.0.1:$PORT"
echo "Log: $LOG_FILE"
