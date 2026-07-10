#!/bin/bash
# Gemma-4-12B on GPU, port 8080. The dispatcher (8079) fronts this.
cd /media/shushunya/SHUSHUNYA/shushunya || exit 1
LOG=CoreOfMadness/llm-host/runtime/gemma-server.log
mkdir -p CoreOfMadness/llm-host/runtime
if curl -fsS --max-time 3 http://127.0.0.1:8080/health >/dev/null 2>&1; then
  echo "gemma already up"; exit 0
fi
: > "$LOG"
setsid nohup CoreOfMadness/llm-host/llama.cpp/llama-server \
  --model CoreOfMadness/gemma-4-12b-it-UD-Q5_K_XL.gguf \
  --mmproj CoreOfMadness/vision/mmproj-google-gemma-4-12B-it-BF16.gguf \
  --embeddings --pooling mean \
  --host 127.0.0.1 --port 8080 \
  --ctx-size 65536 --n-gpu-layers 999 --parallel 2 \
  --jinja --reasoning off \
  --flash-attn auto --cache-type-k q4_0 --cache-type-v q4_0 \
  --no-cache-prompt --cache-ram 0 --cache-reuse 0 \
  --slot-prompt-similarity 0.0 --no-cache-idle-slots \
  --slot-save-path CoreOfMadness/llm-host/runtime/slots \
  >> "$LOG" 2>&1 &
echo "gemma pid $!"
