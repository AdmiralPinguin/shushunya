#!/usr/bin/env bash
# Mechanicum barrier — one native Ceraxia-to-Skitarii architecture, fully required.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "== REQUIRED: Skitarii Warband focused tests =="
python3 -W error -m unittest \
  EyeOfTerror.Mechanicum.Skitarii.test_skitarii \
  EyeOfTerror.Mechanicum.Skitarii.test_eval_hardening \
  EyeOfTerror.Mechanicum.Skitarii.test_service_patch_bundle \
  EyeOfTerror.Mechanicum.Skitarii.test_ceraxia_facade

echo "== REQUIRED: Skitarii modules compile =="
python3 -m py_compile \
  EyeOfTerror/Mechanicum/Skitarii/*.py \
  EyeOfTerror/Warmaster/eye_of_terror/skitarii_bridge.py \
  EyeOfTerror/Warmaster/eye_of_terror/inner_circle/ceraxia.py \
  EyeOfTerror/Warmaster/eye_of_terror/inner_circle/ceraxia_service.py
echo "   ok"

echo "mechanicum barrier: GREEN (Skitarii, fully required)"
