#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install uv first: https://docs.astral.sh/uv/"
  exit 1
fi

uv venv WarpWails-XTTS --python 3.10 --allow-existing
uv pip install --python WarpWails-XTTS/bin/python --index-url https://download.pytorch.org/whl/cpu "torch==2.1.2+cpu" "torchaudio==2.1.2+cpu"
uv pip install --python WarpWails-XTTS/bin/python -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

echo "Ready."
echo "Activate: source /media/shushunya/SHUSHUNYA/shushunya/WarpWails/WarpWails-XTTS/bin/activate"
echo "Run check: python /media/shushunya/SHUSHUNYA/shushunya/WarpWails/warpwails.py --check"
