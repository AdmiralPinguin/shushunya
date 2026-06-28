# EyeOfTerror

EyeOfTerror is the command layer above the Mechanicum workers.

It is not a worker and should not execute long specialist work directly. Its
job is to accept user chat/tasks, pick the right Inner Circle governor, track
the task state, and return status/results to the user.

## Ports

| Port | Service | Role |
| --- | --- | --- |
| 7000 | Warmaster Gateway | User-facing chat/orchestration entrypoint |
| 7101 | Iskandar Khayon | Lore, research, reconstruction task governor |

Mechanicum workers use ports `7001+`. Legacy backends may keep their existing
ports while relay workers adapt them to the common worker API.

## Routing Rule

The Warmaster Gateway should only do top-level routing:

1. Accept a user message.
2. Decide whether it is chat, status, cancellation, or a task.
3. For tasks, create a task contract.
4. Assign one Inner Circle governor.
5. Let the governor coordinate Mechanicum workers.

The gateway should not micromanage individual worker steps.

Run the gateway:

```bash
PYTHONPATH=EyeOfTerror python3 -m eye_of_terror.warmaster_gateway
```

Gateway endpoints:

- `GET /health`
- `GET /capabilities`
- `GET /state`
- `GET /doctor`
- `GET /governors`
- `GET /governors?health=1`
- `GET /workers`
- `GET /workers?health=1`
- `POST /task`
- `GET /runs`
- `GET /runs/<task_id>`
- `GET /runs/<task_id>/summary`
- `GET /runs/<task_id>/ledger`
- `GET /runs/<task_id>/contract`
- `GET /runs/<task_id>/dispatch`
- `GET /runs/<task_id>/worker_tasks`
- `GET /runs/<task_id>/events`
- `GET /runs/<task_id>/artifacts`
- `GET /runs/<task_id>/artifact_text?path=/work/...`
- `POST /runs/<task_id>/execute_local`
- `POST /runs/<task_id>/execute_http`
- `POST /runs/<task_id>/start_local`
- `POST /runs/<task_id>/start_http`
- `POST /runs/<task_id>/cancel`
- `POST /recover_stale`

Client-facing gateway behavior is specified in
`EyeOfTerror/contracts/warmaster_api.md`.

`GET /workers` returns the static port registry enriched with available
`Mechanicum/*/worker.json` metadata. Add `?health=1` to include a live
best-effort `/health` snapshot for each worker service.

`GET /state` is the preferred client bootstrap endpoint after an app restart.
It returns gateway capabilities, governors, workers, recent runs, and run status
counts in one response.

`GET /runs/<task_id>/worker_tasks` maps a Warmaster run to the task ids sent to
Mechanicum workers. Add `?live=1` for a best-effort lookup against worker
runtime task endpoints.

`GET /doctor` checks registry, manifest, and service consistency and is safe to
call from an admin client.

`POST /runs/<task_id>/cancel` marks the Warmaster ledger as cancelling and
best-effort forwards cancellation to HTTP worker task endpoints from the run
dispatch package.

## Iskandar Service

Run the first Inner Circle governor:

```bash
PYTHONPATH=EyeOfTerror python3 -m eye_of_terror.inner_circle.iskandar_service
```

It exposes:

- `GET /health`
- `GET /capabilities`
- `POST /plan`
- `POST /prepare_run`

Governor services should follow `EyeOfTerror/contracts/governor_api.md`.

## Local Prototype Run

Run a fast registry and manifest doctor:

```bash
python3 EyeOfTerror/doctor.py
```

## Local Prototype Run

Build an Iskandar run package:

```bash
PYTHONPATH=EyeOfTerror python3 -m eye_of_terror.inner_circle.iskandar \
  'Собери все известное о событиях Скалатракса и сделай реконструкцию.' \
  --task-id test-skalathrax \
  --run-dir runtime/iskandar-test
```

Execute registered local prototype workers:

```bash
PYTHONPATH=EyeOfTerror python3 -m eye_of_terror.local_executor \
  runtime/iskandar-test \
  --workspace-root runtime/eye-local-work
```

Execute through already running worker services on their dispatch ports:

```bash
PYTHONPATH=EyeOfTerror python3 -m eye_of_terror.http_executor runtime/iskandar-test
```
