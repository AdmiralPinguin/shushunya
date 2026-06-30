# Ceraxia Change Plan

Goal: кодовая задача: улучши обработку ошибок в этом python приложении, но требования и ожидаемый формат ошибки не заданы. Если вариантов несколько, не угадывай.
CERAXIA_TARGET_REPO: EyeOfTerror/field_trial_runs/ceraxia-field-ambiguous-task-20260630-164731/fixture/repo

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files

## Ranked Repo Map
- api.py: score=9 reasons=[imported_by:test_api.py, python_source]
- test_api.py: score=5 reasons=[test_surface, linked_test]
- parser.py: score=1 reasons=[python_source]

## Test Source Links
- test_api.py -> api.py

## Recommended Read Order
- inspect_source: api.py (imported_by:test_api.py, python_source)
- inspect_test: test_api.py (test_surface, linked_test)
- inspect_source: parser.py (python_source)

## Targeted Reading Plan
- api.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=1
- test_api.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0
- parser.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=1

## Hypothesis Log
- [high] api.py is likely relevant to the requested code change. evidence=[imported_by:test_api.py, python_source] risk=public behavior may affect dependents
- [medium] test_api.py is likely relevant to the requested code change. evidence=[test_surface, linked_test] risk=local change risk appears limited
- [medium] parser.py is likely relevant to the requested code change. evidence=[python_source] risk=public behavior may affect dependents

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- api.py: impact=medium dependents=1 tests=[test_api.py]
- test_api.py: impact=low dependents=0 tests=[]
- parser.py: impact=high dependents=1 tests=[]

## Risk Register
- [medium] public_surface_without_static_test_link: run broader verification or require manual coverage review before approval

## Acceptance Criteria
- requested_behavior_addressed: patch candidate selected from explicit contract, task text, or test evidence
- source_scope_is_explained: changed files map back to repo survey or review warns about drift
- changed_python_compiles: py_compile runs for changed Python files
- task_verification_passes: requested or inferred verification commands return zero
- review_has_no_blockers: code_review decision record approves final package

## Test Strategy
- primary: python -m unittest discover
- primary: python -m unittest test_api
- linked: test_api.py
- fallback: python -m py_compile <changed .py files>
- fallback: git diff --check

## Test Surface
- test_api.py

## Python Symbol Surface
- api.py: functions=[handle_payload] classes=[]
- parser.py: functions=[parse_amount] classes=[]
- test_api.py: functions=[] classes=[ApiTest]

## Suggested Verification
- python -m unittest discover
- python -m unittest test_api

## Implementation Policy
- Produce an auditable patch manifest before mutating source files.
- Require verification commands or explicit blockers before final readiness.

## Task Profile
- kinds: bugfix
- complexity: low
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
