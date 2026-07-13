# Shushunya Core design

## Subject, not a bundle

ShushunyaCore is one logical, durable subject. Archive is memory, Abaddon is the
task bus and hands, Administratum is time, Iskandar is research sight, Ceraxia
commands the coding warband, Vox owns unsaid reports, and WarpWails is an
optional voice sink. The Core talks to warbands only through Abaddon.

Personality has four separately inspectable layers:

1. **Identity** — slow values, temperament and opinions. Model output cannot
   rewrite it directly.
2. **Relationship** — the evolving contract and history with the owner.
3. **Agency** — agenda, commitments, effects and honest reconciliation.
4. **Attention** — whether a fact deserves interruption, a later digest, or
   silence.

## Honest turn

```text
durable input
  -> one assembled situation (identity + relationship + Archive + live truth)
  -> proposal and speech intent
  -> code-owned authority check
  -> transactional commitment + outbox
  -> organ execution
  -> factual verification
  -> final acknowledgement/report
  -> memory proposal
```

A conversational answer is complete in the first pass. An external action has
no final speech before execution. If structured decision generation fails once,
Core retries the contract once and then degrades to speech only; it never
fabricates an effect.

## State vocabulary

Commitments use `queued`, `working`, `revising`, `waiting_user`,
`waiting_external`, `retry_wait`, `succeeded`, `failed`, `cancelled`, and
`quarantined`. There is deliberately no `blocked`.

Every waiting/error state contains:

- a stable diagnostic code;
- a human explanation;
- factual evidence;
- the required next action;
- an explicit resume condition.

An executable subordinate revision maps to `revising`. An unexplained
subordinate `blocked` result maps to bounded `retry_wait` and then
`quarantined`: Core keeps the diagnosis but does not pretend that repair has
started. Only a proven external dependency with a resume condition maps to
`waiting_external`.

Continuation is action-driven, not status-driven. Core may execute only a
concrete POST action published by Abaddon and accepted by a hard allow-list:
revision, resume, immutable-run reprepare, or a verified apply carrying the
repository/patch/checks SHA triplet. A label such as `revise_code_mission`
without a method and endpoint is diagnostic evidence, not an executable act.

## Continuous steward

The steward is not a make-work loop. It scores finite agenda items by expected
value, confidence, urgency, cost and risk. Every item has a stop condition,
resource budget and attempt limit. Empty or low-value agenda means deliberate
idle.

The first deployed steward does two useful jobs only:

- deliver already-authorized durable effects with stable idempotency keys;
- reconcile delegated commitments against Abaddon's canonical orchestration
  state after normal operation or reboot.

Self-directed research, memory curation and corpus preparation are later slices,
after attention/digest delivery and resource preemption are proven live.

## Migration slices

1. Durable Core, boot recovery, identity/relationship projections, Abaddon
   effect and commitment reconciliation.
2. Replace Archive's poor-context `decide_chat_turn_action` with one rich Core
   envelope while preserving `/archive/client/*`, SSE, jobs and history.
3. Context-scoped preference evidence and explicit autonomy promotion.
4. Attention/digest integration through a repaired Vox outbox.
5. Move remaining answer/effect orchestration out of Archive; Archive becomes a
   memory and transport organ only.
6. Add bounded self-directed steward work and model/corpus promotion gates.

## Deliberately later

- Resume a `waiting_user` commitment from a natural-language owner reply through
  a typed clarification capability, without guessing which mission it belongs
  to.
- Move answer generation fully behind Core so Archive becomes transport plus
  memory persistence, not a second conversational controller.
- Add token-aware situation packing when the temporary 6144-token one-GPU
  profile is replaced; the current deployment uses a measured 2800-character
  hard envelope and 1200-token output reserve.
- Give voice/vision/desktop control their own typed effect adapters and policy
  boundaries. Telegram remains a transport, never the personality architecture.
