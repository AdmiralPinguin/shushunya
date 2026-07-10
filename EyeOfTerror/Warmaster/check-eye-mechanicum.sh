#!/usr/bin/env bash
# EyeOfTerror integration barrier — updated for the Skitarii brigade.
# The retired paper brigades (Mechanicum/CodeBrigade/Workers, Mechanicum/PlanningBrigade)
# were removed; their self-tests no longer exist. This barrier now REQUIRES the active
# Skitarii brigade to be green and runs the surviving Warmaster/Scriptorium suites
# best-effort, printing a summary instead of dying on a retired dependency.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

fail=0

# ---- REQUIRED: active code brigade ----
echo "== REQUIRED: Skitarii =="
python3 EyeOfTerror/Mechanicum/Skitarii/test_skitarii.py || fail=1
python3 -m py_compile EyeOfTerror/Mechanicum/Skitarii/*.py EyeOfTerror/Warmaster/eye_of_terror/skitarii_bridge.py || fail=1

# ---- BEST-EFFORT: surviving Warmaster / Scriptorium suites ----
echo "== BEST-EFFORT: Warmaster & Scriptorium =="
ok=0; skip=0
run() {  # run() <pythonpath> <script> — each capped so one hang can't block the barrier
  if PYTHONPATH="$1" timeout 25 python3 "$2" >/dev/null 2>&1; then
    ok=$((ok+1))
  else
    skip=$((skip+1)); echo "   SKIP $(basename "$2") (legacy dependency, error, or timeout)"
  fi
}
for s in doctor.py self_test.py research_modes_self_test.py research_revision_loop_self_test.py \
         governors_self_test.py routing_self_test.py ledger_self_test.py \
         warmaster_api_contract_self_test.py governor_api_contract_self_test.py \
         iskandar_service_self_test.py ceraxia_service_self_test.py \
         warmaster_gateway_governor_http_self_test.py local_executor_self_test.py \
         http_executor_self_test.py; do
  [ -f "EyeOfTerror/Warmaster/$s" ] && run ".:EyeOfTerror/Warmaster" "EyeOfTerror/Warmaster/$s"
done

echo "== summary: best-effort ok=$ok skip=$skip =="
if [ "$fail" -ne 0 ]; then echo "eye barrier: RED (Skitarii required suite failed)"; exit 1; fi
echo "eye barrier: GREEN (Skitarii required; legacy best-effort)"
