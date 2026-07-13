#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Runtime is missing. Run ./scripts/install.sh first." >&2
  exit 2
fi

mkdir -p runtime/formats
export QT_QPA_PLATFORM=offscreen
export QT_QUICK_BACKEND=software

.venv/bin/python -m shushunya_desktop.main --preview-role presence --preview-size 1366x768 --capture runtime/formats/presence-1366x768.png
.venv/bin/python -m shushunya_desktop.main --preview-role presence --preview-size 2560x1080 --capture runtime/formats/presence-2560x1080.png
.venv/bin/python -m shushunya_desktop.main --preview-role mind --preview-size 900x1600 --capture runtime/formats/mind-900x1600.png
.venv/bin/python -m shushunya_desktop.main --preview-role mind --preview-size 1080x1920 --capture runtime/formats/mind-1080x1920.png
.venv/bin/python -m shushunya_desktop.main --preview-role canvas --preview-size 1280x1024 --capture runtime/formats/canvas-1280x1024.png
.venv/bin/python -m shushunya_desktop.main --preview-role canvas --preview-size 1080x1920 --capture runtime/formats/canvas-1080x1920.png
.venv/bin/python -m shushunya_desktop.main --preview-role ambient --preview-size 1920x1080 --capture runtime/formats/ambient-1920x1080.png
.venv/bin/python -m shushunya_desktop.main --preview-role ambient --preview-size 1080x1920 --capture runtime/formats/ambient-1080x1920.png
