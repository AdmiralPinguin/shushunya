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
- `forge_service.schemas`: strict Pydantic runtime contracts for jobs, projects, assets,
  artifacts, and memory proposals.
- `forge_service.registries`: model, LoRA, embedding, engine, sampler,
  scheduler, and capability discovery.
- `forge_service.downloader`: approved-source asset download validation.
- `forge_service.client`: Python client for the Forge API.
- `forge_service.queue`: job lifecycle and execution queue.
- `forge_service.server`: FastAPI boundary for capabilities, planning, jobs,
  projects, gallery, memory, and artifacts.
- `forge_service.reports` and `forge_service.storage`: persistent reports,
  manifests, jobs, and artifacts.

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

- `forge_service.planner` delegates to `Pictorium` Promptwright.
- `forge_service.thinker` delegates to `Pictorium` PromptThinker.
- `forge_service.evaluator` delegates to `Pictorium` ImageVerifier.
- `forge_service.projects` keeps runtime project storage/masks and delegates
  project planning to `Pictorium` ProjectPlanner.

Remaining cleanup after Moriana worker services exist:

- Asset policy decisions should move out of `forge_service.registries` and
  `forge_service.downloader`; DemonsForge should only expose discovered runtime
  facts and execute approved downloads.
- Quality-policy reports should move out of `tests/quality_bench.py` and
  become ImageVerifier scenarios.
- Final user-facing delivery reports should move out of forge runtime reports
  and become ArtifactFinalis manifests.

Keep these responsibilities in `DemonsForge`:

- Engine adapters.
- Job queue and job lifecycle.
- Artifact and metadata storage.
- Runtime capabilities and health endpoints.
- Raw gallery/artifact file serving.

## Migration Order

1. Create Moriana planned contracts and brigade documentation.
2. Add worker service shells in `Pictorium/Brigade`.
3. Move planner/spec logic into `Promptwright` while leaving compatibility
   wrappers in DemonsForge. Done.
4. Move resource policy into `ModelQuartermaster`; keep DemonsForge discovery
   as runtime facts only.
5. Move job submit/monitor protocol into `ForgeDispatcher`; DemonsForge remains
   the job API.
6. Move deterministic artifact checks into `ImageVerifier`. Done.
7. Move final manifest/report assembly into `ArtifactFinalis`.
8. Switch Warmaster image governor registry from `ForgeMasterGovernor` to
   `Moriana` only after service tests pass.
9. Delete old DemonsForge planning/agent wrappers after compatibility tests prove
   no clients depend on them.

## Not Yet Done

- Moriana service implementation.
- Warmaster `image_generation` task contract builder.
- Worker API services for each brigade role.
- Registry switch from `ForgeMasterGovernor` to active `Moriana`.
- Real image-generation campaign/run execution under Warmaster.
