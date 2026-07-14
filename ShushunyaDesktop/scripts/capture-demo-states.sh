#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Runtime is missing. Run ./scripts/install.sh first." >&2
  exit 2
fi

OUTPUT="${1:-runtime/demo-states}"
mkdir -p "$OUTPUT"
export QT_QPA_PLATFORM=offscreen
export QT_QUICK_BACKEND=software

states=(sleep attention thinking forging waiting speaking triumph wounded sealing)

for state in "${states[@]}"; do
  .venv/bin/python -m shushunya_desktop.main \
    --preview-role presence \
    --preview-size 1920x1080 \
    --demo-state "$state" \
    --capture "$OUTPUT/$state-presence-1920x1080.png"

  .venv/bin/python -m shushunya_desktop.main \
    --preview-role mind \
    --preview-size 1080x1920 \
    --demo-state "$state" \
    --capture "$OUTPUT/$state-mind-1080x1920.png"
done

for state in sleep forging waiting triumph wounded sealing; do
  .venv/bin/python -m shushunya_desktop.main \
    --preview-role canvas \
    --preview-size 1920x1080 \
    --demo-state "$state" \
    --capture "$OUTPUT/$state-canvas-1920x1080.png"
done

for state in attention thinking forging waiting speaking triumph wounded sealing; do
  .venv/bin/python -m shushunya_desktop.main \
    --preview-role ambient \
    --preview-size 1920x1080 \
    --demo-state "$state" \
    --capture "$OUTPUT/$state-ambient-1920x1080.png"
done
