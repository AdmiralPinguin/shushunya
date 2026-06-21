# ShushunyaAgent

Minimal tool-using agent runner for Shushunya.

The runner has no long-term memory. It talks to `ArchiveOfHeresy` for model
responses and memory context, then executes allowed actions through the isolated
sandbox on the `ARCHIVE` disk.

Default flow:

```text
task -> ArchiveOfHeresy(agent memory) -> JSON action -> sandbox executor -> tool result -> ArchiveOfHeresy(agent memory) -> final
```

By default every model step is archived through `ArchiveOfHeresy` with
`memory_namespace=agent`, `user=shushunya-agent`, automatic Magos retrieval
enabled, and Librarian post-processing enabled. The agent therefore has its own
focus bookshelf under the archive focus root with the same 10-file limit as the
normal chat. Wiki, vector, and GraphRAG memory are also scoped to that same
namespace, so agent tool loops remain long-term searchable by the agent without
leaking into the default chat memory context.

## Requirements

Start the model host and archive gateway first:

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/Mechanicum/ShushunyaAgent
./scripts/start-stack.sh
```

The sandbox launcher must exist:

```bash
shushunya-agent-shell /usr/bin/bash -lc 'pwd; touch /work/check && rm /work/check'
```

The runner itself uses the `shushunya-agent` group and the sandbox profile
directly by default, so it can run from the `codexbox` session without a sudo
password after group membership is configured.

## Run

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/Mechanicum/ShushunyaAgent
./scripts/run-agent.sh "создай файл hello.txt в песочнице"
```

Concise technical output:

```bash
./scripts/run-agent.sh --technical "создай файл hello.txt в песочнице"
```

Machine-readable result and trace:

```bash
./scripts/run-agent.sh --json --technical "создай файл hello.txt в песочнице"
```

HTTP API:

```bash
./scripts/start-agent-api.sh
./scripts/start-agent-tunnel.sh
./scripts/stop-agent-tunnel.sh
curl -sS http://127.0.0.1:8095/run \
  -H 'Content-Type: application/json' \
  -d '{"task":"создай /work/hello.txt с текстом hello","technical":true}'
```

Model replies default to `1024` tokens. Override per HTTP request with
`"max_tokens": 2048` when a longer action JSON or final answer is needed.
HTTP request bodies are capped by `SHUSHUNYA_AGENT_MAX_REQUEST_BYTES`, default
`1048576`, so broken clients fail with `413` before entering the agent loop.
The `task` text itself is capped by `SHUSHUNYA_AGENT_MAX_TASK_CHARS`, default
`50000`, to avoid oversized prompts before model context compaction can help.
Transient model HTTP errors `429`, `502`, `503`, and `504` are retried up to
`SHUSHUNYA_AGENT_LLM_RETRIES`, default `3`.
Total agent runtime is capped by `SHUSHUNYA_AGENT_MAX_RUNTIME_SEC`, default
`1800`, and can be overridden per HTTP request with `max_runtime_sec`.

Streaming HTTP API for Codex-style progress:

```bash
curl -N -sS http://127.0.0.1:8095/run-stream \
  -H 'Content-Type: application/json' \
  -d '{"task":"проверь sandbox_status","technical":true,"shell_enabled":false}'
```

`/run-stream` returns one NDJSON object per event: `start`, `task`, `step`,
`action`, `tool_result`, `warning`, `heartbeat`, `final`, and `done`. The
`task` event includes the stable `task_id` and `memory_namespace`.
`tool_result` and `final` events include `duration_sec` for operational
visibility. During long in-flight model or tool calls, `/run-stream` emits
`heartbeat` every `SHUSHUNYA_AGENT_STREAM_HEARTBEAT_SEC` seconds, default `15`,
so mobile/tunnel clients can see that the stream is still alive.

Runtime state:

```bash
curl -sS http://127.0.0.1:8095/state
```

`/state` reports whether the serialized runner is busy, queued request count,
current task id, last completed task id, git revision, API uptime, and the
request size limit.
`/health` is intentionally minimal by default and reports only the agent service
status, git revision, plus Archive status. Use `/health?detail=1` for the full
Archive health payload only when `SHUSHUNYA_AGENT_API_KEY` is configured and the
request includes the matching bearer token.

Cooperative cancellation:

```bash
curl -sS http://127.0.0.1:8095/cancel \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"agent-memory-check"}'
```

If `task_id` is omitted, `/cancel` targets the currently running task. The
runner checks cancellation between agent steps and returns a structured
`cancelled=true` final event; in-flight model or tool calls are allowed to
finish first.
Omitting `task_id` requires `SHUSHUNYA_AGENT_API_KEY` plus bearer auth; public
clients should cancel the explicit task id they received from `/run-stream`.

Every run writes a compact JSONL journal under `runtime/task-journals/`.
Pass a stable task id when a caller wants resumable task history:

```bash
curl -N -sS http://127.0.0.1:8095/run-stream \
  -H 'Content-Type: application/json' \
  -d '{"task":"проверь память агента","task_id":"agent-memory-check","technical":true}'
```

