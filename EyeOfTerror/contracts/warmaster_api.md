# Warmaster API Contract

Warmaster Gateway is the user-facing orchestration entrypoint. Clients should
use it as the source of truth for task submission, state bootstrap, run
inspection, cancellation, and service diagnostics.

## Core Endpoints

```text
GET  /health
GET  /capabilities
GET  /state
GET  /state?health=1
GET  /doctor
GET  /brigade_plan
GET  /brigade_plan?host=127.0.0.1
GET  /brigade_health
GET  /brigade_health?host=127.0.0.1
GET  /governors
GET  /governors?health=1
GET  /workers
GET  /workers?health=1
POST /task_preflight
POST /task
GET  /runs
GET  /runs?limit=20
GET  /runs/{task_id}
GET  /runs/{task_id}/summary
GET  /runs/{task_id}/snapshot
GET  /runs/{task_id}/active
GET  /runs/{task_id}/steps/{step_id}
GET  /runs/{task_id}/steps/{step_id}/artifacts
GET  /runs/{task_id}/ledger
GET  /runs/{task_id}/contract
GET  /runs/{task_id}/dispatch
GET  /runs/{task_id}/worker_tasks
GET  /runs/{task_id}/worker_tasks?live=1
GET  /runs/{task_id}/events
GET  /runs/{task_id}/events?limit=20
GET  /runs/{task_id}/events?after=0
GET  /runs/{task_id}/artifacts
GET  /runs/{task_id}/artifact_text?path=/work/...
GET  /runs/{task_id}/artifact_text?path=/work/...&max_bytes=1000
POST /runs/{task_id}/preflight_local
POST /runs/{task_id}/preflight_http
POST /runs/{task_id}/execute_local
POST /runs/{task_id}/execute_http
POST /runs/{task_id}/execute_revision_local
POST /runs/{task_id}/execute_revision_http
POST /runs/{task_id}/resume_local
POST /runs/{task_id}/resume_http
POST /runs/{task_id}/start_local
POST /runs/{task_id}/start_http
POST /runs/{task_id}/start_revision_local
POST /runs/{task_id}/start_revision_http
POST /runs/{task_id}/start_resume_local
POST /runs/{task_id}/start_resume_http
POST /runs/{task_id}/cancel
POST /recover_stale
```

On normal server startup, Warmaster creates the run root and marks stale
`running`/`cancelling` ledgers as `interrupted`. Operators can disable this with
`--no-recover-stale-on-start` for diagnostics.

Operators can choose the default governor planning boundary with
`--governor-transport local|http` and `--governor-host`. Per-request
`governor_transport` and `governor_host` override those defaults.

## Client Bootstrap

Clients should call `GET /state` after startup or reconnect. The response
contains gateway capabilities, governor registry, worker registry, run status
counts, process-local active run ids, recent run summaries, and the expected
service-separated brigade startup plan. `state.actions` and
`capabilities.actions` expose client-facing gateway action hints, including the
preferred task flow: preflight, create, then start.
Use `GET /state?health=1` for an admin/bootstrap snapshot that also includes
best-effort `brigade_health`; plain `/state` stays lightweight for polling.

`GET /brigade_plan` returns the same expected service topology without run
history. The optional `host` query parameter must be loopback. The response
includes `startup_stages` so admin clients can start dependency-free services
first, wait for their health URLs, then start dependent services.

`GET /brigade_health` combines that topology with best-effort health checks for
governors and workers. It also reports governor worker requirements when a
reachable governor exposes `required_workers` from `/capabilities`.

`GET /governors?health=1` includes reachable governor `/capabilities` payloads
inside each governor runtime snapshot.

## Task Creation

`POST /task_preflight` accepts the same routing fields as `POST /task`, but does
not write a run package or ledger. It returns the selected route, governor,
contract validation result, missing worker references, and the run directory
that would be created. It also returns a compact `contract_summary` with planned
step ids, workers, dependency edges, expected artifact paths, and artifact
counts for client review.

```json
{
  "message": "User task text",
  "task_id": "optional-stable-id",
  "governor_transport": "local|http",
  "governor_host": "optional-loopback-host"
}
```

Task ids are durable run identifiers. Creating a task with an existing `task_id`
must return a conflict instead of overwriting the run history.

If provided, `task_id` must match `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}` and must
not contain `..`.

`governor_transport` defaults to `local`. When set to `http`, Warmaster calls
the selected active governor service on its registry port and writes the
Warmaster ledger after the governor prepares the run package. `governor_host`
must be loopback. If the gateway was started with `--governor-transport http`,
clients can omit this field and still use the service-separated path.

For HTTP governor planning, Warmaster reads reachable governor capabilities and
rejects the task with `error_code=governor_workers_missing` when
`required_workers` are absent from the Mechanicum registry.

For every planning path, Warmaster rejects a produced task contract with
`error_code=contract_workers_missing` when `worker_plan` references workers that
are absent from the Mechanicum registry.

When routing terms match a planned but inactive governor, task creation and task
preflight return `error_code=governor_inactive`, the matched `governor`, `kind`,
and a compact `route` object. When no route matches, they return
`error_code=no_supported_governor`.

## Run Inspection

Clients should use:

- `/runs/{task_id}/summary` for lightweight polling.
- `/runs/{task_id}/snapshot` for a compact polling view containing summary,
  process-local active state, cursor events, and artifact metadata.
- `/runs/{task_id}/steps/{step_id}` for one normalized step state from
  `summary.progress.step_states`.
- `/runs/{task_id}/steps/{step_id}/artifacts` for expected and produced
  artifact status scoped to one worker step.
