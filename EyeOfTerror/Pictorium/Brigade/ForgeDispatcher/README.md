# ForgeDispatcher

Planned Pictorium worker for DemonsForge job submission and monitoring.

Initial source material:

- `DemonsForge/forge_service/client.py`
- `DemonsForge/forge_service/queue.py`
- `DemonsForge/forge_service/server.py`
- `DemonsForge/forge_service/projects.py`

Expected output:

- `/work/pictorium/forge_jobs.json`

This worker should use DemonsForge through an API boundary, not by importing
engine adapters directly.
