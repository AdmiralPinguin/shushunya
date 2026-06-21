#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install uv first: https://docs.astral.sh/uv/"
  exit 1
fi

uv venv WarpWails --allow-existing
uv pip install --python WarpWails/bin/python -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
fi

echo "Ready."
echo "Activate: source /media/shushunya/SHUSHUNYA/shushunya/WarpWails/WarpWails/bin/activate"
echo "Configure: /media/shushunya/SHUSHUNYA/shushunya/WarpWails/.env"
