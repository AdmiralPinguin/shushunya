# EyeOfTerror Status

## Active Development Focus

- Current focus: Ceraxia, the code-brigade governor, and her Mechanicum code
  worker pipeline.
- Removed: standalone mobile agent. User-facing tasks now enter through
  Warmaster and are routed to governors/workers from there.
- Ceraxia exposes task profiling, risk flags, worker specialization briefs,
  dispatch-level worker briefs, and Warmaster-visible final execution reports
  for code tasks.
- Ceraxia code workers now preserve engineering investigation records: import
  dependency graph, simple call graph, targeted reading plan, hypothesis log,
  design-decision seed, review decision records, and focused revision context.
- Ceraxia can infer a narrow arithmetic return-expression patch from a
  two-argument unittest assertion when the candidate is unique and verified.
- CodeBrigade diagnostic repair requests now support `name_error` as a guarded
  repair signal when preserved tests expose a single safe missing symbol and
  literal expectation.
- CodeBrigade now has a bounded diagnostic repair loop: it applies the guarded
  source repair, reruns the allowlisted failed verification command, records
  attempt history, and returns a Ceraxia/Planning replan packet instead of
  repeating the same failed repair signature.
- CodeBrigade now also supports Ceraxia `project_creation` runs through a
  greenfield project adapter: it can create a new file tree in an empty or
  Ceraxia-marked workspace, run allowlisted verification, and pass the normal
  review/audit pipeline instead of being limited to repairing existing repos.
  Greenfield runs now write a contracted `greenfield_project_brief.json` with
  project type, stack, entrypoints, expected files, artifact contract,
  workspace policy, definition of done, architecture/file/module/dependency/
  verification plans, template contract metadata, common failure fixes, and
  model-brain guidance. GreenfieldArchitect project-brief creation, project
  type inference, plan artifact construction, and implementation trace planning
  now live in `greenfield_architect.py`; task-derived ImplementationWorker
  feature generation now lives in `greenfield_feature_worker.py`; DependencyWorker
  package-manager discovery, manifest checks, install allowlisting, and lockfile
  snapshots now live in `greenfield_dependency_worker.py`; GreenfieldReview
  launchability, README, entrypoint, verification-status, module-contract,
  semantic anti-stub, and reviewer model-guidance gates now live in
  `greenfield_review_worker.py`; the greenfield verification loop, failure
  signatures, GreenfieldRepairWorker model guidance, bounded template repairs,
  reruns, and semantic stop reasons now live in `greenfield_verification_worker.py`
  and run memory records, repaired-file history, command history, review
  findings, and reusable learnings now live in `greenfield_memory_worker.py`
  while ScaffoldWorker workspace policy checks, file normalization, directory
  creation, generated file writes, rollback, operation reports, and patch
  manifests now live in `greenfield_scaffold_worker.py`
  instead of being buried in the greenfield orchestrator. The template registry now covers Python CLI, FastAPI,
  Python library, Vite frontend, static site, Telegram bot, data-processing
  tool, and local agent tool scaffolds. Greenfield reports now also preserve
  reviewer/repair model guidance and a `greenfield_memory_record` with chosen
  stack, template, dependency outcome, verification attempts, review findings,
  commands, failure fixes, and reusable learnings. The Greenfield
  ImplementationWorker now records module sequence, requirement-to-file and
  function/component traces, paired tests, milestones, and anti-stub policy;
  it also implements recognized task-derived features instead of returning only
  generic `ready` scaffolds, starting with Python CLI calculator behavior,
  argument parsing, arithmetic tests, and division-by-zero rejection, and now
  static browser todo-list behavior with DOM controls, add/complete/delete
  logic, localStorage persistence, module contracts, and structure tests, plus
  FastAPI-compatible notes-service behavior with create/list/get/delete logic,
  route wiring, invalid-title handling, and tests that do not require a live
  server, plus CSV summary data-processing behavior with row/column counts,
  numeric sums, numeric averages, JSON CLI output, contracts, and tests;
  GreenfieldArchitect now writes first-class plan artifacts
  `architecture_plan.json`, `file_tree_plan.json`, `module_contracts.json`, and
  `verification_plan.json` into generated projects; ImplementationWorker now
  also records an `implementation_feature_report` with recognized task-derived
  feature ids, changed generated files, changed module contracts, strategy, and
  role-specific model guidance;
  GreenfieldReview reads generated artifacts back from disk and blocks missing
  files, empty generated files, placeholder markers, missing module traces, and
  source-without-test scaffolds while avoiding false positives for domain words
  such as todo-list applications. The greenfield verification loop can now apply
  bounded repairs before rerunning: it restores missing files only from the
  selected template contract and adds missing README command blocks when those
  commands are declared in the project brief. DependencyWorker now records
  package-manager availability, validates manifest files, snapshots lockfiles
  before/after install, blocks non-allowlisted or workspace-escaping install
  commands, and records package-manager stacks as manifests-only unless explicit
  install commands are provided.
- Warmaster gateway HTTP-governor preparation now has a focused live self-test;
  the old monolithic gateway self-test is opt-in through
  `RUN_MONOLITHIC_GATEWAY_SELF_TEST=1` so normal checks do not hang on one huge
  scenario bundle.
