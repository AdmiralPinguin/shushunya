# Promptwright

Planned Pictorium worker for image intent parsing and prompt/job-spec planning.

Owned modules:

- `EyeOfTerror/Pictorium/Moriana/moriana_core/promptwright.py`
- `EyeOfTerror/Pictorium/Moriana/moriana_core/prompt_thinker.py`
- `EyeOfTerror/Pictorium/Moriana/moriana_core/project_planner.py`
- `EyeOfTerror/Pictorium/Moriana/forge_runtime/schemas.py`

Expected output:

- `/work/pictorium/image_plan.json`

The worker must return a strict job specification and must not claim that
non-local assets are available.
