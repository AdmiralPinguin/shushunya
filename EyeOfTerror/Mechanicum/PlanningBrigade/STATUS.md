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
- dependency map and work breakdown generation
- impact analysis across source, tests, public API, security, runtime config,
  data compatibility, internal architecture, and concurrency runtime surfaces
- surface verification matrix generation
- execution forecast for CodeBrigade iterations, timeout, and escalation
- risk register, quality bar, acceptance contract, and CodeBrigade handoff
- implementation work packages with read scope, edit scope, verification
  scope, risk controls, handoff criteria, and review order
- planning review gate with score, blockers, and warnings
- role contracts for the five planning roles
- field trials for multiple task shapes with coverage gates for task kinds,
  impacted surfaces, and implementation work packages
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
- expand CodeBrigade from explicit patch operations toward safe
  natural-language patch planning behind the existing preflight, verification,
  rollback, and audit contracts
