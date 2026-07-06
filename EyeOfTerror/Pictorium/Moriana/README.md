# Moriana

Moriana is the active Pictorium governor for image generation and visual forge
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
- `Comics`: active first-pass scenario, storyboard, character sheet, panel
  package, layout, and manifest workflows on top of Image Brigade.
- `Video`: future motion-generation backends and GPU scheduling.

## Current Image Workers

- `Promptwright`: turns user intent into a structured image prompt/spec.
- `ModelQuartermaster`: checks models, LoRAs, embeddings, licenses, and download
  requirements.
- `ForgeDispatcher`: submits and monitors Forge runtime jobs.
- `ImageVerifier`: evaluates artifacts, metadata, dimensions, and edit risks.
- `ArtifactFinalis`: writes final manifest, gallery metadata, and delivery
  handoff.

## Current Comics Workers

- `ScenarioScribe`: turns a comic request into scenario, cast, style, and beats.
- `StoryboardArchitect`: maps beats into ordered panel prompts and continuity.
- `CharacterSheetwright`: prepares character-sheet plans through Image Brigade.
- `Panelwright`: prepares per-panel Image Brigade plans, resource checks, and
  Forge dry-runs.
- `LayoutFinalis`: assembles page layout and final comic manifest.

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

Warmaster routes image-generation tasks to Moriana on port `7103`.
