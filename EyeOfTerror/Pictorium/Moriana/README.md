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

Moriana now owns the image-agent logic that was previously mixed into
DemonsForge:

- `moriana_core.promptwright`, `prompt_thinker`, and `project_planner` are
  Promptwright input.
- `moriana_core.asset_catalog`, `asset_downloader`, and `character_profiles`
  are ModelQuartermaster input.
- `DemonsForge/forge_service/client.py`, `queue.py`, `server.py`, and
  `projects.py` are ForgeDispatcher runtime input.
- `moriana_core.image_evaluator`, `forge_reports`, and `benches/` are
  ImageVerifier and ArtifactFinalis input.

DemonsForge should remain a narrow runtime: schemas, config, engine adapters,
queue, storage, project masks, and API surface. It should not regain planner,
thinker, catalog-policy, downloader-policy, report-policy, or bench ownership.

The existing `Warmaster/InnerCircle/ForgeMasterGovernor` remains a planned
legacy placeholder until Moriana replaces it through a tested registry change.
