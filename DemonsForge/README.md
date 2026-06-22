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

Core endpoints:

- `GET /health`
- `GET /forge/capabilities`
- `GET /forge/runtime`
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

Architecture:

- `forge_service/registries.py`: engine, model, LoRA, sampler, scheduler and
  capability discovery.
- `forge_service/queue.py`: single-worker VRAM/RAM-aware job queue with
  progress logs, cancellation state, runtime status and idle model unload.
- `forge_service/storage.py`: SQLite job and gallery store at
  `runtime/forge.sqlite3`.
- `forge_service/engines/`: backend adapters. The current vertical slice uses a
  lazy diffusers adapter for `txt2img`; unsupported operations fail explicitly.
- `forge_service/planner.py`: Russian natural-language planner that returns a
  valid structured job spec. Missing model/LoRA/control assets become
  `asset_request` objects requiring user approval.
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

Smoke test without heavy image generation:

```bash
DemonsForge/bin/python tests/smoke_forge_api.py
```
