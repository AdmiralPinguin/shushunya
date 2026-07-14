#!/bin/bash
# Qwen3-Coder-Next 80B-A3B on CPU, port 8081. Code brigade (Skitarii) points here.
# 32 threads is the measured sweet spot on the Threadripper 3970X.
cd /media/shushunya/SHUSHUNYA/shushunya || exit 1
LOG=CoreOfMadness/llm-host/runtime/qwen-server.log
mkdir -p CoreOfMadness/llm-host/runtime
if curl -fsS --max-time 3 http://127.0.0.1:8081/v1/models >/dev/null 2>&1; then echo "qwen already up"; exit 0; fi
: > "$LOG"
# GPU-оффлоад: внимание+KV на 2060 (сборка Vulkan, поэтому --device, а не CUDA_VISIBLE_DEVICES),
# эксперты MoE в RAM, контекст общим котлом на 4 слота (~6.7ГБ VRAM).
# Замер 2026-07-12: промпт 20->177 т/с, генерация 12->23 т/с; запрос на 50к токенов проходит.
setsid nohup CoreOfMadness/llm-host/llama.cpp/llama-server \
  --model CoreOfMadness/models/Qwen3-Coder-Next-Q6_K/Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf \
  --host 127.0.0.1 --port 8081 --ctx-size 131072 --threads 32 --threads-batch 32 \
  -ngl 99 --cpu-moe --device Vulkan0 --jinja --flash-attn auto --no-webui >> "$LOG" 2>&1 &
echo "qwen pid $!"
