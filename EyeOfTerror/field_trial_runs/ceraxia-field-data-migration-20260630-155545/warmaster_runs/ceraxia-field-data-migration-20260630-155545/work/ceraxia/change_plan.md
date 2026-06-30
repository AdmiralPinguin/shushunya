# Ceraxia Change Plan

Goal: кодовая задача: введи новую форму records, сохрани чтение старой формы и проверь writer rollback risk.
CERAXIA_TARGET_REPO: EyeOfTerror/field_trial_runs/ceraxia-field-data-migration-20260630-155545/fixture/repo
CERAXIA_DATA_MIGRATION:
{
  "source_path": "records.py",
  "test_path": "test_records.py",
  "read_function": "normalize_record",
  "write_function": "serialize_record",
  "id_field": "id",
  "old_field": "amount",
  "new_field": "total_amount",
  "verification_commands": ["python -m unittest test_records", "python -m py_compile records.py"]
}

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files

## Ranked Repo Map
- records.py: score=8 reasons=[goal_symbol_match:normalize,record,records, python_source]

## Test Source Links

## Recommended Read Order
- inspect_source: records.py (goal_symbol_match:normalize,record,records, python_source)

## Targeted Reading Plan
- records.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0

## Hypothesis Log
- [high] records.py is likely relevant to the requested code change. evidence=[goal_symbol_match:normalize,record,records, python_source] risk=local change risk appears limited

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- records.py: impact=medium dependents=0 tests=[]

## Risk Register
- [medium] no_test_surface_detected: require syntax checks and task-specific verification commands

## Acceptance Criteria
- requested_behavior_addressed: patch candidate selected from explicit contract, task text, or test evidence
- source_scope_is_explained: changed files map back to repo survey or review warns about drift
- changed_python_compiles: py_compile runs for changed Python files
- task_verification_passes: requested or inferred verification commands return zero
- review_has_no_blockers: code_review decision record approves final package

## Test Strategy
- fallback: python -m py_compile <changed .py files>
- fallback: git diff --check

## Test Surface

## Python Symbol Surface
- records.py: functions=[normalize_record] classes=[]

## Suggested Verification

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
