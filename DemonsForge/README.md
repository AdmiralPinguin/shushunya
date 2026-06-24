# DemonsForge

Local image generation setup.

The apps are configured to run on CPU/RAM by default so GPU memory can stay available for the main LLM.

## Models

- SD 3.5 Large: `stabilityai/stable-diffusion-3.5-large`
- SDXL Base: `stabilityai/stable-diffusion-xl-base-1.0`
- FLUX Schnell: `black-forest-labs/FLUX.1-schnell`

## Gated model access

SD 3.5 Large and FLUX Schnell are gated on Hugging Face. Before downloading them, log in and accept access for:

https://huggingface.co/stabilityai/stable-diffusion-3.5-large

https://huggingface.co/black-forest-labs/FLUX.1-schnell

Then run:

```bash
DemonsForge/bin/huggingface-cli login
./download-model.sh
./download-flux.sh
```

## Start

```bash
./start.sh       # SD 3.5 Large, port 7860
./start-sdxl.sh  # SDXL, port 7861
./start-flux.sh  # FLUX, port 7862
```

Open one of:

http://localhost:7860
http://localhost:7861
http://localhost:7862

## Forge HTTP API

The new Forge service is a separate local HTTP API and job queue. It does not
replace the existing Gradio entrypoints above.
It is forced into CPU-only mode (`CUDA_VISIBLE_DEVICES=""`) and uses all
available logical CPU cores for CPU math thread pools by default.

```bash
./start-forge-api.sh
# http://localhost:8110
./check-forge-api.sh
```

By default the API starts an embedded worker thread. To isolate generation from
the HTTP process:

```bash
FORGE_EMBEDDED_WORKER=0 ./start-forge-api.sh
./start-forge-worker.sh
```

For long CPU test batches, recycle the worker after a known number of jobs so
torch/OpenMP thread pools die with the worker instead of idling inside the HTTP
API process:

```bash
FORGE_EMBEDDED_WORKER=0 ./start-forge-api.sh
FORGE_WORKER_MAX_JOBS=4 ./start-forge-worker.sh
```

Start a fresh worker for the next batch. The API remains available on port
`8110`.

Core endpoints:

- `GET /health`
- `GET /forge/capabilities`
- `GET /forge/state`
- `GET /forge/runtime`
- `POST /forge/runtime/unload`
- `POST /forge/runtime/checkpoint`
- `GET /forge/schema/job`
- `GET /forge/engines`
- `GET /forge/models`
- `GET /forge/loras`
- `GET /forge/embeddings`
- `GET /forge/samplers`
- `GET /forge/schedulers`
- `GET /forge/aspect-presets`
- `GET /forge/planner/thinker`
- `POST /forge/registries/refresh`
- `GET /forge/assets/downloads`
- `POST /forge/plan`
- `POST /forge/jobs`
- `GET /forge/jobs`
- `GET /forge/queue`
- `GET /forge/events`
- `POST /forge/queue/pause`
- `POST /forge/queue/resume`
- `GET /forge/jobs/{job_id}`
- `GET /forge/jobs/{job_id}/manifest`
- `GET /forge/jobs/{job_id}/spec`
- `GET /forge/jobs/{job_id}/logs`
- `GET /forge/jobs/{job_id}/events`
- `POST /forge/jobs/{job_id}/cancel`
- `POST /forge/jobs/{job_id}/clone`
- `POST /forge/jobs/{job_id}/retry`
- `GET /forge/artifacts/{artifact_id}`
- `GET /forge/artifacts/{artifact_id}/file`
- `GET /forge/artifacts/{artifact_id}/metadata`
- `GET /forge/artifacts/{artifact_id}/verify`
- `GET /forge/artifacts/{artifact_id}/thumbnail`
- `GET /forge/gallery`
- `GET /forge/memory/status`
- `GET /forge/memory/policy`
- `GET /forge/memory/gateway`
- `GET /forge/memory/catalog`
- `GET /forge/memory/search`
- `GET /forge/memory/events`
- `GET /forge/memory/proposals`
- `POST /forge/memory/propose`

Example plan request:

```bash
curl -s http://127.0.0.1:8110/forge/plan \
  -H 'content-type: application/json' \
  -d '{"request":"Нарисуй кинематографичный портрет демона в кузнице, вертикально"}'
```

