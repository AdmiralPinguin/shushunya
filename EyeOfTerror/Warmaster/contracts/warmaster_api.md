# Warmaster API Contract

Warmaster Gateway is the user-facing orchestration entrypoint. Clients should
use it as the source of truth for task submission, state bootstrap, run
inspection, cancellation, and service diagnostics.

## Core Endpoints

```text
GET  /health
GET  /capabilities
GET  /state
GET  /state?health=1
GET  /recovery
GET  /doctor
GET  /brigade_plan
GET  /brigade_plan?host=127.0.0.1
GET  /brigade_health
GET  /brigade_health?host=127.0.0.1
GET  /governors
GET  /governors?health=1
GET  /workers
GET  /workers?health=1
GET  /events
GET  /events?limit=20
GET  /events?after=0
POST /task_preflight
POST /campaign_preflight
POST /campaign
GET  /campaigns
GET  /campaigns/{campaign_id}
POST /campaigns/{campaign_id}/start
POST /campaigns/{campaign_id}/resume
POST /campaigns/{campaign_id}/cancel
POST /orchestrate
POST /orchestrate_start
POST /orchestrate_run
GET  /runs
GET  /runs?limit=20
GET  /runs/{task_id}
GET  /runs/{task_id}/summary
GET  /runs/{task_id}/snapshot
GET  /runs/{task_id}/activity
GET  /runs/{task_id}/orchestration
GET  /runs/{task_id}/active
GET  /runs/{task_id}/steps/{step_id}
GET  /runs/{task_id}/steps/{step_id}/artifacts
GET  /runs/{task_id}/ledger
GET  /runs/{task_id}/package
GET  /runs/{task_id}/contract
GET  /runs/{task_id}/oversight
GET  /runs/{task_id}/dispatch
GET  /runs/{task_id}/worker_tasks
GET  /runs/{task_id}/worker_tasks?live=1
GET  /runs/{task_id}/events
GET  /runs/{task_id}/events?limit=20
GET  /runs/{task_id}/events?after=0
GET  /runs/{task_id}/artifacts
GET  /runs/{task_id}/final
GET  /runs/{task_id}/final?max_bytes=1000
GET  /runs/{task_id}/artifact_text?path=/work/...
GET  /runs/{task_id}/artifact_text?path=/work/...&max_bytes=1000
GET  /runs/{task_id}/artifact_text?path=work/skitarii.patch
POST /runs/{task_id}/apply_patch
POST /runs/{task_id}/preflight_local
POST /runs/{task_id}/preflight_http
POST /runs/{task_id}/execute_local
POST /runs/{task_id}/execute_http
POST /runs/{task_id}/execute_revision_local
POST /runs/{task_id}/execute_revision_http
POST /runs/{task_id}/resume_local
POST /runs/{task_id}/resume_http
POST /runs/{task_id}/start_local
POST /runs/{task_id}/start_http
POST /runs/{task_id}/start_revision_local
POST /runs/{task_id}/start_revision_http
POST /runs/{task_id}/start_resume_local
POST /runs/{task_id}/start_resume_http
POST /runs/{task_id}/research_loop_local
POST /runs/{task_id}/research_loop_http
POST /runs/{task_id}/start_research_loop_local
POST /runs/{task_id}/start_research_loop_http
POST /recovery/start_resume_local
POST /recovery/start_resume_http
POST /runs/{task_id}/cancel
POST /recover_stale
```

On normal server startup, Warmaster creates the run root and marks stale
`running`/`cancelling` ledgers as `interrupted`. Operators can disable this with
`--no-recover-stale-on-start` for diagnostics.

Operators can choose the default governor planning boundary with
`--governor-transport local|http` and `--governor-host`. Per-request
`governor_transport` and `governor_host` override those defaults.

## Client Bootstrap

