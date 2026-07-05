# Moriana

Moriana is the planned Pictorium governor for image generation and visual forge
work. She coordinates specialist brigades and uses DemonsForge only as a
low-level graphical engine package.

## Responsibility

- Convert visual user requests into strict image task contracts.
- Select text-to-image, image-to-image, inpaint, upscale, comics, and future
  video flows.
- Decide which engine should be used: SD3.5, SDXL, Flux, or a future backend.
- Detect required models, LoRAs, embeddings, ControlNet units, IP-Adapters, and
  reference assets.
- Require approval before downloading new model assets.
- Dispatch jobs through `forge_runtime`.
- Verify image artifacts and package final deliverables for Warmaster.

## Brigades

- `Image`: still image generation/editing and current real Forge workflows.
- `Comics`: long-form visual sequences, panels, lettering, and consistency.
- `Video`: future motion-generation backends and GPU scheduling.

## Current Image Workers

- `Promptwright`: turns user intent into a structured image prompt/spec.
- `ModelQuartermaster`: checks models, LoRAs, embeddings, licenses, and download
  requirements.
- `ForgeDispatcher`: submits and monitors Forge runtime jobs.
- `ImageVerifier`: evaluates artifacts, metadata, dimensions, and edit risks.
- `ArtifactFinalis`: writes final manifest, gallery metadata, and delivery
  handoff.

## DemonsForge Boundary

Moriana owns the API/runtime layer in `forge_runtime`:

- `client.py`
- `config.py`
- `queue.py`
- `schemas.py`
- `server.py`
- `storage.py`
- `projects.py`
- `archive_memory.py`

DemonsForge should not regain API, queue, schema, storage, planner, thinker,
policy, report, or bench ownership. It should remain engine adapters and local
model/demo assets only.

The existing `Warmaster/InnerCircle/ForgeMasterGovernor` remains a planned
legacy placeholder until Moriana replaces it through a tested registry change.
