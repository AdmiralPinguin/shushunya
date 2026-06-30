# Ceraxia Change Plan

Goal: кодовая задача: исправь расхождение настройки между JSON config, Python loader и shell entrypoint.
CERAXIA_TARGET_REPO: EyeOfTerror/field_trial_runs/ceraxia-field-cross-language-config-20260630-155545/fixture/repo
CERAXIA_CONFIG_RUNTIME:
{
  "config_path": "app/settings.json",
  "loader_path": "app/config_loader.py",
  "entrypoint_path": "bin/run-app.sh",
  "test_path": "tests/test_config_loader.py",
  "setting_key": "service_url",
  "env_var": "SERVICE_URL",
  "default_value": "http://localhost:8080",
  "verification_commands": ["python -m unittest tests.test_config_loader", "python -m py_compile app/config_loader.py"]
}

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files

## Ranked Repo Map

## Test Source Links

## Recommended Read Order

## Targeted Reading Plan

## Hypothesis Log
- [low] No strong source candidate was found from filenames, symbols, or tests. evidence=[repository map has no high-signal ranked files] risk=manual task clarification or broader survey may be required

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix

## Risk Register
- [high] no_ranked_source_candidate: block broad source mutation until a focused file or failing test identifies the target
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

## Suggested Verification

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
