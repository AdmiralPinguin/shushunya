# Ceraxia

Ceraxia is the Inner Circle governor for code tasks.

Current project focus: Ceraxia and her code brigade are the active development
track. Treat standalone ShushunyaAgent work as parked unless the user
explicitly reactivates it.

Current evaluation status: Ceraxia has stronger planning, readiness, patch,
verification, repair, and review artifacts than the original prototype, but a
real 7/10 engineering score is not considered proven by self-tests. The
evaluation protocol lives in `EVALUATION.md`; representative field trials live
in `field_trials.json`. Regression tests may prevent known breakage, but only
reviewed field-trial ledger entries can support the real 7/10 claim.
Honest score reporting requires a complete evidence package, not just a legacy
ledger score. At minimum the accepted evidence must include a readable
`trial_result.json`, a readable `final_manifest.json`, and passed
`honest_evidence` checks from `ceraxia_evidence_contract.py`.

The next-stage live-task target is stricter than the fixture arena: 20 real
repository tasks, at least 10 task classes, at least 70% success, zero false
successes, failed/blocked postmortems, and at least 5 multi-file nonfixture
tasks. Live tasks are cataloged in `field_trials.json` under `live_tasks`.
Use `EyeOfTerror/Warmaster/ceraxia_live_task_prepare.py` to create a task
packet, `ceraxia_next_stage_package.py` to build the evidence package, and
`ceraxia_live_task_register.py` to register only validated live evidence in the
ledger. Fixture runs deliberately set `fixture_only=true` and cannot satisfy
the live target.

She owns code-task decomposition, repository survey, scoped implementation
planning, patch manifest handoff, verification planning, code review, and final
handoff packaging.

## Mechanicum Controller

`ceraxia.py` is the local Mechanicum-facing controller for the new Ceraxia
brigade structure. It currently runs a dry-run management pipeline:

```text
task
  -> PlanningBrigade planning_packet.json
  -> repo_survey.json
  -> CodeBrigade implementation_brief.json
  -> worker_report.json
  -> verification_report.json
  -> review_gate.json
  -> diagnostic_repair_request.json
  -> planning_feedback_request.json
  -> execution_readiness.json
  -> evidence_matrix.json
  -> run_summary.json
  -> artifact_manifest.json
  -> run_audit.json
  -> final_report.md
```

Lifecycle states:

```text
received -> planned -> surveyed -> implementation_ready -> implemented -> verified -> reviewed -> finalized
```

The dry-run path deliberately does not edit source. It proves that Ceraxia can
shape the task, enforce the planning contract, inspect the target repository in
read-only mode, build the CodeBrigade handoff, review honesty, and persist
artifacts. Real CodeBrigade execution can replace the dry-run worker report
without changing the surrounding artifact contract.
CLI `ok` and `package_ok` mean the dry-run package is internally consistent;
`ready_for_execution` remains false until real CodeBrigade execution is wired.
Use `--execute-verification` to run allowlisted verification commands while
keeping source mutation in dry-run mode.
Execution mode is explicit:

- `dry_run`: build planning, survey, handoff, review, and run package without
  source mutation.
- `guarded_patch`: allow the current explicit or guarded-inferred CodeBrigade
  patch adapter after mutation preflight.
- `repo_engineer`: reserved for the broader autonomous CodeBrigade executor.
- `review_only`: keep mutation disabled and use Ceraxia as an audit/review
  surface.

`--execute` remains a compatibility alias for `--mode guarded_patch`.
CodeBrigade now writes an `edit_plan` before mutation. The plan records
read-before-edit targets, allowed new files, planned diff intent, acceptance
criteria, and verification commands; mutation is blocked when these gates are
missing, except for explicitly planned new-file creation.
`evidence_matrix.json` maps the PlanningBrigade quality bar to concrete or
planned evidence sources, and `run_summary.json` carries the same coverage
counters for fast orchestration checks.
`run_summary.json` also mirrors per-surface verification status counts from
`review_gate.json`, so orchestration can distinguish executed, partial,
planned-only, missing, failed, and blocked evidence instead of reading only one
aggregate surface status.
Each `review_gate.json.surface_verification_sufficiency.surface_evidence` row
also carries output-signal and diagnostic counts for the commands matched to
that surface.
`repo_survey.json.truncated=true` means the survey hit its file limit; Ceraxia
keeps the package usable, but `review_gate.json` records a partial-coverage
warning.
`repo_survey.json.python_symbols_truncated=true` is narrower: source files were
listed, but Python symbol/import evidence is partial.
When verification output contains failed, blocked, traceback, assertion,
syntax, missing-import, or zero-test diagnostics, `review_gate.json` now
builds a `diagnostic_repair_queue` with impacted surfaces, work-package ids,
read targets, stop conditions, and required repair evidence; `run_summary.json`
and `final_report.md` expose its status and item count for the next CodeBrigade
repair pass.
With `--execute-diagnostic-repair`, Ceraxia also writes
`diagnostic_repair_execution_result.json` from CodeBrigade's narrow guarded
repair adapter.
When review findings point back at the planning packet, matrices, investigation
playbook, change-control plan, trace matrices, assumption register, or
worker-output contract, Ceraxia writes `planning_feedback_request.json` as a
formal PlanningBrigade return package. `run_summary.json` mirrors its status and
finding count, and `run_audit.json` blocks drift between the two artifacts.

