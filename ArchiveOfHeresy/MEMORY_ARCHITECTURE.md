# ArchiveOfHeresy Memory Architecture

ArchiveOfHeresy is the mandatory gateway between clients and the model. Clients
should not send long chat tails directly to the model. The archive prepares
compact memory context before the answer and maintains memory after the answer.

## Namespaces

Memory is scoped with `memory_namespace`.

- `default` is the normal chat namespace.
- `agent` is the ShushunyaAgent namespace.

Focus, wiki, vector retrieval, graph memory, and memory maintenance events are
namespace-aware. This keeps agent tool loops searchable by the agent without
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
- Agent mode: `focus/namespaces/agent/files/*.md`.
- Each namespace keeps at most 10 focus files.
- Removal prefers the least important files, then the oldest files.

Magos selects or creates the active focus before the model answer. The Librarian
fills or updates the focus after the answer.

## Layer 2: Wiki

Wiki memory stores durable sorted knowledge: decisions, stable project facts,
preferences, statuses, open questions, and superseded decisions.

- Default chat: `wiki/pages/*.md`.
- Agent mode: `wiki/namespaces/agent/pages/*.md`.

Wiki updates run after `ARCHIVE_WIKI_INTERVAL_MESSAGES` archived messages within
the namespace.

## Layer 3: Vector

Vector memory stores hashed sparse embeddings for archived user and assistant
chunks in `vector/index.sqlite3`.

Each chunk carries `memory_namespace`; searches from Magos and explicit agent
tools filter by namespace. This keeps old agent tool output out of default chat
retrieval.

## Layer 4: GraphRAG

Graph memory stores stable entities and relations.

- Default chat: `graph/graph.sqlite3`.
- Agent mode: `graph/namespaces/agent/graph.sqlite3`.

Graph updates run after `ARCHIVE_GRAPH_INTERVAL_MESSAGES` archived messages
within the namespace.

## Agents

Magos runs before the main model request. It:

- chooses an existing focus or opens a new empty focus;
- gathers compact context from focus, wiki, vector, and graph;
- fails soft, so the main model request can continue without it.

The Librarian runs after successful model answers. It:

- indexes vector chunks;
- updates focus;
- periodically updates wiki;
- periodically updates graph;
- writes memory maintenance events under `archive/memory_events`.

## Memory Gateway

ArchiveOfHeresy exposes a controlled Memory Gateway on the archive HTTP port.
Agents should use this port instead of reading or writing memory files.

Read endpoints:

```text
GET /archive/memory/gateway
GET /archive/memory/catalog?namespace=agent&requester=name
GET /archive/memory/search?namespace=agent&q=query&limit=5&requester=name
GET /archive/memory/focus?namespace=agent&id=active&requester=name
GET /archive/memory/wiki?namespace=agent&id=page-id&requester=name
GET /archive/memory/events?namespace=agent&limit=20&component=librarian
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
agent memory browsing visible without turning every read into a full archive
turn. Event reads can be filtered by `component` and `event_action`.

Unknown namespaces are rejected on read endpoints unless `create=1` is passed
explicitly. Chat/proposal writes can still create namespace memory through the
normal librarian path.

ShushunyaAgent tools over this gateway:

```text
archive_memory_catalog
archive_memory_search
archive_memory_read
archive_memory_propose
archive_memory_events
```

The agent tools are fail-soft: HTTP 400/404/503 responses become tool results
with `ok=false` instead of crashing the agent loop.

## Prompt Injection Policy

The model receives:

- the ArchiveOfHeresy system prompt;
- the active focus for the namespace;
- optional Magos memory context.

Direct vector and graph injection is disabled by default with
`ARCHIVE_VECTOR_INJECTION_ENABLED=0` and `ARCHIVE_GRAPH_INJECTION_ENABLED=0`.
Lower layers are used through Magos unless those flags are deliberately enabled.

## Diagnostics

Use:

```bash
./check-memory.sh agent "memory query"
./check-memory-gateway.sh agent "memory gateway"
./check-namespace-smoke.py
```

The memory event API is:

```text
GET /archive/memory/events?namespace=agent&limit=20&component=librarian
```
