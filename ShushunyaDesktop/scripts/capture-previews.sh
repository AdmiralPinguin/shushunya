#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Runtime is missing. Run ./scripts/install.sh first." >&2
  exit 2
fi

mkdir -p runtime/previews
export QT_QPA_PLATFORM=offscreen
export QT_QUICK_BACKEND=software

.venv/bin/python -m shushunya_desktop.main --preview-role presence --preview-size 1920x1080 --capture runtime/previews/presence-1920x1080.png
.venv/bin/python -m shushunya_desktop.main --preview-role mind --preview-size 1080x1920 --capture runtime/previews/mind-1080x1920.png
.venv/bin/python -m shushunya_desktop.main --preview-role canvas --preview-size 1920x1080 --capture runtime/previews/canvas-1920x1080.png
.venv/bin/python -m shushunya_desktop.main --preview-role ambient --preview-size 1920x1080 --capture runtime/previews/ambient-1920x1080.png
