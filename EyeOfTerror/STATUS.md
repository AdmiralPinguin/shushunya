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
- Worker services expose `GET /health`, `GET /capabilities`, `POST /run`,
  `GET /tasks`, `GET /tasks/{task_id}`, and
  `POST /tasks/{task_id}/cancel` through the shared runtime.
- EyeOfTerror can execute dispatch packets through HTTP services with `eye_of_terror.http_executor`.
- The end-to-end HTTP pipeline test reaches a `ready` final manifest for the Skalathrax test task.
- Local and HTTP executors write `task_ledger.json` with task status, step status, artifacts, and event history.
- Warmaster Gateway can prepare Iskandar run packages, expose run status, execute local dev pipelines, and execute HTTP worker-service pipelines.
- Warmaster Gateway can start local/HTTP execution in a background thread and expose progress through the ledger.
- Warmaster Gateway exposes `GET /governors`, `GET /workers`, and
  `GET /workers?health=1`; worker listings are enriched with available
  `Mechanicum/*/worker.json` metadata.
- Warmaster Gateway exposes `GET /state` as a client bootstrap snapshot with
  capabilities, governors, workers, and recent runs.
- Warmaster Gateway exposes `GET /doctor` for registry and manifest diagnostics.
- Warmaster Gateway exposes focused run inspection endpoints for contract,
  dispatch packets, and ledger event history.
- Warmaster Gateway exposes lightweight run summaries, progress counters, and
  worker task mappings for client polling/debugging.
- Warmaster Gateway rejects duplicate `task_id` creation instead of silently
  overwriting existing run history.
- Warmaster validates user-provided task ids and constrains execution workspace
  paths to each run directory.
- Iskandar `prepare_run` constrains custom run output paths to its configured
  default run root.
- Warmaster artifact text reads support bounded previews through `max_bytes`.
- Task ledgers, run packages, and executor reports are written atomically to
  avoid partial JSON reads during background execution.
- Task ledger saves merge concurrent event/step/cancel updates from stale
  in-process ledger instances.
- Warmaster run listing/state endpoints tolerate corrupt ledger files and report
  those runs as `corrupt` instead of dropping the whole API response.
- Warmaster routing rejects unsupported code/image/general tasks until a matching governor exists.
- Warmaster Gateway can request cooperative cancellation through the task ledger,
  and best-effort forwards cancellation to HTTP worker task endpoints from the
  run dispatch package.
- HTTP execution preflights all worker `/health` endpoints before running steps
  and rejects worker identity mismatches before dispatch.
- Warmaster Gateway can mark stale `running`/`cancelling` ledgers as `interrupted` after a process restart.
- Mechanicum worker manifests are validated for required metadata, stable ports,
  API contract, and service-registry consistency.
- Inner Circle governors are tracked in `EyeOfTerror/registry/governors.json`; code/image governors are explicit planned entries.
- Warmaster, Governor, and Worker API contracts are covered by self-tests against
  advertised runtime capabilities/endpoints.
- `CogitatorCodewrightGovernor` now has planned scope documentation but remains inactive.
- `ForgeMasterGovernor` now has planned scope documentation but remains inactive.

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
- Warmaster Gateway background execution is in-process only; restart recovery marks stale jobs interrupted but does not resume them.
- `GET /state` is a snapshot endpoint, not a live event stream; clients still
  need polling or a future push channel for real-time updates.
- Cancellation is still cooperative. It prevents future worker starts and marks
  worker task state, but it does not forcibly interrupt a worker already blocked
  inside a model call or external process.

## Next Good Steps

- Add richer ranking and source-type classification for live discovery results.
- Add more playbooks only when they are task-class patterns, not one-off hacks.
- Add durable background execution recovery and stronger interruption for
  already-running worker calls where the underlying worker supports it.
- Add code and image governors instead of routing unsupported task classes to Iskandar.
