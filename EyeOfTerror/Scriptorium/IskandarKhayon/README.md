# Iskandar Khayon

Port: `7101`

Iskandar Khayon is the first Inner Circle governor. He owns research, source
reconstruction, translation, and long-form synthesis tasks. Lore reconstruction
is a specialized training case inside that broader mandate.

He is a brigade leader, not a low-level worker. He should coordinate Mechanicum
workers and decide whether their outputs are good enough.

His service capabilities expose the required Mechanicum worker set for the
research/writing pipeline, so Warmaster/admin clients can compare requirements
against the worker registry before execution.
They also expose a compact pipeline summary with step dependencies and expected
artifacts, built from the same worker-plan source used for concrete contracts.

## Responsibilities

1. Convert the user's request into a task contract.
2. Decide which Mechanicum workers are needed.
3. Sequence the worker calls.
4. Reject shallow source coverage.
5. Send weak drafts back for revision.
6. Return a final package only after critic review passes or blockers are
   explicit.

## Default Worker Pipeline

```text
CorpusIngestor
  -> Lexmechanic
  -> AuspexBrowser
  -> OcularisRenderium
  -> NoosphericExtractor
  -> Chronologis
  -> ScriptoriumDaemon
  -> ReductorVerifier
  -> FabricatorFinalis
```

## Research Quality Gates

- Source map exists and separates primary, secondary, wiki, and community
  sources.
- Direct claims/facts are separated from reconstruction and interpretation.
- Timeline or source order records uncertainty and contradictions.
- Writer does not invent facts absent from extractor output.
- Critic checks the result against the original task contract.
- Final answer reports gaps honestly.

## Training Scenario

The Battle of Skalathrax task is the first training case. A shallow wiki summary
is considered a failure. A pass requires source coverage, direct event notes,
timeline, reconstruction, coverage report, and critic review.
