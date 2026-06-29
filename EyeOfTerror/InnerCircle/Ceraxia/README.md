# Ceraxia

Ceraxia is the Inner Circle governor for code tasks.

She owns code-task decomposition, repository survey, scoped implementation
planning, patch manifest handoff, verification planning, code review, and final
handoff packaging.

## Default Worker Pipeline

```text
LogisRepository(repository_survey)
  -> MagosStrategos(change_planning)
  -> FerrumPatchwright(implementation)
  -> OrdinatusVerifier(verification)
  -> JudicatorCodicis(code_review)
  -> SealwrightFinalis(finalize)
```

## Current Boundary

The named workers currently share the same execution core, which keeps the
protocol stable while their internals are split into stronger specialized
implementations.

`FerrumPatchwright` can apply explicit patch operations embedded in the task:

```text
CERAXIA_TARGET_REPO: /absolute/path/to/repo
CERAXIA_PATCH:
{
  "operations": [
    {"type": "replace", "path": "module.py", "old": "return 1", "new": "return 2"},
    {"type": "write_file", "path": "new_file.py", "content": "..."}
  ]
}
```

Without explicit patch operations, Ceraxia writes a blocked handoff package
instead of claiming the code task is complete.
