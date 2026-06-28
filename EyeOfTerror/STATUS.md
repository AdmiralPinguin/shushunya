# EyeOfTerror Status

## Working

- Iskandar Khayon can build lore reconstruction task contracts and dispatch packets.
- Dispatch packets propagate dependency output artifacts as worker
  `input_artifacts`.
- Iskandar Khayon exposes his required Mechanicum worker set through
  `/capabilities`.
- Iskandar Khayon exposes a compact pipeline summary through `/capabilities`
  so Warmaster/admin clients can inspect steps before task creation.
- Iskandar Khayon exposes an oversight plan through `/capabilities` and
  `/plan`, including artifact roles, handoffs, quality gates, completion
  criteria, and final review expectations.
- Iskandar run packages persist the governor oversight plan as `oversight.json`,
  and Warmaster exposes it through `GET /runs/{task_id}/oversight`.
- Warmaster oversight inspection includes compact summary and validation
  diagnostics against the current run package.
- Warmaster run summaries expose compact `oversight_summary` so clients and
  higher-level governors can inspect final review requirements without fetching
  the full oversight document.
- Warmaster action hints disable start/resume/revision actions and recommend
  oversight inspection when run oversight validation fails.
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
- The shared worker runtime rejects dispatch packets addressed to a different
  worker before calling worker code.
- The shared worker runtime and local executor reject missing or invalid
  `input_artifacts` before calling worker code.
- EyeOfTerror can execute dispatch packets through HTTP services with `eye_of_terror.http_executor`.
- The end-to-end HTTP pipeline test reaches a `ready` final manifest for the Skalathrax test task.
- Local and HTTP executors write `task_ledger.json` with task status, step status, artifacts, and event history.
- Warmaster Gateway can prepare Iskandar run packages, expose run status, execute local dev pipelines, and execute HTTP worker-service pipelines.
- Warmaster Gateway can preflight existing run packages before local or HTTP
  execution without starting workers.
- Run preflight rejects missing, corrupt, or contract-inconsistent governor
  oversight before execution.
- Warmaster capabilities advertise run execution preflight support.
- Warmaster local and HTTP execution can run validated step subsets without
  marking the whole run completed while downstream steps remain pending.
- Run preflight records compact audit events in the task ledger.
- Warmaster Gateway exposes `POST /task_preflight` for routing, governor,
  contract, and worker checks without creating run history.
- Task preflight contract summaries expose planned worker steps, dependency
  edges, and expected artifacts.
- Task preflight exposes compact governor oversight summaries and validation so
  clients can inspect final review expectations before creating run history.
- Run progress exposes per-step input, expected, and produced artifact statuses.
- Run progress exposes dependency readiness through ready, blocked, and
  dependency-status step hints for higher-level governors.
- Run progress separates waiting steps from ready and dependency-blocked steps.
- Warmaster Gateway can prepare tasks through the local governor path or through
  the active governor's HTTP service.
- Warmaster rejects HTTP-governor task preparation when reachable governor
  `required_workers` are missing from the Mechanicum registry.
- Warmaster rejects produced task contracts whose `worker_plan` references
  workers absent from the Mechanicum registry.
- Warmaster rejects task creation when the selected governor omits oversight or
  returns oversight that does not match the task contract.
- Warmaster verifies HTTP-governor prepared run packages before writing the
  Warmaster ledger.
- Task contract runtime validation rejects non-string non-goals, completion
  criteria, and quality gates.
- Task contract runtime validation requires every required artifact to have one
  worker-plan producer and rejects duplicate artifact producers.
- Task contract validation and schema reject duplicate required artifacts,
  dependencies, and expected artifacts.
- Task contract JSON schema mirrors runtime `/work/...` artifact path and
  non-empty string constraints.
- Warmaster Gateway can default new task planning to local or HTTP governor
  transport through startup flags, with per-request override.
- `EyeOfTerror/start_brigade.py` can start the current service-separated
  Warmaster + Iskandar + Mechanicum stack, with a dry-run self-test.
- The brigade launcher can emit a machine-readable startup plan with service
  names, ports, commands, and runtime roots.
