# ArchiveOfHeresy

ArchiveOfHeresy is the required gateway between user-facing clients and the local model.

Default flow:

```text
Telegram bot -> ArchiveOfHeresy main -> CoreOfMadness LLM host -> ArchiveOfHeresy main -> Telegram bot
```

The gateway is where memory preparation, prompt preparation, and model-dialog archiving should be added.

ArchiveOfHeresy supports both regular and streaming OpenAI-compatible chat completions. For streaming requests, it forwards SSE chunks to the caller while collecting the final assistant text for JSONL and SQLite archives.

## Archives

ArchiveOfHeresy writes every chat-completion turn into two parallel archives.

JSONL is the chronological full-dialog archive. It is split by local date:

```text
archive/jsonl/YYYY/MM/YYYY-MM-DD.jsonl
```

Each line contains the request messages, ArchiveOfHeresy-prepared messages, model response, and metadata for one turn. A new day creates a new file. A new month or year creates a new folder.

SQLite is the working archive:

```text
archive/sqlite/archive.sqlite3
```

It stores conversations, turns, and messages for later memory lookup and prompt assembly.

## Librarian

The librarian agent runs inside ArchiveOfHeresy on the same local model. It starts after the model response has been sent back to the caller, while the user is reading the answer.

Agent module:

```text
archivist_agent/
```

The librarian is isolated from the public assistant persona. It does not use Shushunya's character prompt, does not inherit user-facing style, and has its own strict archival prompts.

## Magos

Magos is the pre-answer memory retrieval agent. It runs before ArchiveOfHeresy builds the model prompt.

Magos has one public job:

```text
current request -> inspect memory -> select/create focus -> return compact memory context
```

It first reviews existing focus files. If a paused or active focus matches the request, Magos activates that focus before prompt assembly. If the topic is new or should be fully refreshed, Magos creates a new empty focus file marked with:

```text
created_by: magos
```

The librarian sees those files after the answer and fills them with real post-answer context. Normal focus limits still apply, so extra focus files are removed by the same importance/age policy.

Magos may consult focus, wiki, vector, and graph memory to build a small `Magos memory context` message. This keeps raw vector/graph injection disabled while still allowing one controlled retrieval agent to pull useful facts when needed.

Magos is fail-soft. If it fails, ArchiveOfHeresy continues the model request without Magos context. Magos decisions are logged to the Archive runtime log. If Magos created an empty focus and the main model request fails, ArchiveOfHeresy pauses that empty focus so it does not remain active without a real answer for the librarian to process.

Magos uses relevance thresholds for lower memory layers before showing them to the model-agent. Weak wiki, vector, or graph matches are discarded before the Magos prompt is assembled. This keeps noisy lower-layer memory from leaking into Shushunya's prompt.

The librarian is also physically cut off from memory contents at the model level. Focus memory is exposed to it as books on a controlled bookshelf. The model only sees a catalog by default; when it needs book contents, it must request a tool such as `read_active_focus`, receive a tool result, and then finish with a structured action.

Chat requests are queued with a single in-process lock:

```text
request -> model response -> archive -> caller receives answer -> librarian updates focus -> next request may reach model
```

The librarian owns the active memory-maintenance cycle for the four managed memory layers:

- `focus` - current-topic compact context
- `wiki` - durable sorted knowledge
- `vector` - similarity retrieval over archived chunks
- `graph` - GraphRAG entities and relations

The focus librarian cycle is:

```text
catalog -> tool request -> tool result -> finish action -> bookshelf writes files
```

The librarian keeps compact focus files for current topics:

```text
focus/index.json
focus/files/*.md
```

When the topic continues, it updates the active focus file. When the topic changes, it marks the previous focus as `paused` and creates a new active focus file. Each focus has importance from `1` to `5`. ArchiveOfHeresy keeps at most 10 focus files, removing the least important files first and then the oldest files when importance is equal.

ArchiveOfHeresy injects the active focus file into model requests as compact context. Clients should not send long tails of previous chat messages; the active focus file replaces that history pressure.
The archivist is instructed to keep every important decision, constraint, correction, status, path, command, and next step in that focus, so old chat messages should not be needed for normal continuation.

The active focus is currently global for the allowed ArchiveOfHeresy conversation flow. Non-allowlisted clients should disable focus injection so they do not read or affect this shared memory.

## Wiki Memory

Wiki memory is the next long-term memory layer managed by the same isolated librarian. It is stored as a second bookshelf:

```text
wiki/index.json
wiki/state.json
wiki/pages/*.md
```

