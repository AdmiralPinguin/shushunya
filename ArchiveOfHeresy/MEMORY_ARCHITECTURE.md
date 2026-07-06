# ArchiveOfHeresy Memory Architecture

ArchiveOfHeresy is the mandatory gateway between clients and the model. Clients
should not send long chat tails directly to the model. The archive prepares
compact memory context before the answer and maintains memory after the answer.

## Namespaces

Memory is scoped with `memory_namespace`.

- `default` is the normal chat namespace.
- `warmaster` is the Warmaster orchestration and brigade namespace.
- `telegram` is the Telegram bot namespace.
- `mobile` is the Android app chat namespace.
- `voice` and `translator` are reserved for voice and translation flows when
  those clients start using Archive memory directly.

Focus, wiki, vector retrieval, graph memory, and memory maintenance events are
namespace-aware. This keeps worker tool loops searchable by Warmaster without
leaking into the normal chat context.

## Layer 0: Raw Archive

Raw turns are stored in two parallel forms:

- JSONL by date under `archive/jsonl/YYYY/MM/YYYY-MM-DD.jsonl`.
- SQLite under `archive/sqlite/archive.sqlite3`.

The SQLite `turns` table stores `memory_namespace` so lower memory layers can
rebuild namespace-scoped indexes.

## Layer 1: Focus

Focus files are compact current-topic context.

- Default chat: `focus/files/*.md`.
- Warmaster mode: `focus/namespaces/warmaster/files/*.md`.
- Each namespace keeps at most 10 focus files.
- Removal prefers the least important files, then the oldest files.

Magos selects or creates the active focus before the model answer. The Librarian
fills or updates the focus after the answer.

## Layer 2: Wiki

Wiki memory stores durable sorted knowledge: decisions, stable project facts,
preferences, statuses, open questions, and superseded decisions.

- Default chat: `wiki/pages/*.md`.
- Warmaster mode: `wiki/namespaces/warmaster/pages/*.md`.

Wiki updates run after `ARCHIVE_WIKI_INTERVAL_MESSAGES` archived messages within
the namespace.

## Layer 3: Vector

Vector memory stores hashed sparse embeddings for archived user and assistant
chunks in `vector/index.sqlite3`.

Each chunk carries `memory_namespace`; searches from Magos and explicit agent
tools filter by namespace. This keeps worker tool output out of default chat
retrieval.

## Layer 4: GraphRAG

Graph memory stores stable entities and relations.

- Default chat: `graph/graph.sqlite3`.
- Warmaster mode: `graph/namespaces/warmaster/graph.sqlite3`.

Graph updates run after `ARCHIVE_GRAPH_INTERVAL_MESSAGES` archived messages
within the namespace.

## Agents

Magos runs before the main model request. It:

- chooses an existing focus or opens a new empty focus;
- gathers compact lower-layer context only from `ARCHIVE_MAGOS_CONTEXT_LAYERS`;
- fails soft, so the main model request can continue without it.

The Librarian runs after successful model answers. It:

- indexes vector chunks;
- updates focus;
- periodically updates wiki;
- periodically updates graph;
- writes memory maintenance events under `archive/memory_events`.

## Memory Gateway

ArchiveOfHeresy exposes a controlled Memory Gateway on the archive HTTP port.
Warmaster workers should use this port instead of reading or writing memory files.

Read endpoints:

```text
GET /archive/memory/gateway
GET /archive/memory/catalog?namespace=warmaster&requester=name
GET /archive/memory/search?namespace=warmaster&q=query&limit=5&layers=focus,wiki,vector,graph&include_content=0&requester=name
GET /archive/memory/focus?namespace=warmaster&id=active&requester=name
GET /archive/memory/wiki?namespace=warmaster&id=page-id&requester=name
GET /archive/memory/events?namespace=warmaster&limit=20&component=memory_gateway&requester=warmaster
```

Write endpoint:

```text
POST /archive/memory/propose-change
```

`propose-change` accepts a requested `target` of `auto`, `focus`, `wiki`,
`vector`, or `graph`, clamps `importance` to 1-5, trims oversized proposal
payloads, archives the proposal as a normal turn, and then lets the Librarian
decide what should actually change. It does not grant direct file write access
to the requester.

Read-only gateway operations are audited as `memory_gateway` events. This makes
worker memory browsing visible without turning every read into a full archive
turn. Event reads can be filtered by `component`, `event_action`, and
`requester`.

Unified memory search returns separate `focus`, `wiki`, `vector`, and `graph`
sections plus a `counts` summary. Workers should use `counts` to decide whether
the gateway found enough relevant memory before reading full focus/wiki books.
Search defaults to compact snippets; pass `include_content=1` only after the
compact result looks relevant and raw vector chunk text is actually needed.
Use `layers=focus,wiki,vector,graph` to restrict scope when a lower layer would
add noise.

Unknown namespaces are rejected on read endpoints unless `create=1` is passed
explicitly. Chat/proposal writes can still create namespace memory through the
normal librarian path.

Recommended namespaces:

- `default`: ordinary chat memory.
- `telegram`: Telegram bot memory.
- `warmaster`: Warmaster orchestration and brigade memory.
- `mobile`: mobile client memory.
- `demonsforge`: DemonsForge long-term forge memory. DemonsForge SQLite remains
  a runtime/job/gallery store; durable forge facts should enter ArchiveOfHeresy
  through `/archive/memory/propose-change` only.

Worker tools over this gateway:

```text
archive_memory_gateway
archive_memory_catalog
archive_memory_search
archive_memory_read
archive_memory_propose
archive_memory_events
```

The worker tools are fail-soft: HTTP 400/404/503 responses become tool results
with `ok=false` instead of crashing the worker loop.

## Prompt Injection Policy

The model receives:

- the ArchiveOfHeresy system prompt;
- the active focus for the namespace;
- optional Magos memory context.

Direct vector and graph injection is disabled by default with
`ARCHIVE_VECTOR_INJECTION_ENABLED=0` and `ARCHIVE_GRAPH_INJECTION_ENABLED=0`.
Magos lower-layer context injection is also opt-in. Set
`ARCHIVE_MAGOS_CONTEXT_LAYERS=wiki,vector,graph` or a narrower comma list when
pre-answer lower-layer context should be eligible for the model prompt.
The current Magos lower-layer list is exposed as `magos_context_layers` in
`/health` and `/archive/memory/gateway`.

Strict stateless chat mode is enabled by setting `ARCHIVE_CHAT_CONTEXT_MESSAGES=0`.
In this mode mobile chat stores raw messages for archive/history display but
does not feed previous raw chat messages into the next model request. Continuity
must come from active focus and Magos-selected memory.

## Diagnostics

Use:

```bash
./check-memory.sh warmaster "memory query"
./check-memory-gateway.sh warmaster "memory gateway"
./check-namespace-smoke.py
```

The memory event API is:

```text
GET /archive/memory/events?namespace=warmaster&limit=20&component=memory_gateway&requester=warmaster
```