- The startup plan expands registered Mechanicum workers with their names,
  ports, module paths, and modules.
- The startup plan exposes top-level service dependencies, dependency-ordered
  startup stages, and readiness URLs for future supervisors and admin clients.
- The brigade launcher can start services by dependency stage and wait for each
  stage's readiness URLs before starting dependent services.
- Launcher readiness covers Warmaster, Iskandar, and registered Mechanicum
  workers.
- The brigade launcher fails fast when one managed process exits and terminates
  the remaining managed processes.
- The brigade launcher checks managed port availability before starting, with a
  diagnostic opt-out.
- Warmaster Gateway can start local/HTTP execution in a background thread and expose progress through the ledger.
- Warmaster Gateway exposes `GET /governors`, `GET /workers`, and
  `GET /workers?health=1`; worker listings are enriched with available
  `Mechanicum/*/worker.json` metadata.
- Warmaster governor health snapshots include reachable governor service
  capabilities, including required worker declarations.
- Warmaster Gateway exposes `GET /state` as a client bootstrap snapshot with
  capabilities, governors, workers, and recent runs.
- Warmaster Gateway exposes gateway-level action hints for preflight, creation,
  start, resume, revision, cancellation, and diagnostics.
- Run action hints include `next_action` with a recommended endpoint and reason
  for chat clients and higher-level governors.
- Warmaster Gateway exposes the expected service-separated brigade topology
  through `GET /brigade_plan` and embeds it in `GET /state`.
- Warmaster Gateway exposes `GET /brigade_health` for combined expected
  topology and best-effort governor/worker health.
- Brigade health reports whether reachable governor `required_workers` are
  present in the Mechanicum registry.
- Brigade health reports reachable governor pipeline summaries when governor
  services expose them.
- Warmaster Gateway keeps plain `GET /state` lightweight and embeds brigade
  health only when clients request `GET /state?health=1`.
- Warmaster Gateway exposes `GET /doctor` for registry and manifest diagnostics.
- Warmaster Gateway exposes focused run inspection endpoints for contract,
  dispatch packets, and ledger event history.
- Warmaster Gateway exposes lightweight run summaries, progress counters, and
  worker task mappings for client polling/debugging.
- Warmaster state and run-list responses expose recoverable interrupted runs
  with resume `next_action` hints.
- Warmaster exposes `GET /recovery` for lightweight interrupted-run recovery
  queues.
- Recovery summaries distinguish startable interrupted run packages from
  malformed interrupted ledgers that need inspection.
- Warmaster exposes bulk recovery start endpoints that resume valid
  interrupted runs while reporting malformed run packages per item.
- Warmaster Gateway supports cursor-based ledger event polling with
  `/runs/{task_id}/events?after=N`.
- Warmaster Gateway exposes aggregate cursor-based run event polling through
  `/events?after=N` for clients that need one feed across runs.
- Aggregate run events include run status, governor, run update time, per-run
  event index, and global index for client-side list updates.
- Aggregate run events include the current `run_next_action` and
  `run_final_manifest_summary` so polling clients can update controls and final
  quality state without fetching every run summary.
- Warmaster Gateway exposes compact per-run snapshots for mobile/client
  polling through `/runs/{task_id}/snapshot`.
- Warmaster Gateway exposes process-local active run snapshots through
  `GET /runs/{task_id}/active` and `GET /state`.
- Warmaster Gateway rejects duplicate `task_id` creation instead of silently
  overwriting existing run history.
- Warmaster validates user-provided task ids and constrains execution workspace
  paths to each run directory.
- Warmaster constrains HTTP worker service `host` parameters to loopback hosts.
- Iskandar `prepare_run` constrains custom run output paths to its configured
  default run root.
- Warmaster artifact text reads support bounded previews through `max_bytes`.
- Warmaster artifact listings expand final manifest package files for client
  display and artifact fetching.
- Final manifest artifact listings include compact manifest summaries with
  critic metrics, warnings, blockers, and revision focus for client display.
