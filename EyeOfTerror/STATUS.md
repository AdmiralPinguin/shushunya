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
- Warmaster Gateway exists as a thin HTTP entrypoint that prepares Iskandar run packages.

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
- Warmaster Gateway does not execute worker pipelines yet; it prepares routed run packages.

## Next Good Steps

- Add richer ranking and source-type classification for live discovery results.
- Add more playbooks only when they are task-class patterns, not one-off hacks.
- Expose task ledger state and execution controls through Warmaster Gateway.
- Add Warmaster Gateway only after the Iskandar/Mechanicum boundary remains stable.
