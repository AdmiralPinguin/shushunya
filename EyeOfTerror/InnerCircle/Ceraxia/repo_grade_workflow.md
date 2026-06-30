# Ceraxia Repo-Grade Workflow

Ceraxia must not treat large coding work as a single local patch. A task enters
repo-grade mode when it mentions architecture, refactor, migration,
compatibility, real repo work, or a broad 8-15 file change.

## Required Passes

1. Survey: map source, tests, docs, config, dependency edges, and likely public
   surfaces before mutation.
2. Architecture decision: record the chosen approach, rejected alternatives,
   tradeoffs, compatibility notes, and rollback path.
3. Implementation: mutate only scoped files through explicit, marker-synthesized,
   or guarded inferred patch paths.
4. Focused verification: run task-specific checks for changed behavior.
5. Broad verification: run a repo-level regression check such as unittest
   discovery, or preserve an explicit blocker.
6. Self-review: gate patch scope, architecture evidence, diagnostics, focused
   verification, and broad verification.
7. Revision: if review blocks, rerun implementation and verification with
   focused context.
8. Final package: return a patch package and PR-style summary with changed
   files, verification, risks, blockers, and rollback notes.

## Acceptance

A repo-grade run is not ready unless the final manifest contains:

- `repo_grade_workflow`
- `architecture_decision_record`
- `verification_strategy`
- `review_decision_record`
- `patch_package`
- `pr_summary`

The review decision record must include architecture and broad-verification
checks. If broad verification cannot run, the final state is blocked until the
operator explicitly accepts that gap.
