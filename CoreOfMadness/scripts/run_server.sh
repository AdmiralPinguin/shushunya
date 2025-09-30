#!/usr/bin/env bash
set -euo pipefail
PROJ="/media/acab/LMS/Shushunya"
CORE="$PROJ/CoreOfMadness"
VENV="$CORE/coremad"

if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"
python -m pip install --upgrade pip
pip install -r "$CORE/requirements.txt"

export COREMAD_CONFIG="$CORE/configs/default.yaml"
fuser -k 8013/tcp 2>/dev/null || true
python -m app.main
