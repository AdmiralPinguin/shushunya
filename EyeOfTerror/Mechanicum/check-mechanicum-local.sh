#!/usr/bin/env bash
set -euo pipefail

PYTHONPATH=EyeOfTerror/Mechanicum python3 EyeOfTerror/Mechanicum/boundary_self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum python3 EyeOfTerror/Mechanicum/mechanicum_status_self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum/CodeBrigade python3 EyeOfTerror/Mechanicum/CodeBrigade/focused_self_test.py
if [[ "${RUN_FULL_CODE_BRIGADE_SELF_TEST:-0}" == "1" ]]; then
  PYTHONPATH=EyeOfTerror/Mechanicum/CodeBrigade python3 EyeOfTerror/Mechanicum/CodeBrigade/self_test.py
fi
PYTHONPATH=EyeOfTerror/Mechanicum/CodeBrigade python3 EyeOfTerror/Mechanicum/CodeBrigade/verification_self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum python3 EyeOfTerror/Mechanicum/contracts_self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum/PlanningBrigade python3 EyeOfTerror/Mechanicum/PlanningBrigade/self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum/PlanningBrigade python3 EyeOfTerror/Mechanicum/PlanningBrigade/role_service_self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum/PlanningBrigade python3 EyeOfTerror/Mechanicum/PlanningBrigade/field_trial_runner.py >/dev/null
PYTHONPATH=EyeOfTerror/Mechanicum/Ceraxia:EyeOfTerror/Mechanicum/PlanningBrigade:EyeOfTerror/Mechanicum/CodeBrigade python3 EyeOfTerror/Mechanicum/Ceraxia/run_report_self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum/Ceraxia:EyeOfTerror/Mechanicum/PlanningBrigade:EyeOfTerror/Mechanicum/CodeBrigade python3 -m unittest EyeOfTerror/Mechanicum/Ceraxia/self_test.py -k full_dry_run_pipeline
PYTHONPATH=EyeOfTerror/Mechanicum/Ceraxia:EyeOfTerror/Mechanicum/PlanningBrigade:EyeOfTerror/Mechanicum/CodeBrigade python3 -m unittest EyeOfTerror/Mechanicum/Ceraxia/self_test.py -k go_module_block_import_edges
PYTHONPATH=EyeOfTerror/Mechanicum/Ceraxia:EyeOfTerror/Mechanicum/PlanningBrigade:EyeOfTerror/Mechanicum/CodeBrigade python3 -m unittest EyeOfTerror/Mechanicum/Ceraxia/self_test.py -k normalized_dependency_graph
PYTHONPATH=EyeOfTerror/Mechanicum/Ceraxia:EyeOfTerror/Mechanicum/PlanningBrigade:EyeOfTerror/Mechanicum/CodeBrigade python3 -m unittest EyeOfTerror/Mechanicum/Ceraxia/self_test.py -k passed_report_with_no_tests_ran_surface_output
PYTHONPATH=EyeOfTerror/Mechanicum/Ceraxia:EyeOfTerror/Mechanicum/PlanningBrigade:EyeOfTerror/Mechanicum/CodeBrigade python3 -m unittest EyeOfTerror/Mechanicum/Ceraxia/self_test.py -k run_audit_blocks_missing_artifact
if [[ "${RUN_FULL_CERAXIA_SELF_TEST:-0}" == "1" ]]; then
  PYTHONPATH=EyeOfTerror/Mechanicum/Ceraxia:EyeOfTerror/Mechanicum/PlanningBrigade:EyeOfTerror/Mechanicum/CodeBrigade python3 EyeOfTerror/Mechanicum/Ceraxia/self_test.py
fi
if [[ "${RUN_CERAXIA_HANDOFF_FIELD_TRIALS:-0}" == "1" ]]; then
  PYTHONPATH=EyeOfTerror/Mechanicum/Ceraxia:EyeOfTerror/Mechanicum/PlanningBrigade:EyeOfTerror/Mechanicum/CodeBrigade python3 EyeOfTerror/Mechanicum/Ceraxia/handoff_field_trials.py >/dev/null
fi
