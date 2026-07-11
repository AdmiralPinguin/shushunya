# Iskandar Khayon

Port: `7101`

Architecture status: the current pipeline is frozen as a legacy compatibility
service while a clean Research Warband is designed. See
[`REWRITE_DECISION.md`](REWRITE_DECISION.md) for the accepted replacement scope
and cutover gates.

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
  -> ScriptoriumArchitect
  -> ScriptoriumDaemon
  -> ReductorVerifier
  -> FabricatorFinalis
```

Lore reconstruction is routed through this same research/writing pipeline; it is
not allowed to bypass `research_corpus.json`, `structure_map.json`, or
`synthesis_plan.json`.

## Research/Writing Pipeline

General research tasks now start with intent classification. Iskandar classifies
the request as one of `event_reconstruction`, `topic_report`, `comparison`,
`qa_answer`, `investigation`, `longform_article`, or `book`, then chooses an
output mode and whether chronology or chapters are required.

The shared evidence artifact is `research_corpus.json`. It is produced beside
the compatibility `direct_event_notes.json` and contains sources, snapshots,
rendered text excerpts, events, claims, arguments, definitions, evidence
excerpts, contradictions, confidence, and gaps.

For event tasks, `Chronologis` writes both `timeline.json` and
`structure_map.json`. For analytical tasks it writes source order, argument
flow, and topic structure into `structure_map.json` while preserving
`timeline.json` compatibility. Short Q&A tasks may skip `Chronologis` entirely.

`ScriptoriumArchitect` then writes `synthesis_plan.json`; book tasks also get
`book_outline.json` and `chapter_plan.json`. `ScriptoriumDaemon` writes from
`research_corpus + synthesis_plan + output_mode`; sections without evidence
trace must stay blocked instead of being filled with invented text.

## Research Quality Gates

- Source map exists and separates primary, secondary, wiki, and community
  sources.
- Research corpus exists and contains claims or events with evidence trace.
- Direct claims/facts are separated from reconstruction and interpretation.
- Structure map or timeline records uncertainty and contradictions when needed.
- Writer does not invent facts absent from extractor output.
- Critic checks the result against the original task contract.
- Final answer reports gaps honestly.

## Training Scenario

The Battle of Skalathrax task is the first training case. A shallow wiki summary
is considered a failure. A pass requires source coverage, direct event notes,
timeline, reconstruction, coverage report, and critic review.
