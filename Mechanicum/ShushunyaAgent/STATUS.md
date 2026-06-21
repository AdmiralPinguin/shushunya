# ShushunyaAgent Status

Last verified: 2026-06-21 18:08 KST.

## Running Services

- LLM host: `http://127.0.0.1:8080`
- ArchiveOfHeresy: `http://127.0.0.1:8090`
- ShushunyaAgent API: `http://127.0.0.1:8095`
- SearXNG: `http://127.0.0.1:8888`
- Agent tunnel can be stopped with `scripts/stop-agent-tunnel.sh`.

## Verified

- Archive health is `ok`.
- Agent API health is `ok`.
- `scripts/check-agent.sh` verifies local SearXNG in addition to LLM, Archive,
  Agent API, and self-test.
- `scripts/start-agent-tunnel.sh` fails fast if the configured `cloudflared`
  executable is missing.
- Agent API `/health` is minimal by default; full Archive details through
  `/health?detail=1` require a configured API key and bearer auth.
- Archive health reports `default` and `agent` memory namespaces.
- Agent namespace memory smoke run completed and wrote focus/vector/event data.
- Sandbox hides `/media` and `/root`.
- Sandbox network is blocked by default.
- Structured file tools work.
- `file_info` can return bounded SHA-256 metadata for file identity checks.
- `read_file` reports binary-slice metadata with `is_binary` and `encoding`.
- `replace_in_file` works.
- Python tool works inside sandbox.
- `shell_enabled=false` blocks shell execution.
- HTTP shell is locked by default: payload `shell_enabled=true` is ignored unless
  API key auth or an explicit env override allows it.
- `/run` returns JSON trace and omits stderr unless requested.
- `/run` is serialized by process-local and file locks.
- Local web search source is `searxng` when SearXNG is running.
- Default search providers are `searxng,marginalia,wikipedia,brave`.
- Brave is an optional fallback only and is skipped unless `brave` is present in
  `SHUSHUNYA_AGENT_SEARCH_PROVIDERS`.
- Archive Memory Gateway tools are available: `archive_memory_gateway`,
  `archive_memory_catalog`, `archive_memory_search`, `archive_memory_read`,
  `archive_memory_propose`, and filtered `archive_memory_events`.
- Archive memory tools are fail-soft: HTTP 400/404 responses become tool results
  with `ok=false`.
- Agent runs emit `task` stream events and write compact JSONL task journals in
  `runtime/task-journals/`.
- Stream `tool_result` and `final` events include `duration_sec`.
- `list_files` and `find_files` support `limit`/`offset` pagination for large
  directories.
- `search_text` reports scanned and per-file-truncated file counts.
- `replace_in_file` has a bounded file-size guard for small text edits.
- Agent runs have a total runtime limit through `max_runtime_sec` /
  `SHUSHUNYA_AGENT_MAX_RUNTIME_SEC`.
- `GET /task-journal?task_id=...` can inspect recent journal events, and
  `resume_task_id` can feed recent journal context into a follow-up run.
- Task journal retention keeps the newest configured JSONL journals.
- Task journal reads use a bounded tail instead of loading whole journal files.
- Individual task journals are capped by `SHUSHUNYA_AGENT_TASK_JOURNAL_MAX_BYTES`
  and restart with a `journal_rotated` marker when exceeded.
- HTTP task journal responses and `resume_task_id` require API-key bearer auth;
  journal responses omit host filesystem paths.
- Resume context is compacted before entering the model prompt.
- `GET /state` reports runner busy/queue/current/last task state.
- `GET /state` and `/health` include Agent API uptime.
- `wait_for_slot=false` can fail fast with `409 agent busy` instead of waiting
  behind another active or queued run.
- `POST /cancel` requests cooperative cancellation for the current task or a
  supplied `task_id`; the runner stops between agent steps and reports
  `cancelled=true`.
- `/run-stream` emits `heartbeat` events during long in-flight model/tool calls
  so mobile and tunnel clients can keep the connection observable.
- `web_fetch` detects binary responses and returns metadata without decoded
  binary text.
- `web_fetch` falls back to UTF-8 when a response declares an unknown charset.
- Web URL validation rejects credential-bearing URLs and SearXNG scheme
  mismatches.
- Invalid JSON request bodies return `400`; oversized request bodies return
  `413` before the agent loop starts.
- Transient model HTTP errors `429`, `502`, `503`, and `504` are retried before
  the agent step fails.
- Malformed model JSON gets a minimal repair pass with memory disabled before
  the runner spends another normal step.
- Context-size model `400` responses are retried with compacted messages and
  then with Archive memory injection disabled.
- Tool exceptions become fail-soft `ok=false` tool results instead of crashing
  the whole agent run.

## Local SearXNG

Setup and run:

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/Mechanicum/SearXNG
./scripts/setup-searxng.sh
./scripts/start-searxng.sh
./scripts/check-searxng.sh
```

Agent env:

```bash
SHUSHUNYA_AGENT_SEARXNG_URL=http://127.0.0.1:8888
SHUSHUNYA_AGENT_SEARCH_PROVIDERS=searxng,marginalia,wikipedia,brave
```

## Known Limits

- The `500G` limit is enforced by structured file tools as a soft policy.
- Shell and Python tools can only be hard-limited by enabling ext4 project quota.
- Hard quota helper is available as `scripts/setup-hard-quota.sh`, but applying
  it requires sudo and quota tools such as `xfs_quota`.
- The API is bound to localhost by default. If exposed beyond localhost, set
  `SHUSHUNYA_AGENT_API_KEY`.
- Graph/wiki long-term layers update by message interval, so a fresh namespace
  may show pending status before the first interval sync.
