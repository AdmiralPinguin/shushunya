# ForgeDispatcher

Planned Pictorium worker for DemonsForge job submission and monitoring.

Initial source material:

- `EyeOfTerror/Pictorium/Moriana/forge_runtime/client.py`
- `EyeOfTerror/Pictorium/Moriana/forge_runtime/queue.py`
- `EyeOfTerror/Pictorium/Moriana/forge_runtime/server.py`
- `EyeOfTerror/Pictorium/Moriana/forge_runtime/projects.py`

Expected output:

- `/work/pictorium/forge_jobs.json`

This worker should use DemonsForge through an API boundary, not by importing
engine adapters directly.
