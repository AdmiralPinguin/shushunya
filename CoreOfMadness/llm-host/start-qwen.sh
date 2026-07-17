#!/bin/bash
# Qwen3.6-35B-A3B Q8_K_XL, port 8081. Code brigade (Skitarii) points here.
# Пересадка с 80B-Coder на 35B (2026-07-16): та же скорость на жирном контексте
# (33k: префилл 390 / ген 28.9 т/с), сильнее по кодингу (SWE 73.4 vs 70.6), вдвое легче по RAM.
# Спекуляция (MTP/DFlash) НЕ окупается на cpu-moe — прод БЕЗ неё (см. память llm-host-gpu-offload).
# 32 threads = sweet spot на Threadripper 3970X. Старый 80B-конфиг: start-qwen.sh.80b-coder.bak
cd /media/shushunya/SHUSHUNYA/shushunya || exit 1
BIN=CoreOfMadness/llm-host/llama.cpp-b10042/llama-server
export LD_LIBRARY_PATH="$PWD/CoreOfMadness/llm-host/llama.cpp-b10042:${LD_LIBRARY_PATH:-}"
LOG=CoreOfMadness/llm-host/runtime/qwen-server.log
mkdir -p CoreOfMadness/llm-host/runtime
if curl -fsS --max-time 3 http://127.0.0.1:8081/v1/models >/dev/null 2>&1; then echo "qwen already up"; exit 0; fi
: > "$LOG"
# GPU-оффлоад: внимание+KV на Vulkan0 (сборка Vulkan -> --device, не CUDA_VISIBLE_DEVICES),
# эксперты MoE в RAM (--no-mmap: 37ГБ Q8 целиком в оперативку, не зависим от диска), ~7-8ГБ VRAM.
# ДУМАЮЩАЯ модель: reasoning on, thoughts -> message.reasoning_content, чистый ответ -> message.content.
# ВАЖНО бригаде: давать щедрый max_tokens (thinking съедает сотни токенов) и читать content, не reasoning_content.
setsid nohup "$BIN" \
  --model CoreOfMadness/models/Qwen3.6-35B-A3B-MTP-Q8/Qwen3.6-35B-A3B-UD-Q8_K_XL.gguf \
  --host 127.0.0.1 --port 8081 --ctx-size 65536 --threads 32 --threads-batch 32 \
  -ngl 99 --cpu-moe --device Vulkan0 --no-mmap --jinja --flash-attn auto --no-webui \
  --reasoning on --reasoning-format deepseek --reasoning-budget "${QWEN_REASONING_BUDGET:-6144}" >> "$LOG" 2>&1 &
echo "qwen pid $!"