Current `EyeOfTerror/Mechanicum` planning quality gates:

- PlanningBrigade extracts explicit path hints and passes them to the repository
  survey.
- `survey_quality_gate` blocks missing/unsafe explicit path hints, missing
  candidate files, and high-risk tasks with no discovered test surface.
- `impact_analysis` names the affected engineering surfaces.
- `surface_verification_matrix` maps those surfaces to planned evidence and
  blocks incomplete coverage before CodeBrigade handoff.
- `surface_package_matrix` preserves which CodeBrigade work packages own each
  impacted surface.
- `change_control_plan` is carried through the CodeBrigade implementation plan
  and review gate, so protected invariants, rollback triggers, and post-change
  proofs cannot disappear during handoff.
- `acceptance_trace_matrix` is carried through the same path, so the review
  gate can verify that definition-of-done and quality-bar requirements still
  map to planned evidence and CodeBrigade package ownership. Ceraxia also
  audits explicit definition-of-done trace counts in `review_gate.json`,
  `run_summary.json`, and `evidence_matrix.json`.
- `assumption_register` is also preserved through worker reports, so task,
  repository, verification, and specialized risk assumptions remain visible to
  review and orchestration.
- `worker_output_contract` is preserved through the CodeBrigade implementation
  plan. The review gate blocks missing package statuses, missing package-level
  acceptance requirements, missing evidence sources, or contract rows that no
  longer match the planned work packages.
- `planning_feedback_request.json` is a contracted return channel from Ceraxia
  back to PlanningBrigade when plan structure or handoff contracts need a replan.
- `planning_review_gate` scores the packet and blocks unclear or structurally
  unsafe plans.
- `run_summary.json` and `final_report.md` expose planning review, survey
  quality, implementation package dependency, worker-output sufficiency, and
  execution evidence decisions for orchestration history, including per-surface
  verification status counts.
- `repo_survey.json.source_summaries` gives shallow multi-language symbol and
  import-like evidence for common source files; Python still has the deeper
  AST symbol/import path used for local dependency edges.
- `repo_survey.json.generic_import_edges` resolves local relative JS/TS/TSX/JSX
  imports, barrel exports, and side-effect imports to repository files and
  passes them through the CodeBrigade handoff.
- `repo_survey.json.caller_candidates` groups reverse dependency callers for
  candidate files, and `contract_surface_candidates` flags likely API, schema,
  route, endpoint, or contract files for compatibility review.
- `repo_survey.json.package_manifest_candidates` captures common package and
  dependency manifests such as `package.json`, `pyproject.toml`,
  `requirements.txt`, `go.mod`, `Cargo.toml`, `pom.xml`, and Gradle files, then
  passes package name, dependency counts, dev dependency counts, scripts, and
  parse errors through the CodeBrigade handoff.
- `repo_survey.json.recommended_read_order` ranks explicit path hints,
  entrypoints, source candidates, dependency neighbours, and tests for worker
  inspection before mutation.

Smoke command:

```bash
python3 EyeOfTerror/Mechanicum/Ceraxia/ceraxia.py --task "почини security bug и добавь pytest negative tests" --repo-path /absolute/repo
```

Generated run artifacts live under `runs/` and are intentionally ignored by
git.

Repo-grade workflow requirements are documented in
`repo_grade_workflow.md`. In short, high-risk architecture/refactor/migration
tasks must be treated like a small PR pipeline: survey, architecture decision,
implementation, focused verification, broad verification, self-review,
revision if needed, and final package.

## Default Worker Pipeline

```text
LogisRepository(repository_survey)
  -> MagosStrategos(change_planning)
  -> FerrumPatchwright(implementation)
  -> OrdinatusVerifier(verification)
  -> JudicatorCodicis(code_review)
  -> SealwrightFinalis(finalize)
```

