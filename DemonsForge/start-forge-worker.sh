#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
ARCHIVE_ENV="$PWD/../ArchiveOfHeresy/.env"
FORGE_ENV="$PWD/.env"
if [ -f "$ARCHIVE_ENV" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ARCHIVE_ENV"
  set +a
fi
if [ -f "$FORGE_ENV" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$FORGE_ENV"
  set +a
fi
export HF_HOME="$PWD/hf_home"
export CUDA_VISIBLE_DEVICES=""
export FORGE_MEMORY_ENABLED="${FORGE_MEMORY_ENABLED:-1}"
export FORGE_MEMORY_NAMESPACE="${FORGE_MEMORY_NAMESPACE:-demonsforge}"
export FORGE_MEMORY_REQUESTER="${FORGE_MEMORY_REQUESTER:-demonsforge}"
export FORGE_ARCHIVE_BASE_URL="${FORGE_ARCHIVE_BASE_URL:-http://127.0.0.1:8090}"
export FORGE_ARCHIVE_API_KEY="${FORGE_ARCHIVE_API_KEY:-${ARCHIVE_API_KEY:-}}"
export FORGE_GIT_COMMIT="${FORGE_GIT_COMMIT:-$(git rev-parse --short HEAD 2>/dev/null || true)}"
THREADS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 32)"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-$THREADS}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-$THREADS}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-$THREADS}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-$THREADS}"

exec "$PWD/DemonsForge/bin/python" "$PWD/run_forge_worker.py"
