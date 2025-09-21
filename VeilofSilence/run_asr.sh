#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source VeilofSilence/bin/activate
export PYTHONUNBUFFERED=1
[ -f .env ] && { set -o allexport; source .env; set +o allexport; }
export CTRANSLATE2_NUM_EXPERIMENTAL_PACKED_GEMM=1
python -m uvicorn app.main:app --host "${SERVER_HOST:-0.0.0.0}" --port "${SERVER_PORT:-8011}"