## Current Boundary

The named workers currently share the same execution core, which keeps the
protocol stable while their internals are split into stronger specialized
implementations.

Each worker's `worker.json` exposes a `role_contract` with its owned step,
authority boundary, expected artifact names, and next handoff. The registry
self-test checks those contracts for the active six-worker code brigade.
Ceraxia also writes a per-step `role_policy` into the oversight quality matrix
and dispatch requests. Worker artifacts preserve that policy, so final packages
can prove whether a step was read-only, allowed scoped source mutation, or
limited to allowlisted verification and narrow repairs.
The shared code-worker core enforces the mutation boundary: implementation will
not apply patches when `may_mutate_source=false`, and verifier repair loops will
record a blocker instead of editing source under a read-only policy.
`OrdinatusVerifier` writes both `verification_report.json` and
`repair_loop_state.json`; the latter records executed command counts, failed
commands, applied repairs, blocked repairs, and the next safe action for the
reviewer or governor. When failed command output includes Python traceback
frames inside the target repo, the state also records `candidate_source_paths`
for focused revision. If a failed command has no useful source traceback,
Ceraxia falls back to ranked source files from `repo_survey.json`.

`LogisRepository` records Python symbol summaries for scanned `.py` files and
suggests safe verification commands from discovered test files. `MagosStrategos`
includes those symbol and verification sections in the change plan. The repo
map also includes `recommended_read_order`, a ranked source/test inspection
sequence that patch workers should consume before mutation.
The repository survey now records `engineering_investigation`: import
dependency edges, simple AST call edges, targeted reading questions, hypothesis
logs, and design-decision seed rules. `MagosStrategos` writes those sections
into `change_plan.md`, and final manifests preserve them for Warmaster review.
`MagosStrategos` also writes machine-readable `problem_statement.json` and
`architecture_options.json`. `JudicatorCodicis` blocks final readiness if those
architect planning artifacts are missing, so code work cannot silently skip the
problem-definition and design-option pass.

`FerrumPatchwright` can apply explicit patch operations embedded in the task:

```text
CERAXIA_TARGET_REPO: /absolute/path/to/repo
CERAXIA_PATCH:
{
  "operations": [
    {"type": "replace", "path": "module.py", "old": "return 1", "new": "return 2"},
    {"type": "write_file", "path": "new_file.py", "content": "..."}
  ],
  "verification_commands": ["python -m py_compile module.py"]
}
```

Without an explicit, marker-synthesized, or guarded test-inferred patch path,
Ceraxia writes a blocked handoff package instead of claiming the code task is
complete.

`write_file` is idempotent when the target file already contains the requested
content. Different existing content still requires `"overwrite": true`, so
retries can succeed without weakening accidental overwrite protection.

Patch operation batches are atomic. If any operation in a batch fails,
Ceraxia restores every file touched earlier in the batch and reports the task
as blocked instead of leaving a partial code change behind. The patch manifest
records rollback evidence under `rollback.applied` and `rollback.files`.

The governor exposes the same machine-readable `patch_contract` through
`/capabilities`, `/plan`, and the saved oversight plan. It lists supported
markers, patch operation types, verification allowlist entries, safety gates,
and narrow repair loops for Warmaster and client-side planning.

Ceraxia also exposes `POST /callable_contract` for orchestration. That endpoint
turns a chat task plus optional `repo_path` and constraints into a
machine-readable specialized-brigade contract: normalized task text, worker
briefs, patch contract, execution flow, and final package schema. The main
orchestrator should treat that response as the function signature for invoking
Ceraxia and then use `/prepare_run` plus Warmaster execution endpoints.

Ceraxia also exposes a machine-readable `task_profile` and
`worker_specialization_briefs` through `/capabilities`, `/plan`, oversight, and
dispatch packets. The profile classifies code-task kinds, complexity, risk
flags, and required governor checks. Each worker brief states the worker's
concrete duty, required evidence, handoff question, and authority boundary for
the current task.

Patch and final manifests expose `patch_source` and `operation_count`, so
callers can distinguish explicit JSON patches from marker-synthesized or
natural-language-inferred patches. Test-inferred patches also expose
`diagnostics` with the test path, target module, function name, and expected
or actual values used to derive the patch.
Patch and final manifests also expose `patch_scope_evidence`, linking changed
files back to the repo map score/reasons where possible and listing changed
files that were outside the survey map. The same evidence includes static
test/source links when `LogisRepository` can derive them from Python imports.
`JudicatorCodicis` turns that evidence into `patch_scope_review`: mapped
changes pass as covered, unmapped files or source files without linked tests are
reported as warnings for manual review. Final packages preserve
`recommended_read_order` and `patch_scope_review` so the governor can inspect
scope and test evidence without reopening every intermediate artifact.

