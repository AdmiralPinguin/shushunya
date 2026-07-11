# Legacy Mechanicum

The active EyeOfTerror architecture no longer stores primary workers in this
root directory.

Current ownership:

- Warmaster/mobile API: `EyeOfTerror/Warmaster/eye_of_terror/warmaster_gateway.py`
- Search service: `EyeOfTerror/Services/Search/SearXNG`
- Scriptorium render worker: `EyeOfTerror/Scriptorium/Brigade/OcularisRenderium`
- Ceraxia delegates code missions to the native Skitarii warband at
  `EyeOfTerror/Mechanicum/Skitarii`; it is outside this legacy worker runtime
  and is not registered in `worker_services.json` or `worker_aliases.json`.

This directory remains as a compatibility and runtime layer for:

- shared Worker API runtime scripts (`start_worker.py`, `worker_runtime.py`)
- Worker service registry compatibility (`worker_services.json`)
- external relays that are not migrated yet (`ForgeRelay`, `MnemosyneRelay`)
- explicit legacy wrappers for SearXNG script paths
- parked temporary work under `_temporary/`

Do not add new primary workers here. Put new workers under the owning
EyeOfTerror department and register them through Warmaster/worker manifests.