Clients should call `GET /state` after startup or reconnect. The response
contains gateway capabilities, governor registry, worker registry, run status
counts, process-local active run ids, recent run summaries, and the expected
service-separated brigade startup plan. It also includes a compact `recovery`
section listing interrupted runs whose action hints allow resume. It also
includes `orchestration_cards`, a compact chat/mobile list with each recent
run's phase, decision, display status, and next action for restoring task
history after a client reconnect. `state.actions` and
`capabilities.actions` expose client-facing gateway action hints, including the
preferred command-protocol flow: `POST /orchestrate_run`, then poll the run
orchestration/snapshot endpoints. `can_check_brigade_readiness`
means clients can use `GET /brigade_health` summary fields to decide whether
the service-separated brigade is runnable. `GET /capabilities` also includes a
compact registry `summary`, `display`, and executable `client_action` pointing
clients to the current state snapshot.
Clients can call `GET /recovery` when they only need the recoverable interrupted
run list without a full bootstrap snapshot. Each candidate reports
`resume_ready`, `resume_errors`, pending step ids, and executable
`client_action` method/path/body fields. Candidates also include compact
`display` fields so chat/mobile clients can render startable recovery work and
malformed run packages without rebuilding recovery diagnostics.
Operators can call `POST /recovery/start_resume_local` or
`POST /recovery/start_resume_http` to start all currently recoverable
interrupted runs in the background. The response is per-run: a malformed or
incomplete run package must be reported as skipped instead of blocking other
recoverable runs. Each per-run result carries `next_action` and executable
`client_action` for polling started runs, polling already-active runs, or
inspecting skipped run packages.
Use `GET /state?health=1` for an admin/bootstrap snapshot that also includes
best-effort `brigade_health`; plain `/state` stays lightweight for polling.

`GET /brigade_plan` returns the same expected service topology without run
history. The optional `host` query parameter must be loopback. The response
includes `startup_stages` so admin clients can start dependency-free services
first, wait for their health URLs, then start dependent services.
`EyeOfTerror/Warmaster/start_brigade.py --wait-ready` uses these stages when launching the
local stack.

`GET /brigade_health` combines that topology with best-effort health checks for
governors and workers. It also reports governor worker requirements when a
reachable governor exposes `required_workers` from `/capabilities`, and
governor pipeline summaries when reachable governors expose `pipeline`. The
compact `summary` includes `ready`, `blockers`, and `warnings`; planned services
are warnings, while unreachable runnable services or unsatisfied governor worker
requirements are blockers.

`GET /governors` and `GET /workers` include compact `summary` and `display`
fields for registry screens. `GET /governors?health=1` includes reachable
governor `/capabilities` payloads inside each governor runtime snapshot, and
health-checked registry summaries include reachable/unreachable counts.

## Task Creation

`POST /task_preflight` accepts routing fields for diagnostic review, but does
not write a run package or ledger. It returns the selected route, governor,
contract validation result, missing worker references, and the run directory
that would be created. It also returns a compact `contract_summary` with planned
step ids, workers, dependency edges, expected artifact paths, and artifact
counts for client review. When the governor exposes oversight, task preflight
also returns `oversight_summary` and `oversight_validation` so clients can
inspect final review expectations before creating a run package.
When the governor plan exposes action hints, task preflight preserves them in
`governor_plan_actions` so higher-level orchestrators can compare Warmaster's
next step with the selected governor's own prepare-run recommendation.
Set `include_brigade_health=true` to include compact `brigade_readiness`
(`ready`, blocker/warning counts, blockers, and warnings) in the preflight
response without fetching the full `/brigade_health` service payload.
Task preflight responses include `actions.can_create_task` and
`actions.next_action` so chat clients can move into `POST /orchestrate`, inspect
an existing run after a `task_id` conflict, or inspect brigade/governor
diagnostics after validation failures. HTTP responses also expose top-level
`phase`, `decision`, `display`, `next_action`, and executable `client_action`
fields for the same recommendation.
Clients submit work through `POST /orchestrate_run`. There is no direct task
creation endpoint that bypasses Abaddon's commander-order and orchestration
boundary.
When task preflight is run with explicit or default HTTP governor transport,
its `create_task` and retry action bodies preserve `governor_transport` and
`governor_host` so clients can follow `actions.next_action` without switching
planning boundaries. Task preflight action bodies include the original
`message`, making successful `create_task` hints directly executable by simple
chat clients. Rejected task creation responses that recommend retrying task
preflight preserve the same executable body shape.

