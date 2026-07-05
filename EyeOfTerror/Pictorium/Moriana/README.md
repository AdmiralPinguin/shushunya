# Moriana

Moriana is the planned Pictorium governor for image generation and visual forge
work. She should coordinate DemonsForge through specialist workers instead of
embedding image-engine calls inside the general agent loop.

## Responsibility

- Convert visual user requests into strict image task contracts.
- Select text-to-image, image-to-image, inpaint, upscale, and project flows.
- Decide which engine should be used: SD3.5, SDXL, Flux, or a future backend.
- Detect required models, LoRAs, embeddings, ControlNet units, IP-Adapters, and
  reference assets.
- Require approval before downloading new model assets.
- Dispatch jobs to DemonsForge through a worker boundary.
- Verify image artifacts and package final deliverables for Warmaster.

## Initial Brigade

- `Promptwright`: turns user intent into a structured image prompt/spec.
- `ModelQuartermaster`: checks models, LoRAs, embeddings, licenses, and download
  requirements.
- `ForgeDispatcher`: submits and monitors DemonsForge jobs.
- `ImageVerifier`: evaluates artifacts, metadata, dimensions, and edit risks.
- `ArtifactFinalis`: writes final manifest, gallery metadata, and delivery
  handoff.

## DemonsForge Reuse

Moriana should reuse existing DemonsForge internals through stable boundaries:

- `forge_service.planner` and `forge_service.thinker` become Promptwright input.
- `forge_service.registries` and `forge_service.downloader` become
  ModelQuartermaster input.
- `forge_service.client`, `forge_service.queue`, and `forge_service.server`
  become ForgeDispatcher input.
- `forge_service.evaluator`, `forge_service.reports`, and
  `forge_service.storage` become ImageVerifier and ArtifactFinalis input.

The existing `Warmaster/InnerCircle/ForgeMasterGovernor` remains a planned
legacy placeholder until Moriana replaces it through a tested registry change.
