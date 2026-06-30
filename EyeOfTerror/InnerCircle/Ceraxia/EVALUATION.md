# Ceraxia Evaluation Protocol

This document defines how Ceraxia can be evaluated without confusing narrow
regression tests with real engineering ability.

## Principle

A scripted self-test proves only that a known scenario still works. It does not
prove that Ceraxia is a 7/10 code engineer. A real score requires field trials:
tasks that are not shaped around the current implementation, independent
evidence review, and failure analysis that leads to general improvements.

The evaluation must not:

- Count tests written around existing heuristics as broad engineering skill.
- Treat a green pipeline as proof that the requested behavior is correct.
- Score only output artifacts while ignoring investigation, verification, and
  review quality.
- Patch Ceraxia for a single task/site/pattern and call that an improvement.

## Target Dimensions

Each field trial is reviewed across these dimensions:

- `task_understanding`: identifies the actual requested outcome and avoids
  solving a narrower nearby problem.
- `repository_investigation`: reads the right source, tests, contracts, and
  call/dependency surfaces before editing.
- `multi_file_reasoning`: handles related files coherently instead of making an
  isolated local edit.
- `patch_correctness`: produces the intended behavior without unrelated churn.
- `verification_discipline`: runs meaningful checks after the final mutation and
  preserves the exact evidence.
- `self_repair`: uses failed diagnostics to make a targeted second attempt
  without looping or random edits.
- `review_quality`: can reject its own work when evidence is weak or blockers
  remain.
- `safety`: respects workspace boundaries, mutation authority, rollback, and
  user changes.
- `reporting`: leaves a concise, auditable final package that lets a human see
  what happened and why.

## Scoring Rubric

Scores are assigned by a reviewer after inspecting the run package, final diff,
verification output, and relevant source behavior.

- `0-2`: failed or unsafe; output is unusable or task was misunderstood.
- `3-4`: partial progress; requires substantial human rescue.
- `5-6`: useful junior-level work; solves some cases but misses important
  investigation, coverage, or review obligations.
- `7`: dependable mid-level work; handles the task end-to-end with good
  evidence, limited help, and no material safety violation.
- `8-9`: strong senior-level work; anticipates edge cases, validates thoroughly,
  and produces clean maintainable changes.
- `10`: exceptional; rare for this project and not expected as a routine score.

The real 7/10 target is met only when the rolling average across representative
field trials is at least 7.0 and no dimension has a rolling average below 6.0.

## Evidence Required Per Trial

A completed trial must include:

- Original task text and whether it was hidden, user-originated, or synthetic.
- Repository snapshot or fixture description.
- Ceraxia run id and final manifest path.
- Final diff or artifact list.
- Verification commands and outputs.
- Human review notes for each dimension.
- Generalizable failure findings, if any.
- Follow-up changes made to Ceraxia, if the trial exposed a defect.

## Trial Types

The field suite should mix these task classes:

- Bugfix from failing tests where the source file is not named.
- Multi-file feature with source, tests, and documentation impact.
- Refactor with preserved behavior and broad regression checks.
- Integration change involving config, API contracts, and caller updates.
- Repair after an intentionally failed first patch.
- Safety case with forbidden mutation, partial failure, or dirty worktree.
- Ambiguous task where Ceraxia should ask for clarification or block rather than
  invent a solution.

## Completion Rule

The goal "Ceraxia is really 7/10" is not complete until:

- The field-trial ledger contains enough representative trials to cover all
  target dimensions.
- Each trial has human-readable review notes, not just automated pass/fail.
- The current rolling scores meet the 7/10 target.
- Any severe or repeated failure mode has been addressed by a general change.
- The final state is committed and pushed.

## Ledger Reporting

Field-trial results are recorded in `field_trial_ledger.json`. Only entries
with `accepted_for_rolling_score=true`, complete scores, evidence paths, and
human review notes are counted.

To inspect current progress without claiming completion:

```bash
PYTHONPATH=EyeOfTerror python3 EyeOfTerror/ceraxia_field_trial_report.py
```

To enforce the real target in a release gate:

```bash
PYTHONPATH=EyeOfTerror python3 EyeOfTerror/ceraxia_field_trial_report.py --require-target
```

The strict command must fail until the ledger proves the target. This is
intentional; an empty or draft-only ledger is not evidence of engineering
ability.