- Ceraxia review gate now treats matched surface verification output with
  diagnostic failure semantics, including `no_tests_ran`, as failed surface
  evidence even when the command/report claims `passed`.
- Ceraxia repository survey now resolves Go module imports rooted in `go.mod`
  into local dependency edges, so CodeBrigade handoffs and reverse dependency
  indexes can see Go package impact instead of only JS/TS/Python edges.
- Ceraxia repository survey also emits a normalized
  `repository_dependency_graph` with source nodes, language counts, reverse
  impact, high-impact nodes, and Rust `mod`/`crate::` edges; CodeBrigade
  receives that graph in its implementation plan.
- Ceraxia run-package audit now compares saved artifact manifest hashes and
  sizes against current artifacts, builds an artifact semantic index, and blocks
  final reports whose human summary disagrees with source JSON decisions even
  when the manifest was freshly regenerated.
- CodeBrigade has a focused self-test for active planning handoff, diagnostic
  NameError repair gates, bounded repair-loop verification/replan behavior, and
  greenfield project creation workspace boundaries; the large historical
  self-test is opt-in from the local Mechanicum check through
  `RUN_FULL_CODE_BRIGADE_SELF_TEST=1`.
- Ceraxia's fast local check now includes greenfield integration scenarios for
  Python CLI, FastAPI-like API service, and static frontend site creation.
- The local Mechanicum check now runs focused Ceraxia slices by default; the
  full Ceraxia suite and handoff field trials are opt-in through
  `RUN_FULL_CERAXIA_SELF_TEST=1` and `RUN_CERAXIA_HANDOFF_FIELD_TRIALS=1`.
- PlanningBrigade roles now expose an executable read-only role-service
  runtime with `GET /health`, `GET /capabilities`, and `POST /plan`; ports
  `7111-7115` are active HTTP-ready contracts while the existing in-process
  packet builder remains available for compatibility. `start_role_services.py`
  now prints the supervisor manifest or launches all five role services
  together on their reserved ports.
- Ceraxia/CodeBrigade handoffs expose implementation work packages, package
  surface coverage, package statuses, and review blocking for blocked packages.
- CodeBrigade real source mutation for medium/high-risk tasks now requires a
  ready PlanningBrigade/Ceraxia planning handoff package before execution; dry
  runs expose the same gate diagnostically.

## Working

- Shared model-backed decisions are centralized in `EyeOfTerror/model_brain.py`.
  Worker HTTP services, local pipeline execution, and the Iskandar/Ceraxia
  governor services expose a `model_brain` contract and attach a
  `model_brain` status object to task results/plans.
- Ceraxia CodeBrigade workers consume model guidance inside their own
  artifacts: repository survey records model-guided risks, change planning
  writes model guidance into the plan/problem/options, implementation records
  model guidance in the mutation decision record, verification preserves it in
  diagnostic/repair artifacts, review records it as advisory critique, and the
  final manifest carries the model-guidance trail.
- FerrumPatchwright can now request a model-generated `CERAXIA_PATCH` when no
  explicit or guarded inferred patch exists, then apply it through the normal
  patch/verification/review pipeline.
- `EyeOfTerror/Warmaster/start_brigade.py` requires model-backed service runs
  with `EYE_MODEL_BASE_URL=http://127.0.0.1:8080/v1` and
  `EYE_MODEL_NAME=gemma-4-12b-it-UD-Q5_K_XL.gguf`. There is no zero-model mode:
  self-tests exercise the live OpenAI-compatible LLM endpoint.
- Iskandar Khayon can build lore reconstruction task contracts and dispatch packets.
- Dispatch packets propagate dependency output artifacts as worker
  `input_artifacts`.
- Dispatch packets written with governor oversight propagate per-step
  `quality_expectations` into worker requests.
- Iskandar Khayon exposes his required Mechanicum worker set through
  `/capabilities`.
- Iskandar Khayon exposes a compact pipeline summary through `/capabilities`
  so Warmaster/admin clients can inspect steps before task creation.
- Iskandar Khayon exposes an oversight plan through `/capabilities` and
  `/plan`, including artifact roles, handoffs, quality gates, completion
  criteria, and final review expectations.
- Iskandar Khayon oversight includes a per-step quality matrix with required
  inputs, expected artifacts, checks, blockers, and revision targets for every
  pipeline step.
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
- Workers can run as HTTP services through `LegacyMechanicum/worker_runtime.py`.
- Worker services expose `GET /health`, `GET /capabilities`, `POST /run`,
  `GET /tasks`, `GET /tasks/{task_id}`, and
  `POST /tasks/{task_id}/cancel` through the shared runtime.
- Worker runtime responses expose compact task `summary`, `phase`, `decision`,
  `display`, `next_action`, and executable `client_action` fields for
  Warmaster/admin worker-state screens.
- Worker task-list responses use a dedicated `task_list` phase and executable
  `/tasks` client action for worker task-history screens.
- The shared worker runtime rejects `/run` requests without `task_id` so every
  worker step remains pollable and cancellable.
- The shared worker runtime rejects late cancellation of terminal worker tasks
  without rewriting completed/failed task state.
