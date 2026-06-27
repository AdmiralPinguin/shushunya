# Worker API Contract

All Inner Circle governors and Mechanicum workers should expose the same small
HTTP contract, even if their internal implementations differ.

## Required Endpoints

```text
GET  /health
GET  /capabilities
POST /run
GET  /tasks/{task_id}
POST /tasks/{task_id}/cancel
```

## POST /run Request

```json
{
  "task_id": "stable-task-id",
  "contract": {},
  "input_artifacts": [],
  "output_schema": {},
  "max_runtime_sec": 1800
}
```

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

