# DemonsForge Extraction Plan

This document maps the current DemonsForge implementation into Moriana's planned
brigade. The goal is to reuse working engine logic without letting a single
forge service become an unsupervised all-purpose agent.

`DemonsForge` must end as a narrow forge runtime: engine adapters, queue,
artifact storage, and an HTTP API. Agent-like planning, resource policy,
verification policy, and final packaging belong in `Pictorium`.

## Current DemonsForge Components

- `forge_service.planner`: heuristic image request parser and baseline job-spec
  builder.
- `forge_service.thinker`: optional OpenAI-compatible advisory JSON patcher for
  planner output.
- `forge_service.schemas`: strict Pydantic contracts for jobs, projects, assets,
  artifacts, and memory proposals.
- `forge_service.registries`: model, LoRA, embedding, engine, sampler,
  scheduler, and capability discovery.
- `forge_service.downloader`: approved-source asset download validation.
- `forge_service.client`: Python client for the Forge API.
- `forge_service.queue`: job lifecycle and execution queue.
- `forge_service.server`: FastAPI boundary for capabilities, planning, jobs,
  projects, gallery, memory, and artifacts.
- `forge_service.evaluator`: deterministic image/metadata/pixel checks.
- `forge_service.reports` and `forge_service.storage`: persistent reports,
  manifests, jobs, and artifacts.

## Brigade Split

1. `Promptwright` owns image intent parsing and job-spec planning.
   It should start by wrapping `planner.build_heuristic_plan`,
   `planner.plan_txt2img`, `schemas.JobSpec`, and `thinker.PlannerThinker`.

2. `ModelQuartermaster` owns engine/resource readiness.
   It should wrap `registries.capabilities`, `discover_models`,
   `discover_loras`, `discover_embeddings`, `asset_profiles`, and the safe
   parts of `downloader.validate_download_spec`.

3. `ForgeDispatcher` owns job submission and monitoring.
   It should call DemonsForge through `DemonsForgeClient` or `ForgeRelay`, not
   import engine adapters directly.

4. `ImageVerifier` owns generated artifact checks.
   It should reuse `evaluator.evaluate_artifact` and later add vision-model
   semantic review as a separate optional check.

5. `ArtifactFinalis` owns final packaging.
   It should combine job records, artifact metadata, verification reports, and
   user-facing delivery hints into `/work/pictorium/final_manifest.json`.

## Cleanup Target

After Moriana has working replacements, remove or shrink these responsibilities
inside `DemonsForge`:

- High-level user-intent planning should move out of `forge_service.planner`.
- Optional LLM advisory thinking should move out of `forge_service.thinker`.
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
   wrappers in DemonsForge.
4. Move resource policy into `ModelQuartermaster`; keep DemonsForge discovery
   as runtime facts only.
5. Move job submit/monitor protocol into `ForgeDispatcher`; DemonsForge remains
   the job API.
6. Move deterministic artifact checks into `ImageVerifier`.
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
