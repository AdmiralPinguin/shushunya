# Ceraxia Change Plan

Goal: кодовая expert-задача: проведи public API evolution с deprecated параметром, сохрани старых callers через warning, обнови нового caller, docs и tests old/new call styles.
CERAXIA_TARGET_REPO: /media/shushunya/SHUSHUNYA/shushunya/EyeOfTerror/field_trial_runs/ceraxia-expert-public-api-deprecation-20260630-181345/fixture/repo
CERAXIA_FILES:
{
  "files": [
    {
      "path": "payments/api.py",
      "overwrite": true,
      "content": "import warnings\n\ndef calculate_total(gross, fee=0, *, service_fee=None):\n    if service_fee is None:\n        service_fee = fee\n        if fee != 0:\n            warnings.warn('fee is deprecated; use service_fee', DeprecationWarning, stacklevel=2)\n    return gross - service_fee\n"
    },
    {
      "path": "payments/client.py",
      "content": "from payments.api import calculate_total\n\ndef client_total(gross, service_fee):\n    return calculate_total(gross, service_fee=service_fee)\n"
    },
    {
      "path": "tests/test_api_deprecation.py",
      "content": "import warnings\nimport unittest\nfrom payments.api import calculate_total\nfrom payments.client import client_total\n\nclass ApiDeprecationTest(unittest.TestCase):\n    def test_old_positional_fee_still_works_with_warning(self):\n        with warnings.catch_warnings(record=True) as caught:\n            warnings.simplefilter('always')\n            self.assertEqual(calculate_total(100, 15), 85)\n        self.assertTrue(any(item.category is DeprecationWarning for item in caught))\n\n    def test_new_keyword_path_and_caller(self):\n        self.assertEqual(calculate_total(80, service_fee=5), 75)\n        self.assertEqual(client_total(80, 5), 75)\n\nif __name__ == '__main__':\n    unittest.main()\n"
    },
    {
      "path": "docs/api_deprecation.md",
      "content": "# API Deprecation\n\n`fee` remains supported with a warning; new callers use `service_fee`.\n"
    }
  ],
  "verification_commands": [
    "python -m unittest tests.test_api_deprecation",
    "python -m py_compile payments/api.py payments/client.py"
  ]
}

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files
- payments/api.py

## Ranked Repo Map
- payments/api.py: score=15 reasons=[goal_filename_match, goal_symbol_match:api,calculate,payments,total, python_source]

## Test Source Links

## Recommended Read Order
- inspect_source: payments/api.py (goal_filename_match, goal_symbol_match:api,calculate,payments,total, python_source)

## Targeted Reading Plan
- payments/api.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0

## Hypothesis Log
- [high] payments/api.py is likely relevant to the requested code change. evidence=[goal_filename_match, goal_symbol_match:api,calculate,payments,total, python_source] risk=local change risk appears limited

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- payments/api.py: impact=medium dependents=0 tests=[]

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
- payments/api.py: functions=[calculate_total] classes=[]

## Suggested Verification

## Implementation Policy
- Produce an auditable patch manifest before mutating source files.
- Require verification commands or explicit blockers before final readiness.

## Task Profile
- kinds: explicit_patch, test_repair, api_contract, multi_file, bugfix
- complexity: high
- risk: multi_file_scope_drift
- risk: test_diagnostic_required
- risk: public_contract_regression

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
