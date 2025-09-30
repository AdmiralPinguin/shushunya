#!/usr/bin/env bash
set -euo pipefail
CORE="/media/acab/LMS/Shushunya/CoreOfMadness"
CFG="$CORE/configs/default.yaml"
source "$CORE/coremad/bin/activate"

read -r HOST PORT MODEL_PATH TP_SIZE MAXLEN <<EOFCONF
$(python - <<'PY'
import yaml
cfg=yaml.safe_load(open("/media/acab/LMS/Shushunya/CoreOfMadness/configs/default.yaml"))
print(cfg["server"]["host"],
      cfg["server"]["port"],
      cfg["engine"]["model_path"],
      cfg["engine"]["tp_size"],
      cfg["engine"]["max_model_len"])
PY
)
EOFCONF

fuser -k "${PORT}/tcp" 2>/dev/null || true

python -m vllm.entrypoints.openai.api_server \
  --host "$HOST" --port "$PORT" \
  --model "$MODEL_PATH" \
  --trust-remote-code \
  --tensor-parallel-size "$TP_SIZE" \
  --max-model-len "$MAXLEN"