- `/runs/{task_id}/events` for ledger event history.
- `/runs/{task_id}/events?after=N` for incremental client polling. Responses
  include `cursor.after`, `cursor.next`, and `cursor.total`.
- `/runs/{task_id}/artifacts` for result artifact metadata. If the final result
  is a `final_manifest.json`, the response should also expand manifest `files`
  so clients can list and fetch the whole final package.
- `/runs/{task_id}/contract` and `/runs/{task_id}/dispatch` for orchestration
  debugging.
- `/runs/{task_id}/worker_tasks?live=1` when worker services are running and
  live worker task state is needed.

Run summaries include an `actions` object with client-facing booleans:
`can_preflight_local`, `can_preflight_http`, `can_execute`, `can_start`,
`can_cancel`, `can_resume`, `can_execute_revision`, `can_start_revision`, and
`force_required_for_rerun`. Clients should use these hints for button state
instead of duplicating Warmaster status rules. When `revision_plan.required` is
true, ordinary `can_execute` and `can_start` are false; clients should use the
revision actions instead. When a run is `interrupted`, ordinary `can_execute`
and `can_start` are false; clients should use resume actions instead.
Revision actions are false when the required `revision_plan` is structurally
invalid or references workers that do not match the run dispatch package;
summaries expose these diagnostics as `revision_plan_errors`.

`actions.next_action` gives chat clients and higher-level governors one
recommended next operation with `kind`, `method`, `endpoint`, `body`, and
`reason`. It must prefer resume for interrupted runs, revision execution for
valid required revisions, revision inspection for invalid revision plans,
polling for active runs, and force-gated rerun guidance for completed runs. When
the recommendation is a completed-run rerun, `body.force` must be true.

Run summaries also expose `last_preflight` when the ledger has a recorded run
preflight. It contains the event timestamp, mode, selected steps, result, and
failure counters so clients do not have to parse the full event stream for the
latest startup check.

Run summaries also include ordered progress hints:
`planned_step_ids`, `completed_step_ids`, `failed_step_ids`,
`pending_step_ids`, `ready_step_ids`, `blocked_step_ids`, `waiting_step_ids`,
`next_step_id`, `next_ready_step_id`, and `step_states`. These are derived from
the run package and ledger records, so clients can display restart/resume
position, worker ownership, dependency readiness, expected artifacts, produced
artifacts, and per-step summaries without parsing dispatch packets. Progress
also includes count fields for pending, ready, blocked, and waiting steps. Step
states include
`depends_on`, `dependency_status`, `dependencies_ready`, `dependencies_blocked`,
`input_artifacts`, `expected_artifacts`, and produced `artifacts`; each artifact
set also has status entries with `exists`, `bytes`, and `host_path` when a run
workspace is known.

## Cancellation

`POST /runs/{task_id}/cancel` marks the Warmaster ledger as cancelling and
best-effort forwards cancellation to HTTP worker task endpoints from the run
dispatch package. Cancellation is cooperative unless a worker implements a
stronger interruption mechanism.

## Execution Paths

`POST /runs/{task_id}/preflight_local` and
`POST /runs/{task_id}/preflight_http` inspect an existing run package without
executing workers. They report unreadable dispatch packets, missing local worker
commands, HTTP worker health failures, and input artifact failures. Full-run
preflight treats artifacts produced by earlier selected steps as satisfiable by
the same run; restricted preflight can accept `step_ids` and then requires
unselected dependency artifacts to already exist when a `workspace_root` is
provided. Each run preflight records a compact `run_preflight_recorded` ledger
event with mode, selected steps, result, and failure counts.

Execution endpoints also accept optional `step_ids` for orchestrator-controlled
step subsets. `step_ids` must be a list of unique non-empty strings and every
requested id must exist in the run package. Unknown or duplicate step ids must
be rejected before dispatch. For `resume_*` and `*_revision_*` endpoints, a
client-provided subset must stay inside the automatically eligible resume or
revision step set.

When a selected subset succeeds but does not cover the whole run package, the
executor response includes `partial_execution=true`, the requested step ids, and
the execution mode. The ledger must remain `interrupted` instead of
`completed`, so clients and higher-level governors continue through resume
actions rather than treating a partial worker call as a finished user task.

If execution endpoints accept `workspace_root`, that path must stay inside the
selected run directory. Relative paths are resolved below the run directory.

If execution, cancellation, or live worker task endpoints accept `host`, it must
be a loopback worker service host such as `127.0.0.1` or `localhost`.

Revision execution endpoints use the current run ledger `revision_plan` and run
only those dispatch steps, followed by `critic_review` and `finalize`. They must
reject runs that do not have `revision_plan.required=true`.

Resume execution endpoints run only `pending_step_ids` from an `interrupted`
run package through the selected executor and must reject runs whose ledger
status is not `interrupted`. They record `resume_execution_requested` before
dispatch so clients can audit manual recovery.

Manual `POST /recover_stale` remains available for diagnostics and maintenance,
but clients should not need to call it after a normal gateway restart.

When a revision rerun reaches the writer, the executor passes a focused
`revision_context` from the previous `revision_plan`. Writer artifacts should
record that context, the critic should expose whether it saw the revision focus,
and the final manifest should carry `revision_focus` for client display and
debugging.

## Rules

- Warmaster must not do specialist worker jobs directly.
- Warmaster must preserve existing run history.
- Warmaster must expose corrupt or interrupted run state diagnostically instead
  of hiding the run or crashing the client endpoint.
- Warmaster must route only to active governors.
