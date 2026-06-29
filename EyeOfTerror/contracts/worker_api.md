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
  "api_contract": "EyeOfTerror/contracts/worker_api.md",
  "phase": "available",
  "decision": {
    "can_poll": false,
    "can_cancel": false,
    "recommended_kind": "inspect_capabilities",
    "recommended_endpoint": "GET /capabilities"
  },
  "display": {
    "headline": "Lexmechanic is ready",
    "detail": "worker service is available",
    "severity": "info"
  },
  "next_action": {
    "kind": "inspect_capabilities",
    "method": "GET",
    "endpoint": "GET /capabilities",
    "body": {},
    "reason": "inspect worker capabilities"
  },
  "client_action": {
    "kind": "inspect_capabilities",
    "method": "GET",
    "path": "/capabilities",
    "body": {},
    "reason": "inspect worker capabilities"
  }
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
contract says a missing input is an explicit blocker. The shared Mechanicum
runtime and local EyeOfTerror executor reject missing or non-`/work/` input
artifacts before calling the worker implementation.

`task_id` is required. Worker runtimes must reject `/run` requests without it so
orchestrators can poll, cancel, and audit every worker step through `/tasks`.

`revision_context` is optional. Orchestrators set it only when rerunning a
worker from a failed or blocked run's `revision_plan`. Workers should treat it
as focused correction context, not as user input, and should still validate all
required source artifacts before reporting completion.

When `/run` receives a full dispatch packet with a top-level `worker` field,
the shared worker runtime must reject packets addressed to a different worker
before calling the worker implementation with a `worker mismatch` error. This
keeps direct service calls and misrouted dispatch packets from bypassing
Warmaster preflight identity checks.

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
  },
  "phase": "completed",
  "decision": {
    "can_poll": false,
    "can_cancel": false,
    "recommended_kind": "inspect_task",
    "recommended_endpoint": "GET /tasks/stable-task-id"
  },
  "display": {
    "headline": "Lexmechanic task completed",
    "detail": "completed",
    "severity": "info"
  },
  "client_action": {
    "kind": "inspect_task",
    "method": "GET",
    "path": "/tasks/stable-task-id",
    "body": {},
    "reason": "inspect recorded worker task"
  }
}
```

`GET /tasks` includes a compact `summary` by status plus a `display` object for
worker task-list screens. `GET /tasks/{task_id}`, `/run`, and cancellation
responses include `phase`, `decision`, `display`, `next_action`, and executable
`client_action` fields so Warmaster or an admin client can render worker state
without interpreting raw task dictionaries.

## POST /tasks/{task_id}/cancel

Cancellation is cooperative. A worker runtime must record the cancellation flag
and must not start a task that was already cancelled. A worker that is already
inside a long model call or external process may only stop after that call
returns unless the worker implements a stronger interruption mechanism.
Cancelling an already terminal task must be rejected and must not rewrite the
recorded terminal status or result.

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

Executors treat a successful terminal payload as complete only when `ok=true`,
`revision_plan.required` is not true, and `status` is one of `ready`,
`completed`, `passed`, or `passed_with_warnings`. Missing or unknown terminal
statuses must not close the whole run as completed.

## Rules

- Workers are functions with bounded inputs and structured outputs.
- Workers must not ask the user directly.
- Workers must report blockers instead of pretending the task is complete.
- Writers must not invent facts that are absent from researcher/extractor
  outputs.
- Critics must be independent from the writer role.