When `POST /task_preflight` returns
`error_code=multi_governor_decomposition_required`, clients should follow
`actions.next_action` to `POST /campaign_preflight` instead of retrying a
single run. `POST /campaign_preflight` builds a strict `campaign_plan.json`
without writing state. `POST /campaign` persists `campaign_plan.json` and
`campaign_state.json` under the service campaign directory, then recommends
`POST /campaigns/{campaign_id}/start`. `GET /campaigns` lists campaign cards,
and `GET /campaigns/{campaign_id}` returns the plan, current state, handoff
records, and final report when available. `POST /campaigns/{campaign_id}/start`
runs the campaign in the background, while
`POST /campaigns/{campaign_id}/resume` advances ready subruns synchronously for
debugging. `POST /campaigns/{campaign_id}/cancel` marks the campaign cancelled
and cooperatively forwards cancellation to any non-terminal subrun ledgers and
HTTP worker task endpoints.

`POST /orchestrate` is a prepare-only orchestration helper for chat clients. It
performs task preflight, task creation, and run preflight in order, records the
run preflight event, and returns a `trace` plus the next safe action. It does
not start worker execution; successful responses end at `phase=ready_to_start`
with a `start_*` recommendation in `next_action` and executable
`client_action` fields for simple clients. Failed prepare responses also carry
`client_action` when their `next_action` can be executed directly.

`POST /orchestrate_start` starts an existing prepared run in the background only
when Warmaster run-summary action gates allow it. It chooses normal start,
resume, or required revision execution from `summary.actions`, records the
background-start event, and returns an immediate `snapshot` plus a polling
`next_action` and executable `client_action`. Completed runs are not rerun
unless `force=true` is explicit.

`POST /orchestrate_run` is the one-shot chat submission helper. It performs the
same prepare sequence as `POST /orchestrate` and, by default, immediately follows
with `POST /orchestrate_start` semantics when the prepared run is startable. It
returns the prepare payload, start payload, trace, current orchestration state,
top-level `decision`/`display` copies for chat UI, and polling `next_action`.
Set `auto_start=false` to stop after preparation while keeping the same response
envelope. With the default `reuse_existing=true`, repeating the same stable
`task_id` returns the existing run orchestration state instead of treating
reconnects or client retries as hard failures.

`GET /runs/{task_id}/orchestration` is the read-only decision view for chat
clients. It wraps the run snapshot with an orchestration `phase` such as
`running`, `completed`, `ready_to_start`, `resume_required`,
`revision_required`, or `needs_attention`, preserves the next safe action, and
includes the bounded final package when the run has completed. Its `decision`
object exposes booleans such as `can_poll`, `can_start`, `can_resume`,
`can_execute_revision`, `can_inspect_final`, and `can_inspect_diagnostics` so
clients do not need to reimplement phase parsing. Its `mission_state` object is
the canonical lifecycle view for clients and diagnostics: it contains
`mission_id`, `task_id`, normalized lifecycle `status`, raw `run_status`,
`mission_status`, current `phase`, `active`, `assigned_governor`, `next_owner`,
`user_visible_state`, and `revision_is_internal=true`. Older top-level
`status`, `phase`, `active`, and mobile `running/success/cancelled` fields are
compatibility fields; new UI should prefer `mission_state`. Its `display`
object exposes compact chat/UI fields such as `headline`, `detail`, `severity`, progress
counts, next step/worker, and final deliverable path so clients do not need to
parse the full run summary for common status rendering. The response also
copies bounded `display_events` to the top level for task-detail history views.
It also includes `governor_activity`, a chat-independent brigade-tab report.
`governor_activity.progress_events` is the primary mission-protocol stream
loaded from `progress_events.jsonl`; `protocol_activity_cards` is the direct UI
card projection of those events. `summary_activity_cards` adds diagnostic
run-summary cards such as task received, step states, artifacts, revision
blockers, and final report. `activity_cards` concatenates protocol cards first
and summary cards second. These fields are for observability only; they are not
the answer that Shushunya later sends to the main chat, and clients must not use
text logs as the primary activity source.
`client_action` contains an executable method/path/body form of `next_action`
with `{task_id}` already resolved for simple clients.

```json
{
  "message": "User task text",
  "task_id": "optional-stable-id",
  "governor_transport": "local|http",
  "governor_host": "optional-loopback-host",
  "run_mode": "local|http",
  "include_brigade_health": true,
  "auto_start": true,
  "reuse_existing": true
}
```

