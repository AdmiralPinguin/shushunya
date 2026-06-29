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

The first prototype produces auditable coding artifacts and a safe handoff.
The named workers currently share the same execution core, which keeps the
protocol stable while their internals are split into stronger specialized
implementations.
