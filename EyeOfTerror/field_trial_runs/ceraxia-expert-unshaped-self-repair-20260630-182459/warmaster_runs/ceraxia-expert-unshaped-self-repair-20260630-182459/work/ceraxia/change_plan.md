# Ceraxia Change Plan

Goal: кодовая expert-задача без structured patch marker: проверь self-repair discipline. Выведи цель из tests, сохрани diagnostic от первой failed verification и исправь только source по mismatch. Не редактируй tests.
CERAXIA_TARGET_REPO: EyeOfTerror/field_trial_runs/ceraxia-expert-unshaped-self-repair-20260630-182459/fixture/repo
CERAXIA_VERIFY: python -m unittest tests.test_quota
CERAXIA_VERIFY: python -m py_compile quota.py

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files
- quota.py
- tests/test_quota.py

## Ranked Repo Map
- quota.py: score=20 reasons=[goal_filename_match, imported_by:tests/test_quota.py, goal_symbol_match:quota, python_source]
- tests/test_quota.py: score=19 reasons=[goal_filename_match, test_surface, linked_test, goal_symbol_match:quota,test,tests,unittest]

## Test Source Links
- tests/test_quota.py -> quota.py

## Recommended Read Order
- inspect_source: quota.py (goal_filename_match, imported_by:tests/test_quota.py, goal_symbol_match:quota)
- inspect_test: tests/test_quota.py (goal_filename_match, test_surface, linked_test)

## Targeted Reading Plan
- quota.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=1
- tests/test_quota.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0

## Hypothesis Log
- [high] quota.py is likely relevant to the requested code change. evidence=[goal_filename_match, imported_by:tests/test_quota.py, goal_symbol_match:quota, python_source] risk=public behavior may affect dependents
- [high] tests/test_quota.py is likely relevant to the requested code change. evidence=[goal_filename_match, test_surface, linked_test, goal_symbol_match:quota,test,tests,unittest] risk=local change risk appears limited

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- quota.py: impact=medium dependents=1 tests=[tests/test_quota.py]
- tests/test_quota.py: impact=medium dependents=0 tests=[]

## Risk Register

## Acceptance Criteria
- requested_behavior_addressed: patch candidate selected from explicit contract, task text, or test evidence
- source_scope_is_explained: changed files map back to repo survey or review warns about drift
- changed_python_compiles: py_compile runs for changed Python files
- task_verification_passes: requested or inferred verification commands return zero
- review_has_no_blockers: code_review decision record approves final package

## Test Strategy
- primary: python -m unittest discover
- primary: python -m unittest tests.test_quota
- linked: tests/test_quota.py
- fallback: python -m py_compile <changed .py files>
- fallback: git diff --check

## Test Surface
- tests/test_quota.py

## Python Symbol Surface
- quota.py: functions=[max_daily_exports] classes=[]
- tests/test_quota.py: functions=[] classes=[QuotaTest]

## Suggested Verification
- python -m unittest discover
- python -m unittest tests.test_quota

## Implementation Policy
- Produce an auditable patch manifest before mutating source files.
- Require verification commands or explicit blockers before final readiness.

## Task Profile
- kinds: test_repair, bugfix
- complexity: low
- risk: test_diagnostic_required
- risk: natural_language_patch_inference

## Worker Brief
- brief: Convert repo evidence into a scoped implementation plan.
- handoff_question: What is the narrowest safe patch path?
- must_produce: candidate file rationale
- must_produce: test surface
- must_produce: verification suggestions
- must_produce: risk notes

## Role Policy
- role: change_strategist
- authority: scoped_plan_from_repository_evidence
- may_mutate_source: False
- required_evidence: candidate_files
- required_evidence: test_surface
- required_evidence: implementation_policy
