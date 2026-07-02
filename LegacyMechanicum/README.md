# Legacy Mechanicum

The active EyeOfTerror architecture no longer stores primary workers in this
root directory.

Current ownership:

- Warmaster/mobile API: `EyeOfTerror/Warmaster/MobileGateway/ShushunyaAgent`
- Search service: `EyeOfTerror/Services/Search/SearXNG`
- Scriptorium render worker: `EyeOfTerror/Scriptorium/Brigade/OcularisRenderium`
- Ceraxia code workers: `EyeOfTerror/Mechanicum/CodeBrigade/Workers`

This directory remains as a compatibility and runtime layer for:

- shared Worker API runtime scripts (`start_worker.py`, `worker_runtime.py`)
- Worker service registry compatibility (`worker_services.json`)
- external relays that are not migrated yet (`ForgeRelay`, `MnemosyneRelay`)
- explicit legacy wrappers for old ShushunyaAgent and SearXNG script paths
- parked temporary work under `_temporary/`

Do not add new primary workers here. Put new workers under the owning
EyeOfTerror department and register them through Warmaster/worker manifests.
