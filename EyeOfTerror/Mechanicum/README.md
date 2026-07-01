# EyeOfTerror Mechanicum

This folder is the EyeOfTerror-side home for code-governance structure.

- `Ceraxia/` contains the code-brigade governor evaluation protocol, field
  trials, ledger, dry-run lifecycle controller, and review material.
- `CodeBrigade/` contains code-worker grouping and implementation brigade
  contracts.
- `PlanningBrigade/` contains Ceraxia's advisory planning department: task
  triage, repository survey request, design strategy, verification strategy,
  and risk register.

Runtime worker implementations can stay in the top-level `Mechanicum/` service
tree until each brigade boundary is stable.

`boundary_self_test.py` protects this split: brigade governance and contracts
live here, while root-level `Mechanicum/` remains the legacy/shared worker
runtime until a worker is intentionally migrated.

`mechanicum_status.py` reports component maturity for orchestration. It should
stay honest: contract-only components must not be reported as executable
workers until a real adapter exists.

`contracts_self_test.py` checks that generated packets still satisfy the
required fields declared by the local JSON schema files. It is intentionally
small and stdlib-only; detailed schema validation can come later.