- The shared worker runtime rejects dispatch packets addressed to a different
  worker before calling worker code.
- The shared worker runtime and local executor reject missing or invalid
  `input_artifacts` before calling worker code.
- The shared worker runtime and local executor reject contradictory
  `quality_expectations` before calling worker code.
- EyeOfTerror can execute dispatch packets through HTTP services with `eye_of_terror.http_executor`.
- The end-to-end HTTP pipeline test reaches a `ready` final manifest for the Skalathrax test task.
- The generic lore smoke test verifies that non-playbook tasks fail fast at
  source discovery without leaking Skalathrax-specific verifier findings.
- Local and HTTP executors write `task_ledger.json` with task status, step status, artifacts, and event history.
- Local and HTTP executors require explicit allowed terminal statuses before
  marking a run completed.
- Local and HTTP executors record malformed dispatch packets as structured
  failed/preflight-failed runs instead of crashing.
- Local executor retries one flaky worker timeout once by default while keeping
  zero-second timeout tests as hard failures.
- Warmaster Gateway can prepare Iskandar run packages, expose run status, execute local dev pipelines, and execute HTTP worker-service pipelines.
- Warmaster Gateway can preflight existing run packages before local or HTTP
  execution without starting workers.
- Pipeline run package writing removes obsolete dispatch packets before writing
  the current governor plan.
- Run preflight rejects missing, corrupt, or contract-inconsistent governor
  oversight before execution.
- Warmaster capabilities advertise run execution preflight support.
- Warmaster local and HTTP execution can run validated step subsets without
  marking the whole run completed while downstream steps remain pending.
- Run preflight records compact audit events in the task ledger.
- Run preflight responses expose action hints for starting execution after
  success or inspecting package/oversight/brigade diagnostics after failure.
- Run preflight responses expose current `run_summary`, `phase`, `decision`,
  `display`, top-level `next_action`, and executable `client_action` fields.
- Run preflight action hints respect run-summary lifecycle gates, so completed
  runs recommend force rerun instead of unsafe plain start.
- Warmaster Gateway exposes `POST /task_preflight` for routing, governor,
  contract, and worker checks without creating run history.
- Task creation and task-preflight responses expose `phase`, `decision`,
  `display`, top-level `next_action`, and executable `client_action` fields for
  chat/mobile routing cards.
- Warmaster Gateway exposes prepare-only `POST /orchestrate` for chat clients:
  task preflight, task creation, run preflight, trace, and next start/inspect
  action without launching long worker execution.
- Prepare-only orchestration responses expose executable `client_action`
  fields for both successful start recommendations and direct diagnostic
  actions.
- Warmaster Gateway exposes `POST /orchestrate_start` to safely start prepared
  runs in the background through run-summary action gates and return an
  immediate polling snapshot with an executable polling `client_action`.
- Warmaster Gateway exposes one-shot `POST /orchestrate_run` for chat clients
  that should prepare a task and safely start it in the background through the
  same orchestration gates.
- One-shot orchestration reuses existing runs for repeated stable `task_id`
  submissions by default, so reconnects can restore state without creating
  duplicate history or surfacing task-id conflicts as user-facing failures.
- One-shot orchestration responses copy `decision` and `display` to the top
  level so chat/mobile clients can render the immediate response without
  traversing the nested orchestration state.
- Warmaster Gateway exposes `GET /runs/{task_id}/orchestration` as a read-only
  chat decision view with phase, snapshot, next action, and final package when
  completed.
- Orchestration state exposes a stable `decision` object so clients can render
  poll/start/resume/revision/final/diagnostic controls without parsing phase
  strings.
- Orchestration state exposes a compact `display` object with headline, detail,
  severity, progress counts, next step/worker, and final deliverable for chat
  and mobile status rendering.
- Orchestration and one-shot chat responses expose top-level `display_events`
  so task detail screens can render recent history without traversing snapshots.
- Orchestration responses and cards expose executable `client_action`
  method/path/body fields with `task_id` already resolved from `next_action`.
- Recovery summaries expose executable `client_action` method/path/body fields
  for each interrupted-run candidate.
- Recovery summaries expose compact `display` fields so clients can render
  startable and blocked interrupted-run recovery without custom diagnostics.
- Task preflight contract summaries expose planned worker steps, dependency
  edges, and expected artifacts.
- Task preflight exposes compact governor oversight summaries and validation so
  clients can inspect final review expectations before creating run history.
- Task preflight preserves selected governor plan action hints for
  higher-level orchestrators that need to compare Warmaster and governor next
  steps.
- Task preflight can optionally include compact `brigade_readiness` when clients
  set `include_brigade_health=true`.
- Optional task-preflight brigade readiness fails soft as a diagnostic payload
  instead of crashing the preflight response.
- Task preflight responses expose `actions.can_create_task` and
  `actions.next_action` so chat clients can continue without hardcoded flow
  rules.
- HTTP task preflight and task creation responses expose top-level executable
  `client_action` fields for the recommended next step.
- HTTP execution preserves compact worker runtime `display`, `decision`,
  `next_action`, and `client_action` state in ledger step details and step
  events under `worker_view`.
