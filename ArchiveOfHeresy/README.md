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

The librarian is also physically cut off from memory contents at the model level. Focus memory is exposed to it as books on a controlled bookshelf. The model only sees a catalog by default; when it needs book contents, it must request a tool such as `read_active_focus`, receive a tool result, and then finish with a structured action.

Chat requests are queued with a single in-process lock:

```text
request -> model response -> archive -> caller receives answer -> librarian updates focus -> next request may reach model
```

The librarian cycle is:

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

New decisions should replace or supersede old decisions instead of being appended as unresolved contradictions. The wiki prompt also tells the librarian not to preserve actionable harmful instructions; unsafe topics may be retained only as high-level safety context without operational detail.

Clients may disable archiving and focus injection per request with internal flags:

```json
{
  "archive_enabled": false,
  "focus_enabled": false
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
- `ARCHIVE_LLM_BASE_URL` - default `http://127.0.0.1:8080`
- `ARCHIVE_SYSTEM_PROMPT` - archive-level system prompt prepended to chat requests; default personality is Shushunya, a sarcastic daemon of Tzeentch
- `ARCHIVE_JSONL_ROOT` - default `ArchiveOfHeresy/archive/jsonl`
- `ARCHIVE_SQLITE_PATH` - default `ArchiveOfHeresy/archive/sqlite/archive.sqlite3`
- `ARCHIVE_FOCUS_ROOT` - default `ArchiveOfHeresy/focus`
- `ARCHIVE_WIKI_ROOT` - default `ArchiveOfHeresy/wiki`
- `ARCHIVE_FOCUS_CONTEXT_CHARS` - default `6000`
- `ARCHIVE_FOCUS_MAX_FILES` - default `10`
- `ARCHIVE_WIKI_INTERVAL_MESSAGES` - default `20`
- `ARCHIVE_WIKI_MAX_RECENT_TURNS` - default `12`
- `ARCHIVE_LIBRARIAN_MODEL` - default `gemma-4-12b-it-UD-Q5_K_XL.gguf`
- `ARCHIVE_LIBRARIAN_MAX_AGENT_STEPS` - default `4`
- `ARCHIVE_LIBRARIAN_SYSTEM_PROMPT` - isolated librarian system prompt
- `ARCHIVE_LIBRARIAN_TASK_PROMPT` - isolated librarian task prompt
- `ARCHIVE_LIBRARIAN_WIKI_TASK_PROMPT` - isolated wiki-memory task prompt
