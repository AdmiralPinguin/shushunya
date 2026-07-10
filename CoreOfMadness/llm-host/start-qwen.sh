#!/bin/bash
# Qwen3-Coder-Next 80B-A3B on CPU, port 8081. Code brigade (Skitarii) points here.
# 32 threads is the measured sweet spot on the Threadripper 3970X.
cd /media/shushunya/SHUSHUNYA/shushunya || exit 1
LOG=CoreOfMadness/llm-host/runtime/qwen-server.log
mkdir -p CoreOfMadness/llm-host/runtime
if curl -fsS --max-time 3 http://127.0.0.1:8081/v1/models >/dev/null 2>&1; then echo "qwen already up"; exit 0; fi
: > "$LOG"
setsid nohup CoreOfMadness/llm-host/llama.cpp/llama-server \
  --model CoreOfMadness/models/Qwen3-Coder-Next-Q6_K/Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf \
  --host 127.0.0.1 --port 8081 --ctx-size 32768 --threads 32 --threads-batch 32 \
  -ngl 0 --jinja --flash-attn auto --no-webui >> "$LOG" 2>&1 &
echo "qwen pid $!"
