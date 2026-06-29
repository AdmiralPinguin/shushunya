# Lore Reconstruction Playbook

Use this playbook for tasks like "collect everything known about an event and
turn it into a readable reconstruction".

## Contract

The governor must define:

- target event/topic;
- direct focus;
- non-goals;
- source classes to search;
- required artifacts;
- pass/fail criteria.

## Required Artifacts

```text
/work/<slug>/source_map.json
/work/<slug>/direct_event_notes.json
/work/<slug>/timeline.json
/work/<slug>/reconstruction_ru.md
/work/<slug>/coverage_report.md
/work/<slug>/critic_report.json
```

## Worker Sequence

1. `CorpusIngestor`: local user-provided corpus index and primary-text candidates.
2. `Lexmechanic`: source discovery and reliability map.
3. `AuspexBrowser`: source acquisition for public URLs and local corpus texts.
4. `NoosphericExtractor`: fact extraction with confidence labels.
5. `Chronologis`: event ordering, contradictions, missing links.
6. `ScriptoriumDaemon`: readable Russian reconstruction from extracted facts.
7. `ReductorVerifier`: independent criticism against the task contract.
8. `FabricatorFinalis`: package files and send requested outputs.

## Failure Conditions

- only one wiki source was used for a broad research task;
- direct events were replaced by consequences or general lore;
- source gaps were hidden;
- critic report is missing;
- final text contains unsupported facts;
- final text is only a short summary when the task asked for full coverage.
