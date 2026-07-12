#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$ROOT/.." && pwd)"
DPORT="${LLM_DISPATCH_PORT:-8079}"
DSCRIPT="$PROJECT_ROOT/CoreOfMadness/llm-dispatcher/dispatcher.py"
DRUNTIME="$PROJECT_ROOT/CoreOfMadness/llm-dispatcher/runtime"
DPID_FILE="$DRUNTIME/dispatcher-${DPORT}.pid"
DHEALTH="http://127.0.0.1:${DPORT}/dispatcher/health"
[[ "$DPORT" =~ ^[0-9]+$ ]] && (( 10#$DPORT >= 1 && 10#$DPORT <= 65535 )) \
  || { echo "invalid dispatcher port" >&2; exit 1; }
mkdir -p "$DRUNTIME"
systemctl --user start shushunya-embedder.service 2>/dev/null || true

dispatcher_ok() {
  curl -fsS --max-time 3 "$DHEALTH" 2>/dev/null | python3 -c '
import json,sys
d=json.load(sys.stdin); r=d.get("routes", {})
ok=(type(d.get("version")) is int and d["version"]==2
    and type(r.get("gemma",{}).get("capacity")) is int and r["gemma"]["capacity"]==4
    and type(r.get("qwen",{}).get("capacity")) is int and r["qwen"]["capacity"]==1
    and r["gemma"].get("upstream_timeout_sec")==600.0
    and r["gemma"].get("queue_timeout_sec")==300.0
    and r["qwen"].get("upstream_timeout_sec")==90000.0
    and r["qwen"].get("queue_timeout_sec")==0.0)
raise SystemExit(0 if ok else 1)' >/dev/null 2>&1
}
listener_pid() {
  ss -H -ltnp "sport = :$DPORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true
}
is_dispatcher() {
  [[ "$1" =~ ^[0-9]+$ && -r "/proc/$1/cmdline" ]] \
    && grep -Fzqx -- "$DSCRIPT" "/proc/$1/cmdline"
}
stop_dispatcher() {
  is_dispatcher "$1" || return 1
  kill -TERM "$1"
  for _ in {1..50}; do kill -0 "$1" 2>/dev/null || return 0; sleep 0.2; done
  return 1
}

export LLM_DISPATCH_GEMMA_CONCURRENCY=4
export LLM_DISPATCH_QWEN_CONCURRENCY=1
export LLM_DISPATCH_GEMMA_TIMEOUT_SEC=600
export LLM_DISPATCH_GEMMA_QUEUE_TIMEOUT_SEC=300
export LLM_DISPATCH_QWEN_TIMEOUT_SEC=90000
export LLM_DISPATCH_QWEN_QUEUE_TIMEOUT_SEC=0
exec 9> "$DRUNTIME/dispatcher-${DPORT}.lock"
flock -w 15 9 || { echo "dispatcher startup lock timeout" >&2; exit 1; }

DPID="$(listener_pid)"
if dispatcher_ok; then
  is_dispatcher "$DPID" || { echo "dispatcher health is served by an unknown listener" >&2; exit 1; }
else
  echo "dispatcher contract mismatch; restarting only the verified dispatcher" >&2
  if ss -H -ltn "sport = :$DPORT" 2>/dev/null | grep -q .; then
    is_dispatcher "$DPID" || { echo "foreign listener on dispatcher port $DPORT" >&2; exit 1; }
    stop_dispatcher "$DPID" || { echo "dispatcher PID $DPID did not stop" >&2; exit 1; }
  elif [[ -f "$DPID_FILE" ]]; then
    read -r DPID < "$DPID_FILE" || true
    if kill -0 "${DPID:-}" 2>/dev/null; then
      is_dispatcher "$DPID" && stop_dispatcher "$DPID" \
        || { echo "dispatcher PID file points to a wrong live process" >&2; exit 1; }
    fi
  elif DPID="$(pgrep -f -- "$DSCRIPT" 2>/dev/null || true)" && [[ -n "$DPID" ]]; then
    is_dispatcher "$DPID" && stop_dispatcher "$DPID" \
      || { echo "unverified dispatcher process already exists" >&2; exit 1; }
  fi
  rm -f "$DPID_FILE"
  ss -H -ltn "sport = :$DPORT" 2>/dev/null | grep -q . \
    && { echo "dispatcher port $DPORT is still occupied" >&2; exit 1; }
  setsid nohup python3 "$DSCRIPT" >> "$PROJECT_ROOT/CoreOfMadness/llm-dispatcher/dispatcher.log" \
    2>&1 </dev/null 9>&- &
  DPID=$!
  echo "$DPID" > "$DPID_FILE"
  READY=0
  for _ in {1..50}; do
    if dispatcher_ok && [[ "$(listener_pid)" == "$DPID" ]] && is_dispatcher "$DPID"; then READY=1; break; fi
    kill -0 "$DPID" 2>/dev/null || break
    sleep 0.2
  done
  if (( ! READY )); then
    is_dispatcher "$DPID" && kill -TERM "$DPID" 2>/dev/null || true
    rm -f "$DPID_FILE"
    echo "dispatcher failed readiness contract v2/gemma=4:600:300/qwen=1:90000:0" >&2
    exit 1
  fi
fi
echo "$DPID" > "$DPID_FILE"
flock -u 9
exec 9>&-

export ARCHIVE_LLM_BASE_URL="${ARCHIVE_LLM_BASE_URL:-http://127.0.0.1:${DPORT}}"
"$(cd "$ROOT/../Vox" && pwd)/start-vox.sh" 2>/dev/null || true
ENV_DIR="$ROOT/ArchiveOfHeresy"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/archive-main.pid"
LOG_FILE="$RUNTIME_DIR/archive-main.log"
ENV_FILE="$ROOT/.env"

if [[ -f "$ENV_FILE" ]]; then set -a; . "$ENV_FILE"; set +a; fi
if [[ ! -x "$ENV_DIR/bin/python" ]]; then
  echo "Python environment not found: $ENV_DIR" >&2
  exit 1
fi
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null \
    && grep -q "main\.py" "/proc/$(cat "$PID_FILE")/cmdline" 2>/dev/null; then
  echo "ArchiveOfHeresy main is already running with PID $(cat "$PID_FILE")"
  exit 0
fi
rm -f "$PID_FILE"
mkdir -p "$RUNTIME_DIR"

export ARCHIVE_HOST="${ARCHIVE_HOST:-0.0.0.0}"
export ARCHIVE_CHAT_CONTEXT_MESSAGES="${ARCHIVE_CHAT_CONTEXT_MESSAGES:-0}"
export ARCHIVE_MAGOS_CONTEXT_LAYERS="${ARCHIVE_MAGOS_CONTEXT_LAYERS:-wiki,vector,graph}"
export ARCHIVE_VECTOR_INJECTION_ENABLED="${ARCHIVE_VECTOR_INJECTION_ENABLED:-0}"
export ARCHIVE_GRAPH_INJECTION_ENABLED="${ARCHIVE_GRAPH_INJECTION_ENABLED:-0}"
export ARCHIVE_MAGOS_ENABLED="${ARCHIVE_MAGOS_ENABLED:-1}"
export ARCHIVE_VECTOR_BACKFILL_ON_START="${ARCHIVE_VECTOR_BACKFILL_ON_START:-0}"
export ARCHIVE_GRAPH_BACKFILL_ON_START="${ARCHIVE_GRAPH_BACKFILL_ON_START:-0}"
export ARCHIVE_MEMORY_QUALITY_REPORT_ENABLED="${ARCHIVE_MEMORY_QUALITY_REPORT_ENABLED:-1}"
export ARCHIVE_MEMORY_QUALITY_REPORT_HOUR="${ARCHIVE_MEMORY_QUALITY_REPORT_HOUR:-4}"
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

case "${ARCHIVE_LLM_PROVIDER:-llamacpp}" in
  litert|litert-lm)
    export ARCHIVE_DEFAULT_MODEL="${ARCHIVE_DEFAULT_MODEL:-gemma4-12b,gpu,2048}" ;;
  llamacpp|llama.cpp|llama)
    export ARCHIVE_DEFAULT_MODEL="${ARCHIVE_DEFAULT_MODEL:-gemma-4-12b-it-UD-Q5_K_XL.gguf}" ;;
  *) echo "Unsupported ARCHIVE_LLM_PROVIDER: $ARCHIVE_LLM_PROVIDER" >&2; exit 1 ;;
esac
export ARCHIVE_MAGOS_MODEL="${ARCHIVE_MAGOS_MODEL:-$ARCHIVE_DEFAULT_MODEL}"
export ARCHIVE_LIBRARIAN_MODEL="${ARCHIVE_LIBRARIAN_MODEL:-$ARCHIVE_DEFAULT_MODEL}"
setsid "$ENV_DIR/bin/python" "$ROOT/main.py" >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"
echo "ArchiveOfHeresy main started: PID $(cat "$PID_FILE"), http://${ARCHIVE_HOST}:${ARCHIVE_PORT:-8090}"
echo "Log: $LOG_FILE"