Focus memory is optimized for the current topic. Wiki memory is optimized for durable, sorted knowledge: project architecture, active decisions, superseded decisions, user preferences, stable facts, statuses, open questions, and next steps.

The librarian updates wiki memory after every `ARCHIVE_WIKI_INTERVAL_MESSAGES` archived messages. The default interval is `20`, which usually means 10 user/assistant turns. During a wiki pass the librarian receives a catalog of wiki pages and the recent archived turns since the last sync. It may request existing wiki pages through a controlled `read_wiki_page` tool before finishing with page updates.

New decisions should replace or supersede old decisions instead of being appended as unresolved contradictions.

## Vector Memory

Vector memory is the retrieval layer for old archived turns:

```text
vector/index.sqlite3
```

After a successful archived answer, the librarian indexes the latest user message and assistant answer as chunks. The local starter enables direct vector context injection with `ARCHIVE_VECTOR_INJECTION_ENABLED=1`, so this layer is currently active in model prompts while the memory is still small.

This first version intentionally has no external dependency and no network dependency. It uses stable hashed token and character n-gram sparse vectors stored in SQLite. The retrieval interface can later be swapped to real embedding vectors without changing the gateway flow.

On startup, ArchiveOfHeresy incrementally backfills vector memory from the existing working SQLite archive by default, so old archived turns become searchable too without reprocessing turns already indexed with the current embedding version.

Vector memory follows the same allowlist behavior as focus memory: when a client disables focus injection, vector retrieval is disabled by default too. A request can explicitly send `vector_enabled: false` to disable vector retrieval for that turn.

Manual search check:

```bash
curl 'http://127.0.0.1:8090/archive/vector/search?q=memory'
```

## GraphRAG Memory

GraphRAG memory stores entities and relations extracted by the librarian:

```text
graph/graph.sqlite3
```

The graph layer is for relationships that are awkward to represent as raw chunks or isolated wiki pages: project components, agents, memory layers, decisions, dependencies, superseded decisions, ownership, storage, retrieval, and status links.

After every `ARCHIVE_GRAPH_INTERVAL_MESSAGES` archived messages, the librarian reviews recent turns, extracts stable nodes and edges, and merges them into the graph. The local starter enables direct graph context injection with `ARCHIVE_GRAPH_INJECTION_ENABLED=1`, so this layer is currently active in model prompts while the memory is still small.

On startup, if the graph is empty, ArchiveOfHeresy asks the librarian to seed it from the latest archived turns by default.

Manual graph search check:

```bash
curl 'http://127.0.0.1:8090/archive/graph/search?q=ArchiveOfHeresy'
```

Clients may disable archiving and focus injection per request with internal flags:

```json
{
  "archive_enabled": false,
  "focus_enabled": false,
  "vector_enabled": false,
  "graph_enabled": false
}
```

These flags are consumed by ArchiveOfHeresy and are not forwarded to the model host.

## Local Environment

The Python environment for this module is stored inside the module itself:

```text
ArchiveOfHeresy/ArchiveOfHeresy/
```

Create it with:

```bash
python3 -m venv ArchiveOfHeresy/ArchiveOfHeresy
```

## Run

Start the local model host first:

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/CoreOfMadness
./llm-host/scripts/start-host.sh
```

Then start the archive gateway:

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/ArchiveOfHeresy
./start-main.sh
```

Check the gateway:

```bash
./check-main.sh
```

Stop it:

```bash
./stop-main.sh
```

## Settings

