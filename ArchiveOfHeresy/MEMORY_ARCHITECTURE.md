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
./check-namespace-smoke.py
```

The memory event API is:

```text
GET /archive/memory/events?namespace=agent&limit=20
```