Set `"use_memory":false` in plan requests for fast/offline planning without
ArchiveOfHeresy memory search.
Set `"use_thinker":false` to force the deterministic heuristic planner. By
default, `/forge/plan` may use the optional planner thinker when it is enabled
and configured.

Engine policy:

- `stable_diffusion` / SD3.5 and `flux` are concept engines for first
  text-to-image generation.
- `sdxl` is the workhorse for operations on existing images: `img2img`,
  `inpaint`, future `outpaint`, variation/refinement, LoRA and control workflows.
- The planner defaults plain `txt2img` requests to SD3.5 when available, then
  Flux, then SDXL. Explicit engine requests are respected when that engine
  supports the requested job type.

Example txt2img job:

```bash
curl -s http://127.0.0.1:8110/forge/jobs \
  -H 'content-type: application/json' \
  -d '{
    "type":"txt2img",
    "engine":"sdxl",
    "model":"stable-diffusion-xl-base-1.0",
    "prompt":"cinematic forge, dramatic light",
    "negative_prompt":"low quality, blurry",
    "width":512,
    "height":512,
    "steps":2,
    "guidance":5.0,
    "sampler":"default",
    "scheduler":"native",
    "seed":123
  }'
```

Validate a job without queueing it:

```bash
curl -s 'http://127.0.0.1:8110/forge/jobs?dry_run=true' \
  -H 'content-type: application/json' \
  -d '{"type":"txt2img","engine":"sdxl","prompt":"dry run","width":512,"height":512,"steps":1}'
```

Dry-run responses include a conservative CPU-only resource estimate with pixel
budget ratio, estimated RAM floor, loaded engine state, and warnings.
Job listing supports `status`, `engine`, `job_type`, and `limit` query filters.
The same validation runs before normal job submission, including scheduler,
sampler, LoRA availability, embeddings, control hooks, source-image checks, and
backend capability gates.
`metadata-read` stays inside the DemonsForge tree and records file size,
SHA-256, MIME type, image dimensions/info, and small JSON sidecars.
Existing jobs can be cloned or retried without copying records in SQLite
manually. Clone accepts `overrides` and keeps the original seed by default;
set `reuse_seed:false` to request a new random seed for supported generative
jobs. Both clone and retry support `?dry_run=true`.
SDXL diffusion jobs (`txt2img`, `img2img`, `inpaint`) are validated at
`512x512` minimum because smaller sizes can fail inside the pipeline.
`img2img` and `inpaint` also require `steps * strength >= 1`, since very low
strength with one step can produce zero denoising timesteps.
Real CPU-only SDXL smoke checks have passed for `img2img` and `inpaint` at
`512x512`, `steps=1`, `strength=1.0`.
On the Threadripper 3970X / 128GB DDR4 runtime, full CPU-only Forge checks also
passed with real generation:

- SDXL `txt2img` `512x512`, `steps=6`: about 25 seconds.
- SDXL `img2img` `512x512`, `steps=4`: about 15 seconds.
- SDXL `inpaint` `512x512`, `steps=4`: about 15 seconds.
- FLUX Schnell `txt2img` `512x512`, `steps=1`: about 80 seconds.
- SD3.5 Large `txt2img` `512x512`, `steps=1`: about 60 seconds.

Architecture:

- `forge_service/registries.py`: engine, model, LoRA, sampler, scheduler and
  capability discovery. Known engines are registered explicitly; additional
  local model folders with `model_index.json` are surfaced as discovered models.
  `/forge/capabilities` exposes implemented job types, service-level jobs,
  unsupported job types, future feature hooks, and per-engine feature flags.
- `forge_service/queue.py`: single-worker VRAM/RAM-aware job queue with
  progress logs, cancellation state, runtime status and idle model unload. It
  can run embedded in the API process or as a separate worker process polling
  SQLite.
- `forge_service/storage.py`: SQLite job and gallery store at
  `runtime/forge.sqlite3`.
- `forge_service/engines/`: backend adapters. The current vertical slice uses a
  lazy diffusers adapter for `txt2img`; unsupported operations fail explicitly.
  Diffusers step callbacks are used when available for live progress and
  cooperative cancellation between inference steps.
