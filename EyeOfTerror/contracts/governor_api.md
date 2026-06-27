# Governor API Contract

Inner Circle governors coordinate Mechanicum workers for a task class. They are
not user-facing chat personalities and should not do specialist worker jobs
directly.

## Required Endpoints

```text
GET  /health
GET  /capabilities
POST /plan
POST /prepare_run
```

## GET /health Response

```json
{
  "ok": true,
  "governor": "IskandarKhayon"
}
```

## GET /capabilities Response

```json
{
  "ok": true,
  "governor": "IskandarKhayon",
  "api_version": 1,
  "task_kinds": ["research", "lore_reconstruction"],
  "capabilities": ["lore_reconstruction_planning", "dispatch_packet_preparation"],
  "endpoints": ["GET /health", "GET /capabilities", "POST /plan", "POST /prepare_run"]
}
```

## POST /plan Request

```json
{
  "task": "User task text",
  "task_id": "optional-stable-id"
}
```

## POST /plan Response

```json
{
  "ok": true,
  "contract": {},
  "validation": {},
  "summary": "..."
}
```

## POST /prepare_run Request

```json
{
  "task": "User task text",
  "task_id": "optional-stable-id",
  "run_dir": "optional/output/path"
}
```

## Rules

- Governors decide which workers are needed and in what sequence.
- Governors must produce structured contracts and dispatch packets, not freeform
  instructions hidden in chat text.
- Governors must report missing workers or unsupported task kinds explicitly.
- Governors should verify worker outputs through critic/verifier workers when
  task quality depends on evidence or correctness.