```json
{
  "task_id": "existing-run-id",
  "run_mode": "local|http",
  "host": "127.0.0.1",
  "timeout_sec": 1800,
  "force": false
}
```

Task ids are durable run identifiers. Creating a task with an existing `task_id`
must return a conflict instead of overwriting the run history.

If provided, `task_id` must match `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}` and must
not contain `..`.

`governor_transport` defaults to `local`. When set to `http`, Warmaster calls
the selected active governor service on its registry port and writes the
Warmaster ledger after the governor prepares the run package. `governor_host`
must be loopback. If the gateway was started with `--governor-transport http`,
clients can omit this field and still use the service-separated path.

For worker-planned governor domains, Warmaster reads reachable governor
capabilities and rejects the task with `error_code=governor_workers_missing`
when `required_workers` are absent from the Mechanicum registry. Native Ceraxia
missions declare a warband backend instead; readiness is checked against the
Skitarii service and its loaded-source identity.

For worker-planned domains, Warmaster rejects a produced task contract with
`error_code=contract_workers_missing` when `worker_plan` references workers that
are absent from the Mechanicum registry. If all unresolved workers are known but
`planned` and not runnable, the response uses
`error_code=contract_workers_unavailable` and includes `unavailable_workers`.

For worker-planned domains, Warmaster rejects task creation with
`error_code=invalid_oversight` when the governor plan omits oversight or the
oversight does not match the task contract. This prevents creating run packages
that cannot later pass run preflight.

For HTTP governor preparation, Warmaster verifies the written run package before
creating the ledger. Worker-planned domains verify contract, oversight, status,
and dispatch consistency. Native Ceraxia code runs instead verify
`contract.json`, `ceraxia_directive.json`, `governor_plan.json`, `status.json`,
and their SHA-bound prepare receipt; the package must contain exactly one
`skitarii_mission` execution step and must not contain `worker_plan` or a
`dispatch/` directory. Any mismatch fails with
`error_code=governor_prepare_invalid_run`.
When a governor prepare failure leaves an unregistered run directory without
`task_ledger.json`, Warmaster attempts to remove that directory and reports the
result in `cleanup`.

When routing terms match a planned but inactive governor, task creation and task
preflight return `error_code=governor_inactive`, the matched `governor`, `kind`,
and a compact `route` object. Planned-governor route failures also include
`required_governor` with registry metadata such as status, port, service,
task kinds, and route terms so clients and operators can see which coordinator
must be implemented or activated. When no route matches, they return
`error_code=no_supported_governor`.

## Run Inspection

Clients should use:

- `/runs` for a run list plus aggregate status, recoverable interrupted run
  summary, and compact `orchestration_cards` for chat/mobile list rendering.
- `/events?after=N` for a compact aggregate run-event feed when the client
  wants one polling cursor across all runs. Responses include `display_events`
  with compact headline/detail/severity fields for chat/mobile history views.
  Step events produced by HTTP worker services may also include
  `worker_display` and `worker_client_action` copied from the worker runtime
  response. Those fields describe the worker service task, not a Warmaster
  endpoint.
- `/runs/{task_id}/summary` for lightweight polling.
- `/runs/{task_id}/snapshot` for a compact polling view containing summary,
  process-local active state, cursor events, executable `run_client_action`, and
  artifact metadata. It includes `governor_activity` for brigade-tab rendering.
  Completed run summaries include `final_manifest_summary` when the final
  artifact is available.
- `/runs/{task_id}/activity` for only the brigadier activity report. Use this in
  brigade tabs when the UI needs structured `progress_events`,
  `protocol_activity_cards`, `summary_activity_cards`, and `activity_cards`
  without fetching final artifacts or mixing the report with Shushunya's chat
  response. The response also includes the same canonical `mission_state` as the
  orchestration view.
- `/runs/{task_id}/steps/{step_id}` for one normalized step state from
  `summary.progress.step_states`. The response includes the standard run detail
  client-view fields.
- `/runs/{task_id}/steps/{step_id}/artifacts` for expected and produced
  artifact status scoped to one worker step. The response includes the standard
  run detail client-view fields.
