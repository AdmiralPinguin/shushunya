#!/usr/bin/env bash
set -euo pipefail

ROOT=/media/shushunya/SHUSHUNYA/shushunya
cd "$ROOT" || exit 1
GPU_UUID="${RTX3090_UUID:-GPU-a5dffcde-00f4-8625-fd65-193cfd964696}"
PORT="${VLLM_PORT:-8080}"
ALIAS=gemma-4-12b-it-UD-Q5_K_XL.gguf
MODEL_REL=CoreOfMadness/models/google-gemma-4-12B-it-qat-w4a16-ct
BIN_REL=CoreOfMadness/vllm-host/venv/bin/vllm
MODEL="$ROOT/$MODEL_REL"
BIN="$ROOT/$BIN_REL"
RUNTIME="${VLLM_RUNTIME_DIR:-CoreOfMadness/vllm-host/runtime}"
PID_FILE="$RUNTIME/gemma-vllm-${PORT}.pid"
LOG="$RUNTIME/gemma-vllm-${PORT}.log"
BASE="http://127.0.0.1:${PORT}"
MAX_SEQS="${VLLM_MAX_NUM_SEQS:-4}"
READY_TIMEOUT="${VLLM_READY_TIMEOUT_SEC:-300}"
CUDA_HOME_DIR="$ROOT/CoreOfMadness/vllm-host/venv/lib/python3.12/site-packages/nvidia/cu13"
[[ "$MAX_SEQS" == 4 ]] || { echo "VLLM_MAX_NUM_SEQS must be 4" >&2; exit 1; }
[[ "$PORT" =~ ^[0-9]+$ && "$READY_TIMEOUT" =~ ^[1-9][0-9]*$ ]] \
  && (( 10#$PORT >= 1 && 10#$PORT <= 65535 )) \
  || { echo "invalid vLLM port/readiness timeout" >&2; exit 1; }
mkdir -p "$RUNTIME"

ready() {
  curl -fsS --max-time 3 "$BASE/health" >/dev/null 2>&1 \
    && curl -fsS --max-time 3 "$BASE/v1/models" 2>/dev/null | python3 -c '
import json,sys
a=sys.argv[1]; rows=json.load(sys.stdin).get("data",[])
raise SystemExit(0 if any(isinstance(x,dict) and x.get("id")==a and x.get("owned_by")=="vllm" for x in rows) else 1)' "$ALIAS" >/dev/null 2>&1
}
listener_pid() {
  ss -H -ltnp "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true
}
is_expected() {
  [[ "$1" =~ ^[0-9]+$ && -r "/proc/$1/cmdline" && -r "/proc/$1/environ" ]] || return 1
  python3 - "$1" "$BIN_REL" "$BIN" "$MODEL_REL" "$MODEL" "$PORT" "$ALIAS" "$GPU_UUID" <<'PY'
import sys
p,br,ba,mr,ma,port,alias,gpu=sys.argv[1:]
a=[x.decode(errors="replace") for x in open(f"/proc/{p}/cmdline","rb").read().split(b"\0") if x]
e=dict(x.decode(errors="replace").split("=",1) for x in open(f"/proc/{p}/environ","rb").read().split(b"\0") if b"=" in x)
pair=lambda k,v:any(a[i:i+2]==[k,v] for i in range(len(a)-1))
ok=((br in a or ba in a) and "serve" in a and (mr in a or ma in a)
    and pair("--port",port) and pair("--host","127.0.0.1")
    and pair("--served-model-name",alias) and pair("--max-num-seqs","4")
    and e.get("CUDA_VISIBLE_DEVICES")==gpu)
raise SystemExit(0 if ok else 1)
PY
}
wait_ready() {
  local pid="$1" i owner
  for ((i=0; i<READY_TIMEOUT; i++)); do
    kill -0 "$pid" 2>/dev/null || return 1
    if ready; then
      owner="$(listener_pid)"
      [[ "$owner" == "$pid" ]] && is_expected "$pid" && return 0
      return 1
    fi
    sleep 1
  done
  return 1
}

exec 9> "$RUNTIME/gemma-vllm-${PORT}.lock"
flock -w 15 9 || { echo "vLLM startup lock timeout" >&2; exit 1; }
PID=""
if [[ -f "$PID_FILE" ]]; then
  read -r PID < "$PID_FILE" || true
  if kill -0 "${PID:-}" 2>/dev/null; then
    is_expected "$PID" || { echo "vLLM PID file points to a wrong live process" >&2; exit 1; }
  else
    PID=""; rm -f "$PID_FILE"
  fi
fi

if ss -H -ltn "sport = :$PORT" 2>/dev/null | grep -q .; then
  OWNER="$(listener_pid)"
  [[ "$OWNER" =~ ^[0-9]+$ ]] && is_expected "$OWNER" \
    || { echo "foreign listener on vLLM port $PORT" >&2; exit 1; }
  [[ -z "$PID" || "$PID" == "$OWNER" ]] \
    || { echo "vLLM PID file and port owner disagree" >&2; exit 1; }
  PID="$OWNER"; echo "$PID" > "$PID_FILE"
  ready || wait_ready "$PID" || { echo "existing vLLM is not ready" >&2; exit 1; }
  echo "Gemma vLLM ($PORT) already ready with PID $PID"
  exit 0
fi

if [[ -n "$PID" ]]; then
  wait_ready "$PID" || { echo "existing vLLM PID $PID failed readiness" >&2; exit 1; }
  echo "Gemma vLLM ($PORT) became ready with PID $PID"
  exit 0
fi
OTHER="$(pgrep -f 'llama-server.*gemma-4-12b|vllm.*serve.*gemma' 2>/dev/null || true)"
[[ -z "$OTHER" ]] || { echo "another Gemma server is live ($OTHER); refusing duplicate" >&2; exit 1; }
ss -H -ltn "sport = :$PORT" 2>/dev/null | grep -q . \
  && { echo "port $PORT became occupied" >&2; exit 1; }

find CoreOfMadness/vllm-host/venv -path '*/bin/*' -type f ! -perm -u+x -exec chmod +x {} + 2>/dev/null
[[ -x "$BIN" && -d "$MODEL" && -d "$CUDA_HOME_DIR" ]] \
  || { echo "vLLM/model/CUDA runtime is incomplete" >&2; exit 1; }
CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES="$GPU_UUID" CUDA_HOME="$CUDA_HOME_DIR" \
PATH="$ROOT/CoreOfMadness/vllm-host/venv/bin:$CUDA_HOME_DIR/bin:$PATH" \
VLLM_USE_FLASHINFER_SAMPLER=0 VLLM_ATTENTION_BACKEND=FLASH_ATTN \
setsid nohup "$BIN_REL" serve "$MODEL_REL" \
  --served-model-name "$ALIAS" google/gemma-4-12B-it-qat-w4a16-ct \
  --host 127.0.0.1 --port "$PORT" --max-model-len "${MAX_MODEL_LEN:-32768}" \
  --max-num-seqs "$MAX_SEQS" --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.66}" \
  --dtype auto --trust-remote-code >> "$LOG" 2>&1 </dev/null 9>&- &
PID=$!
echo "$PID" > "$PID_FILE"
if ! wait_ready "$PID"; then
  is_expected "$PID" && kill -TERM "$PID" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "new vLLM failed readiness for alias $ALIAS" >&2
  exit 1
fi
echo "Gemma vLLM (3090) ready: PID $PID, alias $ALIAS"
