#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="$ROOT/ArchiveOfHeresy"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/archive-main.pid"
LOG_FILE="$RUNTIME_DIR/archive-main.log"
ENV_FILE="$ROOT/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

if [ ! -x "$ENV_DIR/bin/python" ]; then
  echo "Python environment not found: $ENV_DIR" >&2
  echo "Create it with: python3 -m venv \"$ENV_DIR\"" >&2
  exit 1
fi

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "ArchiveOfHeresy main is already running with PID $(cat "$PID_FILE")"
  exit 0
fi

mkdir -p "$RUNTIME_DIR"

export ARCHIVE_HOST="${ARCHIVE_HOST:-0.0.0.0}"
export ARCHIVE_MAGOS_CONTEXT_LAYERS="${ARCHIVE_MAGOS_CONTEXT_LAYERS:-wiki,vector,graph}"
export ARCHIVE_VECTOR_INJECTION_ENABLED="${ARCHIVE_VECTOR_INJECTION_ENABLED:-1}"
export ARCHIVE_GRAPH_INJECTION_ENABLED="${ARCHIVE_GRAPH_INJECTION_ENABLED:-1}"
export ARCHIVE_MAGOS_ENABLED="${ARCHIVE_MAGOS_ENABLED:-1}"
export ARCHIVE_VECTOR_BACKFILL_ON_START="${ARCHIVE_VECTOR_BACKFILL_ON_START:-1}"
export ARCHIVE_GRAPH_BACKFILL_ON_START="${ARCHIVE_GRAPH_BACKFILL_ON_START:-1}"
export ARCHIVE_MEMORY_QUALITY_REPORT_ENABLED="${ARCHIVE_MEMORY_QUALITY_REPORT_ENABLED:-1}"
export ARCHIVE_MEMORY_QUALITY_REPORT_HOUR="${ARCHIVE_MEMORY_QUALITY_REPORT_HOUR:-4}"

setsid "$ENV_DIR/bin/python" "$ROOT/main.py" >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"

echo "ArchiveOfHeresy main started: PID $(cat "$PID_FILE"), http://${ARCHIVE_HOST}:${ARCHIVE_PORT:-8090}"
echo "Log: $LOG_FILE"
