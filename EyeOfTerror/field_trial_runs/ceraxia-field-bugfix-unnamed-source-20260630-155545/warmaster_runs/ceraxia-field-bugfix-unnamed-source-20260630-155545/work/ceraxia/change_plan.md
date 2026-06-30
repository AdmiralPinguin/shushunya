# Ceraxia Change Plan

Goal: кодовая задача: почини python приложение. Тесты падают, источник ошибки специально не указан. Найди причину, исправь реализацию, проверь focused и broad тесты.
CERAXIA_TARGET_REPO: EyeOfTerror/field_trial_runs/ceraxia-field-bugfix-unnamed-source-20260630-155545/fixture/repo

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files

## Ranked Repo Map
- checkout.py: score=9 reasons=[imported_by:test_checkout.py, python_source]
- test_checkout.py: score=5 reasons=[test_surface, linked_test]
- pricing.py: score=1 reasons=[python_source]

## Test Source Links
- test_checkout.py -> checkout.py

## Recommended Read Order
- inspect_source: checkout.py (imported_by:test_checkout.py, python_source)
- inspect_test: test_checkout.py (test_surface, linked_test)
- inspect_source: pricing.py (python_source)

## Targeted Reading Plan
- checkout.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=1
- test_checkout.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0
- pricing.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=1

## Hypothesis Log
- [high] checkout.py is likely relevant to the requested code change. evidence=[imported_by:test_checkout.py, python_source] risk=public behavior may affect dependents
- [medium] test_checkout.py is likely relevant to the requested code change. evidence=[test_surface, linked_test] risk=local change risk appears limited
- [medium] pricing.py is likely relevant to the requested code change. evidence=[python_source] risk=public behavior may affect dependents

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- checkout.py: impact=medium dependents=1 tests=[test_checkout.py]
- test_checkout.py: impact=low dependents=0 tests=[]
- pricing.py: impact=high dependents=1 tests=[]

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
- primary: python -m unittest test_checkout
- linked: test_checkout.py
- fallback: python -m py_compile <changed .py files>
- fallback: git diff --check

## Test Surface
- test_checkout.py

## Python Symbol Surface
- checkout.py: functions=[total_after_discount] classes=[]
- pricing.py: functions=[discounted_price] classes=[]
- test_checkout.py: functions=[] classes=[CheckoutTest]

## Suggested Verification
- python -m unittest discover
- python -m unittest test_checkout

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
