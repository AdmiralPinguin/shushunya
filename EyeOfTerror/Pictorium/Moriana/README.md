# Moriana

Moriana is the active Pictorium governor for image generation and visual forge
work. She coordinates specialist brigades and uses DemonsForge only as a
low-level graphical engine package.

## Responsibility

- Convert visual user requests into strict image task contracts.
- Select text-to-image, image-to-image, inpaint, upscale, image-series, and
  comics flows.
- Decide which engine should be used: SD3.5, SDXL, Flux, or a future backend.
- Detect required models, LoRAs, embeddings, ControlNet units, IP-Adapters, and
  reference assets.
- Require approval before downloading new model assets.
- Dispatch jobs through `forge_runtime`.
- Verify image artifacts and package final deliverables for Warmaster.

## Brigades

- `Image`: still image generation/editing and current real Forge workflows.
- `Image series`: repeated Image Brigade execution with one shared run registry
  and a series final manifest.
- `Comics`: active first-pass scenario, storyboard, character sheet, panel
  package, layout, and manifest workflows on top of Image Brigade.
- `Video`: intentionally out of the current active scope.

## Run Runtime

Moriana owns visual run state under `runtime/pictorium/runs/<run_id>/` by
default. A run workspace contains the user request, Moriana plan, brigade
decisions, prompts, parameters, results, errors, revisions, artifacts, and final
manifest in one auditable directory.

The runtime tracks these statuses:

- `created`
- `planning`
- `generating`
- `checking`
- `revising`
- `completed`
- `failed`

The artifact registry records every prompt, dispatch package, verification
report, image, comic panel package, layout, error, revision plan, and final
manifest with creator, step, attempt, status, and rejection reason.

## Quality Trials

`forge_tests/moriana_quality_trials.py` runs the current visual acceptance
scenarios: simple image, complex character/environment image, linked image
series, four-panel comic, eight-panel comic, hard style/character image, and an
existing artifact audit.

The report separates two scores:

- `avg_quality_score`: technical pipeline score from manifests, verifiers,
  blockers, registry states, and revision decisions.
- `evidence_adjusted_score`: the same score penalized for coverage gaps such as
  synthetic image fixtures or synthetic comic panel art.

Synthetic fixtures prove orchestration, artifact registration, revision
behavior, and manifest packaging. They do not prove live visual quality. A report
with `readiness_verdict=needs_live_visual_trials` still requires live generated
images or accepted external artifacts before treating Moriana as production
quality.

Application-facing endpoints:

- `POST /runs`
- `GET /runs`
- `GET /runs/{run_id}/status`
- `GET /runs/{run_id}/artifacts`
- `GET /runs/{run_id}/final`
- `POST /runs/{run_id}/revise`
- `POST /runs/{run_id}/accept`

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
