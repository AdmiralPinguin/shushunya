#!/usr/bin/env bash
set -e
ROOT="/media/acab/LMS/Shushunya"
source "$ROOT/EyeOfTerror/EyeOfTerror/bin/activate"
fuser -k 8010/tcp 2>/dev/null || true
PYTHONPATH="$ROOT" python -m uvicorn EyeOfTerror.app.main:app \
  --host 0.0.0.0 --port 8010 --workers 1
