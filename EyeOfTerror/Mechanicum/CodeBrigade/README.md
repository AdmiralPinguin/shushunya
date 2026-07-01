# Code Brigade

Ceraxia's implementation-side Mechanicum brigade.

Likely members:

- `CogitatorCodewright`
- `FerrumPatchwright`
- `OrdinatusVerifier`
- `JudicatorCodicis`
- `SealwrightFinalis`

The current folder owns the handoff contract from Ceraxia to code workers. Real
worker implementations still run from the top-level `Mechanicum/` service tree
until the specialized brigade is split into separate services.

## Current Contract

Ceraxia writes `implementation_brief.json` before any code worker may mutate
source. The brief must state:

- task kind and risk level;
- selected strategy from `PlanningBrigade`;
- allowed scope;
- forbidden approaches;
- required verification;
- acceptance gates.

Workers return `worker_report.json` using
`code_brigade_contract.schema.json`. `code_brigade_adapter.py` is the current
local adapter: it validates the implementation brief and can acknowledge a
dry-run handoff. Real source execution is intentionally narrow: only explicit
`CERAXIA_PATCH` operations and guarded natural-language single-file operations
against surveyed repo-relative files may pass. A second guarded diagnostic path
can append one missing Python function or module-level constant when existing
tests provide a single imported or module-qualified symbol and safe literal
expectation, or replace a single literal return expression / module-level
constant assignment when that same evidence proves a mismatch.
`implementation_brief_contract.py` owns brief validation shared by the report
adapter and execution adapter.
`execution_adapter.py` is that boundary today. It applies explicit
`CERAXIA_PATCH` `replace`, `replace_return_expression`, `write_file`, and
`create_file` operations, plus guarded natural-language simple replace,
Python add-function, and explicit missing-file creation operations, and a
test-inferred missing-function operation, literal return-mismatch operation,
missing-constant operation, or constant mismatch operation, only after brief
validation and read-only preflight.
Ambiguous tasks still return a formal
`code_brigade_execution_result` blocker.
`execution_contract.py` owns the formal execution result builders so the
execution boundary does not depend on the full worker-report adapter.
`execution_preflight.py` performs read-only mutation preflight checks for the
future real executor: repo availability, scope evidence, survey candidates or
explicit allowed new files, and verification command counts.
The worker report must include `implementation_plan`, which preserves survey
candidate files, test files, local dependency edges, investigation playbook
read stages, caller candidates, contract surface candidates, change-control
protected invariants, rollback triggers, post-change proofs, handoff steps,
verification commands, acceptance trace rows, assumption rows, acceptance
gates, package-level blocking policies, and refusal conditions for the future
real executor.
When broad autonomous source editing is required, `autonomous_execution_request`
also carries a diagnostic input contract and repair-loop stop conditions, so the
future adapter is expected to consume verification diagnostics rather than edit
blindly.
It also includes `execution_policy_status`; this remains
`blocked_until_adapter_is_wired` for dry-run handoffs and blocked execution
requests, and switches to `real_execution_adapter_active` only when the explicit
or guarded inferred patch adapter reports implemented changes. Guarded inference
is intentionally narrow: it only accepts explicit backtick-delimited file paths
and edit literals for simple replacement or Python add-function operations.
Adapter results must satisfy
`execution_result.schema.json`: status, changed files, patch summary, executed
verification commands, blockers, rollback notes, and per-operation patch
results. Failed patch batches are rolled back and must preserve rollback
evidence in the execution result.

`verification_policy.json` records the runtime allowlist and path-token guards
for verification. `verification_adapter.py` can run that narrow allowlist
without a shell, including `py_compile`, `pytest`, `unittest`, and
`git diff --check`. It blocks non-allowlisted commands, absolute/traversal path
tokens, and option values that point outside the repository. Its output is
versioned by `verification_execution.schema.json`; each result also preserves
diagnostic hints for tracebacks, assertion failures, syntax errors, and missing
imports. Planned verification reports
`status: planned`, while executed checks report `passed`, `failed`, or
`blocked`. It is safe enough for explicit verification wiring, but source
mutation remains outside this adapter.
