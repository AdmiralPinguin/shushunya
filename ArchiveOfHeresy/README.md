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

Chat requests are queued with a single in-process lock:

```text
request -> model response -> archive -> caller receives answer -> librarian updates focus -> next request may reach model
```

The librarian keeps compact focus files for current topics:

```text
focus/index.json
focus/files/*.md
```

When the topic continues, it updates the active focus file. When the topic changes, it marks the previous focus as `paused` and creates a new active focus file. Each focus has importance from `1` to `5`. ArchiveOfHeresy keeps at most 10 focus files, removing the least important files first and then the oldest files when importance is equal.

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
- `ARCHIVE_SYSTEM_PROMPT` - archive-level system prompt prepended to chat requests
- `ARCHIVE_JSONL_ROOT` - default `ArchiveOfHeresy/archive/jsonl`
- `ARCHIVE_SQLITE_PATH` - default `ArchiveOfHeresy/archive/sqlite/archive.sqlite3`
- `ARCHIVE_FOCUS_ROOT` - default `ArchiveOfHeresy/focus`
- `ARCHIVE_FOCUS_MAX_FILES` - default `10`
- `ARCHIVE_LIBRARIAN_MODEL` - default `gemma-4-12b-it-UD-Q5_K_XL.gguf`
