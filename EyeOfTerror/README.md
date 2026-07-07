# EyeOfTerror

EyeOfTerror is the command layer above the Mechanicum workers.

It is not a worker and should not execute long specialist work directly. Its
job is to accept user chat/tasks, pick the right Inner Circle governor, track
the task state, and return status/results to the user.

See `EyeOfTerror/ARCHITECTURE.md` for the layer boundaries and registry rules.

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
PYTHONPATH=EyeOfTerror/Warmaster python3 -m eye_of_terror.warmaster_gateway
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
- `POST /task_preflight`
- `POST /orchestrate`
- `POST /orchestrate_start`
- `POST /orchestrate_run`
- `POST /task` diagnostic legacy only, requires `allow_legacy_direct_task=true`
- `GET /runs`
- `GET /runs/<task_id>`
- `GET /runs/<task_id>/summary`
- `GET /runs/<task_id>/snapshot`
- `GET /runs/<task_id>/active`
- `GET /runs/<task_id>/steps/<step_id>`
- `GET /runs/<task_id>/ledger`
- `GET /runs/<task_id>/contract`
- `GET /runs/<task_id>/dispatch`
- `GET /runs/<task_id>/worker_tasks`
- `GET /runs/<task_id>/events`
- `GET /runs/<task_id>/events?after=N`
- `GET /runs/<task_id>/artifacts`
- `GET /runs/<task_id>/artifact_text?path=/work/...`
- `POST /runs/<task_id>/preflight_local`
- `POST /runs/<task_id>/preflight_http`
- `POST /runs/<task_id>/execute_local`
- `POST /runs/<task_id>/execute_http`
- `POST /runs/<task_id>/execute_revision_local`
- `POST /runs/<task_id>/execute_revision_http`
- `POST /runs/<task_id>/resume_local`
- `POST /runs/<task_id>/resume_http`
- `POST /runs/<task_id>/start_local`
- `POST /runs/<task_id>/start_http`
- `POST /runs/<task_id>/start_revision_local`
- `POST /runs/<task_id>/start_revision_http`
- `POST /runs/<task_id>/start_resume_local`
- `POST /runs/<task_id>/start_resume_http`
- `POST /runs/<task_id>/cancel`
- `POST /recover_stale`

Client-facing gateway behavior is specified in
`EyeOfTerror/Warmaster/contracts/warmaster_api.md`.

`POST /task_preflight` checks routing, governor planning, contract validation,
worker availability, and the compact planned step summary without creating run
history.

Execution endpoints accept optional `step_ids` when a governor needs to run a
validated subset of a prepared pipeline. Successful partial execution leaves the
run `interrupted` so the remaining work is resumed instead of silently marked
complete.

`GET /workers` returns the static port registry enriched with available
`Mechanicum/*/worker.json` metadata. Add `?health=1` to include a live
best-effort `/health` snapshot for each worker service.

`GET /governors?health=1` includes best-effort governor `/health` snapshots and,
when reachable, governor `/capabilities` payloads such as `required_workers`.

`GET /state` is the preferred client bootstrap endpoint after an app restart.
It returns gateway capabilities, governors, workers, recent runs, and run status
counts in one response. Gateway action hints identify the preferred task flow:
`POST /orchestrate_run`, then poll `GET /runs/<task_id>/orchestration` or
`GET /runs/<task_id>/snapshot`.

`GET /runs/<task_id>/snapshot` is the preferred per-run polling endpoint for
clients. It returns summary, process-local active state, cursor event updates,
and artifact metadata in one response.

Run summaries expose `actions` hints for client controls such as start, cancel,
resume, and revision execution.

Run progress exposes ordered step ids, completed/failed/pending step ids,
`next_step_id`, and `step_states` for client progress displays and future
partial resume logic. Step states include file status for expected and produced
artifacts when a run workspace is known.

`GET /runs/<task_id>/steps/<step_id>` returns one normalized step state from the
same progress model.
`GET /runs/<task_id>/steps/<step_id>/artifacts` returns the expected and
produced artifact status for that worker step only.

On startup, Warmaster Gateway marks stale `running` or `cancelling` ledgers as
`interrupted`, so clients can resume runs after a gateway restart without first
calling a maintenance endpoint. Pass `--no-recover-stale-on-start` to disable
that behavior for diagnostics.