Final manifests preserve an `execution_report` with task profile, worker brief
presence, changed-file count, verification command count, repair attempt count,
blocker count, and revision requirement. Warmaster compact run summaries expose
the same code-task evidence through `final_manifest_summary`.
`JudicatorCodicis` also writes a `review_decision_record`, including patch
application, verification, scope review, and diagnostic-linkage checks. Blocked
reviews preserve focused revision context with changed files, failed commands,
candidate source paths, patch source, and diagnostics.
Blocked reviews also expose `review_repair_loop`: the triggering checks,
focused context, rerun steps, and completion gate for the next revision cycle.

For repo-grade tasks, final manifests additionally preserve
`repo_grade_workflow`, `problem_statement`, `architecture_options`,
`architecture_decision_record`,
`verification_strategy`, `patch_package`, and `pr_summary`. `JudicatorCodicis`
gates architecture evidence and broad verification before `SealwrightFinalis`
can package the result as ready.

Verification commands run without a shell and must match Ceraxia's allowlist:
`pytest`, `python -m pytest`, `python -m unittest`, or
`python -m py_compile ...`.

For simple tasks, Ceraxia can synthesize the patch spec from markers:

```text
CERAXIA_CREATE_FILE: generated.py
CERAXIA_FILE_CONTENT:
def generated_value():
    return 42

CERAXIA_VERIFY: python -m py_compile generated.py
```

or:

```text
CERAXIA_REPLACE_IN_FILE: module.py
CERAXIA_OLD:
return 1
CERAXIA_NEW:
return 2
CERAXIA_VERIFY: python -m py_compile module.py
```

Ceraxia can also infer one narrow replace operation from a natural-language
task when the target path, old text, and new text are explicit code spans:

```text
В файле `module.py` замени `return 1` на `return 2`.
Проверь `python -m py_compile module.py`.
```

Ceraxia can infer one narrow function append when the task provides an explicit
file path, function name, and safe return literal:

```text
В файле `module.py` добавь функцию `value`, возвращающую `42`.
Проверь `python -m unittest test_module.py`.
```

This mode blocks instead of appending when the target Python file already
defines the requested function name.

For very small unittest repairs, Ceraxia can infer a missing function from a
test file when the task names the test path, the test has exactly one
`from module import function` plus one `assertEqual(function(), literal)`, and
the target module file already exists:

```text
Почини тест `test_module.py`.
```

If that function already exists and contains exactly one simple literal return,
the same test-inferred mode can replace the return literal with the test's
expected literal.

For small multi-file tasks, Ceraxia can synthesize a patch spec from a JSON
file list:

```text
CERAXIA_FILES:
{
  "files": [
    {
      "path": "calc.py",
      "content": "def add(left, right):\n    return left + right\n"
    },
    {
      "path": "test_calc.py",
      "content": "import unittest\nfrom calc import add\n\nclass CalcTest(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
    }
  ],
  "verification_commands": ["python -m unittest test_calc.py"]
}
```

The first verifier repair loop is intentionally narrow: when `py_compile`
reports `SyntaxError: expected ':'` for a changed Python file, Ceraxia can add
the missing colon to the failing line, rerun verification, and record the repair
in the final manifest.

Ceraxia can also repair a narrow unittest/pytest value mismatch:
`AssertionError: 1 != 2` can update exactly one `return 1` in a changed Python
file to `return 2`, rerun the failed verification command, and preserve the
repair evidence.

A second narrow test repair handles `NameError: name 'x' is not defined` when
the failing `assertEqual(..., literal)` exposes a simple expected literal and
the changed Python file contains exactly one `return x`.

Another narrow repair handles `ImportError: cannot import name 'f' from 'm'`
when the changed file is `m.py` and stderr or target test files expose exactly
one `assertEqual(f(), literal)`: Ceraxia appends
`def f(): return literal`, reruns verification, and records the repair.

For small arithmetic unittest tasks, Ceraxia can infer a return-expression patch
from exactly one two-argument `assertEqual(function(a, b), expected)` when the
target function exists and a single arithmetic expression (`+`, `-`, reversed
`-`, or `*`) uniquely satisfies the observed expectation.
