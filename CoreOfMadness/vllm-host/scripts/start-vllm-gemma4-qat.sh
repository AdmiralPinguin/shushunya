#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$ROOT/../.." && pwd)"
MODEL="${MODEL:-$PROJECT_ROOT/CoreOfMadness/models/google-gemma-4-12B-it-qat-w4a16-ct}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-google/gemma-4-12B-it-qat-w4a16-ct}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8081}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-16384}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"
DTYPE="${DTYPE:-auto}"
EXTRA_ARGS="${EXTRA_ARGS:-}"
PID_FILE="$ROOT/runtime/vllm-gemma4-qat.pid"
LOG_FILE="$ROOT/runtime/vllm-gemma4-qat.log"

if [ ! -d "$MODEL" ]; then
  echo "Model directory not found: $MODEL" >&2
  exit 1
fi

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    echo "vLLM Gemma4 QAT server is already running with PID $PID"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

mkdir -p "$ROOT/runtime"

setsid "$ROOT/venv/bin/vllm" serve "$MODEL" \
  --served-model-name "$SERVED_MODEL_NAME" \
  --host "$HOST" \
  --port "$PORT" \
  --max-model-len "$MAX_MODEL_LEN" \
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
  --dtype "$DTYPE" \
  --trust-remote-code \
  $EXTRA_ARGS \
  >"$LOG_FILE" 2>&1 </dev/null &

echo "$!" > "$PID_FILE"
echo "vLLM Gemma4 QAT server started: PID $(cat "$PID_FILE"), http://$HOST:$PORT"
echo "Log: $LOG_FILE"