Normal user tasks must enter through `POST /orchestrate_run`, which creates a
Warmaster `commander_order`, assigns a governor, prepares the run, and starts or
returns the next gated action. `POST /task` is retained only as a diagnostic
legacy endpoint and rejects calls unless `allow_legacy_direct_task=true` is
present.
Passing `governor_transport: "http"` makes Warmaster call the selected active
governor service on its registry port, keeping the planning boundary compatible
with future Inner Circle services.
Before preparing an HTTP-governor run, Warmaster checks reachable governor
`required_workers` against the Mechanicum registry.
Warmaster also rejects any produced task contract whose `worker_plan` references
workers absent from the Mechanicum registry.

Warmaster Gateway can also be started with `--governor-transport http` and
`--governor-host 127.0.0.1` so ordinary `POST /orchestrate_run` submissions use
governor services by default.

`GET /brigade_plan` and `GET /state` expose the expected service-separated
brigade topology, including Warmaster, Iskandar, and registered Mechanicum
workers.
`GET /brigade_health` combines that topology with best-effort service health
checks and reports whether reachable governor `required_workers` are present in
the Mechanicum registry.
`GET /state?health=1` embeds the same health snapshot for admin bootstrap while
plain `/state` remains lightweight.

Start the current service-separated brigade with:

```bash
PYTHONPATH=EyeOfTerror/Warmaster:Mechanicum python3 EyeOfTerror/Warmaster/start_brigade.py --repo-root .
```

Use `--dry-run` to print the Mechanicum worker supervisor, Iskandar service, and
Warmaster Gateway commands without starting them.
Use `--json` to print the same startup plan in a machine-readable form for
diagnostics or future admin clients. The JSON includes top-level services and
the individual Mechanicum worker names, ports, modules, service dependencies,
and readiness URLs.
Use `--wait-ready` to wait for top-level health URLs after starting the stack;
`--ready-timeout-sec` controls the readiness timeout. Readiness covers
Warmaster, Iskandar, and registered Mechanicum worker health URLs.
If one managed process exits, the launcher terminates the remaining managed
processes and returns the first exit code.
Before starting, the launcher checks managed ports and refuses to start over a
busy port. Use `--skip-port-check` only for diagnostics.

`GET /runs/<task_id>/artifacts` expands `final_manifest.json` package files so
clients can fetch the final reconstruction, reports, and manifest through the
same artifact text endpoint.

Revision execution endpoints use a failed/blocked run's `revision_plan` and run
only the requested rework steps, then `critic_review` and `finalize`. Revision
reruns pass focused `revision_context` into the selected worker request; writer,
critic, and finalizer artifacts preserve that focus as `revision_focus`.

Research loop endpoints (`research_loop_local`, `research_loop_http`, and their
`start_` background variants) let Warmaster run start/resume/revision decisions
until a final package is ready or a bounded stop condition is hit. Use
`max_revision_cycles` to cap automatic rework.

Resume endpoints run only the pending steps of an `interrupted` package through
local or HTTP execution and reject non-interrupted runs.

Local corpus files live in `Corpus/` or `SHUSHUNYA_CORPUS_DIR`. Put optional
sidecar metadata next to a file as `<file>.metadata.json`, `<file>.meta.json`,
or `<file>.epub.json`/`<file>.txt.json`; useful fields include `title`,
`language`, `type`, `source_class`, `reliability`, `direct_event_detail_level`,
`tags`, `aliases`, and `expected_use`.

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
PYTHONPATH=EyeOfTerror/Warmaster python3 -m eye_of_terror.inner_circle.iskandar_service
```

It exposes:

- `GET /health`
- `GET /capabilities`
- `POST /plan`
- `POST /prepare_run`

Governor services should follow `EyeOfTerror/Warmaster/contracts/governor_api.md`.

## Local Prototype Run

Run a fast registry and manifest doctor:

```bash
python3 EyeOfTerror/Warmaster/doctor.py
```

## Local Prototype Run

Build an Iskandar run package:

```bash
PYTHONPATH=EyeOfTerror/Warmaster python3 -m eye_of_terror.inner_circle.iskandar \
  'Собери все известное о событиях Скалатракса и сделай реконструкцию.' \
  --task-id test-skalathrax \
  --run-dir runtime/iskandar-test
```

Execute registered local prototype workers:

```bash
PYTHONPATH=EyeOfTerror/Warmaster python3 -m eye_of_terror.local_executor \
  runtime/iskandar-test \
  --workspace-root runtime/eye-local-work
```

Execute through already running worker services on their dispatch ports:

```bash
PYTHONPATH=EyeOfTerror/Warmaster python3 -m eye_of_terror.http_executor runtime/iskandar-test
```
