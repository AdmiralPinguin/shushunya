# Iskandar Rewrite Decision

Status: **accepted, implementation deferred**
Recorded: 2026-07-11

## Decision

Freeze the current Iskandar implementation as a legacy compatibility system. Do
not continue expanding its ten-worker linear pipeline. Build the next Research
Warband from a clean architecture, as was done for the new Ceraxia/Skitarii
coding warband.

This is not an instruction to delete the live service immediately. Port `7101`
and the current Warmaster contracts stay available until the replacement passes
its own evaluation suite and a compatibility cutover is ready.

## Target Warband

The replacement is organized around six explicit responsibilities:

1. **Scout** — discover candidate sources and choose acquisition strategy.
2. **Reader** — fetch, render, parse, and normalize source material.
3. **EvidenceLedger** — store claims, excerpts, provenance, conflicts, gaps,
   and confidence without losing traceability.
4. **Analyst** — compare evidence, reconstruct chronology/arguments, and mark
   inference separately from sourced fact.
5. **Writer** — produce the requested output from the evidence ledger and plan.
6. **Verifier** — independently test coverage, citation support, contradiction
   handling, and task compliance before acceptance.

## Preserve, Do Not Rebuild

Reuse proven infrastructure behind adapters where it remains useful:

- HTTP fetching and retry logic;
- Playwright/browser rendering;
- document and page parsers;
- the existing Corpus data and compatible provenance fields;
- Warmaster mission lifecycle and compatibility endpoints needed for cutover.

The legacy worker names and fixed ten-stage topology are not architectural
constraints for the replacement.

## Cutover Gates

The new Research Warband may replace Iskandar only when it demonstrates:

- held-out research tasks with no answer-key leakage;
- source/provenance retention from acquisition through final prose;
- explicit fact/inference/uncertainty separation;
- immutable verification fixtures and an independent acceptance pass;
- honest blocked states for missing evidence or unavailable infrastructure;
- Warmaster preflight, mission lifecycle, and rollback compatibility;
- a measured result at least equal to the current service on retained legacy
  scenarios and materially better on multi-source synthesis.

Until those gates pass, the old Iskandar is frozen legacy, not deleted production
code.
