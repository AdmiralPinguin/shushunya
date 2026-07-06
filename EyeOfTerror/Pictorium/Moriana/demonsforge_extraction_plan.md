# DemonsForge Extraction Plan

This document records the boundary after extracting application logic out of
DemonsForge.

## Final Boundary

`DemonsForge` is not the visual application. It is the low-level graphical
engine area:

- `DemonsForge/forge_service/engines/`
- local model and asset folders
- generated artifact/runtime data
- standalone direct model demo/download scripts

`Pictorium` owns everything above that:

- API server/client
- queue and worker lifecycle
- schemas and contracts
- project storage and masks
- memory bridge
- planning, policy, reports, and verification
- runtime scripts, tests, and benches

## Pictorium Ownership

- `Moriana/forge_runtime`: API, queue, storage, schemas, project masks, memory
  bridge, and runtime control.
- `Moriana/moriana_core`: prompt planning, thinker patching, project planning,
  character profiles, asset catalog, asset downloader policy, reports, and
  deterministic image evaluation.
- `Moriana/forge_tests`: runtime smoke/self/cycle tests.
- `Moriana/benches`: long API, quality, and project bench scenarios.
- `Moriana/scripts`: Forge runtime start/check scripts.

## Three Brigades

1. `Brigades/Image`: current still-image workflows.
   Workers: Promptwright, ModelQuartermaster, ForgeDispatcher, ImageVerifier,
   ArtifactFinalis.
2. `Brigades/Comics`: planned long-form visual workflows: storyboards,
   multi-panel pages, lettering, character consistency, and layout.
3. `Brigades/Video`: planned motion workflows: video backends, clip contracts,
   GPU scheduling, and text/image-to-video verification.

## Removed From DemonsForge

- planner, thinker, evaluator
- characters, registries, downloader, reports
- API server/client
- queue, storage, schemas, project runtime
- memory bridge
- runtime start/check scripts
- smoke/self/cycle tests
- quality/project/long benches

## Still Not Active

- Moriana governor service implementation.
- Worker API services for the Image brigade roles.
- Comics brigade implementation.
- Video backend selection and scheduling.
- Warmaster registry switch from the legacy image governor to Moriana. Done:
  Warmaster now routes image-generation tasks to Moriana on port 7103.
