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
  "required_workers": ["Lexmechanic", "NoosphericExtractor"],
  "pipeline": {
    "kind": "lore_reconstruction",
    "step_count": 2,
    "required_workers": ["Lexmechanic", "NoosphericExtractor"],
    "steps": [
      {
        "step_id": "source_discovery",
        "worker": "Lexmechanic",
        "depends_on": [],
        "expected_artifacts": ["/work/capabilities/source_map.json"],
        "expected_artifact_count": 1
      }
    ]
  },
  "oversight": {
    "governor": "IskandarKhayon",
    "kind": "lore_reconstruction_oversight",
    "quality_gates": [],
    "completion_criteria": [],
    "artifact_roles": {},
    "handoffs": [],
    "final_review": {}
  },
  "capabilities": ["lore_reconstruction_planning", "dispatch_packet_preparation", "oversight_plan"],
  "endpoints": ["GET /health", "GET /capabilities", "POST /plan", "POST /prepare_run"]
}
```

`required_workers` is ordered by the governor's normal pipeline dependency
shape, so an orchestrator or admin client can compare the governor requirements
against the Mechanicum registry before starting a task.
`pipeline` is a compact task-class plan summary built from the same worker plan
source as concrete task contracts; clients can inspect step dependencies and
expected artifacts before creating a run.
`oversight` is the governor's task-class quality-control plan. It should expose
artifact roles, worker handoffs, completion criteria, quality gates, and final
review expectations so Warmaster and admin clients can inspect how the governor
intends to supervise worker output.

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
  "oversight": {}
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

If a governor accepts `run_dir`, it must keep that path inside its configured
default run root. Relative paths are resolved below the default root.

## Rules

- Governors decide which workers are needed and in what sequence.
- Governors must produce structured contracts and dispatch packets, not freeform
  instructions hidden in chat text.
- Governors must report missing workers or unsupported task kinds explicitly.
- Governors should verify worker outputs through critic/verifier workers when
  task quality depends on evidence or correctness.