- `forge_service/planner.py`: Russian natural-language planner that returns a
  valid structured job spec. Missing model/LoRA/control assets become
  `asset_request` objects requiring user approval.
  It recognizes engine hints, explicit dimensions like `512x768`, `steps`,
  `seed`, negative prompts and local LoRA references like `lora:name@0.8`.
  It also emits a structured `quality_preset` such as `smoke`, `draft`,
  `quality`, `edit_soft`, `edit_strong`, or `inpaint_precise` to make planner
  tradeoffs visible to clients and artifact metadata.
  It also performs fail-soft read-only memory search through the `demonsforge`
  namespace and stores compact planning hints in `spec.safety.memory_context`.
  It recognizes `txt2img`, `img2img`, `inpaint`, and `upscale` intent; image
  editing plans include `planner_note` reminders for required source/mask
  inputs instead of pretending those assets exist.
- `forge_service/thinker.py`: optional OpenAI-compatible planner thinker. It is
  advisory only: the deterministic planner builds the baseline plan first, the
  thinker can return a compact JSON patch, and Forge filters plus validates that
  patch back into a `JobSpec`. Invalid thinker output falls back to the baseline
  plan and is reported in `spec.safety.planner_thinker`.
- `forge_service/downloader.py`: controlled asset downloader abstraction. It
  accepts only approved jobs, stores source/license/hash metadata, keeps files
  inside DemonsForge, rejects unverified hosts, validates SHA-256 format, writes
  through `.part` files, and enforces `FORGE_MAX_ASSET_DOWNLOAD_BYTES`.
- `forge_service/client.py`: thin client intended for later ShushunyaAgent tool
  integration.

Runtime logs are appended as JSONL to `runtime/logs/jobs.jsonl`. Loaded
diffusers pipelines are automatically unloaded after
`FORGE_MODEL_IDLE_SECONDS` seconds, default `1800`, to return RAM to the rest of
the system.
The runtime SQLite store uses WAL mode and a busy timeout so the HTTP API and
an external worker can read/write the job database concurrently with fewer
`database locked` failures.
Use `POST /forge/runtime/unload` or `POST /forge/runtime/unload?engine=sdxl` to
release loaded pipelines immediately.
Queue pause/resume is stored in Forge runtime SQLite, so both embedded and
separate worker modes observe the same pause flag.

Generated outputs are stored under `artifacts/{job_id}/` with PNG files and JSON
metadata containing prompt, negative prompt, engine, model, LoRA list, seed,
dimensions, sampler, steps, guidance/CFG, source images, creation time and job
id.
Gallery supports filters: `q`, `engine`, `model`, `job_type`, `kind`, and
`limit`.

`prompt-enhance` and `metadata-read` are implemented as lightweight CPU-only job
types. `prompt-enhance` produces a deterministic enhanced prompt metadata
artifact; `metadata-read` reads image dimensions, embedded PIL metadata and
adjacent `.json` sidecars into a metadata artifact.

`upscale` is implemented as a lightweight CPU-only PIL/Lanczos job. SDXL
`img2img` and `inpaint` have real diffusers adapter hooks and require local
source images, with `inpaint` also requiring `mask_image`. SQLite uses
`PRAGMA user_version` for schema versioning.
SDXL LoRA adapters are loaded once per active pipeline and then re-weighted via
`set_adapters`, avoiding repeated adapter loads for reused pipelines.

## ArchiveOfHeresy memory

DemonsForge uses ArchiveOfHeresy as its long-term memory. Its own SQLite
database remains only a runtime/job/gallery store for jobs, artifacts,
downloads and logs. Forge code and agents must not read or write ArchiveOfHeresy
memory files directly.

Default memory config:

```bash
FORGE_MEMORY_ENABLED=1
FORGE_MEMORY_NAMESPACE=demonsforge
FORGE_MEMORY_REQUESTER=demonsforge
FORGE_ARCHIVE_BASE_URL=http://127.0.0.1:8090
FORGE_ARCHIVE_API_KEY=...
FORGE_MEMORY_READ_TIMEOUT_SECONDS=5
FORGE_MEMORY_WRITE_TIMEOUT_SECONDS=30
```

`start-forge-api.sh` and `start-forge-worker.sh` source
`../ArchiveOfHeresy/.env` first and then optional local `DemonsForge/.env`, so
Forge can reuse `ARCHIVE_API_KEY` without copying the secret into this module.

The Forge API exposes thin proxy endpoints over the ArchiveOfHeresy Memory
Gateway:

