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
```

By default the API starts an embedded worker thread. To isolate generation from
the HTTP process:

```bash
FORGE_EMBEDDED_WORKER=0 ./start-forge-api.sh
./start-forge-worker.sh
```

Core endpoints:

- `GET /health`
- `GET /forge/capabilities`
- `GET /forge/runtime`
- `GET /forge/schema/job`
- `GET /forge/models`
- `GET /forge/loras`
- `GET /forge/assets/downloads`
- `POST /forge/plan`
- `POST /forge/jobs`
- `GET /forge/jobs`
- `GET /forge/jobs/{job_id}`
- `GET /forge/jobs/{job_id}/events`
- `POST /forge/jobs/{job_id}/cancel`
- `GET /forge/artifacts/{artifact_id}`
- `GET /forge/artifacts/{artifact_id}/thumbnail`
- `GET /forge/gallery`
- `GET /forge/memory/status`
- `GET /forge/memory/gateway`
- `GET /forge/memory/catalog`
- `GET /forge/memory/search`
- `GET /forge/memory/events`
- `POST /forge/memory/propose`

Example plan request:

```bash
curl -s http://127.0.0.1:8110/forge/plan \
  -H 'content-type: application/json' \
  -d '{"request":"Нарисуй кинематографичный портрет демона в кузнице, вертикально"}'
```

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
SDXL diffusion jobs (`txt2img`, `img2img`, `inpaint`) are validated at
`512x512` minimum because smaller sizes can fail inside the pipeline.
`img2img` and `inpaint` also require `steps * strength >= 1`, since very low
strength with one step can produce zero denoising timesteps.

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
  It also performs fail-soft read-only memory search through the `demonsforge`
  namespace and stores compact planning hints in `spec.safety.memory_context`.
- `forge_service/downloader.py`: controlled asset downloader abstraction. It
  accepts only approved jobs, stores source/license/hash metadata, keeps files
  inside DemonsForge, and rejects unverified hosts.
- `forge_service/client.py`: thin client intended for later ShushunyaAgent tool
  integration.

Runtime logs are appended as JSONL to `runtime/logs/jobs.jsonl`. Loaded
diffusers pipelines are automatically unloaded after
`FORGE_MODEL_IDLE_SECONDS` seconds, default `1800`, to return RAM to the rest of
the system.

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
    "proposal":"SDXL is the preferred default engine for CPU-only txt2img smoke jobs.",
    "evidence":"Forge runtime is CPU-only; SDXL low-step smoke succeeded; GPU is reserved for the main LLM."
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

Smoke test without heavy image generation:

```bash
DemonsForge/bin/python tests/smoke_forge_api.py
```

Memory gateway diagnostic:

```bash
./check-forge-memory.sh "DemonsForge CPU GPU memory"
```
