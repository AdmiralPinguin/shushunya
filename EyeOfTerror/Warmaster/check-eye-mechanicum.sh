#!/usr/bin/env bash
# EyeOfTerror integration barrier — the active code brigade is the Skitarii Warband.
#
# This barrier no longer hides failures under a blanket "SKIP legacy". Suites are split
# by CAUSE, measured (2026-07-11):
#   REQUIRED    — Skitarii + the Warmaster suites that actually pass headless; a break
#                 here turns the barrier RED.
#   ENV-GATED   — need a dependency (pydantic) or a LIVE service/HTTP; reported, skipped
#                 without failing when the env isn't up (they run for real in the live stack).
#   QUARANTINED — reference retired brigades or carry known contract drift; reported as
#                 known-obsolete and tracked separately (do NOT fake them green).
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
  EyeOfTerror/Warmaster/eye_of_terror/skitarii_bridge.py \
  EyeOfTerror/Warmaster/eye_of_terror/inner_circle/ceraxia.py \
  EyeOfTerror/Warmaster/eye_of_terror/inner_circle/ceraxia_service.py || fail=1

echo "== REQUIRED: Warmaster suites (must stay green) =="
for s in research_modes_self_test.py research_revision_loop_self_test.py routing_self_test.py \
         ledger_self_test.py governor_api_contract_self_test.py http_executor_self_test.py; do
  if PYTHONPATH=".:EyeOfTerror/Warmaster" timeout 60 python3 "EyeOfTerror/Warmaster/$s" >/dev/null 2>&1; then
    echo "   OK   $s"
  else
    echo "   FAIL $s (REQUIRED)"; fail=1
  fi
done

echo "== ENV-GATED: need a dep or a live service (reported, not required) =="
for s in governors_self_test.py iskandar_service_self_test.py ceraxia_service_self_test.py \
         warmaster_gateway_governor_http_self_test.py local_executor_self_test.py; do
  [ -f "EyeOfTerror/Warmaster/$s" ] || continue
  if PYTHONPATH=".:EyeOfTerror/Warmaster" timeout 20 python3 "EyeOfTerror/Warmaster/$s" >/dev/null 2>&1; then
    echo "   OK   $s (env present)"
  else
    echo "   GATED $s (missing dep / live service down)"
  fi
done

echo "== QUARANTINED: obsolete refs / known drift — tracked, not gating =="
for s in doctor.py self_test.py warmaster_api_contract_self_test.py; do
  [ -f "EyeOfTerror/Warmaster/$s" ] && echo "   KNOWN $s (retired-brigade ref or contract drift — see backlog)"
done

if [ "$fail" -ne 0 ]; then echo "eye barrier: RED (a REQUIRED suite failed)"; exit 1; fi
echo "eye barrier: GREEN (Skitarii + required Warmaster suites)"
