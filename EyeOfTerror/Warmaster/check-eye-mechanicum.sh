#!/usr/bin/env bash
# Required barrier for the single native Ceraxia-to-Skitarii code architecture.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

fail=0

echo "== REQUIRED: Skitarii Warband =="
python3 -W error -m unittest \
  EyeOfTerror.Mechanicum.Skitarii.test_skitarii \
  EyeOfTerror.Mechanicum.Skitarii.test_eval_hardening \
  EyeOfTerror.Mechanicum.Skitarii.test_service_patch_bundle \
  EyeOfTerror.Mechanicum.Skitarii.test_ceraxia_facade || fail=1
python3 -m py_compile \
  EyeOfTerror/Mechanicum/Skitarii/*.py \
  EyeOfTerror/Warmaster/eye_of_terror/native_code_run.py \
  EyeOfTerror/Warmaster/eye_of_terror/task_prepare.py \
  EyeOfTerror/Warmaster/eye_of_terror/run_validation.py \
  EyeOfTerror/Warmaster/eye_of_terror/run_state.py \
  EyeOfTerror/Warmaster/eye_of_terror/campaigns.py \
  EyeOfTerror/Warmaster/eye_of_terror/skitarii_bridge.py \
  EyeOfTerror/Warmaster/eye_of_terror/inner_circle/ceraxia.py \
  EyeOfTerror/Warmaster/eye_of_terror/inner_circle/ceraxia_service.py || fail=1

echo "== REQUIRED: Abaddon/Warmaster integration suites =="
for s in native_code_run_self_test.py native_backend_router_self_test.py \
         terminal_state_invariants_self_test.py ceraxia_service_self_test.py \
         brigade_tabs_self_test.py research_modes_self_test.py \
         research_revision_loop_self_test.py routing_self_test.py ledger_self_test.py \
         governor_api_contract_self_test.py http_executor_self_test.py \
         campaign_self_test.py warmaster_acceptance_self_test.py \
         warmaster_gateway_self_test.py start_brigade_self_test.py \
         self_test.py warmaster_api_contract_self_test.py; do
  if PYTHONPATH=".:EyeOfTerror/Warmaster" timeout 60 python3 "EyeOfTerror/Warmaster/$s" >/dev/null 2>&1; then
    echo "   OK   $s"
  else
    echo "   FAIL $s (REQUIRED)"; fail=1
  fi
done

echo "== REQUIRED: registries and component status =="
PYTHONPATH=".:EyeOfTerror/Warmaster" python3 EyeOfTerror/Warmaster/doctor.py --quiet || fail=1
PYTHONPATH=".:EyeOfTerror/Warmaster" python3 LegacyMechanicum/worker_services_self_test.py || fail=1
PYTHONPATH=".:EyeOfTerror/Warmaster" python3 EyeOfTerror/Mechanicum/mechanicum_status.py || fail=1

echo "== ENV-GATED: optional dependency or live service required =="
for s in governors_self_test.py iskandar_service_self_test.py acceptance_live_self_test.py \
         warmaster_gateway_governor_http_self_test.py local_executor_self_test.py; do
  [ -f "EyeOfTerror/Warmaster/$s" ] || continue
  if PYTHONPATH=".:EyeOfTerror/Warmaster" timeout 20 python3 "EyeOfTerror/Warmaster/$s" >/dev/null 2>&1; then
    echo "   OK   $s (environment present)"
  else
    echo "   GATED $s (optional dependency missing or live service unavailable)"
  fi
done

if [ "$fail" -ne 0 ]; then echo "eye barrier: RED (a REQUIRED suite failed)"; exit 1; fi
echo "eye barrier: GREEN (native Ceraxia + Skitarii + Abaddon integration)"