- `/runs/{task_id}/events` for ledger event history.
- `/runs/{task_id}/events?after=N` for incremental client polling. Responses
  include `cursor.after`, `cursor.next`, `cursor.total`, `display_events`, and
  executable `run_client_action`, plus the standard run detail `phase`,
  `decision`, `display`, `next_action`, and executable `client_action`.
  Raw step events preserve compact worker runtime state under
  `payload.details.worker_view` when the step was executed through the common
  Mechanicum worker API.
Aggregate `/events` responses include the same cursor shape plus `task_id`,
`run_status`, `governor`, `run_updated_at`, `event_index`, `global_index`,
`run_next_action`, executable `run_client_action`, and
`run_final_manifest_summary` for each event.
- `/runs/{task_id}/artifacts` for result artifact metadata. If the final result
  is a `final_manifest.json`, the response should also expand manifest `files`
  so clients can list and fetch the whole final package. The final manifest
  artifact item includes `manifest_summary` with status, critic status, critic
  metrics, event-review coverage, corpus requirements, readiness checks,
  revision focus, warnings, and blockers, or `manifest_error` when the manifest
  exists but cannot be parsed. The response includes the standard run detail
  client-view fields.
- `/runs/{task_id}/final` for a completed final package in one response:
  manifest summary, full manifest object, deliverable path, package files, and
  bounded text previews. `max_bytes` limits each file preview and is clamped by
  the same artifact text maximum. The response includes the standard run detail
  client-view fields.
- `/runs/{task_id}/artifact_text` and `/runs/{task_id}/worker_tasks` include the
  same run detail client-view fields so artifact and worker-task screens can
  render the current run state without a follow-up summary request.
- `/runs/{task_id}/apply_patch` is the authenticated retry/manual entry point
  for the Skitarii repository transaction. It requires the recorded repository,
  patch, and verification-set fingerprints. Production ordinary orchestration
  enters this transaction automatically: verify, live apply, post-check,
  mission-path-only commit on `main`, non-force push to `origin/main`, remote
  byte proof, then protocol completion. `apply_intent`, `applied_unverified`,
  `publishing`, `push_pending`, and `protocol_finalize_pending` are durable
  recoverable states; the gateway resumes them after restart without regenerating
  code, reapplying a committed patch, or requiring an operator click.
- `/runs/{task_id}/package` for run-package diagnostics across
  `contract.json`, `oversight.json`, `status.json`, and dispatch packets. The
  response also includes `run_summary`, `phase`, `decision`, `display`,
  `next_action`, and executable `client_action` fields so clients can render the
  diagnostics and continue with the recommended run action without a separate
  summary fetch.
- `/runs/{task_id}/contract` and `/runs/{task_id}/dispatch` for orchestration
  debugging. Focused run inspection responses include the same `run_summary`,
  `phase`, `decision`, `display`, `next_action`, and executable
  `client_action` fields as package diagnostics.
- `/runs/{task_id}/oversight` for immutable governor supervision saved with the
  run package. Worker-planned domains expose their oversight plan. Native code
  runs expose Ceraxia's leadership directive and the single-delegation governor
  plan, with validation against the SHA-bound native package.
- `/runs/{task_id}/worker_tasks?live=1` when worker services are running and
  live worker task state is needed.

Run summaries include an `actions` object with client-facing booleans:
`can_preflight_local`, `can_preflight_http`, `can_execute`, `can_start`,
`can_cancel`, `can_resume`, `can_execute_revision`, `can_start_revision`, and
`force_required_for_rerun`. Clients should use these hints for button state
instead of duplicating Warmaster status rules. When `revision_plan.required` is
true, ordinary `can_execute` and `can_start` are false; clients should use the
revision actions instead. When a run is `interrupted`, ordinary `can_execute`
and `can_start` are false; clients should use resume actions instead unless a
required revision plan is present.
`GET /runs/{task_id}/summary` also includes top-level `phase`, `decision`,
`display`, `next_action`, and executable `client_action` fields derived from
the same orchestration view used by run cards.
`GET /runs/{task_id}` preserves raw `status` and `ledger` fields while also
including `summary`, `phase`, `decision`, `display`, `next_action`, and
executable `client_action` when the ledger can be read.
Revision actions are false when the required `revision_plan` is structurally
invalid or references workers that do not match the run dispatch package;
summaries expose these diagnostics as `revision_plan_errors`.
Summaries and snapshots also expose `revision_plan_summary` with `required`,
`valid`, compact step/worker lists, top reasons, and validation errors so chat
clients and higher-level governors can choose revision controls without parsing
the full raw plan.
Start, resume, and revision actions are false when `package_errors` is
non-empty; `actions.next_action.kind=inspect_package` points clients to
`GET /runs/{task_id}/package` for diagnostics.
Start, resume, and revision actions are also false when `oversight_errors` is
non-empty; `actions.next_action.kind=inspect_oversight` points clients to
`GET /runs/{task_id}/oversight` for diagnostics.