Inspect the journal:

```bash
curl -sS 'http://127.0.0.1:8095/task-journal?task_id=agent-memory-check&limit=80' \
  -H "Authorization: Bearer $SHUSHUNYA_AGENT_API_KEY"
```

`/task-journal` and `resume_task_id` require `SHUSHUNYA_AGENT_API_KEY` plus a
matching bearer token, because journal entries may contain task details.

Continue with recent journal context:

```bash
curl -N -sS http://127.0.0.1:8095/run-stream \
  -H 'Content-Type: application/json' \
  -d '{"task":"продолжи предыдущую задачу","resume_task_id":"agent-memory-check","technical":true}'
```

Resume context is compacted before it is appended to the prompt. Large journals
are read as bounded tails, so they cannot be loaded or replayed into the model
wholesale.
The HTTP journal response omits the host filesystem path.
Journal retention keeps the newest `SHUSHUNYA_AGENT_TASK_JOURNAL_MAX_FILES`
JSONL files, default `500`. A single journal is capped by
`SHUSHUNYA_AGENT_TASK_JOURNAL_MAX_BYTES`, default `10485760`; when exceeded,
the file starts over with a `journal_rotated` marker.

Optional API key:

```bash
SHUSHUNYA_AGENT_API_KEY='change-me' ./scripts/start-agent-api.sh
curl -sS http://127.0.0.1:8095/run \
  -H 'Authorization: Bearer change-me' \
  -H 'Content-Type: application/json' \
  -d '{"task":"проверь sandbox_status","technical":true}'
```

`POST /run` is serialized with both a process-local lock and a runtime file lock
so concurrent callers do not mutate the same sandbox at the same time.
Set `"wait_for_slot": false` on `/run` or `/run-stream` when a caller wants an
immediate `409 agent busy` response instead of waiting for the serialized runner.
Waiting callers are bounded by `SHUSHUNYA_AGENT_MAX_QUEUE`, default `3`; once
full, new runs fail with `429 agent queue full`. `/run-stream` returns that
`429` before opening an NDJSON stream, so clients can handle queue overflow as a
normal HTTP error.
HTTP requests cannot enable the shell tool unless `SHUSHUNYA_AGENT_API_KEY` is
configured or `SHUSHUNYA_AGENT_HTTP_ALLOW_SHELL_WITHOUT_API_KEY=1` is set.
The phone client sends `shell_enabled=false`.

Add `"include_stderr": true` to a `/run` payload when debugging the internal
step log.

Set `"include_steps": false` when a caller only needs the final message and exit
code.

Set `"shell_enabled": false` for mobile or public clients; the agent can still
use structured file/search tools, Python, sandbox status, archive tools, and
supervised public web tools.

## Local Search

Local SearXNG is the primary search provider. It runs on localhost:

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/Mechanicum/SearXNG
./scripts/setup-searxng.sh
./scripts/start-searxng.sh
./scripts/check-searxng.sh
```

Default endpoint:

```bash
export SHUSHUNYA_AGENT_SEARXNG_URL=http://127.0.0.1:8888
export SHUSHUNYA_AGENT_SEARCH_PROVIDERS=searxng,marginalia,wikipedia,brave
```

`scripts/start-agent-api.sh` sets `SHUSHUNYA_AGENT_SEARXNG_URL` to
`http://127.0.0.1:8888` when it is not already set. To confirm the active
provider, run a web search and inspect the returned `source`; it should be
`searxng` while local SearXNG is running.

For an interactive prompt:

```bash
./scripts/run-agent.sh
```

CLI runs also accept `--max-tokens`, `--max-runtime-sec`, and `--llm-retries`,
matching the HTTP `max_tokens`, `max_runtime_sec`, and `llm_retries` fields.

## Safety Boundaries

- Shell commands run only through the sandbox profile on the `ARCHIVE` disk.
- The sandbox hides host `/media`, `/home`, `/root`, and the project disk.
- Network is disabled inside the sandbox by default.
- The runner rejects non-JSON model replies and asks the model to repair them.
- If a model step returns malformed JSON, the runner first attempts a minimal
  JSON repair pass with memory disabled before spending another normal step.
- If Archive returns a context-size `400`, the runner retries with smaller model
  context budgets and then retries with Archive memory injection disabled.
- Tool exceptions are converted into `ok=false` tool results, so the agent can
  recover or explain the failure instead of crashing the whole run.
- Long-term memory stays in `ArchiveOfHeresy`; this runner stores no persistent
  memory of its own.
- Task journals are operational traces, not long-term memory. They are used for
  resume/debugging and can be inspected with `GET /task-journal`.
- `GET /state` exposes process-local runner state for UI/ops checks without
  starting a model request.
- `POST /cancel` requests cooperative cancellation for the current task or a
  supplied `task_id`.
- `/run-stream` emits `heartbeat` events during long in-flight model/tool calls.
- Bad or non-object JSON request bodies return `400`; oversized bodies return
  `413`.