- Task routing failures also expose action hints that direct clients toward
  gateway capability inspection instead of attempting run creation.
- Planned-governor routing failures expose `required_governor` registry
  metadata so clients can identify the missing coordinator without hardcoded
  task-class rules.
- Successful task creation responses expose action hints that recommend run
  preflight before execution.
- Rejected task creation responses expose action hints for existing-run,
  brigade, governor, capability, or preflight diagnostics.
- Task preflight action bodies preserve governor transport fields so clients
  following `next_action` keep the intended local or HTTP planning boundary.
- Task preflight `create_task` hints include the original message, making the
  recommended `POST /task` body directly executable by simple chat clients.
- Retry-style rejected task creation hints preserve the same executable message
  and governor transport body shape.
- Governor API contract checks document the active Iskandar required worker
  chain exposed through `/capabilities`.
- Iskandar `/plan` resolves worker metadata and reports planned workers as
  `unavailable_workers` instead of marking the plan runnable.
- Iskandar `/plan` exposes concrete pipeline summaries and action hints for
  preparing valid run packages or inspecting governor capabilities.
- Iskandar capabilities and plan responses expose compact `summary`, `phase`,
  `decision`, `display`, `next_action`, and executable `client_action` fields
  so Warmaster/admin clients can render governor handoffs consistently.
- Iskandar `/capabilities` exposes worker availability metadata for the default
  lore pipeline before a concrete task plan is created.
- Run progress exposes per-step input, expected, and produced artifact statuses.
- Run progress exposes dependency readiness through ready, blocked, and
  dependency-status step hints for higher-level governors.
- Run progress separates waiting steps from ready and dependency-blocked steps.
- Run progress step states expose preserved HTTP worker `worker_view` state
  when ledger step records include it.
- Run progress step states expose compact governor `quality_hints` from
  oversight-backed dispatch packets.
- Warmaster Gateway can prepare tasks through the local governor path or through
  the active governor's HTTP service.
- Brigade launcher derives Warmaster and Iskandar service ports from
  `EyeOfTerror/Warmaster/registry/ports.json`.
- Brigade launcher rejects incomplete `LegacyMechanicum/worker_services.json` entries
  instead of silently omitting workers from the startup plan.
- Warmaster rejects HTTP-governor task preparation when reachable governor
  `required_workers` are missing or known-but-planned in the Mechanicum
  registry.
- Warmaster rejects produced task contracts whose `worker_plan` references
  missing or planned Mechanicum workers, preserving `unavailable_workers`
  details for clients.
- Warmaster rejects task creation when the selected governor omits oversight or
  returns oversight that does not match the task contract.
- Warmaster verifies HTTP-governor prepared run packages before writing the
  Warmaster ledger.
- Warmaster rejects HTTP-governor prepared runs with missing, unexpected, or
  corrupt dispatch packets before ledger creation.
- Warmaster validates prepared dispatch packet step ids, workers, task ids, and
  worker request task ids against the written run status.
- Warmaster cleans up unregistered HTTP-governor run directories when prepare
  fails before ledger creation.
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
- `EyeOfTerror/Warmaster/start_brigade.py` can start the current service-separated
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
- Warmaster Gateway can start local/HTTP execution in a background thread and
  expose progress through the ledger.
- Direct background run start responses expose polling `next_action` and
  executable `client_action` fields.
- Synchronous run execution responses expose post-execution `run_summary`,
  `phase`, `decision`, `display`, `next_action`, and executable
  `client_action` fields.
- Warmaster Gateway exposes `GET /governors`, `GET /workers`, and
  `GET /workers?health=1`; worker listings are enriched with available
  `Mechanicum/*/worker.json` metadata.
- Warmaster governor health snapshots include reachable governor service
  capabilities, including required worker declarations.
- Warmaster Gateway exposes `GET /state` as a client bootstrap snapshot with
  capabilities, governors, workers, and recent runs.
- Warmaster Gateway exposes compact `orchestration_cards` in `/state` and
  `/runs` so chat/mobile clients can restore task history, controls, and status
  displays without parsing full run summaries.
- Run summary responses expose the same top-level `phase`, `decision`,
  `display`, `next_action`, and executable `client_action` fields as
  orchestration views.
- Base run status responses preserve raw status/ledger data and expose
  `summary`, `phase`, `decision`, `display`, `next_action`, and executable
  `client_action` fields when the ledger is readable.
- Orchestration cards and single-run orchestration states share the same
  phase/decision/display builder to keep list and detail views consistent.
- Warmaster Gateway exposes gateway-level action hints for preflight, creation,
  start, resume, revision, cancellation, and diagnostics.
- Warmaster capabilities expose compact registry summaries, display text, and
  an executable state-inspection `client_action` for client bootstrap screens.
- Run action hints include `next_action` with a recommended endpoint and reason
  for chat clients and higher-level governors.
- Warmaster Gateway exposes the expected service-separated brigade topology
  through `GET /brigade_plan` and embeds it in `GET /state`.
- Warmaster Gateway exposes `GET /brigade_health` for combined expected
  topology and best-effort governor/worker health.
