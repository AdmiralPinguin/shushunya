# Ceraxia Change Plan

Goal: кодовая expert-задача без structured concurrency marker: исправь cache invalidation/concurrent readers. Выведи контракт из tests и docs: invalidate idempotent, reload works, concurrent readers share one loaded value, sleep-based synchronization нельзя использовать.
CERAXIA_TARGET_REPO: EyeOfTerror/field_trial_runs/ceraxia-expert-unshaped-concurrency-cache-20260701-211544/fixture/repo
CERAXIA_VERIFY: python -m unittest tests.test_cache_store
CERAXIA_VERIFY: python -m py_compile cache_store.py

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files
- cache_store.py
- docs/cache_store.md
- tests/test_cache_store.py

## Ranked Repo Map
- cache_store.py: score=21 reasons=[goal_filename_match, imported_by:tests/test_cache_store.py, goal_symbol_match:cache,store, python_source]
- tests/test_cache_store.py: score=19 reasons=[goal_filename_match, test_surface, linked_test, goal_symbol_match:cache,store,test,tests,unittest]
- docs/cache_store.md: score=6 reasons=[goal_filename_match]

## Test Source Links
- tests/test_cache_store.py -> cache_store.py

## Recommended Read Order
- inspect_source: cache_store.py (goal_filename_match, imported_by:tests/test_cache_store.py, goal_symbol_match:cache,store)
- inspect_test: tests/test_cache_store.py (goal_filename_match, test_surface, linked_test)
- inspect_source: docs/cache_store.md (goal_filename_match)

## Targeted Reading Plan
- cache_store.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=1
- tests/test_cache_store.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0
- docs/cache_store.md: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0

## Hypothesis Log
- [high] cache_store.py is likely relevant to the requested code change. evidence=[goal_filename_match, imported_by:tests/test_cache_store.py, goal_symbol_match:cache,store, python_source] risk=public behavior may affect dependents
- [high] tests/test_cache_store.py is likely relevant to the requested code change. evidence=[goal_filename_match, test_surface, linked_test, goal_symbol_match:cache,store,test,tests,unittest] risk=local change risk appears limited
- [medium] docs/cache_store.md is likely relevant to the requested code change. evidence=[goal_filename_match] risk=local change risk appears limited

## Problem Statement
- status: recorded
- observed_problem: Infer the concrete behavior gap from the task text, repository survey, tests, docs, and diagnostics before mutation.
- success_criteria: changed files directly address the requested behavior
- success_criteria: source scope is justified by repo survey or explicit patch contract
- success_criteria: verification evidence is preserved after the final mutation
- success_criteria: review either approves with evidence or blocks with focused revision steps
- ambiguity_policy: If multiple incompatible interpretations remain after survey, block with a focused clarification instead of broad mutation.

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## Architecture Options
- minimal_targeted_patch: recommended=True summary=Patch the narrowest source surface that satisfies the observed behavior gap.
- compatibility_wrapper: recommended=False summary=Preserve old callers while adding the new behavior behind a compatible boundary.
- broad_rewrite: recommended=False summary=Rewrite the surrounding module or subsystem.
- selection_rule: Choose the first option that satisfies the task with the least public-surface churn and adequate verification.

## Architecture Decision Record
- status: recorded
- decision: Apply the smallest coherent repo-grade patch across the impacted source, tests, docs, config, and compatibility surfaces.
- driver: preserve existing public behavior unless the task explicitly changes it
- driver: prefer source changes backed by focused tests and broad regression checks
- driver: keep rollback evidence and changed-file scope review in the final package
- rejected: single-file shortcut because repo-grade tasks need caller, tests, docs, and compatibility evidence, not only a local source edit
- rejected: broad rewrite because larger rewrites increase regression risk and hide the task-specific diff
- rollback: Revert the changed files listed in patch_package.changed_files; no hidden state mutation is allowed.

## Repo-Grade Workflow
- mode: focused_fix
- required_pass: survey
- required_pass: implementation
- required_pass: verification
- required_pass: self_review
- required_pass: final_package
- requires_architecture_decision_record: False
- requires_focused_and_broad_verification: False
- requires_pr_summary: True

## File Impact Matrix
- cache_store.py: impact=medium dependents=1 tests=[tests/test_cache_store.py]
- tests/test_cache_store.py: impact=medium dependents=0 tests=[]
- docs/cache_store.md: impact=low dependents=0 tests=[]

## Risk Register

## Acceptance Criteria
- requested_behavior_addressed: patch candidate selected from explicit contract, task text, or test evidence
- source_scope_is_explained: changed files map back to repo survey or review warns about drift
- changed_python_compiles: py_compile runs for changed Python files
- task_verification_passes: requested or inferred verification commands return zero
- review_has_no_blockers: code_review decision record approves final package

## Test Strategy
- primary: python -m unittest discover
- primary: python -m unittest tests.test_cache_store
- linked: tests/test_cache_store.py
- fallback: python -m py_compile <changed .py files>
- fallback: git diff --check

## Test Surface
- tests/test_cache_store.py

## Python Symbol Surface
- cache_store.py: functions=[] classes=[CacheStore]
- tests/test_cache_store.py: functions=[] classes=[CacheStoreTest]

## Suggested Verification
- python -m unittest discover
- python -m unittest tests.test_cache_store

## Implementation Policy
- Produce an auditable patch manifest before mutating source files.
- Require verification commands or explicit blockers before final readiness.

## Task Profile
- kinds: test_repair, bugfix
- complexity: low
- risk: test_diagnostic_required
- risk: natural_language_patch_inference

## Worker Brief
- brief: Convert repo evidence into a scoped implementation plan and architecture decision.
- handoff_question: What is the narrowest safe patch path, and which architecture tradeoffs were rejected?
- must_produce: candidate file rationale
- must_produce: test surface
- must_produce: verification suggestions
- must_produce: risk notes
- must_produce: architecture decision record
- must_produce: repo-grade workflow

## Role Policy
- role: change_strategist
- authority: scoped_plan_from_repository_evidence
- may_mutate_source: False
- required_evidence: candidate_files
- required_evidence: test_surface
- required_evidence: implementation_policy
