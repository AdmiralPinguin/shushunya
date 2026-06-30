# Ceraxia Change Plan

Goal: кодовая expert-задача: первая зеленая реализация с hardcoded branch должна считаться review failure; сделай targeted revision с расширяемой архитектурой, caller compatibility и сохраненной evidence.
CERAXIA_TARGET_REPO: /media/shushunya/SHUSHUNYA/shushunya/EyeOfTerror/field_trial_runs/ceraxia-expert-failed-review-revision-20260630-181347/fixture/repo
CERAXIA_FILES:
{
  "files": [
    {
      "path": "tax/rates.py",
      "overwrite": true,
      "content": "RATES = {'standard': 0.20, 'reduced': 0.05}\n\ndef tax_for(amount, category='standard'):\n    return amount * RATES[category]\n"
    },
    {
      "path": "tax/invoice.py",
      "content": "from tax.rates import tax_for\n\ndef invoice_tax(amount, category='standard'):\n    return tax_for(amount, category)\n"
    },
    {
      "path": "tests/test_tax_rates.py",
      "content": "import unittest\nfrom tax.invoice import invoice_tax\nfrom tax.rates import tax_for\n\nclass TaxRatesTest(unittest.TestCase):\n    def test_standard_and_reduced_rates(self):\n        self.assertEqual(tax_for(100), 20)\n        self.assertEqual(tax_for(100, 'reduced'), 5)\n        self.assertEqual(invoice_tax(100, 'reduced'), 5)\n\nif __name__ == '__main__':\n    unittest.main()\n"
    },
    {
      "path": "docs/review_revision.md",
      "content": "# Review Revision\n\nThe final shape avoids hard-coded branch logic and keeps caller compatibility through `invoice_tax`.\n"
    }
  ],
  "verification_commands": [
    "python -m unittest tests.test_tax_rates",
    "python -m py_compile tax/rates.py tax/invoice.py"
  ]
}

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files
- tax/rates.py

## Ranked Repo Map
- tax/rates.py: score=14 reasons=[goal_filename_match, goal_symbol_match:for,rates,tax, python_source]

## Test Source Links

## Recommended Read Order
- inspect_source: tax/rates.py (goal_filename_match, goal_symbol_match:for,rates,tax, python_source)

## Targeted Reading Plan
- tax/rates.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0

## Hypothesis Log
- [high] tax/rates.py is likely relevant to the requested code change. evidence=[goal_filename_match, goal_symbol_match:for,rates,tax, python_source] risk=local change risk appears limited

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- tax/rates.py: impact=medium dependents=0 tests=[]

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
- tax/rates.py: functions=[tax_for] classes=[]

## Suggested Verification

## Implementation Policy
- Produce an auditable patch manifest before mutating source files.
- Require verification commands or explicit blockers before final readiness.

## Task Profile
- kinds: explicit_patch, test_repair, multi_file, bugfix
- complexity: high
- risk: multi_file_scope_drift
- risk: test_diagnostic_required

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