`actions.next_action` gives chat clients and higher-level governors one
recommended next operation with `kind`, `method`, `endpoint`, `body`, and
`reason`. It must prefer package inspection for invalid run packages, oversight
inspection for invalid governor oversight, revision inspection for invalid
revision plans, revision execution for valid required revisions, resume for
interrupted runs without a required revision plan, polling for active runs, and
force-gated rerun guidance for completed runs. When the recommendation is a
completed-run rerun, `body.force` must be true.

Gateway capabilities expose `actions.run_inspection` with the preferred
run-specific diagnostic endpoints for chat clients that need to render
inspection buttons or follow `actions.next_action` without hardcoded URLs.

Run summaries include `oversight_summary` when the run package has
`oversight.json`. It is a compact view of the governor's run-specific
supervision plan: governor, kind, artifact role highlights, quality gate count,
completion criteria count, handoff count, step quality matrix counts, final
review requirements, and revision policy. Use `/runs/{task_id}/oversight` for
the full oversight object.

Run summaries also expose `last_preflight` when the ledger has a recorded run
preflight. It contains the event timestamp, mode, selected steps, result, and
failure counters so clients do not have to parse the full event stream for the
latest startup check.

Run summaries also include ordered progress hints:
`planned_step_ids`, `completed_step_ids`, `failed_step_ids`,
`pending_step_ids`, `ready_step_ids`, `blocked_step_ids`, `waiting_step_ids`,
`next_step_id`, `next_ready_step_id`, and `step_states`. These are derived from
the run package and ledger records, so clients can display restart/resume
position, worker ownership, dependency readiness, expected artifacts, produced
artifacts, and per-step summaries without parsing dispatch packets. Progress
also includes count fields for pending, ready, blocked, and waiting steps. Step
states include
`depends_on`, `dependency_status`, `dependencies_ready`, `dependencies_blocked`,
`input_artifacts`, `expected_artifacts`, compact `quality_hints`, and produced
`artifacts`; each artifact set also has status entries with `exists`, `bytes`,
and `host_path` when a run workspace is known. `quality_hints` exposes check
counts, blocker counts, and revision targets from the governor oversight
without requiring clients to parse dispatch packets. When an HTTP worker step
preserved runtime state in the ledger, its step state includes `worker_view`
with the worker runtime's compact `display`, `decision`, `next_action`, and
`client_action`.

## Cancellation

`POST /runs/{task_id}/cancel` marks the Warmaster ledger as cancelling and
best-effort forwards cancellation to HTTP worker task endpoints from the run
dispatch package. Cancellation is cooperative unless a worker implements a
stronger interruption mechanism.
Cancelling an already terminal run must return a conflict and must not rewrite
the recorded terminal ledger status. Cancel responses include `next_action` and
executable `client_action` fields for polling cooperative cancellation or
inspecting an already-terminal run.

## Execution Paths

