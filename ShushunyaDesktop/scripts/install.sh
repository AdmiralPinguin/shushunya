#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

UV="$(command -v uv || true)"
if [[ -z "$UV" && -x /home/codexbox/.local/bin/uv ]]; then
  UV=/home/codexbox/.local/bin/uv
fi
PYTHON="${SHUSHUNYA_PYTHON:-/usr/bin/python3}"

if [[ -n "$UV" ]]; then
  "$UV" venv --clear --python "$PYTHON" .venv
  "$UV" pip install --python .venv/bin/python -e .
else
  "$PYTHON" -m venv --clear .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -e .
fi

install -d -m 1777 runtime/live
echo "Shushunya Desktop runtime installed at $ROOT/.venv"
