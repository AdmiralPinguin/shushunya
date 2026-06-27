# EyeOfTerror Status

## Working

- Iskandar Khayon can build lore reconstruction task contracts and dispatch packets.
- Mechanicum prototype workers cover the current lore pipeline:
  - `Lexmechanic`
  - `AuspexBrowser`
  - `NoosphericExtractor`
  - `Chronologis`
  - `ScriptoriumDaemon`
  - `ReductorVerifier`
  - `FabricatorFinalis`
- Workers can run as local subprocesses through `eye_of_terror.local_executor`.
- Workers can run as HTTP services through `Mechanicum/worker_runtime.py`.
- EyeOfTerror can execute dispatch packets through HTTP services with `eye_of_terror.http_executor`.
- The end-to-end HTTP pipeline test reaches a `ready` final manifest for the Skalathrax test task.
- Local and HTTP executors write `task_ledger.json` with task status, step status, artifacts, and event history.
- Warmaster Gateway can prepare Iskandar run packages, expose run status, execute local dev pipelines, and execute HTTP worker-service pipelines.
- Warmaster Gateway can start local/HTTP execution in a background thread and expose progress through the ledger.
- Warmaster routing rejects unsupported code/image/general tasks until a matching governor exists.
- Warmaster Gateway can request cooperative cancellation through the task ledger; executors stop before the next worker step.
- HTTP execution preflights all worker `/health` endpoints before running steps.

## Main Check

```bash
EyeOfTerror/check-eye-mechanicum.sh
```

This runs contract tests, worker self-tests, local executor tests, service tests,
and the end-to-end HTTP worker pipeline.

Optional live discovery smoke:

```bash
PYTHONPATH=Mechanicum/Lexmechanic LEXMECHANIC_LIVE_DISCOVERY=1 python3 Mechanicum/Lexmechanic/live_discovery_smoke.py
```

## Current Limits

- `Lexmechanic` uses source playbooks plus optional live discovery; allowlisted live results can become source candidates.
- `NoosphericExtractor` still uses rule-based event playbooks; Skalathrax rules now live in data, not Python code.
- `AuspexBrowser` performs guarded HTTP text fetches; it does not yet render JavaScript pages or screenshots.
- The pipeline records inaccessible primary books as gaps instead of solving book acquisition.
- Warmaster Gateway background execution is in-process only; durable job recovery across process restarts is not implemented yet.

## Next Good Steps

- Add richer ranking and source-type classification for live discovery results.
- Add more playbooks only when they are task-class patterns, not one-off hacks.
- Add durable background execution recovery and hard cancellation for already-running worker calls.
- Add code and image governors instead of routing unsupported task classes to Iskandar.