- Governor and worker registry endpoints expose compact `summary` and `display`
  fields, including reachable/unreachable counts when health checks are
  requested.
- Brigade health summary exposes `ready`, `blockers`, and `warnings` so
  higher-level orchestrators can decide whether the service-separated brigade is
  runnable without reinterpreting every service health payload.
- Warmaster capabilities advertise brigade readiness checks through
  `brigade_readiness_summary` and `actions.can_check_brigade_readiness`.
- Brigade health reports whether reachable governor `required_workers` are
  present and runnable in the Mechanicum registry.
- Brigade health reports reachable governor pipeline summaries when governor
  services expose them.
- Warmaster Gateway keeps plain `GET /state` lightweight and embeds brigade
  health only when clients request `GET /state?health=1`.
- Warmaster Gateway exposes `GET /doctor` for registry and manifest diagnostics.
- Warmaster Gateway exposes focused run inspection endpoints for contract,
  dispatch packets, and ledger event history.
- Focused contract, oversight, and dispatch inspection endpoints expose
  `run_summary`, `phase`, `decision`, `display`, `next_action`, and executable
  `client_action` fields for client detail screens.
- Warmaster exposes `GET /runs/{task_id}/package` for combined run-package
  diagnostics across contract, oversight, status, and dispatch files.
- Run-package diagnostics include `run_summary`, `phase`, `decision`,
  `display`, `next_action`, and executable `client_action` fields so clients
  can render the diagnostic state and continue from the same response.
- Warmaster action hints block start, resume, and revision actions when
  run-package diagnostics fail and point clients to package inspection.
- Warmaster capabilities expose preferred run-inspection endpoints for clients.
- Warmaster action hints prefer required revision execution over ordinary
  interrupted-run resume.
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
- Bulk recovery start results expose per-run `next_action` and executable
  `client_action` fields for started, already-active, and skipped runs.
- Warmaster Gateway supports cursor-based ledger event polling with
  `/runs/{task_id}/events?after=N`.
- Warmaster Gateway exposes aggregate cursor-based run event polling through
  `/events?after=N` for clients that need one feed across runs.
- Run and aggregate event polling responses expose `display_events` with
  headline, detail, severity, and timestamp for chat/mobile history rendering.
- Step display events expose `worker_display` and `worker_client_action` when
  HTTP worker execution preserved a worker runtime view in the ledger event.
- Per-run event and snapshot polling responses expose executable
  `run_client_action` so task detail screens can update controls without
  rebuilding actions from summaries.
- Run events, worker-task mappings, artifacts, artifact text, and final package
  endpoints expose the standard run detail `phase`, `decision`, `display`,
  `next_action`, and executable `client_action` fields for standalone client
  screens.
- Aggregate run events include run status, governor, run update time, per-run
  event index, and global index for client-side list updates.
- Aggregate run events include the current `run_next_action` and
  executable `run_client_action` plus `run_final_manifest_summary` so polling
  clients can update controls and final quality state without fetching every run
  summary.
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
- Final manifest artifact listings expose `manifest_error` when the manifest
  exists but cannot be parsed.
- Completed run summaries expose `final_manifest_summary` so clients can render
  final quality state without an extra artifact listing request.
- Warmaster exposes `GET /runs/{task_id}/final` so clients and higher-level
  governors can fetch the final manifest, deliverable path, package files, and
  bounded text previews in one request.
- Task ledgers, run packages, and executor reports are written atomically to
  avoid partial JSON reads during background execution.
- Task ledger saves merge concurrent event/step/cancel updates from stale
  in-process ledger instances.
- Task ledger saves preserve terminal status/result and reject direct late
  cancellation of terminal runs.
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
- Warmaster self-tests cover corpus-first revision plans, including
  `CorpusIngestor` through downstream reconstruction and final review steps.
- Warmaster action hints disable ordinary start/execute for interrupted runs so
  clients prefer resume controls.
- Warmaster action hints expose run preflight controls for local and HTTP
  execution.
- Warmaster progress summaries expose ordered planned/completed/failed/pending
  step ids, `next_step_id`, per-step state records, and artifact file status.
- Warmaster run summaries expose the latest run preflight result as
  `last_preflight`.
- Warmaster exposes `GET /runs/{task_id}/steps/{step_id}` for focused step
  inspection with standard run detail client-view fields.
- Warmaster exposes `GET /runs/{task_id}/steps/{step_id}/artifacts` for
  focused artifact inspection by worker step with standard run detail
  client-view fields.
- Warmaster routing rejects unsupported code/image/general tasks until a matching governor exists.
- Warmaster routing is driven by `route_terms` in the governor registry instead
  of hardcoded per-governor keyword lists.
- Warmaster Gateway can request cooperative cancellation through the task ledger,
  and best-effort forwards cancellation to HTTP worker task endpoints from the
  run dispatch package.
- Warmaster cancel responses expose polling or inspection `next_action` and
  executable `client_action` fields.
- Warmaster Gateway rejects late cancellation of terminal runs without
  rewriting completed/failed ledger state.
- Warmaster revision execution uses `revision_plan` to rerun focused worker
  steps, passes `revision_context` into those reruns, and preserves the observed
  `revision_focus` through writer, critic, and final manifest artifacts.
