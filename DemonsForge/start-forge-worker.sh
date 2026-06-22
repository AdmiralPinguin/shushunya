#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export HF_HOME="$PWD/hf_home"
export CUDA_VISIBLE_DEVICES=""
THREADS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 32)"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-$THREADS}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-$THREADS}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-$THREADS}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-$THREADS}"

exec "$PWD/DemonsForge/bin/python" "$PWD/run_forge_worker.py"
