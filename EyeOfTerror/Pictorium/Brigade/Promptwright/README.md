# Promptwright

Planned Pictorium worker for image intent parsing and prompt/job-spec planning.

Initial source material:

- `DemonsForge/forge_service/planner.py`
- `DemonsForge/forge_service/thinker.py`
- `DemonsForge/forge_service/schemas.py`

Expected output:

- `/work/pictorium/image_plan.json`

The worker must return a strict job specification and must not claim that
non-local assets are available.