- `ReductorVerifier` expands revision plans through downstream pipeline
  dependencies, so source/fact/timeline fixes also rerun the dependent draft
  and review path instead of leaving stale derived artifacts.
- Warmaster run summaries and snapshots expose `revision_plan_summary` for
  compact client/governor decisions about required revision work.
- `IskandarKhayon` oversight plans include a `revision_policy` describing the
  critic source step, allowed revision steps, required final review steps,
  downstream rerun requirement, and focused revision context requirement.
- Warmaster validates `revision_policy` against the run steps and exposes it in
  compact oversight summaries, so broken revision supervision blocks execution.
- Warmaster rejects revision plans that reference steps outside
  `revision_policy.allowed_steps`, so a governor's review policy constrains
  actual revision execution instead of remaining informational metadata.
- Warmaster validates oversight `step_quality_matrix` entries against run steps,
  workers, artifacts, checks, blockers, and revision targets, so broken per-step
  supervision blocks execution.
- Revision execution appends final review steps from the saved oversight
  `revision_policy.final_steps` instead of relying on hardcoded step names.
- Warmaster enforces `revision_policy.requires_downstream_rerun`, so a
  revision of an upstream step cannot skip stale dependent artifacts.
- `FabricatorFinalis` normalizes final revision plans by merging duplicate
  step ids, preserving multiple reasons/sources, and sorting steps in pipeline
  order before Warmaster executes them.
- Shared local and HTTP worker preflights validate worker-facing
  `quality_expectations.revision_policy` fields before dispatch.
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
- Inner Circle governors are tracked in `EyeOfTerror/Warmaster/registry/governors.json`; Ceraxia is the active code governor and image governance remains planned.
- Warmaster, Governor, and Worker API contracts are covered by self-tests against
  advertised runtime capabilities/endpoints.
- `Ceraxia` now owns code-task governance; `CogitatorCodewrightGovernor` remains only as a disabled legacy stub.
- `ForgeMasterGovernor` now has planned scope documentation but remains inactive.

## Main Check

```bash
EyeOfTerror/check-eye-mechanicum.sh
```

This runs contract tests, worker self-tests,
local executor tests, service tests, and the end-to-end HTTP worker pipeline.

Optional live discovery smoke:

```bash
PYTHONPATH=EyeOfTerror/Scriptorium/Brigade/Lexmechanic LEXMECHANIC_LIVE_DISCOVERY=1 python3 EyeOfTerror/Scriptorium/Brigade/Lexmechanic/live_discovery_smoke.py
```

## Current Limits

- `Lexmechanic` dynamically loads source playbooks plus optional live discovery;
  allowlisted live results can become source candidates.
- `Lexmechanic` ranks source candidates and labels live discovery results with
  source types and ranking reasons.
- `Lexmechanic` records multi-round discovery strategy and `source_coverage`
  diagnostics, including whether the source set is ready for extraction.
- `Lexmechanic` blocks source discovery early when no source candidates are
  available, while still writing source-map diagnostics for recovery.
- `Lexmechanic` also blocks early when candidates exist but source coverage is
  not extraction-ready, preventing weak source sets from flowing downstream.
- `CorpusIngestor` scans `Corpus/` or `SHUSHUNYA_CORPUS_DIR` for local
  `.epub`, `.fb2`, text, markdown, and HTML files before source discovery.
- `CorpusIngestor` reads per-file sidecar metadata such as
  `<file>.metadata.json`, `<file>.meta.json`, or `<file>.epub.json` so local
  corpus entries can declare title, language, source class, reliability, tags,
  aliases, and expected use instead of relying only on filenames and excerpts.
- Research artifacts expose `corpus_requirements` when primary texts are known
  but neither publicly fetchable nor present in the local corpus.
- Local corpus files flow through source maps, snapshots, and direct-event
  evidence instead of bypassing the normal verifier/finalizer path.
- `NoosphericExtractor` dynamically loads event playbooks when available and
  falls back to low-confidence generic evidence leads from fetched source
  snapshots.
- `NoosphericExtractor` preserves `render_required` source snapshots as explicit
  gaps so downstream timeline, draft, and critic artifacts do not hide browser
  render needs.
- `NoosphericExtractor` summarizes event evidence coverage and marks each event
  as snapshot-matched or missing snapshot evidence.
- `NoosphericExtractor` now preserves source class/type/local-path metadata in
  evidence snapshots, separates `primary_evidence_snapshots`, and reports
  primary snapshot/evidence-lead counts so primary text support is measurable
  instead of inferred from source titles later.
- Event playbook `narrative_ru` and review metadata are preserved through
  `direct_event_notes.json` and `timeline.json`, keeping downstream artifacts
  self-contained.
- `EyeOfTerror/Warmaster/doctor.py` validates source and event playbook structure so
  broken domain playbooks fail loudly before a run.
- `ReductorVerifier` uses `source_coverage` diagnostics as source arbitration:
  weak source sets block approval and trigger source-discovery/downstream
  revision instead of passing as warnings.
- `Chronologis` and `ScriptoriumDaemon` preserve generic evidence-lead and
  low-confidence metadata through timeline and coverage artifacts.
