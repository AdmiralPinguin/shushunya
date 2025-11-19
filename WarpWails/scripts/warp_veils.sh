#!/usr/bin/env bash
set -euo pipefail
PROJ="/media/acab/LMS/Shushunya/WarpWails"
cd "$PROJ"
source "$PROJ/warpwails/bin/activate"
pkill -f "uvicorn .*WarpWails" || true
fuser -k 8009/tcp || true
exec uvicorn app.Core.main:app --host 0.0.0.0 --log-level info --port 8009