`POST /runs/{task_id}/preflight_local` and
`POST /runs/{task_id}/preflight_http` inspect an existing run package without
executing workers. They report missing, corrupt, or run-inconsistent governor
oversight, unreadable dispatch packets, missing local worker commands, HTTP
worker health failures, and input artifact failures. Full-run
preflight treats artifacts produced by earlier selected steps as satisfiable by
the same run; restricted preflight can accept `step_ids` and then requires
unselected dependency artifacts to already exist when a `workspace_root` is
provided. Run preflight responses include `actions.next_action` recommending
start execution after success or package/oversight/brigade diagnostics after
failure. Successful run preflight still respects the current run-summary action
gates, so completed, interrupted, active, or revision-required runs return the
same force, resume, poll, or revision recommendation instead of an unsafe plain
start. Each run preflight records a compact `run_preflight_recorded` ledger
event with mode, selected steps, result, and failure counts. Run preflight
responses include the current `run_summary`, `phase`, `decision`, `display`,
top-level `next_action`, and executable `client_action`.
Direct background start endpoints such as `POST /runs/{task_id}/start_local`,
`POST /runs/{task_id}/start_http`, `POST /runs/{task_id}/start_revision_*`, and
`POST /runs/{task_id}/start_resume_*` return a polling `next_action` and
executable `client_action` when a run starts or is already active.
Research loop endpoints run the current package through start, resume, and
revision decisions until the final package is ready or a bounded stop condition
is hit. `research_loop_local` and `research_loop_http` are synchronous;
`start_research_loop_local` and `start_research_loop_http` run in the
background and return polling actions. Request bodies may include
`max_revision_cycles` from `0` to `8`, `timeout_sec`, `host`, and
`allow_resume`. The loop records `research_loop_*` ledger events and stops on
completion, invalid revision plans, execution failure, repeated revision-plan
fingerprints, or the revision cycle limit.
Synchronous execution endpoints such as `POST /runs/{task_id}/execute_local`,
`POST /runs/{task_id}/execute_http`, `POST /runs/{task_id}/resume_*`, and
`POST /runs/{task_id}/execute_revision_*` include the post-execution
`run_summary`, `phase`, `decision`, `display`, `next_action`, and executable
`client_action`.

Local and HTTP executors must convert malformed dispatch packets into
structured failed or preflight-failed run results instead of crashing before the
ledger can record the failure.

Native Ceraxia code runs never enter either generic executor. All normal start,
resume, revision, recovery, and default auto-start decisions pass through the
central backend router and delegate the single native mission to Skitarii.

Execution endpoints also accept optional `step_ids` for orchestrator-controlled
step subsets. `step_ids` must be a list of unique non-empty strings and every
requested id must exist in the run package. Unknown or duplicate step ids must
be rejected before dispatch. For `resume_*` and `*_revision_*` endpoints, a
client-provided subset must stay inside the automatically eligible resume or
revision step set.

When a selected subset succeeds but does not cover the whole run package, the
executor response includes `partial_execution=true`, the requested step ids, and
the execution mode. The ledger must remain `interrupted` instead of
`completed`, so clients and higher-level governors continue through resume
actions rather than treating a partial worker call as a finished user task.

If execution endpoints accept `workspace_root`, that path must stay inside the
selected run directory. Relative paths are resolved below the run directory.

If execution, cancellation, or live worker task endpoints accept `host`, it must
be a loopback worker service host such as `127.0.0.1` or `localhost`.

Revision execution endpoints use the current run ledger `revision_plan` and run
only those dispatch steps, followed by the saved oversight
`revision_policy.final_steps` (`critic_review` and `finalize` for Iskandar lore
reconstruction runs). They must reject runs that do not have
`revision_plan.required=true` and must reject revision steps outside the saved
oversight `revision_policy.allowed_steps`. When
`revision_policy.requires_downstream_rerun=true`, Warmaster also rejects
revision plans that rerun an upstream dispatch step without its non-final
dependent steps.

Resume execution endpoints run only `pending_step_ids` from an `interrupted`
run package through the selected executor and must reject runs whose ledger
status is not `interrupted`. They record `resume_execution_requested` before
dispatch so clients can audit manual recovery.

Manual `POST /recover_stale` remains available for diagnostics and maintenance,
but clients should not need to call it after a normal gateway restart.

When a revision rerun reaches the writer, the executor passes a focused
`revision_context` from the previous `revision_plan`. Writer artifacts should
record that context, the critic should expose whether it saw the revision focus,
and the final manifest should carry `revision_focus` for client display and
debugging.

## Rules

- Warmaster must not do specialist worker jobs directly.
- Warmaster must preserve existing run history.
- Warmaster must expose corrupt or interrupted run state diagnostically instead
  of hiding the run or crashing the client endpoint.
- Warmaster must route only to active governors.
