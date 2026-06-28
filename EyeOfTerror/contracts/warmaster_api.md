# Warmaster API Contract

Warmaster Gateway is the user-facing orchestration entrypoint. Clients should
use it as the source of truth for task submission, state bootstrap, run
inspection, cancellation, and service diagnostics.

## Core Endpoints

```text
GET  /health
GET  /capabilities
GET  /state
GET  /doctor
GET  /governors
GET  /governors?health=1
GET  /workers
GET  /workers?health=1
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

## Client Bootstrap

Clients should call `GET /state` after startup or reconnect. The response
contains gateway capabilities, governor registry, worker registry, run status
counts, process-local active run ids, and recent run summaries.

## Task Creation

```json
{
  "message": "User task text",
  "task_id": "optional-stable-id"
}
```

Task ids are durable run identifiers. Creating a task with an existing `task_id`
must return a conflict instead of overwriting the run history.

If provided, `task_id` must match `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}` and must
not contain `..`.

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
`can_execute`, `can_start`, `can_cancel`, `can_resume`,
`can_execute_revision`, `can_start_revision`, and
`force_required_for_rerun`. Clients should use these hints for button state
instead of duplicating Warmaster status rules.

Run summaries also include ordered progress hints:
`planned_step_ids`, `completed_step_ids`, `failed_step_ids`,
`pending_step_ids`, `next_step_id`, and `step_states`. These are derived from
the run package and ledger records, so clients can display restart/resume
position, worker ownership, expected artifacts, produced artifacts, and per-step
summaries without parsing dispatch packets. Step states include
`expected_artifact_status` and `artifact_status` entries with `exists`, `bytes`,
and `host_path` when a run workspace is known.

## Cancellation

`POST /runs/{task_id}/cancel` marks the Warmaster ledger as cancelling and
best-effort forwards cancellation to HTTP worker task endpoints from the run
dispatch package. Cancellation is cooperative unless a worker implements a
stronger interruption mechanism.

## Execution Paths

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
