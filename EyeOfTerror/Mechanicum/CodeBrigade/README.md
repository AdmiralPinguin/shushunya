# Code Brigade

Ceraxia's implementation-side Mechanicum brigade.

Likely members:

- `CogitatorCodewright`
- `LogisRepository`
- `MagosStrategos`
- `FerrumPatchwright`
- `OrdinatusVerifier`
- `JudicatorCodicis`
- `SealwrightFinalis`

The current folder owns the handoff contract from Ceraxia to code workers.
`CogitatorCodewright/cogitator_codewright.py` is the compatibility entrypoint
and multi-step dispatcher. The active worker services own their stage modules
inside their own directories:

- `LogisRepository/repository_survey.py`
- `MagosStrategos/change_planning.py`
- `FerrumPatchwright/implementation.py`
- `OrdinatusVerifier/verification.py`
- `JudicatorCodicis/code_review.py`
- `SealwrightFinalis/finalize.py`

`Workers/common/codewright_core.py` is a bounded shared utility layer for
cross-stage filesystem, evidence, and compatibility helpers. Repository survey,
planning, patch inference/application, verification/repair, review, and final
packaging logic live in the named worker directories above. Each named worker
module validates its owned `step_id` before calling its local implementation.

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
For medium and high risk tasks, real source mutation also requires
`planning_handoff_gate.decision=passed`: Ceraxia must attach a
`planning_department` package with accepted RFC, complete multi-pass
investigation, ready work-package handoff, and ready brigade handoff contract.
Dry-run reports still expose the gate, but real execution blocks without it.
The worker report must include `implementation_plan`, which preserves survey
candidate files, test files, local dependency edges, investigation playbook
read stages, caller candidates, contract surface candidates, change-control
protected invariants, rollback triggers, post-change proofs, handoff steps,
verification commands, acceptance trace rows, assumption rows, acceptance
gates, package-level blocking policies, the PlanningBrigade
`worker_output_contract`, and refusal conditions for the future real executor.
The worker-output contract is the runtime checklist for reports, package
statuses, evidence sources, and blockers that Ceraxia will audit before
accepting a result.
When broad autonomous source editing is required, `autonomous_execution_request`
derives its diagnostic input contract, repair-loop read requirements, stop
conditions, and max attempts from the PlanningBrigade `diagnostic_repair_plan`,
so the future adapter is expected to consume verification diagnostics rather
than edit blindly.
`diagnostic_repair_contract.py` validates Ceraxia
`diagnostic_repair_request.json` artifacts and builds a CodeBrigade intake
summary with impacted surfaces, package ids, target files, preserved tests,
per-attempt executor support, and blockers. Its first executor path is
intentionally narrow: assertion-failure repair requests, failed-command
requests, and traceback-backed repair requests may reuse the existing guarded
test-inferred source patch adapter; missing-import repair requests can use the
same guarded path when tests identify the missing source symbol. Unsupported
diagnostics, such as raw syntax-error repair without a guarded oracle, return
blocked execution results with the unsupported reason attached to the attempt.
`diagnostic_repair_intake.schema.json` contracts that intake shape so future
orchestrators can decide whether to execute, replan, or escalate before trying
an unsupported repair. The
repair execution brief includes the same worker-output contract shape as normal
Ceraxia handoff, so diagnostic repair cannot bypass package-status auditing.
Use it directly when inspecting a run package:

```bash
python3 EyeOfTerror/Mechanicum/CodeBrigade/diagnostic_repair_contract.py path/to/diagnostic_repair_request.json
python3 EyeOfTerror/Mechanicum/CodeBrigade/diagnostic_repair_contract.py --execute path/to/diagnostic_repair_request.json
```

It also includes `execution_policy_status`; this remains
`blocked_until_adapter_is_wired` for dry-run handoffs and blocked execution
requests, and switches to `real_execution_adapter_active` only when the explicit
or guarded inferred patch adapter reports implemented changes. Guarded inference
is intentionally narrow: it only accepts explicit backtick-delimited file paths
and edit literals for simple replacement or Python add-function operations.
Adapter results must satisfy
`execution_result.schema.json`: status, changed files, patch summary, executed
verification commands, blockers, rollback notes, and per-operation patch
results. Patch manifests also include per-file rows with operations, applied and
failed operation counts, and whether rollback touched that file. Failed patch
batches are rolled back and must preserve rollback evidence in the execution
result.

`verification_policy.json` records the runtime allowlist and path-token guards
for verification. `verification_adapter.py` can run that narrow allowlist
without a shell, including `py_compile`, `pytest`, `unittest`, and
`git diff --check`. It blocks non-allowlisted commands, absolute/traversal path
tokens, and option values that point outside the repository. Its output is
versioned by `verification_execution.schema.json`; each result also preserves
diagnostic hints for tracebacks, assertion failures, syntax errors, and missing
imports. Verification execution also emits `contract_trace`, which maps
acceptance requirements to matched verification commands and distinguishes
behavior proof from syntax-only, skipped, blocked, failed, planned-only, or
missing evidence. Planned verification reports `status: planned`, while
executed checks report `passed`, `failed`, or `blocked`. It is safe enough for
explicit verification wiring, but source mutation remains outside this adapter.
