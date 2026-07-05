# DemonsForge Extraction Plan

This document maps the current DemonsForge implementation into Moriana's planned
brigade. The goal is to reuse working engine logic without letting a single
forge service become an unsupervised all-purpose agent.

`DemonsForge` must end as a narrow forge runtime: engine adapters, queue,
artifact storage, and an HTTP API. Agent-like planning, resource policy,
verification policy, and final packaging belong in `Pictorium`.

## Current Component Ownership

- `Pictorium/Moriana/moriana_core/promptwright.py`: heuristic image request
  parser and baseline job-spec builder, moved from `forge_service.planner`.
- `Pictorium/Moriana/moriana_core/prompt_thinker.py`: optional
  OpenAI-compatible advisory JSON patcher for planner output, moved from
  `forge_service.thinker`.
- `Pictorium/Moriana/moriana_core/project_planner.py`: concept batch,
  storyboard, and character-sheet planning, moved from the planning section of
  `forge_service.projects`.
- `Pictorium/Moriana/moriana_core/image_evaluator.py`: deterministic
  image/metadata/pixel checks, moved from `forge_service.evaluator`.
- `Pictorium/Moriana/moriana_core/character_profiles.py`: character identity
  profiles and text matching, moved from `forge_service.characters`.
- `Pictorium/Moriana/moriana_core/asset_catalog.py`: model, LoRA, embedding,
  engine, sampler, scheduler, and capability discovery, moved from
  `forge_service.registries`.
- `Pictorium/Moriana/moriana_core/asset_downloader.py`: approved-source asset
  download validation and execution, moved from `forge_service.downloader`.
- `Pictorium/Moriana/moriana_core/forge_reports.py`: quality/report summary and
  pruning helpers, moved from `forge_service.reports`.
- `Pictorium/Moriana/benches`: quality, project, and long Forge scenario
  benches, moved from `DemonsForge/tests`.
- `forge_service.schemas`: strict Pydantic runtime contracts for jobs, projects, assets,
  artifacts, and memory proposals.
- `forge_service.client`: Python client for the Forge API.
- `forge_service.queue`: job lifecycle and execution queue.
- `forge_service.server`: FastAPI boundary for capabilities, planning, jobs,
  projects, gallery, memory, and artifacts.
- `forge_service.storage`: persistent manifests, jobs, and artifacts.

## Brigade Split

1. `Promptwright` owns image intent parsing and job-spec planning.
   It now owns `build_heuristic_plan`, `plan_txt2img`, project planning, and
   `PlannerThinker`.

2. `ModelQuartermaster` owns engine/resource readiness.
   It should wrap `registries.capabilities`, `discover_models`,
   `discover_loras`, `discover_embeddings`, `asset_profiles`, and the safe
   parts of `downloader.validate_download_spec`.

3. `ForgeDispatcher` owns job submission and monitoring.
   It should call DemonsForge through `DemonsForgeClient` or `ForgeRelay`, not
   import engine adapters directly.

4. `ImageVerifier` owns generated artifact checks.
   It now owns `evaluate_artifact` and later can add vision-model semantic
   review as a separate optional check.

5. `ArtifactFinalis` owns final packaging.
   It should combine job records, artifact metadata, verification reports, and
   user-facing delivery hints into `/work/pictorium/final_manifest.json`.

## Cleanup Target

DemonsForge has been cleaned to compatibility wrappers for the first moved
responsibilities:

- `forge_service.planner`, `forge_service.thinker`, `forge_service.evaluator`,
  `forge_service.characters`, `forge_service.registries`,
  `forge_service.downloader`, and `forge_service.reports` were removed.
- `forge_service.server` and `forge_service.queue` import Pictorium-owned logic
  directly.
- `forge_service.projects` keeps runtime project storage and mask generation
  only.

Remaining cleanup after Moriana worker services exist:

- Split direct Pictorium imports from DemonsForge behind real HTTP workers once
  Moriana services exist.
- Make ArtifactFinalis produce final user-facing manifests instead of exposing
  Forge report summaries as the final artifact.

Keep these responsibilities in `DemonsForge`:

- Engine adapters.
- Job queue and job lifecycle.
- Artifact and metadata storage.
- Runtime capabilities and health endpoints.
- Raw gallery/artifact file serving.

## Migration Order

1. Create Moriana planned contracts and brigade documentation.
2. Add worker service shells in `Pictorium/Brigade`.
3. Move planner/spec logic into `Promptwright`. Done.
4. Move resource policy into `ModelQuartermaster`. Done.
5. Move job submit/monitor protocol into `ForgeDispatcher`; DemonsForge remains
   the job API.
6. Move deterministic artifact checks into `ImageVerifier`. Done.
7. Move report/bench assembly into `ArtifactFinalis` and `ImageVerifier`. Done
   for module ownership; worker service activation is still pending.
8. Switch Warmaster image governor registry from `ForgeMasterGovernor` to
   `Moriana` only after service tests pass.
9. Delete old DemonsForge planning/agent modules instead of keeping wrappers.
   Done.

## Not Yet Done

- Moriana service implementation.
- Warmaster `image_generation` task contract builder.
- Worker API services for each brigade role.
- Registry switch from `ForgeMasterGovernor` to active `Moriana`.
- Real image-generation campaign/run execution under Warmaster.
