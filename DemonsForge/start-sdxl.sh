#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export HF_HOME="$PWD/hf_home"
export CUDA_VISIBLE_DEVICES=""
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-32}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-32}"

exec "$PWD/DemonsForge/bin/python" "$PWD/app_sdxl.py"
