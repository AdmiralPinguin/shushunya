#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_HOME="$ROOT/home"
VENV="$ROOT/venv"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-9379}"
VERBOSE="${VERBOSE:-0}"
PID_FILE="$ROOT/runtime/litert-lm.pid"
LOG_FILE="$ROOT/runtime/litert-lm.log"

if [ ! -x "$VENV/bin/litert-lm" ]; then
  echo "LiteRT-LM CLI not found: $VENV/bin/litert-lm" >&2
  exit 1
fi

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    echo "LiteRT-LM server is already running with PID $PID"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

mkdir -p "$ROOT/runtime" "$PROJECT_HOME"

ARGS=(serve --host "$HOST" --port "$PORT")
case "${VERBOSE,,}" in
  1|true|yes|on)
    ARGS+=(--verbose)
    ;;
esac

setsid env HOME="$PROJECT_HOME" XDG_CACHE_HOME="$ROOT/cache" "$VENV/bin/litert-lm" "${ARGS[@]}" \
  >"$LOG_FILE" 2>&1 </dev/null &

echo "$!" > "$PID_FILE"
echo "LiteRT-LM server started: PID $(cat "$PID_FILE"), http://$HOST:$PORT"
echo "Log: $LOG_FILE"
