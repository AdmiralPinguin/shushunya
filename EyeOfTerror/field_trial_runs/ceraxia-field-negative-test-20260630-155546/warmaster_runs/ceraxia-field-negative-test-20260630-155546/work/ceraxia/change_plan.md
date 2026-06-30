# Ceraxia Change Plan

Goal: кодовая задача: исправь retry parsing так, чтобы простой happy-path фикс не был достаточным; добавь негативные edge-case тесты.
CERAXIA_TARGET_REPO: EyeOfTerror/field_trial_runs/ceraxia-field-negative-test-20260630-155546/fixture/repo
CERAXIA_EDGE_FIX:
{
  "source_path": "retry_policy.py",
  "function_name": "parse_retry_count",
  "arguments": ["raw"],
  "body_lines": [
    "value = int(raw)",
    "if value < 0 or value > 10:",
    "    raise ValueError('retry count must be between 0 and 10')",
    "return value"
  ],
  "test_path": "test_retry_policy.py",
  "positive_cases": [
    {"inputs": ["0"], "expected": 0},
    {"inputs": ["3"], "expected": 3},
    {"inputs": ["10"], "expected": 10}
  ],
  "negative_cases": [
    {"inputs": ["-1"], "exception": "ValueError"},
    {"inputs": ["11"], "exception": "ValueError"},
    {"inputs": ["bad"], "exception": "ValueError"}
  ],
  "verification_commands": ["python -m unittest test_retry_policy", "python -m py_compile retry_policy.py"]
}

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files
- retry_policy.py

## Ranked Repo Map
- retry_policy.py: score=15 reasons=[goal_filename_match, goal_symbol_match:count,parse,policy,retry, python_source]

## Test Source Links

## Recommended Read Order
- inspect_source: retry_policy.py (goal_filename_match, goal_symbol_match:count,parse,policy,retry, python_source)

## Targeted Reading Plan
- retry_policy.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0

## Hypothesis Log
- [high] retry_policy.py is likely relevant to the requested code change. evidence=[goal_filename_match, goal_symbol_match:count,parse,policy,retry, python_source] risk=local change risk appears limited

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- retry_policy.py: impact=medium dependents=0 tests=[]

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
- retry_policy.py: functions=[parse_retry_count] classes=[]

## Suggested Verification

## Implementation Policy
- Produce an auditable patch manifest before mutating source files.
- Require verification commands or explicit blockers before final readiness.

## Task Profile
- kinds: test_repair, new_feature, bugfix
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