- `Chronologis` carries event evidence status and evidence-missing counts into
  timeline summaries.
- `ScriptoriumDaemon` exposes source-coverage readiness in the reader-facing
  reconstruction and machine-readable coverage report.
- `ScriptoriumDaemon` marks primary evidence inline in reconstruction evidence
  excerpts, reports primary event-evidence counts per source, and carries
  `primary_evidence` rows into coverage reports.
- `ScriptoriumDaemon` matches evidence source names fuzzily against source-map
  titles and local corpus paths, so local primary files still show direct
  evidence coverage when filenames differ from canonical book titles.
- `ReductorVerifier` and `FabricatorFinalis` carry evidence-lead risk metrics
  into critic reports and final manifests.
- `ReductorVerifier` tracks primary-evidence source counts for comprehensive
  tasks, blocking approval when all direct evidence comes from secondary
  summaries instead of primary/local/published sources.
- `FabricatorFinalis` writes explicit readiness checks for critic approval,
  package completeness, quality expectations, and source coverage.
- `FabricatorFinalis` blocks final readiness when critic metrics explicitly
  report source coverage as not extraction-ready, even if approval was set.
- `FabricatorFinalis` blocks final readiness when `corpus_requirements.required`
  is still true, even if another metric incorrectly claims comprehensive depth
  passed, and emits a corpus-first upstream revision plan.
- `FabricatorFinalis` also blocks final readiness when required event playbook
  events are absent from the timeline, and emits a downstream revision plan for
  extraction, timeline, and draft regeneration.
- `FabricatorFinalis` also blocks final readiness when required events lack
  direct evidence snapshots, even if their event ids are present in the
  timeline and a critic report incorrectly claims approval.
- Warmaster compact final-manifest summaries expose readiness checks,
  event-review coverage, corpus requirements, and warning/blocker/file counts
  for client displays.
- Iskandar oversight now includes an explicit `iteration_policy` with the
  recommended Warmaster research-loop endpoint, revision triggers, stop
  conditions, and final-readiness checks, so the governor acts as a brigade lead
  instead of only emitting a static worker list.
- `ReductorVerifier` preserves worker `quality_expectations` in critic reports
  and blocks approval when they contradict the current step request.
- `ReductorVerifier` performs generic direct-event coverage checks from
  extracted notes to timeline entries, in addition to task-class playbook checks.
- `ReductorVerifier` applies required-event checks from matched event playbooks
  instead of hardcoded task-specific event lists.
- `FabricatorFinalis` preserves worker `quality_expectations` in final manifests
  and blocks final readiness when they contradict the final step request.
- `AuspexBrowser` performs guarded HTTP text fetches and marks low-text
  scripted HTML with `render_required`.
- `OcularisRenderium` runs as the port 7012 JavaScript render worker prototype:
  it consumes `source_snapshots.json`, writes `rendered_snapshots.json`, uses
  optional Playwright rendering when enabled, and otherwise records structured
  browser-runtime gaps instead of hiding render needs.
- `Ceraxia` runs as the active code governor on port 7104. Code tasks are now
  routed to a named six-worker Mechanicum brigade instead of the disabled
  legacy `CogitatorCodewrightGovernor`.
- The Ceraxia code brigade uses `LogisRepository`, `MagosStrategos`,
  `FerrumPatchwright`, `OrdinatusVerifier`, `JudicatorCodicis`, and
  `SealwrightFinalis` on ports 7015-7020. They share
  `Workers/common/codewright_core.py` helpers but own their stage
  implementation modules inside their worker directories, preserving separate
  worker identities, contracts, ports, and review handoffs.
- Ceraxia's six active code workers expose machine-readable `role_contract`
  metadata with owned step, authority boundary, expected artifacts, and next
  handoff; the worker registry self-test enforces those contracts.
- Ceraxia task plans preserve worker `role_contract` metadata in
  `resolved_workers`, so Warmaster and future clients can inspect each worker's
  authority boundary from the plan payload.
- Ceraxia oversight now carries per-step `role_policy` metadata with authority,
  source-mutation permission, required evidence, and forbidden actions. Dispatch
  requests pass this policy to workers, and code worker artifacts preserve it
  through patch, verification, review, and final manifests.
- `CogitatorCodewright` now enforces Ceraxia's source-mutation role boundary:
  patch application is blocked when a step policy sets
  `may_mutate_source=false`, and verifier repair loops record blockers instead
  of mutating source under a read-only policy.
- `OrdinatusVerifier` now produces `repair_loop_state.json` alongside
  `verification_report.json`, preserving command counts, failed commands,
  applied repairs, blocked repairs, pending blockers, and the next safe action
  for review/finalization.
- `repair_loop_state.json` now exposes `candidate_source_paths` extracted from
  Python traceback frames inside the target repository, giving later repair
  cycles focused source files instead of generic rediscovery.
- When failed verification output does not expose useful source traceback
  frames, `OrdinatusVerifier` falls back to ranked source files from the
  `LogisRepository` repo map.
- `LogisRepository` repo maps now include `recommended_read_order`, and
  `MagosStrategos` carries it into `change_plan.md` so patch workers have an
  explicit inspect-before-mutate sequence.