- `ARCHIVE_HOST` - default `127.0.0.1`
- `ARCHIVE_PORT` - default `8090`
- `ARCHIVE_API_KEY` - optional Bearer API key required for `/v1/*` and `/archive/*` endpoints when set
- `ARCHIVE_LLM_BASE_URL` - default `http://127.0.0.1:8080`
- `ARCHIVE_SYSTEM_PROMPT` - archive-level system prompt prepended to chat requests; default personality is Shushunya, a sarcastic daemon of Tzeentch
- `ARCHIVE_JSONL_ROOT` - default `ArchiveOfHeresy/archive/jsonl`
- `ARCHIVE_SQLITE_PATH` - default `ArchiveOfHeresy/archive/sqlite/archive.sqlite3`
- `ARCHIVE_FOCUS_ROOT` - default `ArchiveOfHeresy/focus`
- `ARCHIVE_WIKI_ROOT` - default `ArchiveOfHeresy/wiki`
- `ARCHIVE_VECTOR_ROOT` - default `ArchiveOfHeresy/vector`
- `ARCHIVE_GRAPH_ROOT` - default `ArchiveOfHeresy/graph`
- `ARCHIVE_FOCUS_CONTEXT_CHARS` - default `6000`
- `ARCHIVE_VECTOR_CONTEXT_CHARS` - default `5000`
- `ARCHIVE_GRAPH_CONTEXT_CHARS` - default `5000`
- `ARCHIVE_VECTOR_INJECTION_ENABLED` - default `0`
- `ARCHIVE_GRAPH_INJECTION_ENABLED` - default `0`
- `ARCHIVE_FOCUS_MAX_FILES` - default `10`
- `ARCHIVE_VECTOR_DIMENSIONS` - default `384`
- `ARCHIVE_VECTOR_EMBEDDING_VERSION` - default `hashed-token-chargram-v2`
- `ARCHIVE_VECTOR_CHUNK_CHARS` - default `1200`
- `ARCHIVE_VECTOR_TOP_K` - default `5`
- `ARCHIVE_VECTOR_MIN_SCORE` - default `0.18`
- `ARCHIVE_VECTOR_BACKFILL_ON_START` - default `1`
- `ARCHIVE_GRAPH_INTERVAL_MESSAGES` - default `20`
- `ARCHIVE_GRAPH_MAX_RECENT_TURNS` - default `12`
- `ARCHIVE_GRAPH_TOP_K` - default `5`
- `ARCHIVE_GRAPH_BACKFILL_ON_START` - default `1`
- `ARCHIVE_GRAPH_SYSTEM_PROMPT` - isolated GraphRAG system prompt
- `ARCHIVE_GRAPH_TASK_PROMPT` - isolated GraphRAG extraction prompt
- `ARCHIVE_MAGOS_ENABLED` - default `1`
- `ARCHIVE_MAGOS_MODEL` - default `gemma-4-12b-it-UD-Q5_K_XL.gguf`
- `ARCHIVE_MAGOS_CONTEXT_CHARS` - default `6000`
- `ARCHIVE_MAGOS_CONTEXT_LAYERS` - default empty; comma list of `wiki`, `vector`, `graph` allowed for Magos pre-answer context injection
- `ARCHIVE_MAGOS_MIN_WIKI_SCORE` - default `0.35`
- `ARCHIVE_MAGOS_MIN_VECTOR_SCORE` - default `0.32`
- `ARCHIVE_MAGOS_MIN_GRAPH_SCORE` - default `0.12`
- `ARCHIVE_MAGOS_SYSTEM_PROMPT` - isolated Magos system prompt
- `ARCHIVE_MAGOS_TASK_PROMPT` - isolated Magos task prompt
- `ARCHIVE_WIKI_INTERVAL_MESSAGES` - default `20`
- `ARCHIVE_WIKI_MAX_RECENT_TURNS` - default `12`
- `ARCHIVE_LIBRARIAN_MODEL` - default `gemma-4-12b-it-UD-Q5_K_XL.gguf`
- `ARCHIVE_LIBRARIAN_MAX_AGENT_STEPS` - default `4`
- `ARCHIVE_LIBRARIAN_SYSTEM_PROMPT` - isolated librarian system prompt
- `ARCHIVE_LIBRARIAN_TASK_PROMPT` - isolated librarian task prompt
- `ARCHIVE_LIBRARIAN_WIKI_TASK_PROMPT` - isolated wiki-memory task prompt
- `ARCHIVE_MEMORY_QUALITY_REPORT_ENABLED` - default `1`
- `ARCHIVE_MEMORY_QUALITY_REPORT_HOUR` - default `4`
- `ARCHIVE_REPORTS_ROOT` - default `ArchiveOfHeresy/reports`

`start-main.sh` currently activates memory aggressively for the local daemon:
`ARCHIVE_MAGOS_CONTEXT_LAYERS=wiki,vector,graph`,
`ARCHIVE_VECTOR_INJECTION_ENABLED=1`, `ARCHIVE_GRAPH_INJECTION_ENABLED=1`,
`ARCHIVE_MAGOS_ENABLED=1`, `ARCHIVE_VECTOR_BACKFILL_ON_START=1`, and
`ARCHIVE_GRAPH_BACKFILL_ON_START=1`. It also enables the daily memory quality
report at 04:00 with `ARCHIVE_MEMORY_QUALITY_REPORT_ENABLED=1` and
`ARCHIVE_MEMORY_QUALITY_REPORT_HOUR=4`, unless `.env` overrides them.
