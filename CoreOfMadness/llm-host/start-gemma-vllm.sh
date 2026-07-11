#!/bin/bash
# Gemma-4-12B QAT-W4A16 on vLLM, ENTIRELY on the RTX 3090, port 8080.
# Replaces the llama.cpp backend (kept as start-gemma.sh for fallback).
# The old gguf model name is served as an alias so the dispatcher (8079) and every
# client keep working with zero changes.
cd /media/shushunya/SHUSHUNYA/shushunya || exit 1

RTX3090_UUID="GPU-a5dffcde-00f4-8625-fd65-193cfd964696"   # pinned by UUID, not index
LOG=CoreOfMadness/vllm-host/runtime/gemma-vllm-8080.log
PID_FILE=CoreOfMadness/vllm-host/runtime/gemma-vllm-8080.pid
MODEL=CoreOfMadness/models/google-gemma-4-12B-it-qat-w4a16-ct
mkdir -p CoreOfMadness/vllm-host/runtime

# llama.cpp gemma must not hold the port or the VRAM — stop it FIRST, otherwise the
# health check below sees llama's 8080 and thinks vLLM is already up.
if pgrep -f "llama-server.*gemma-4-12b" >/dev/null 2>&1; then
  echo "stopping llama.cpp gemma…"
  pkill -f "llama-server.*gemma-4-12b"; sleep 3
fi

if curl -fsS --max-time 3 http://127.0.0.1:8080/v1/models >/dev/null 2>&1; then
  echo "gemma vLLM (8080) already up"; exit 0
fi

: > "$LOG"
# inductor needs nvcc; the toolkit lives inside the venv (pip nvidia-cu13), and the
# drive likes to drop exec bits — restore them or triton's ptxas/nvcc fail with EACCES.
CUDA_HOME_DIR="$PWD/CoreOfMadness/vllm-host/venv/lib/python3.12/site-packages/nvidia/cu13"
find CoreOfMadness/vllm-host/venv -path "*/bin/*" -type f ! -perm -u+x -exec chmod +x {} + 2>/dev/null

# flashinfer refuses to JIT on the torch-cu130/nvcc-13.2 mix — disable it; the
# prebuilt FlashAttention kernels are what we want on the 3090 anyway.
CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES="$RTX3090_UUID" \
CUDA_HOME="$CUDA_HOME_DIR" \
PATH="$PWD/CoreOfMadness/vllm-host/venv/bin:$CUDA_HOME_DIR/bin:$PATH" \
VLLM_USE_FLASHINFER_SAMPLER=0 VLLM_ATTENTION_BACKEND=FLASH_ATTN \
setsid nohup CoreOfMadness/vllm-host/venv/bin/vllm serve "$MODEL" \
  --served-model-name gemma-4-12b-it-UD-Q5_K_XL.gguf google/gemma-4-12B-it-qat-w4a16-ct \
  --host 127.0.0.1 --port 8080 \
  --max-model-len "${MAX_MODEL_LEN:-32768}" \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.66}" \
  --dtype auto --trust-remote-code \
  >> "$LOG" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"
echo "gemma vLLM (3090) pid $(cat "$PID_FILE"), log $LOG"
