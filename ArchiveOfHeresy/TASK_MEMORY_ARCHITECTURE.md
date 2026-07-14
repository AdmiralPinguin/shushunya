# Durable task memory

The unit of autonomous work is a durable goal, not one model session and not
one Abaddon run. Every goal owns one canonical task page identified by
`task_memory_id`. Immutable execution attempts are aliases of that page.

## Identity

- `task_memory_id`: stable identity of the goal and its task page.
- `root_task_id`: first task identity in the lineage.
- `run_task_id`: one immutable Abaddon attempt.
- `parent_task_id`: immediately preceding attempt, when this is a continuation.

A run may be replaced; the task page must not be replaced. An existing run
cannot be rebound to another page, and an alias cannot belong to two goals.

## Source of truth

`runtime/task_memory.sqlite3` is the canonical store. Markdown returned by the
Archive API is a bounded rendering of the current structured snapshot; it is
not another writable copy. The store keeps:

- the verbatim goal and success conditions;
- current state and strategy;
- decisions and constraints;
- completed work and failed approaches;
- current working set and next actions;
- open requirements;
- aliases, revisions and an idempotent event log.

Mission ledgers, patches, test output and artifacts remain execution evidence.
The task page is semantic working memory, not authority and not proof that a
check passed.

## Context compaction

Before a fighter reaches the model context limit, the controller asks for a
small structured checkpoint, writes it to the task page with compare-and-swap,
then starts a fresh model context containing only:

1. the fighter system contract;
2. the original goal and success checks;
3. the latest task-page checkpoint.

The workspace is not reset during context compaction. If the backend reports a
context overflow before the proactive threshold, the controller performs the
same reset from a bounded local transcript. A summarizer failure falls back to
a controller-built checkpoint; it does not kill the goal.

Switching tasks therefore behaves like switching durable conversations: each
task resumes from its own page instead of carrying every previous token.

## Failure and recovery

A failed attempt is an event in a live goal. It is not a terminal goal state.
Internal failures must publish an explanation, preserved work and a concrete
next action. Core creates a new immutable Abaddon attempt with the same
`task_memory_id`; the next Skitarii attempt restores the saved patch onto the
byte-identical baseline and continues.

Only these conditions may stop autonomous continuation:

- the goal succeeded with execution evidence;
- the owner explicitly cancelled it;
- a genuine owner decision is required and is expressed as a typed question;
- a real external dependency is unavailable and its resume condition is known.

Generic `blocked` is not a decision and must never silently discard a goal.

## Warband adoption contract

Ceraxia/Skitarii are the first adopter. Every other warband, including
Iskandar's research warband, must use the same identity and page API rather than
inventing a private transcript store. A brigadier reads the page when choosing
the next strategy; workers checkpoint it at context boundaries and meaningful
state changes; Abaddon owns lineage and autonomous replacement attempts.
