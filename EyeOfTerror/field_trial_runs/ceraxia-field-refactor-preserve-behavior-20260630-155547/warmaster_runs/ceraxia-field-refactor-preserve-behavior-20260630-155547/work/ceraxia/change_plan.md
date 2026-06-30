# Ceraxia Change Plan

Goal: кодовая задача: отрефактори duplicated business logic без изменения публичных функций и поведения.
CERAXIA_TARGET_REPO: EyeOfTerror/field_trial_runs/ceraxia-field-refactor-preserve-behavior-20260630-155547/fixture/repo
CERAXIA_REFACTOR:
{
  "helper_path": "common/calculations.py",
  "helper_function": "net_amount",
  "arguments": ["gross", "fee"],
  "return_expression": "gross - fee",
  "baseline_verification_commands": ["python -m unittest discover"],
  "replacements": [
    {
      "path": "orders.py",
      "public_function": "order_total",
      "old": "def order_total(gross, fee):\n    return gross - fee\n",
      "new": "from common.calculations import net_amount\n\n\ndef order_total(gross, fee):\n    return net_amount(gross, fee)\n"
    },
    {
      "path": "refunds.py",
      "public_function": "refund_total",
      "old": "def refund_total(gross, fee):\n    return gross - fee\n",
      "new": "from common.calculations import net_amount\n\n\ndef refund_total(gross, fee):\n    return net_amount(gross, fee)\n"
    }
  ],
  "verification_commands": ["python -m unittest discover", "python -m py_compile orders.py refunds.py common/calculations.py"]
}

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files
- orders.py
- refunds.py

## Ranked Repo Map
- orders.py: score=22 reasons=[goal_filename_match, imported_by:test_totals.py, goal_symbol_match:order,orders,total, python_source]
- refunds.py: score=22 reasons=[goal_filename_match, imported_by:test_totals.py, goal_symbol_match:refund,refunds,total, python_source]
- test_totals.py: score=13 reasons=[test_surface, linked_test, goal_symbol_match:order,orders,refund,refunds,total]

## Test Source Links
- test_totals.py -> orders.py, refunds.py

## Recommended Read Order
- inspect_source: orders.py (goal_filename_match, imported_by:test_totals.py, goal_symbol_match:order,orders,total)
- inspect_source: refunds.py (goal_filename_match, imported_by:test_totals.py, goal_symbol_match:refund,refunds,total)
- inspect_test: test_totals.py (test_surface, linked_test, goal_symbol_match:order,orders,refund,refunds,total)

## Targeted Reading Plan
- orders.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=1
- refunds.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=1
- test_totals.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0

## Hypothesis Log
- [high] orders.py is likely relevant to the requested code change. evidence=[goal_filename_match, imported_by:test_totals.py, goal_symbol_match:order,orders,total, python_source] risk=public behavior may affect dependents
- [high] refunds.py is likely relevant to the requested code change. evidence=[goal_filename_match, imported_by:test_totals.py, goal_symbol_match:refund,refunds,total, python_source] risk=public behavior may affect dependents
- [high] test_totals.py is likely relevant to the requested code change. evidence=[test_surface, linked_test, goal_symbol_match:order,orders,refund,refunds,total] risk=local change risk appears limited

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- orders.py: impact=medium dependents=1 tests=[test_totals.py]
- refunds.py: impact=medium dependents=1 tests=[test_totals.py]
- test_totals.py: impact=medium dependents=0 tests=[]

## Risk Register

## Acceptance Criteria
- requested_behavior_addressed: patch candidate selected from explicit contract, task text, or test evidence
- source_scope_is_explained: changed files map back to repo survey or review warns about drift
- changed_python_compiles: py_compile runs for changed Python files
- task_verification_passes: requested or inferred verification commands return zero
- review_has_no_blockers: code_review decision record approves final package

## Test Strategy
- primary: python -m unittest discover
- primary: python -m unittest test_totals
- linked: test_totals.py
- fallback: python -m py_compile <changed .py files>
- fallback: git diff --check

## Test Surface
- test_totals.py

## Python Symbol Surface
- orders.py: functions=[order_total] classes=[]
- refunds.py: functions=[refund_total] classes=[]
- test_totals.py: functions=[] classes=[TotalsTest]

## Suggested Verification
- python -m unittest discover
- python -m unittest test_totals

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