- Ceraxia patch and final manifests now expose `patch_scope_evidence`, showing
  which changed files were inside the repo map and which were outside the
  surveyed/ranked source surface.
- `LogisRepository` now records Python symbol summaries and suggested unittest
  verification commands, and `MagosStrategos` carries those sections into the
  change plan for downstream patch planning.
- Ceraxia final manifests no longer report `ready` when no source files were
  mutated. The prototype now emits `blocked` with
  `next_safe_action=handoff_to_patch_worker` until a real patch/apply worker
  executes and verifies the code change.
- `FerrumPatchwright` can now apply explicit `CERAXIA_PATCH` operations
  (`replace` and `write_file`) against an explicit `CERAXIA_TARGET_REPO` or the
  current repository. Applied changes record file hashes and `changed_files`.
- `OrdinatusVerifier` runs concrete checks for applied code changes:
  `py_compile` for changed Python files and `git diff --check` when the target
  repository is a git worktree.
- Ceraxia patch specs can request allowlisted verification commands
  (`pytest`, `python -m pytest`, `python -m unittest`, or
  `python -m py_compile ...`). Disallowed commands block final readiness instead
  of running through a shell.
- `FerrumPatchwright` can synthesize patch specs from simpler task markers:
  `CERAXIA_CREATE_FILE`/`CERAXIA_FILE_CONTENT` and
  `CERAXIA_REPLACE_IN_FILE`/`CERAXIA_OLD`/`CERAXIA_NEW`, plus repeated
  `CERAXIA_VERIFY` lines for allowlisted verification commands.
- `FerrumPatchwright` can synthesize multi-file code tasks from
  `CERAXIA_FILES` JSON, preserving every written file in the patch manifest
  and running shared verification through the same allowlisted verifier path.
- `FerrumPatchwright` can infer a narrow natural-language replace patch when
  the task gives explicit backtick-delimited target path, old text, and new
  text, then routes it through the same atomic patch and verifier path.
- `FerrumPatchwright` can infer a narrow natural-language function append when
  the task gives explicit target path, function name, and safe return literal,
  then verifies through the same allowlisted command path; duplicate existing
  Python function definitions are blocked instead of appended.
- `FerrumPatchwright` can infer one missing Python function from a named
  unittest file when the file contains exactly one safe import/assertEqual
  literal candidate and the target module already exists.
- `FerrumPatchwright` can infer one simple return-literal mismatch from a named
  unittest file when the imported function already exists and has exactly one
  simple source return literal.
- `write_file` patch operations are idempotent when the target already has the
  requested content, so repeated Ceraxia runs can prove the same desired state
  without requiring unsafe overwrite flags.
- Ceraxia applies patch operation batches atomically: if one operation fails,
  earlier file mutations in that batch are rolled back before the task is
  reported as blocked.
- Ceraxia patch manifests expose rollback evidence through `rollback.applied`
  and `rollback.files`, so failed atomic batches are auditable instead of only
  surfacing a text blocker.
- Ceraxia patch and final manifests expose `patch_source` and
  `operation_count`, so explicit, marker-synthesized, and inferred code changes
  can be audited separately.
- Test-inferred Ceraxia patches expose `diagnostics` with the test path, target
  module, function name, and expected/actual literals used to derive the edit.
- Ceraxia exposes a machine-readable `patch_contract` through service
  capabilities, task plans, and oversight packages, including supported markers,
  operation types, verification allowlist, safety gates, and repair loops.
- Ceraxia final manifests preserve verification evidence through
  `verification_executed`, `verification_blockers`, and
  `verification_summary`, so the caller can see which commands proved or
  blocked readiness.
- `OrdinatusVerifier` has a first narrow repair loop: when `py_compile`
  reports `SyntaxError: expected ':'` for a changed Python file, it can add the
  missing colon, rerun verification, and expose the repair through
  `verification_repairs` and `verification_summary.repair_count`.
- `OrdinatusVerifier` can also repair a narrow test mismatch pattern:
  `AssertionError: A != B` may update exactly one `return A` in a changed Python
  file to `return B`, rerun the failed allowlisted command, and record the
  repair evidence.
- Python repair writes invalidate matching `__pycache__` entries before
  re-running verification, so repeated checks do not accidentally read stale
  bytecode.
- `OrdinatusVerifier` can repair a narrow NameError pattern when test output
  includes `NameError: name 'x' is not defined`, the failing `assertEqual`
  exposes a simple expected literal, and exactly one changed Python file
  contains `return x`.
- `OrdinatusVerifier` can repair a narrow missing-function import pattern:
  `ImportError: cannot import name 'f' from 'm'` plus
  exactly one discoverable `assertEqual(f(), literal)` in stderr or target
  repo test files appends `def f(): return literal` to changed `m.py` and
  reruns verification.
- The pipeline records inaccessible primary books as `corpus_requirements`;
  operators must provide legitimate local text files when full primary evidence
  is required.
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
- Continue shrinking Ceraxia's shared code-worker helper core by moving more
  role-specific analysis helpers into the owning worker modules.
- Add an image governor instead of routing unsupported image tasks to Iskandar.
