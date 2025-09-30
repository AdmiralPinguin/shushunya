export LD_LIBRARY_PATH=/usr/local/cuda-12.9/targets/x86_64-linux/lib:${LD_LIBRARY_PATH}
export CUDA_HOME=/usr/local/cuda-12.9
export CUDA_HOME=/usr/local/cuda-12.4
export LD_LIBRARY_PATH=/usr/local/cuda-12.4/lib64:${LD_LIBRARY_PATH}
export CT2_USE_CUDA=1
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source VeilofSilence/bin/activate
export PYTHONUNBUFFERED=1
[ -f .env ] && { set -o allexport; source .env; set +o allexport; }
export CTRANSLATE2_NUM_EXPERIMENTAL_PACKED_GEMM=1
python -m uvicorn app.main:app --host "${SERVER_HOST:-0.0.0.0}" --port "${SERVER_PORT:-8011}"
