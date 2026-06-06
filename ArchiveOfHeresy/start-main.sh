#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="$ROOT/ArchiveOfHeresy"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/archive-main.pid"
LOG_FILE="$RUNTIME_DIR/archive-main.log"

if [ ! -x "$ENV_DIR/bin/python" ]; then
  echo "Python environment not found: $ENV_DIR" >&2
  echo "Create it with: python3 -m venv "$ENV_DIR"" >&2
  exit 1
fi

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "ArchiveOfHeresy main is already running with PID $(cat "$PID_FILE")"
  exit 0
fi

mkdir -p "$RUNTIME_DIR"

setsid "$ENV_DIR/bin/python" "$ROOT/main.py" >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"

echo "ArchiveOfHeresy main started: PID $(cat "$PID_FILE"), http://127.0.0.1:${ARCHIVE_PORT:-8090}"
echo "Log: $LOG_FILE"