- Internal agent steps are archived by default and receive automatic
  ArchiveOfHeresy memory handling in the isolated `agent` memory namespace.
  Tool results are included in the following model step, so Magos and the
  Librarian can account for the whole agent loop. The agent can also request
  memory explicitly with the `archive_memory_gateway`, `archive_memory_catalog`,
  `archive_memory_search`, `archive_memory_read`, `archive_memory_events`, and
  legacy `archive_search` actions.
- Agent memory reads go through the ArchiveOfHeresy Memory Gateway instead of
  direct file access. The agent can request changes only with
  `archive_memory_propose`; the Librarian decides what to apply.
- Gateway search is compact by default and only returns raw vector chunk content
  when the agent explicitly sets `include_content`. The agent can also restrict
  search to selected layers with `layers`, for example `focus,wiki`.
- Structured file writes enforce the configured `500G` soft limit. Shell and
  Python tools still require hard filesystem quota for kernel-level enforcement.
- `read_file` reads bounded slices with `max_bytes` and `offset`; it no longer
  loads the whole file before truncating.
- `file_info` can return bounded SHA-256 metadata with `sha256=true`, so the
  agent can verify file identity without reading content into context.
- `list_files` and `find_files` support `limit`/`offset` pagination and return
  `total_count` plus `next_offset` for large directories.
- `search_text` reports `scanned_files` and `truncated_files`, so the agent can
  tell when a search may need a larger `max_bytes_per_file`.
- `replace_in_file` has a bounded `max_file_bytes` guard and is intended for
  small text files.
- Arbitrary shell can be disabled with `SHUSHUNYA_AGENT_SHELL_ENABLED=0` or
  `"shell_enabled": false` in the HTTP API payload.
- Web browsing is exposed as `web_search` and `web_fetch`. The supervisor blocks
  localhost, private, loopback, link-local, multicast, reserved, and unspecified
  IP targets, URL credentials, and unsafe redirects.
- `web_search` uses `SHUSHUNYA_AGENT_SEARCH_PROVIDERS`, defaulting to
  `searxng,marginalia,wikipedia,brave`. Brave Search API is only an optional
  fallback and is not called unless `brave` is present in that provider list,
  even when `SHUSHUNYA_AGENT_BRAVE_SEARCH_API_KEY` is set.
- `web_fetch` always uses strict public URL validation and cannot fetch
  localhost, private, loopback, link-local, multicast, reserved, or unspecified
  addresses or credential-bearing URLs. The local SearXNG localhost exception
  applies only inside the configured `web_search_searxng` provider and only for
  the configured scheme, host, and port.
- `web_fetch` keeps binary responses out of model context and returns
  `is_binary=true` with byte metadata instead of decoded garbage.

Disable automatic memory injection only for debugging:

```bash
SHUSHUNYA_AGENT_INJECT_MEMORY=0 ./scripts/run-agent.sh "задача"
```

Disable archiving internal steps only for debugging:

```bash
SHUSHUNYA_AGENT_ARCHIVE_INTERNAL_STEPS=0 ./scripts/run-agent.sh "задача"
```

Use another memory namespace for a separate agent memory shelf:

```bash
./scripts/run-agent.sh --memory-namespace agent-lab "задача"
```

The same can be set with `SHUSHUNYA_AGENT_MEMORY_NAMESPACE=agent-lab`.

Run the local self-test:

```bash
./scripts/self-test.sh
```

Run the self-test without touching ArchiveOfHeresy:

```bash
SHUSHUNYA_AGENT_SELF_TEST_OFFLINE=1 ./scripts/self-test.sh
```

Check the whole stack:

```bash
./scripts/check-agent.sh
```

The stack check verifies LLM host, ArchiveOfHeresy, local SearXNG, Agent API,
and the agent self-test.

When ArchiveOfHeresy is being changed, use the offline check instead:

```bash
./scripts/check-agent-offline.sh
```

The offline check uses `/state` instead of `/health`, skips Archive integration
tests, and still verifies sandbox, file tools, web URL guards, queue guards, and
model JSON handling. To start the API without using Archive-backed `/health` as
the readiness probe, set:

```bash
SHUSHUNYA_AGENT_START_CHECK_PATH=/state ./scripts/start-agent-api.sh
```

Run a lightweight API watchdog that does not touch ArchiveOfHeresy:

```bash
./scripts/watch-agent-api.sh
```

The watchdog checks `/state` every `SHUSHUNYA_AGENT_WATCH_INTERVAL_SEC`
seconds, default `15`. After `SHUSHUNYA_AGENT_WATCH_MAX_FAILURES` failed checks,
default `2`, it restarts the API and uses `/state` as the readiness probe.

Stop the model host and archive gateway:

```bash
./scripts/stop-stack.sh
```

Prepare hard project quota commands for the `ARCHIVE` sandbox:

```bash
./scripts/setup-hard-quota.sh
```

Apply hard quota only after reviewing the dry run:

```bash
sudo CONFIRM=1 ./scripts/setup-hard-quota.sh
```

## Tools

The supported action contract is documented in `TOOLS.md` and
`tool_schema.json`.

Current verified runtime state is documented in `STATUS.md`.
