# Ceraxia Change Plan

Goal: кодовая expert-задача без structured data migration marker: records API переходит с поля amount на total_amount. Выведи контракт из существующего reader, docs и tests: reader должен принимать старую и новую форму, writer должен отдавать только новую форму. Не редактируй tests как способ сделать их зелеными.
CERAXIA_TARGET_REPO: /media/shushunya/SHUSHUNYA/shushunya/EyeOfTerror/field_trial_runs/ceraxia-expert-unshaped-data-migration-20260630-181347/fixture/repo
CERAXIA_VERIFY: python -m unittest tests.test_records_migration
CERAXIA_VERIFY: python -m py_compile service/records.py

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files
- docs/records.md
- service/records.py
- tests/test_records_migration.py

## Ranked Repo Map
- service/records.py: score=21 reasons=[goal_filename_match, imported_by:tests/test_records_migration.py, goal_symbol_match:records,service, python_source]
- tests/test_records_migration.py: score=19 reasons=[goal_filename_match, test_surface, linked_test, goal_symbol_match:migration,records,service,test,tests]
- docs/records.md: score=6 reasons=[goal_filename_match]

## Test Source Links
- tests/test_records_migration.py -> service/records.py

## Recommended Read Order
- inspect_source: service/records.py (goal_filename_match, imported_by:tests/test_records_migration.py, goal_symbol_match:records,service)
- inspect_test: tests/test_records_migration.py (goal_filename_match, test_surface, linked_test)
- inspect_source: docs/records.md (goal_filename_match)

## Targeted Reading Plan
- service/records.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=1
- tests/test_records_migration.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0
- docs/records.md: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0

## Hypothesis Log
- [high] service/records.py is likely relevant to the requested code change. evidence=[goal_filename_match, imported_by:tests/test_records_migration.py, goal_symbol_match:records,service, python_source] risk=public behavior may affect dependents
- [high] tests/test_records_migration.py is likely relevant to the requested code change. evidence=[goal_filename_match, test_surface, linked_test, goal_symbol_match:migration,records,service,test,tests] risk=local change risk appears limited
- [medium] docs/records.md is likely relevant to the requested code change. evidence=[goal_filename_match] risk=local change risk appears limited

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- service/records.py: impact=medium dependents=1 tests=[tests/test_records_migration.py]
- tests/test_records_migration.py: impact=medium dependents=0 tests=[]
- docs/records.md: impact=low dependents=0 tests=[]

## Risk Register

## Acceptance Criteria
- requested_behavior_addressed: patch candidate selected from explicit contract, task text, or test evidence
- source_scope_is_explained: changed files map back to repo survey or review warns about drift
- changed_python_compiles: py_compile runs for changed Python files
- task_verification_passes: requested or inferred verification commands return zero
- review_has_no_blockers: code_review decision record approves final package

## Test Strategy
- primary: python -m unittest discover
- primary: python -m unittest tests.test_records_migration
- linked: tests/test_records_migration.py
- fallback: python -m py_compile <changed .py files>
- fallback: git diff --check

## Test Surface
- tests/test_records_migration.py

## Python Symbol Surface
- service/records.py: functions=[normalize_record] classes=[]
- tests/test_records_migration.py: functions=[] classes=[RecordsMigrationTest]

## Suggested Verification
- python -m unittest discover
- python -m unittest tests.test_records_migration

## Implementation Policy
- Produce an auditable patch manifest before mutating source files.
- Require verification commands or explicit blockers before final readiness.

## Task Profile
- kinds: test_repair, api_contract, bugfix
- complexity: medium
- risk: test_diagnostic_required
- risk: public_contract_regression
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
