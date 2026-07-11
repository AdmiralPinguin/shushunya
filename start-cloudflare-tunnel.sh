#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLOUDFLARED="$ROOT/android-tools/cloudflared/cloudflared"
TOKEN_FILE="$ROOT/.secrets/cloudflare-named-tunnel-token"
RUNTIME_DIR="$ROOT/runtime/cloudflare"
PID_FILE="$RUNTIME_DIR/shushunya-core.pid"
LOG_FILE="$RUNTIME_DIR/shushunya-core.log"

if [ ! -x "$CLOUDFLARED" ]; then
  echo "cloudflared not executable: $CLOUDFLARED" >&2
  exit 1
fi

if [ ! -f "$TOKEN_FILE" ]; then
  echo "Tunnel token not found: $TOKEN_FILE" >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR"

# после ребута pid может достаться чужому процессу (pid reuse) — верим только
# если это реально cloudflared
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null \
   && grep -q "cloudflared" "/proc/$(cat "$PID_FILE")/cmdline" 2>/dev/null; then
  echo "Cloudflare tunnel already running with PID $(cat "$PID_FILE")"
  exit 0
fi

rm -f "$PID_FILE"
setsid "$CLOUDFLARED" tunnel --no-autoupdate run --token-file "$TOKEN_FILE" >"$LOG_FILE" 2>&1 </dev/null &
echo "$!" > "$PID_FILE"

echo "Cloudflare tunnel started: PID $(cat "$PID_FILE")"
echo "Log: $LOG_FILE"
echo "Domains:"
echo "- https://shushunya.com"
echo "- https://shushunya.wiki (after DNS route is attached in Cloudflare)"
echo "- https://chat.shushunya.com"
echo "- https://translator.shushunya.com"
echo "- https://stt.shushunya.com"
echo "- https://roxdub.shushunya.com"
