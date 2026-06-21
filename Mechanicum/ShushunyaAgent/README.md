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
curl -sS http://127.0.0.1:8095/run \
  -H 'Content-Type: application/json' \
  -d '{"task":"создай /work/hello.txt с текстом hello","technical":true}'
```

Model replies default to `1024` tokens. Override per HTTP request with
`"max_tokens": 2048` when a longer action JSON or final answer is needed.

Streaming HTTP API for Codex-style progress:

```bash
curl -N -sS http://127.0.0.1:8095/run-stream \
  -H 'Content-Type: application/json' \
  -d '{"task":"проверь sandbox_status","technical":true,"shell_enabled":false}'
```

`/run-stream` returns one NDJSON object per event: `start`, `step`, `action`,
`tool_result`, `warning`, `final`, and `done`.

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

## Safety Boundaries

- Shell commands run only through the sandbox profile on the `ARCHIVE` disk.
- The sandbox hides host `/media`, `/home`, `/root`, and the project disk.
- Network is disabled inside the sandbox by default.
- The runner rejects non-JSON model replies and asks the model to repair them.
- Long-term memory stays in `ArchiveOfHeresy`; this runner stores no persistent
  memory of its own.
- Internal agent steps are archived by default and receive automatic
  ArchiveOfHeresy memory handling in the isolated `agent` memory namespace.
  Tool results are included in the following model step, so Magos and the
  Librarian can account for the whole agent loop. The agent can also request
  memory explicitly with the `archive_search` action.
- Structured file writes enforce the configured `500G` soft limit. Shell and
  Python tools still require hard filesystem quota for kernel-level enforcement.
- `read_file` reads bounded slices with `max_bytes` and `offset`; it no longer
  loads the whole file before truncating.
- Arbitrary shell can be disabled with `SHUSHUNYA_AGENT_SHELL_ENABLED=0` or
  `"shell_enabled": false` in the HTTP API payload.
- Web browsing is exposed as `web_search` and `web_fetch`. The supervisor blocks
  localhost, private, loopback, link-local, multicast, reserved, and unspecified
  IP targets, including redirects.
- `web_search` uses `SHUSHUNYA_AGENT_SEARCH_PROVIDERS`, defaulting to
  `searxng,marginalia,wikipedia,brave`. Brave Search API is only an optional
  fallback and is not called unless `brave` is present in that provider list,
  even when `SHUSHUNYA_AGENT_BRAVE_SEARCH_API_KEY` is set.
- `web_fetch` always uses strict public URL validation and cannot fetch
  localhost, private, loopback, link-local, multicast, reserved, or unspecified
  addresses. The local SearXNG localhost exception applies only inside the
  configured `web_search_searxng` provider.

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
SHUSHUNYA_AGENT_MEMORY_NAMESPACE=agent-lab ./scripts/run-agent.sh "задача"
```

Run the local self-test:

```bash
./scripts/self-test.sh
```

Check the whole stack:

```bash
./scripts/check-agent.sh
```

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
