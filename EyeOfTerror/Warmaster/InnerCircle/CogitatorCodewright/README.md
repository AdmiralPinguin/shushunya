# CogitatorCodewrightGovernor

Planned Inner Circle governor for code and repository tasks.

This governor should not be activated until it can produce task contracts,
dispatch worker steps, and pass end-to-end tests comparable to Iskandar.

## Intended Scope

- Repository inspection and code modification tasks.
- Test planning and verification.
- Pulling reusable patterns from external coding agents without copying
  task-specific hacks.
- Coordinating code workers, reviewers, and CI/debug tools.

## Intended Worker Chain

1. `CogitatorCodewright` inspects repository context and proposes an edit plan.
2. A code worker applies scoped edits.
3. A verifier runs tests, linters, and targeted reproduction checks.
4. A reviewer worker checks risk, regressions, and missing tests.
5. A finalizer records summary, changed files, tests, and residual risk.

## Activation Requirements

- Code task contract builder exists.
- At least one end-to-end code task test exists.
- Warmaster routing can distinguish code tasks from general chat safely.
- The governor records task ledgers and artifacts through the same API used by
  Iskandar.
