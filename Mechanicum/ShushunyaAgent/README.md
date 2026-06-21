# ShushunyaAgent

Minimal tool-using agent runner for Shushunya.

The runner has no long-term memory. It talks to `ArchiveOfHeresy` for model
responses and memory context, then executes allowed actions through the isolated
sandbox on the `ARCHIVE` disk.

Default flow:

```text
task -> ArchiveOfHeresy -> JSON action -> sandbox executor -> tool result -> ArchiveOfHeresy -> final
```

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
- Internal agent steps are not archived by default and do not receive automatic
  focus/vector/graph injection. The agent can request memory explicitly with the
  `archive_search` action.
- Structured file writes enforce the configured `500G` soft limit. Shell and
  Python tools still require hard filesystem quota for kernel-level enforcement.
- `read_file` reads bounded slices with `max_bytes` and `offset`; it no longer
  loads the whole file before truncating.
- Arbitrary shell can be disabled with `SHUSHUNYA_AGENT_SHELL_ENABLED=0` or
  `"shell_enabled": false` in the HTTP API payload.
- Web browsing is exposed as `web_search` and `web_fetch`. The supervisor blocks
  localhost, private, loopback, link-local, multicast, reserved, and unspecified
  IP targets, including redirects.
- `web_search` uses a provider chain: Brave Search API when
  `SHUSHUNYA_AGENT_BRAVE_SEARCH_API_KEY` is set, SearXNG when
  `SHUSHUNYA_AGENT_SEARXNG_URL` is set, then public Marginalia search, then
  Wikipedia opensearch. For production-quality general web search, configure
  Brave or a private SearXNG instance instead of relying only on public fallback
  providers.

Enable automatic memory injection for experiments:

```bash
SHUSHUNYA_AGENT_INJECT_MEMORY=1 ./scripts/run-agent.sh "задача"
```

Archive internal steps for debugging:

```bash
SHUSHUNYA_AGENT_ARCHIVE_INTERNAL_STEPS=1 ./scripts/run-agent.sh "задача"
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
