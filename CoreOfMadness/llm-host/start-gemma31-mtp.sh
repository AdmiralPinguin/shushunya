#!/bin/bash
# Gemma-4-31B QAT-W4A16 + MTP (QAT-драфтер) на vLLM 0.22.0 (venv022), порт 8080 — умная голова.
# ПОБЕДНАЯ MTP+int8 конфига (2026-07-17): код ~165 т/с, acceptance 74.8%, средняя длина 3.99,
# KV-кэш 112k токенов (×2.66 к fp16) при max-model-len 65536, concurrency 1.71x. int8 скорость/acceptance НЕ трогает.
#
# ГРАБЛИ (важно, все выстраданы):
#  - venv022 патч парсит CUDA_VISIBLE_DEVICES как int -> ТОЛЬКО индексы (0,1), UUID роняет на импорте gemma4_mm.
#  - int8-KV + MTP ТРЕБУЮТ ПАТЧ PR #40391 на venv022 (gemma-4 мешает head_dim 256/512 -> страницы 520/1032 не
#    унифицируются: "page size not divisible"). Патч паддит global до 1040. Сохранён: CoreOfMadness/vllm-host/patches/
#    pr40391.diff + бэкап оригиналов. БЕЗ патча int8+MTP падает на _initialize_kv_caches -> тогда убрать --kv-cache-dtype.
#  - venv022 требует TRITON_ATTN + FLASHINFER_SAMPLER=0 (cu13 nvcc 13.2, flashinfer JIT ломается).
#  - P2P на плате нет -> NCCL_P2P_DISABLE=1 + --disable-custom-all-reduce.
#  - util 0.68 (не 0.71/0.92): оставить ~7.7ГБ на GPU0 под Qwen-35B-оффлоад (внимание+KV llama.cpp).
#  - фоновый запуск: НЕ ставить pkill без ||true (set-e рубит) и добивать VLLM::Worker_TP* по pid (зомби держат VRAM).
cd /media/shushunya/SHUSHUNYA/shushunya || exit 1
V=CoreOfMadness/vllm-host/vllm022
C="$PWD/$V/lib/python3.12/site-packages/nvidia/cu13"
MODEL=CoreOfMadness/models/google-gemma-4-31B-it-qat-w4a16-ct
DRAFTER="$PWD/CoreOfMadness/models/gemma-4-31B-it-qat-assistant"
LOG=CoreOfMadness/vllm-host/runtime/gemma31-mtp-8080.log
PID_FILE=CoreOfMadness/vllm-host/runtime/gemma31-mtp-8080.pid
mkdir -p CoreOfMadness/vllm-host/runtime
if curl -fsS --max-time 3 http://127.0.0.1:8080/v1/models >/dev/null 2>&1; then echo "gemma (8080) already up"; exit 0; fi
if pgrep -f 'vllm serve.*gemma-4-31B' >/dev/null 2>&1; then pkill -f 'vllm serve.*gemma-4-31B'; sleep 5; fi
find "$V" -path "*/bin/*" -type f ! -perm -u+x -exec chmod +x {} + 2>/dev/null
: > "$LOG"
CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES="${GEMMA_GPUS:-0,1}" \
NCCL_P2P_DISABLE=1 NCCL_CUMEM_ENABLE=0 \
CUDA_HOME="$C" PATH="$PWD/$V/bin:$C/bin:$PATH" \
VLLM_USE_FLASHINFER_SAMPLER=0 VLLM_ATTENTION_BACKEND=TRITON_ATTN \
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
setsid nohup "$V/bin/vllm" serve "$MODEL" \
  --served-model-name gemma-4-12b-it-UD-Q5_K_XL.gguf google/gemma-4-31B-it-qat-w4a16-ct \
  --host 127.0.0.1 --port 8080 --tensor-parallel-size 2 \
  --max-model-len "${MAX_MODEL_LEN:-65536}" \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.68}" \
  --kv-cache-dtype int8_per_token_head \
  --disable-custom-all-reduce --max-num-seqs 2 \
  --speculative-config "{\"model\":\"$DRAFTER\",\"num_speculative_tokens\":${NUM_SPEC:-4}}" \
  --dtype auto --trust-remote-code >> "$LOG" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"
echo "gemma31-mtp vLLM pid $(cat "$PID_FILE"), log $LOG"
