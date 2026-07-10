#!/usr/bin/env bash
# Mechanicum barrier — the active code brigade is the Skitarii Warband. The retired paper
# brigades (CodeBrigade/Workers, PlanningBrigade) and their self-tests were removed, so
# there is no "best-effort legacy" theatre here any more: this barrier is fully REQUIRED.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "== REQUIRED: Skitarii Warband focused tests =="
python3 EyeOfTerror/Mechanicum/Skitarii/test_skitarii.py

echo "== REQUIRED: Skitarii modules compile =="
python3 -m py_compile EyeOfTerror/Mechanicum/Skitarii/*.py
echo "   ok"

echo "mechanicum barrier: GREEN (Skitarii, fully required)"
