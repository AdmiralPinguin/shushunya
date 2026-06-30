# Ceraxia Change Plan

Goal: кодовая expert-задача: исправь path traversal boundary без поломки легитимных относительных путей, добавь malicious и positive edge-case tests, укажи security assumptions.
CERAXIA_TARGET_REPO: /media/shushunya/SHUSHUNYA/shushunya/EyeOfTerror/field_trial_runs/ceraxia-expert-security-boundary-20260630-181346/fixture/repo
CERAXIA_EDGE_FIX:
{
  "source_path": "archive_paths.py",
  "function_name": "safe_archive_path",
  "arguments": [
    "raw"
  ],
  "body_lines": [
    "candidate = str(raw).replace('\\\\\\\\', '/')",
    "parts = [part for part in candidate.split('/') if part not in ('', '.')]",
    "if candidate.startswith('/') or '..' in parts:",
    "    raise ValueError('archive path escapes root')",
    "if not parts:",
    "    raise ValueError('archive path is empty')",
    "return '/'.join(parts)"
  ],
  "test_path": "tests/test_archive_paths.py",
  "positive_cases": [
    {
      "inputs": [
        "books/chapter1.txt"
      ],
      "expected": "books/chapter1.txt"
    },
    {
      "inputs": [
        "./books//chapter2.txt"
      ],
      "expected": "books/chapter2.txt"
    }
  ],
  "negative_cases": [
    {
      "inputs": [
        "../secret.txt"
      ],
      "exception": "ValueError"
    },
    {
      "inputs": [
        "/etc/passwd"
      ],
      "exception": "ValueError"
    },
    {
      "inputs": [
        "books/../../secret.txt"
      ],
      "exception": "ValueError"
    }
  ],
  "verification_commands": [
    "python -m unittest tests.test_archive_paths",
    "python -m py_compile archive_paths.py"
  ]
}

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files
- archive_paths.py

## Ranked Repo Map
- archive_paths.py: score=15 reasons=[goal_filename_match, goal_symbol_match:archive,path,paths,safe, python_source]

## Test Source Links

## Recommended Read Order
- inspect_source: archive_paths.py (goal_filename_match, goal_symbol_match:archive,path,paths,safe, python_source)

## Targeted Reading Plan
- archive_paths.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0

## Hypothesis Log
- [high] archive_paths.py is likely relevant to the requested code change. evidence=[goal_filename_match, goal_symbol_match:archive,path,paths,safe, python_source] risk=local change risk appears limited

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- archive_paths.py: impact=medium dependents=0 tests=[]

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
- archive_paths.py: functions=[safe_archive_path] classes=[]

## Suggested Verification

## Implementation Policy
- Produce an auditable patch manifest before mutating source files.
- Require verification commands or explicit blockers before final readiness.

## Task Profile
- kinds: test_repair, new_feature, bugfix
- complexity: medium
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
