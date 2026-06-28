# Worker API Contract

All Inner Circle governors and Mechanicum workers should expose the same small
HTTP contract, even if their internal implementations differ.

## Required Endpoints

```text
GET  /health
GET  /capabilities
POST /run
GET  /tasks
GET  /tasks/{task_id}
POST /tasks/{task_id}/cancel
```

## GET /health and GET /capabilities

Both endpoints should identify the service and expose its public worker
metadata. Orchestrators must be able to verify that the expected worker is
running on the selected port before sending work.

```json
{
  "ok": true,
  "worker": "Lexmechanic",
  "workspace_root": "/work/mechanicum",
  "metadata": {
    "name": "Lexmechanic",
    "role": "source researcher",
    "capabilities": ["web_search", "source_map", "reliability_labels"],
    "api_contract": "EyeOfTerror/contracts/worker_api.md"
  },
  "capabilities": ["web_search", "source_map", "reliability_labels"],
  "api_contract": "EyeOfTerror/contracts/worker_api.md"
}
```

## POST /run Request

```json
{
  "task_id": "stable-task-id",
  "contract": {},
  "input_artifacts": ["/work/example/source_map.json"],
  "output_schema": {},
  "max_runtime_sec": 1800,
  "revision_context": {
    "reasons": ["Draft misses required event"],
    "source_steps": ["critic_review"],
    "priority": "blocker"
  }
}
```

`input_artifacts` is filled by the orchestrator from the dispatch step's
`depends_on` entries and the dependency steps' `expected_artifacts`. Workers
should treat these paths as required inputs for the step unless their own
contract says a missing input is an explicit blocker.

`revision_context` is optional. Orchestrators set it only when rerunning a
worker from a failed or blocked run's `revision_plan`. Workers should treat it
as focused correction context, not as user input, and should still validate all
required source artifacts before reporting completion.

## GET /tasks/{task_id} Response

```json
{
  "ok": true,
  "worker": "Lexmechanic",
  "task": {
    "task_id": "stable-task-id",
    "worker": "Lexmechanic",
    "status": "completed",
    "cancel_requested": false,
    "result": {}
  }
}
```

## POST /tasks/{task_id}/cancel

Cancellation is cooperative. A worker runtime must record the cancellation flag
and must not start a task that was already cancelled. A worker that is already
inside a long model call or external process may only stop after that call
returns unless the worker implements a stronger interruption mechanism.

## Response Shape

```json
{
  "ok": true,
  "worker": "lexmechanic",
  "task_id": "stable-task-id",
  "status": "completed",
  "summary": "...",
  "artifacts": [],
  "gaps": [],
  "confidence": "medium"
}
```

## Rules

- Workers are functions with bounded inputs and structured outputs.
- Workers must not ask the user directly.
- Workers must report blockers instead of pretending the task is complete.
- Writers must not invent facts that are absent from researcher/extractor
  outputs.
- Critics must be independent from the writer role.
