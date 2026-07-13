#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pattern='[s]hushunya_desktop.main'

"$ROOT/scripts/stop.sh"

for _ in {1..50}; do
  if ! pgrep -u "$(id -u)" -f "$pattern" >/dev/null; then
    exec "$ROOT/scripts/run.sh" "$@"
  fi
  sleep 0.1
done

echo "The previous Shushunya Desktop instance did not stop in time." >&2
exit 1
