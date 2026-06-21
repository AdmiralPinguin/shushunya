#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="$ROOT/ArchiveOfHeresy"
ENV_FILE="$ROOT/.env"
REPORT_DATE="${1:-}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

PYTHON="$ENV_DIR/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

PYTHONPATH="$ROOT" "$PYTHON" - "$REPORT_DATE" <<'PY'
import json
import sys

from main import run_memory_quality_report

report_date = sys.argv[1] or None
result = run_memory_quality_report(report_date=report_date)
print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
PY
