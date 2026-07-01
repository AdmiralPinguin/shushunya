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
- execution forecast for CodeBrigade iterations, timeout, and escalation
- risk register, quality bar, acceptance contract, and CodeBrigade handoff
- acceptance trace matrix mapping definition-of-done, quality-bar, and
  acceptance requirements to package ids and planned evidence
- constraint trace matrix mapping preserved task/user constraints to package
  ids and planned evidence
- implementation work packages with read scope, edit scope, verification
  scope, risk controls, blocking policy, handoff criteria, and review order
- package dependency graphs that force evidence and specialized boundary
  packages before source mutation and final verification
- specialized work packages for compatibility, security boundaries, runtime
  configuration, concurrency runtime, and architecture refactors
- package-to-surface traceability: every planned verification surface must be
  covered by at least one implementation work package
- planning review gate with score, blockers, and warnings
- role contracts for the five planning roles
- planned read-only service interface contracts for future role split on
  reserved ports 7111-7115
- service-port reservation checks against the active EyeOfTerror and
  Mechanicum registries
- field trials for multiple task shapes with coverage gates for task kinds,
  impacted surfaces, implementation work packages, and change-control
  invariant/proof, acceptance-trace package coverage, constraint-trace package
  coverage, and assumption coverage
- CLI validation mode

Current boundaries:

- roles are still in-process, not independent services
- repository evidence is read-only and summarized by Ceraxia
- non-Python source summaries are shallow symbol/import-like extraction, not a
  full dependency graph
- CodeBrigade source mutation is limited to the explicit patch adapter and
  remains blocked for broad natural-language mutation
- planning quality is regression-tested, but real-world quality still depends
  on reviewed field-trial runs against varied repositories

Next useful upgrades:

- split role contracts into callable services after more field trials
- add richer multi-language dependency graphs
- teach review gates to compare executed command output with each impacted
  surface
- expand CodeBrigade from explicit and guarded natural-language patch
  operations toward diagnostic autonomous source edits behind the existing
  preflight, verification, rollback, and audit contracts