```text
GET  /forge/memory/status
GET  /forge/memory/policy
GET  /forge/memory/gateway
GET  /forge/memory/catalog?create=true
GET  /forge/memory/search?q=sdxl&layers=focus,wiki,vector,graph&limit=5
GET  /forge/memory/events?limit=20
GET  /forge/memory/proposals?limit=100
POST /forge/memory/propose
```

`POST /forge/memory/propose` forwards a proposal to
`/archive/memory/propose-change` with namespace `demonsforge` and requester
`demonsforge`. The archive stores the proposal and the librarian decides how to
integrate it into focus/wiki/vector/graph memory.
Forge keeps a local proposal hash in its runtime SQLite store to avoid sending
the same durable proposal twice, including uncertain timeout cases where
ArchiveOfHeresy may already have accepted the write.
Use `POST /forge/memory/propose?dry_run=true` to compute the proposal hash and
duplicate status without writing to ArchiveOfHeresy.

Example:

```bash
curl -s http://127.0.0.1:8110/forge/memory/propose \
  -H 'content-type: application/json' \
  -d '{
    "target":"auto",
    "importance":3,
    "proposal":"SD3.5/Flux are preferred concept engines for first text-to-image jobs, while SDXL is the preferred workhorse for image editing and refinement jobs.",
    "evidence":"Forge runtime is CPU-only on Threadripper 3970X / 128GB RAM; SD3.5, Flux, and SDXL full CPU tests succeeded, and SDXL supports img2img/inpaint workflows."
  }'
```

Remember durable forge facts only:

- hardware/runtime policy, especially that DemonsForge is CPU-only and should
  not reserve GPU memory;
- selected default engines, models, schedulers and samplers;
- stable user style preferences;
- approved or rejected assets;
- locally available models, LoRAs, embeddings, control assets and adapters;
- repeated failures with useful workarounds;
- Forge API architecture decisions.

Do not write ordinary runtime noise into memory:

- progress events;
- every job status change;
- one-off seed/job ids unless they explain a durable decision;
- noisy prompt variants;
- large gallery metadata blobs.

Asset download success/rejection is proposed automatically because it changes
the durable local asset set. Other memory writes should be explicit proposals.

## Planner thinker

The default planner is deterministic and works without an LLM. To connect a
local or remote OpenAI-compatible thinking model for richer Russian request
interpretation, set:

```bash
FORGE_PLANNER_THINKER_ENABLED=1
FORGE_PLANNER_THINKER_BASE_URL=http://127.0.0.1:8000/v1
FORGE_PLANNER_THINKER_API_KEY=optional-key
FORGE_PLANNER_THINKER_MODEL=local-thinking-model
FORGE_PLANNER_THINKER_TIMEOUT_SECONDS=20
```

Status:

```bash
curl -s http://127.0.0.1:8110/forge/planner/thinker | python3 -m json.tool
```

The thinker never executes jobs and never downloads assets. It can only suggest
allowed `JobSpec` fields. Missing models, LoRA, embeddings, ControlNet,
IP-Adapter, or reference assets must become an `asset_request` with
`requires_user_approval=true`.

Smoke test without heavy image generation:

```bash
DemonsForge/bin/python tests/smoke_forge_api.py
```

Long live-API planner/dry-run matrix:

```bash
DemonsForge/bin/python tests/long_forge_api.py --cycles 20
```

Long-test JSON reports are written under `runtime/test-reports/` unless
`--report-json` is supplied.

Optional real CPU SDXL generation smoke:

```bash
DemonsForge/bin/python tests/long_forge_api.py --cycles 5 --generate
```

Optional CPU SDXL quality probe:

```bash
FORGE_EMBEDDED_WORKER=0 ./start-forge-api.sh
FORGE_WORKER_MAX_JOBS=3 ./start-forge-worker.sh
DemonsForge/bin/python tests/long_forge_api.py --cycles 1 --quality-generate
```

This runs real `txt2img`, `img2img`, and `inpaint` jobs, writes a JSON report
under `runtime/test-reports/`, and creates a contact sheet next to the report.
For inpaint, the report includes masked and unmasked mean absolute differences
so prompt-following can be judged separately from whether the pipeline merely
produced an image.

Memory gateway diagnostic:

```bash
./check-forge-memory.sh "DemonsForge CPU GPU memory"
```
