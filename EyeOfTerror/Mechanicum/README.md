# EyeOfTerror Mechanicum

This folder is the EyeOfTerror-side home for code-governance structure.

- `Ceraxia/` contains the code-brigade governor evaluation protocol, field
  trials, ledger, dry-run lifecycle controller, run package audit, summary
  schema, per-surface verification review, repository read-order evidence, and
  review material.
- `CodeBrigade/` contains code-worker grouping and implementation brigade
  contracts. It can accept dry-run handoffs, run allowlisted verification, and
  perform read-only execution preflight checks, but real source mutation is
  still intentionally blocked.
- `PlanningBrigade/` contains Ceraxia's advisory planning department: task
  triage, problem framing, path-hint extraction, repository survey request,
  dependency mapping, work breakdown, impact analysis, surface verification
  mapping, acceptance contracts, self-review, risk register, role quality
  gates, and field-trial coverage gates.

Runtime worker implementations can stay in the top-level `Mechanicum/` service
tree until each brigade boundary is stable.

`boundary_self_test.py` protects this split: brigade governance and contracts
live here, while root-level `Mechanicum/` remains the legacy/shared worker
runtime until a worker is intentionally migrated.

`mechanicum_status.py` reports component maturity for orchestration. It should
stay honest: contract-only components must not be reported as executable
workers until a real adapter exists. Its JSON output also carries the current
architecture roadmap, ordered by priority.

`contracts_self_test.py` checks that generated packets and run artifacts still
satisfy the required fields declared by the local JSON schema files, including
execution policy, execution result, evidence matrix, and run summary contracts.
It is intentionally small and stdlib-only; detailed schema validation can come
later.

Current source mutation status: narrow explicit-patch only. `CodeBrigade` can
apply `CERAXIA_PATCH` operations against surveyed repo-relative files after
brief validation and preflight. The next hard architecture step is expanding
that adapter beyond explicit patches while preserving validation, preflight,
execution result, verification, rollback, and audit contracts.

For fast local iteration inside this folder, run:

```bash
EyeOfTerror/Mechanicum/check-mechanicum-local.sh
```

The repository-level `EyeOfTerror/check-eye-mechanicum.sh` remains the full
integration gate before committing.
