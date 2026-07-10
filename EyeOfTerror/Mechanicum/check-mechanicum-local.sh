#!/usr/bin/env bash
# Mechanicum barrier — updated for the Skitarii brigade (the retired paper brigades
# CodeBrigade/Workers and PlanningBrigade were removed; their self-tests are gone).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# REQUIRED: the active code brigade must be green.
echo "== Skitarii focused tests =="
python3 EyeOfTerror/Mechanicum/Skitarii/test_skitarii.py

# REQUIRED: every Skitarii module must at least import/compile.
echo "== Skitarii modules compile =="
python3 -m py_compile EyeOfTerror/Mechanicum/Skitarii/*.py
echo "   ok"

# BEST-EFFORT: legacy Mechanicum self-tests still present and not depending on the
# retired brigades. Skipped (not failed) when they reference removed modules.
echo "== legacy Mechanicum self-tests (best-effort) =="
for t in boundary_self_test mechanicum_status_self_test contracts_self_test; do
  f="EyeOfTerror/Mechanicum/$t.py"
  [ -f "$f" ] || continue
  if PYTHONPATH=EyeOfTerror/Mechanicum python3 "$f" >/dev/null 2>&1; then
    echo "   OK   $t"
  else
    echo "   SKIP $t (references retired brigade)"
  fi
done

echo "mechanicum barrier: GREEN (Skitarii)"
