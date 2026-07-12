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

The public `IskandarKhayon` service remains the Inner Circle governor on port
`7101`; it is not the execution warband. It emits a strict leadership-level
research directive. A separate native `ResearchWarband` backend (initial shadow
port `7201`) owns detailed research planning and execution. The backend must not
be represented as a synthetic Mechanicum worker, and Iskandar directives must
reject search queries, URLs, worker steps, tool calls, and other detailed plans.

The replacement is organized around six explicit responsibilities:

1. **Scout** — discover candidate sources and choose acquisition strategy.
2. **Reader** — fetch, render, parse, and normalize source material.
3. **EvidenceLedger** — store claims, excerpts, provenance, conflicts, gaps,
   and confidence without losing traceability.
4. **Analyst** — compare evidence, reconstruct chronology/arguments, and mark
   inference separately from sourced fact.
5. **Writer** — produce the requested output from the evidence ledger and plan.
6. **Verifier** — run deterministic gates plus a fresh, context-isolated Gemma
   31B semantic-review pass over coverage, citation support, contradiction
   handling, and task compliance before acceptance.

All internal semantic roles use Gemma 31B. Reader coverage and semantic review
are fresh same-model passes, not separate physical authorities, and must report
`epistemic_independence_claimed=false`. Trust comes from exact
application-owned evidence/provenance, content-bound review sessions, and
deterministic gates. Qwen Coder belongs to the coding Warband; it is neither a
dependency nor a readiness condition for Iskandar.

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
- immutable verification fixtures, deterministic acceptance gates, and a fresh
  context-isolated same-model semantic-review pass;
- honest blocked states for missing evidence or unavailable infrastructure;
- Warmaster preflight, mission lifecycle, and rollback compatibility;
- a measured result at least equal to the current service on retained legacy
  scenarios and materially better on multi-source synthesis.

The 30 public design tasks are a development smoke, not sufficient cutover
evidence. Cutover additionally requires an evaluator outside the warband,
sealed fixtures/keys, post-freeze canaries, staged shadow traffic, and a tested
rollback window. Integrity checks prove snapshot provenance; independent
evaluation belongs only to the external evaluator with sealed fixtures and
rubrics. The internal Gemma semantic-review pass checks whether cited spans
support the claims but does not claim epistemic independence.

## External Evaluator Future Work

The current deterministic oracle for some known-answer tasks compares accepted
output with canned English strings. It can therefore mislabel a semantically
correct multilingual answer or paraphrase as `false_accept`. Do not tune the
Warband to those strings or expand the canned-answer list; either change would
hide an evaluator defect and leak the key.

A future semantic judge must be a physically independent model, while
deterministic provenance, exact span/locator, and relation checks remain in
force. Until such a judge exists, semantic correctness for these cases is
`unverified`, not a demonstrated `false_accept`. Neither another context of the
same Gemma 31B nor Qwen Coder is eligible as that independent judge.

Until those gates pass, the old Iskandar is frozen legacy, not deleted production
code.
