#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLOUDFLARED="${CLOUDFLARED:-/media/shushunya/SHUSHUNYA/shushunya/android-tools/cloudflared/cloudflared}"
TARGET_URL="${TARGET_URL:-http://127.0.0.1:8093}"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/stt-cloudflared.pid"
LOG_FILE="$RUNTIME_DIR/stt-cloudflared.log"
URL_FILE="$RUNTIME_DIR/stt-public-url.txt"

mkdir -p "$RUNTIME_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "STT tunnel already running with PID $(cat "$PID_FILE")"
  cat "$URL_FILE" 2>/dev/null || true
  exit 0
fi

rm -f "$LOG_FILE" "$URL_FILE"
setsid "$CLOUDFLARED" tunnel --url "$TARGET_URL" --protocol http2 --no-autoupdate >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"

for _ in $(seq 1 30); do
  public_url="$(grep -Eo 'https://[-a-zA-Z0-9.]+\.trycloudflare\.com' "$LOG_FILE" | tail -1 || true)"
  if [ -n "$public_url" ]; then
    echo "$public_url" > "$URL_FILE"
    echo "$public_url"
    exit 0
  fi
  sleep 1
done

echo "STT tunnel started, but no public URL appeared yet. Log: $LOG_FILE" >&2
exit 1
