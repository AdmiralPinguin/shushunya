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

She owns code-task decomposition, repository survey, scoped implementation
planning, patch manifest handoff, verification planning, code review, and final
handoff packaging.

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

For repo-grade tasks, final manifests additionally preserve
`repo_grade_workflow`, `architecture_decision_record`,
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
