# Ceraxia Change Plan

Goal: кодовая expert-задача без structured flaky marker: расследуй intermittent ordering failure. Выведи контракт из repeated tests и docs: equal-priority items должны иметь deterministic tie-breaker, skip/sleep/ослабление тестов запрещены.
CERAXIA_TARGET_REPO: /media/shushunya/SHUSHUNYA/shushunya/EyeOfTerror/field_trial_runs/ceraxia-expert-unshaped-flaky-root-cause-20260630-181349/fixture/repo
CERAXIA_VERIFY: python -m unittest tests.test_scheduler
CERAXIA_VERIFY: python -m unittest tests.test_scheduler
CERAXIA_VERIFY: python -m py_compile scheduler.py

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files
- scheduler.py
- tests/test_scheduler.py

## Ranked Repo Map
- scheduler.py: score=20 reasons=[goal_filename_match, imported_by:tests/test_scheduler.py, goal_symbol_match:scheduler, python_source]
- tests/test_scheduler.py: score=19 reasons=[goal_filename_match, test_surface, linked_test, goal_symbol_match:scheduler,test,tests,unittest]

## Test Source Links
- tests/test_scheduler.py -> scheduler.py

## Recommended Read Order
- inspect_source: scheduler.py (goal_filename_match, imported_by:tests/test_scheduler.py, goal_symbol_match:scheduler)
- inspect_test: tests/test_scheduler.py (goal_filename_match, test_surface, linked_test)

## Targeted Reading Plan
- scheduler.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=1
- tests/test_scheduler.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0

## Hypothesis Log
- [high] scheduler.py is likely relevant to the requested code change. evidence=[goal_filename_match, imported_by:tests/test_scheduler.py, goal_symbol_match:scheduler, python_source] risk=public behavior may affect dependents
- [high] tests/test_scheduler.py is likely relevant to the requested code change. evidence=[goal_filename_match, test_surface, linked_test, goal_symbol_match:scheduler,test,tests,unittest] risk=local change risk appears limited

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- scheduler.py: impact=medium dependents=1 tests=[tests/test_scheduler.py]
- tests/test_scheduler.py: impact=medium dependents=0 tests=[]

## Risk Register

## Acceptance Criteria
- requested_behavior_addressed: patch candidate selected from explicit contract, task text, or test evidence
- source_scope_is_explained: changed files map back to repo survey or review warns about drift
- changed_python_compiles: py_compile runs for changed Python files
- task_verification_passes: requested or inferred verification commands return zero
- review_has_no_blockers: code_review decision record approves final package

## Test Strategy
- primary: python -m unittest discover
- primary: python -m unittest tests.test_scheduler
- linked: tests/test_scheduler.py
- fallback: python -m py_compile <changed .py files>
- fallback: git diff --check

## Test Surface
- tests/test_scheduler.py

## Python Symbol Surface
- scheduler.py: functions=[schedule_order] classes=[]
- tests/test_scheduler.py: functions=[] classes=[SchedulerTest]

## Suggested Verification
- python -m unittest discover
- python -m unittest tests.test_scheduler

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
