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
GET  /runs/{task_id}
GET  /runs/{task_id}/summary
GET  /runs/{task_id}/ledger
GET  /runs/{task_id}/contract
GET  /runs/{task_id}/dispatch
GET  /runs/{task_id}/worker_tasks
GET  /runs/{task_id}/worker_tasks?live=1
GET  /runs/{task_id}/events
GET  /runs/{task_id}/artifacts
GET  /runs/{task_id}/artifact_text?path=/work/...
POST /runs/{task_id}/execute_local
POST /runs/{task_id}/execute_http
POST /runs/{task_id}/start_local
POST /runs/{task_id}/start_http
POST /runs/{task_id}/cancel
POST /recover_stale
```

## Client Bootstrap

Clients should call `GET /state` after startup or reconnect. The response
contains gateway capabilities, governor registry, worker registry, run status
counts, and recent run summaries.

## Task Creation

```json
{
  "message": "User task text",
  "task_id": "optional-stable-id"
}
```

Task ids are durable run identifiers. Creating a task with an existing `task_id`
must return a conflict instead of overwriting the run history.

## Run Inspection

Clients should use:

- `/runs/{task_id}/summary` for lightweight polling.
- `/runs/{task_id}/events` for ledger event history.
- `/runs/{task_id}/contract` and `/runs/{task_id}/dispatch` for orchestration
  debugging.
- `/runs/{task_id}/worker_tasks?live=1` when worker services are running and
  live worker task state is needed.

## Cancellation

`POST /runs/{task_id}/cancel` marks the Warmaster ledger as cancelling and
best-effort forwards cancellation to HTTP worker task endpoints from the run
dispatch package. Cancellation is cooperative unless a worker implements a
stronger interruption mechanism.

## Rules

- Warmaster must not do specialist worker jobs directly.
- Warmaster must preserve existing run history.
- Warmaster must expose corrupt or interrupted run state diagnostically instead
  of hiding the run or crashing the client endpoint.
- Warmaster must route only to active governors.
