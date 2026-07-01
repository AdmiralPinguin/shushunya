#!/usr/bin/env bash
set -euo pipefail

PYTHONPATH=EyeOfTerror/Mechanicum python3 EyeOfTerror/Mechanicum/boundary_self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum python3 EyeOfTerror/Mechanicum/mechanicum_status_self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum/CodeBrigade python3 EyeOfTerror/Mechanicum/CodeBrigade/self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum/CodeBrigade python3 EyeOfTerror/Mechanicum/CodeBrigade/verification_self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum python3 EyeOfTerror/Mechanicum/contracts_self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum/PlanningBrigade python3 EyeOfTerror/Mechanicum/PlanningBrigade/self_test.py
PYTHONPATH=EyeOfTerror/Mechanicum/Ceraxia:EyeOfTerror/Mechanicum/PlanningBrigade:EyeOfTerror/Mechanicum/CodeBrigade python3 EyeOfTerror/Mechanicum/Ceraxia/self_test.py
