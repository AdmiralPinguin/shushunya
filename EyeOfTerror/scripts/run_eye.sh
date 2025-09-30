#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# .env (необязательно)
[ -f .env ] && set -a && source .env && set +a

# Жёстко указываем venv с существующим путём
VENV_DIR="$(pwd)/EyeOfTerror"
source "$VENV_DIR/bin/activate"

exec uvicorn app.EyeOfTerror.main:app \
  --host "${EYE_HOST:-0.0.0.0}" \
  --port "${EYE_PORT:-8010}" \
  --log-level info
