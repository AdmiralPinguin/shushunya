# PlanningBrigade Status

PlanningBrigade is the in-process planning department for Ceraxia.

Current capabilities:

- task classification for bugfix, feature, refactor, migration, security,
  config/runtime, API compatibility, test repair, and concurrency/cache/retry
  work
- problem statement and definition-of-done generation
- explicit path hint extraction
- structured constraint and verification-command intake
- read-only repository survey request shaping
- assumption register for task, repository, verification, and specialized risk
  assumptions, with validation sources and replan/blocker triggers
- investigation playbook with ordered read stages, evidence questions,
  mutation blockers, and replan triggers for CodeBrigade
- change-control planning with allowed change intents, protected invariants,
  mutation requirements, diff-review questions, rollback triggers, and
  post-change proofs
- dependency map and work breakdown generation
- impact analysis across source, tests, public API, security, runtime config,
  data compatibility, internal architecture, and concurrency runtime surfaces
- surface verification matrix generation
- surface-specific output evidence requirements for every planned verification
  row
- execution forecast for CodeBrigade iterations, timeout, and escalation
- risk register, quality bar, acceptance contract, and CodeBrigade handoff
- acceptance trace matrix mapping definition-of-done, quality-bar, and
  acceptance requirements to package ids and planned evidence
- separate definition-of-done trace completeness counts that block incomplete
  original-task fulfillment before CodeBrigade handoff
- constraint trace matrix mapping preserved task/user constraints to package
  ids and planned evidence
- implementation work packages with read scope, edit scope, verification
  scope, risk controls, blocking policy, handoff criteria, and review order
- worker-output package result rows carry acceptance requirements so each work
  package remains tied to the task and quality contract it is meant to satisfy
- implementation brief blueprint coverage for required CodeBrigade sections
  and mutation preconditions
- package dependency graphs that force evidence and specialized boundary
  packages before source mutation and final verification
- package dependency graph execution batches that start with repository survey,
  allow independent specialized packages after survey, and end with final
  verification evidence
- specialized work packages for compatibility, security boundaries, runtime
  configuration, concurrency runtime, and architecture refactors
- package-to-surface traceability: every planned verification surface must be
  covered by at least one implementation work package
- Ceraxia review evidence maps impacted surfaces to matched executed
  verification commands when execution is enabled
- Ceraxia summarizes verification stdout/stderr into output signals and blocks
  inconsistent passed reports that contain failure or traceback evidence
- verification diagnostics now preserve traceback, assertion, syntax, and
  missing-import signals for future repair loops and orchestration summaries
- PlanningBrigade now emits an explicit `diagnostic_repair_plan` that caps
  repair attempts, names required diagnostic inputs, read-before-edit evidence,
  stop conditions, and repair evidence for CodeBrigade
- PlanningBrigade now validates Ceraxia `planning_feedback_request.json`
  artifacts and turns planning/handoff review findings into a replan intake
  checklist with a packet-ready `replan_payload`
- planning review gate with score, blockers, and warnings
- role contracts for the five planning roles
- executable in-process role modules for `TaskTriage`, `RepoSurveyor`,
  `DesignStrategos`, `VerificationArchitect`, and `RiskScribe`; planning
  packets now include `role_execution_trace` with module names, outputs, and
  read-only guarantees
- planned read-only service interface contracts for future role split on
  reserved ports 7111-7115
- service-port reservation checks against the active EyeOfTerror and
  Mechanicum registries
- field trials for multiple task shapes, including a high-risk multi-surface
  release-hardening scenario, with coverage gates for task kinds, impacted
  surfaces, implementation work packages, and change-control
  invariant/proof, acceptance-trace package coverage, constraint-trace package
  coverage, definition-of-done trace completeness, assumption coverage,
  surface-output evidence coverage, worker-output acceptance requirement
  coverage, and implementation-brief blueprint coverage
- CLI validation mode

Current boundaries:

- roles are executable in-process modules, not independent HTTP services yet
- repository evidence is read-only and summarized by Ceraxia
- non-Python source summaries are still shallow symbol/import-like extraction;
  JS/TS relative imports, barrel exports, and side-effect imports are resolved,
  Python package-relative imports are resolved, but this is not a full
  language-aware dependency graph
- CodeBrigade source mutation is limited to the explicit patch adapter and
  narrow guarded natural-language operations; broad natural-language mutation
  remains blocked
- guarded create-file tasks may use explicit allowed new files even when the
  repository has no existing source candidate for that module
- planning quality is regression-tested, but real-world quality still depends
  on reviewed field-trial runs against varied repositories

Next useful upgrades:

- split role contracts into callable services after more field trials
- add richer multi-language dependency graphs beyond current JS/TS relative
  import resolution
- teach review gates to compare command stdout/stderr semantics with each
  impacted surface more deeply than current output-signal and diagnostic counts
- implement CodeBrigade's broader autonomous source-edit adapter against the
  generated diagnostic repair request, existing preflight, verification,
  rollback, and audit contracts
