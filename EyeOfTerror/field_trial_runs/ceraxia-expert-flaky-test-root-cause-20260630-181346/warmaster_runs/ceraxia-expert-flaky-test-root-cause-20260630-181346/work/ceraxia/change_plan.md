# Ceraxia Change Plan

Goal: кодовая expert-задача: расследуй intermittent/flaky ordering failure, исправь root cause без skip/xfail, докажи стабильность repeated verification.
CERAXIA_TARGET_REPO: /media/shushunya/SHUSHUNYA/shushunya/EyeOfTerror/field_trial_runs/ceraxia-expert-flaky-test-root-cause-20260630-181346/fixture/repo
CERAXIA_FILES:
{
  "files": [
    {
      "path": "scheduler.py",
      "overwrite": true,
      "content": "def schedule_order(items):\n    return sorted(items, key=lambda item: (item['priority'], item['id']))\n"
    },
    {
      "path": "tests/test_scheduler.py",
      "content": "import unittest\nfrom scheduler import schedule_order\n\nclass SchedulerTest(unittest.TestCase):\n    def test_stable_order_for_equal_priority(self):\n        items = [{'id': 'b', 'priority': 1}, {'id': 'a', 'priority': 1}]\n        self.assertEqual([item['id'] for item in schedule_order(items)], ['a', 'b'])\n\n    def test_repeated_stability(self):\n        for _ in range(20):\n            items = [{'id': 'c', 'priority': 2}, {'id': 'a', 'priority': 1}, {'id': 'b', 'priority': 1}]\n            self.assertEqual([item['id'] for item in schedule_order(items)], ['a', 'b', 'c'])\n\nif __name__ == '__main__':\n    unittest.main()\n"
    },
    {
      "path": "docs/flaky_root_cause.md",
      "content": "# Flaky Root Cause\n\nOrdering by priority alone left equal-priority items unstable; id is the deterministic tie-breaker.\n"
    }
  ],
  "verification_commands": [
    "python -m unittest tests.test_scheduler",
    "python -m unittest tests.test_scheduler",
    "python -m py_compile scheduler.py"
  ]
}

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files

## Ranked Repo Map
- scheduler.py: score=8 reasons=[goal_symbol_match:order,schedule,scheduler, python_source]

## Test Source Links

## Recommended Read Order
- inspect_source: scheduler.py (goal_symbol_match:order,schedule,scheduler, python_source)

## Targeted Reading Plan
- scheduler.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0

## Hypothesis Log
- [high] scheduler.py is likely relevant to the requested code change. evidence=[goal_symbol_match:order,schedule,scheduler, python_source] risk=local change risk appears limited

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- scheduler.py: impact=medium dependents=0 tests=[]

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
- scheduler.py: functions=[schedule_order] classes=[]

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
