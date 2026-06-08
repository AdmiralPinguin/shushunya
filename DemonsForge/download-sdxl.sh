#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export HF_HOME="$PWD/hf_home"
exec "$PWD/DemonsForge/bin/python" "$PWD/download_sdxl.py"
