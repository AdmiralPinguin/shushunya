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
  "required_workers": [
    "Lexmechanic",
    "AuspexBrowser",
    "NoosphericExtractor",
    "Chronologis",
    "ScriptoriumDaemon",
    "ReductorVerifier",
    "FabricatorFinalis"
  ],
  "pipeline": {
    "kind": "lore_reconstruction",
    "step_count": 7,
    "required_workers": [
      "Lexmechanic",
      "AuspexBrowser",
      "NoosphericExtractor",
      "Chronologis",
      "ScriptoriumDaemon",
      "ReductorVerifier",
      "FabricatorFinalis"
    ],
    "steps": [
      {
        "step_id": "source_discovery",
        "worker": "Lexmechanic",
        "depends_on": [],
        "expected_artifacts": ["/work/capabilities/source_map.json"],
        "expected_artifact_count": 1
      },
      {
        "step_id": "source_acquisition",
        "worker": "AuspexBrowser",
        "depends_on": ["source_discovery"],
        "expected_artifacts": ["/work/capabilities/source_snapshots.json"],
        "expected_artifact_count": 1
      },
      {
        "step_id": "finalize",
        "worker": "FabricatorFinalis",
        "depends_on": ["critic_review"],
        "expected_artifacts": ["/work/capabilities/final_manifest.json"],
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
artifact roles, worker handoffs, completion criteria, quality gates, final
review expectations, and revision policy so Warmaster and admin clients can
inspect how the governor intends to supervise worker output and rerun focused
rework when verification fails.

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
  "pipeline": {},
  "oversight": {},
  "actions": {
    "can_prepare_run": true,
    "can_inspect_capabilities": true,
    "next_action": {
      "kind": "prepare_run",
      "method": "POST",
      "endpoint": "POST /prepare_run",
      "body": {"task": "User task text", "task_id": "optional-stable-id"},
      "reason": "governor plan is valid and required workers are available"
    }
  }
}
```

`POST /plan` responses include a concrete pipeline status with `step_count`,
ordered `required_workers`, step dependencies, input artifacts, expected
artifacts, and missing dependency diagnostics. The
`actions` object tells Warmaster or another orchestrator whether the governor
can proceed to `POST /prepare_run`; failed plans recommend capability
inspection instead of forcing clients to infer the next step from validation
fields.

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