- Completed run summaries expose `final_manifest_summary` so clients can render
  final quality state without an extra artifact listing request.
- Warmaster exposes `GET /runs/{task_id}/final` so clients and higher-level
  governors can fetch the final manifest, deliverable path, package files, and
  bounded text previews in one request.
- Task ledgers, run packages, and executor reports are written atomically to
  avoid partial JSON reads during background execution.
- Task ledger saves merge concurrent event/step/cancel updates from stale
  in-process ledger instances.
- Warmaster run listing/state endpoints tolerate corrupt ledger files and report
  those runs as `corrupt` instead of dropping the whole API response.
- Warmaster run inspection endpoints tolerate corrupt `status.json` and
  `contract.json` files and return diagnostic JSON instead of crashing.
- Warmaster run summaries expose `actions` hints for start/cancel/resume and
  revision controls.
- Warmaster action hints disable ordinary start/execute when a required
  revision plan exists.
- Warmaster validates required revision plans against the run dispatch package
  and disables revision actions when the plan is invalid.
- Warmaster action hints disable ordinary start/execute for interrupted runs so
  clients prefer resume controls.
- Warmaster action hints expose run preflight controls for local and HTTP
  execution.
- Warmaster progress summaries expose ordered planned/completed/failed/pending
  step ids, `next_step_id`, per-step state records, and artifact file status.
- Warmaster run summaries expose the latest run preflight result as
  `last_preflight`.
- Warmaster exposes `GET /runs/{task_id}/steps/{step_id}` for focused step
  inspection.
- Warmaster exposes `GET /runs/{task_id}/steps/{step_id}/artifacts` for
  focused artifact inspection by worker step.
- Warmaster routing rejects unsupported code/image/general tasks until a matching governor exists.
- Warmaster routing is driven by `route_terms` in the governor registry instead
  of hardcoded per-governor keyword lists.
- Warmaster Gateway can request cooperative cancellation through the task ledger,
  and best-effort forwards cancellation to HTTP worker task endpoints from the
  run dispatch package.
- Warmaster revision execution uses `revision_plan` to rerun focused worker
  steps, passes `revision_context` into those reruns, and preserves the observed
  `revision_focus` through writer, critic, and final manifest artifacts.
- HTTP execution preflights all worker `/health` endpoints before running steps
  and rejects worker identity mismatches before dispatch.
- Warmaster Gateway can mark stale `running`/`cancelling` ledgers as `interrupted` after a process restart.
- Warmaster Gateway performs stale run recovery on normal startup, with an
  opt-out flag for diagnostics.
- Warmaster Gateway exposes explicit local/HTTP resume endpoints for
  pending steps in `interrupted` run packages and records resume requests in
  the ledger.
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
- `Lexmechanic` ranks source candidates and labels live discovery results with
  source types and ranking reasons.
- `NoosphericExtractor` uses data playbooks when available and falls back to
  low-confidence generic evidence leads from fetched source snapshots.
- `Chronologis` and `ScriptoriumDaemon` preserve generic evidence-lead and
  low-confidence metadata through timeline and coverage artifacts.
- `ReductorVerifier` and `FabricatorFinalis` carry evidence-lead risk metrics
  into critic reports and final manifests.
- `AuspexBrowser` performs guarded HTTP text fetches; it does not yet render JavaScript pages or screenshots.
- The pipeline records inaccessible primary books as gaps instead of solving book acquisition.
- Warmaster Gateway background execution is in-process only; restart recovery
  marks stale jobs interrupted, and operators can bulk-start recoverable runs
  through explicit recovery endpoints.
- `GET /state` is a snapshot endpoint, not a live event stream; clients still
  need polling or a future push channel for real-time updates.
- Cancellation is still cooperative. It prevents future worker starts and marks
  worker task state, but it does not forcibly interrupt a worker already blocked
  inside a model call or external process.

## Next Good Steps

- Add more playbooks only when they are task-class patterns, not one-off hacks.
- Add durable background execution recovery and stronger interruption for
  already-running worker calls where the underlying worker supports it.
- Add code and image governors instead of routing unsupported task classes to Iskandar.
