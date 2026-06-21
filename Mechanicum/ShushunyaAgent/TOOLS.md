# ShushunyaAgent Tools

The model must return exactly one JSON object per step.

## Final

```json
{"action":"final","message":"short result for the user"}
```

## Sandbox Status

```json
{"action":"sandbox_status"}
```

Returns sandbox identity, visible paths, and disk usage for `/work`.

## File Tools

Paths must be inside sandbox writable roots. Relative paths resolve under
`/work`.

```json
{"action":"list_files","path":"/work","max_depth":2}
{"action":"read_file","path":"/work/file.txt","max_bytes":20000,"offset":0}
{"action":"write_file","path":"/work/file.txt","content":"text"}
{"action":"append_file","path":"/work/file.txt","content":"text"}
{"action":"replace_in_file","path":"/work/file.txt","old":"old text","new":"new text","count":1}
{"action":"mkdir","path":"/work/dir"}
{"action":"remove_file","path":"/work/file.txt"}
{"action":"file_info","path":"/work/file.txt"}
{"action":"find_files","path":"/work","pattern":"*.txt","max_depth":4}
{"action":"search_text","path":"/work","query":"needle","case_sensitive":false,"max_matches":50}
```

`remove_file` refuses directories unless `recursive` is exactly `true`.
`find_files` and `search_text` are structured alternatives to shell search for
mobile callers that disable shell execution.
For large files, call `file_info` or `search_text` first, then use `read_file`
with explicit `max_bytes` and `offset` slices. `read_file` reports `next_offset`
when more content remains.

## Python

```json
{"action":"python","code":"print('hello')","timeout":60}
```

Runs `/usr/bin/python3 -c` inside the sandbox.

## Web

```json
{"action":"web_search","query":"current query","limit":5}
{"action":"web_fetch","url":"https://example.com/page","max_bytes":200000}
```

Web tools run through the supervisor, not through sandbox shell. They allow only
public `http` and `https` URLs and reject localhost, private, loopback,
link-local, multicast, reserved, and unspecified IP targets. `web_fetch` returns
status, final URL, content type, title, extracted text, and truncation status.
`web_search` uses configured providers first: Brave Search API
(`SHUSHUNYA_AGENT_BRAVE_SEARCH_API_KEY`), SearXNG (`SHUSHUNYA_AGENT_SEARXNG_URL`),
then public fallback providers.

## Shell

```json
{"action":"shell","cmd":"pwd && ls -la","timeout":60,"reason":"why this is needed"}
```

Shell is available for cases where structured tools are not enough. The
supervisor rejects obvious host-control commands such as `sudo`, `mount`,
`systemctl`, `docker`, and `ssh`.

## Archive Search

Check ArchiveOfHeresy status without reading memory:

```json
{"action":"archive_status"}
```

Inspect recent memory maintenance events for the current agent namespace:

```json
{"action":"archive_memory_events","limit":20}
{"action":"archive_memory_events","component":"memory_gateway","event_action":"search","limit":20}
{"action":"archive_memory_events","component":"memory_gateway","requester":"shushunya-agent","limit":20}
```

Search or inspect memory explicitly:

```json
{"action":"archive_search","kind":"focus","query":"active"}
{"action":"archive_search","kind":"vector","query":"search terms"}
{"action":"archive_search","kind":"graph","query":"search terms"}
{"action":"archive_memory_gateway"}
{"action":"archive_memory_catalog"}
{"action":"archive_memory_search","query":"search terms","limit":5,"layers":"focus,wiki,vector,graph","include_content":false}
{"action":"archive_memory_read","kind":"focus","id":"active","max_chars":12000}
{"action":"archive_memory_read","kind":"wiki","title":"page title","max_chars":12000}
```

The runner has no long-term memory. It can ask `ArchiveOfHeresy` for memory
through these explicit actions. Prefer the `archive_memory_*` gateway actions
over direct layer-specific search when possible: they go through the controlled
ArchiveOfHeresy Memory Gateway, audit read operations, and return fail-soft tool
results on HTTP 400/404 instead of crashing the agent loop.
Gateway search returns compact snippets by default. Set `include_content` to
`true` only when the compact result is relevant and raw vector chunk text is
needed.
Use `layers` to narrow noisy searches, for example `focus,wiki` for curated
context or `vector` for raw archive recall.

To request a memory change, submit a proposal:

```json
{"action":"archive_memory_propose","target":"focus","importance":3,"proposal":"fact to preserve","evidence":"why this is known"}
```

The proposal is archived and reviewed by the Librarian. The agent never writes
memory files directly.

Normal agent runs also pass every model step through `ArchiveOfHeresy` with
`memory_namespace=agent`, so the agent has a separate focus bookshelf with the
same 10-file limit as the default chat focus bookshelf, plus namespace-scoped
wiki/vector/graph memory.

## Task Journals

Agent API runs write compact operational journals under
`runtime/task-journals/`. They are not long-term memory and are meant for
debugging and resume.

HTTP callers can set:

```json
{"task_id":"stable-task-id"}
{"resume_task_id":"stable-task-id"}
```

Read a journal through:

```text
GET /task-journal?task_id=stable-task-id&limit=80
```

Resume context is compacted before it is appended to the next prompt. A large
journal remains inspectable through `/task-journal`, but it is not replayed into
the model wholesale.

## Runtime State

The HTTP API exposes process-local runner state:

```text
GET /state
```

The response includes `busy`, `queued`, `current_task_id`, `last_task_id`,
`last_exit_code`, `completed`, and `max_request_bytes`. This endpoint is meant
for UI/ops checks and does not start a model request.

HTTP callers can set `wait_for_slot=false` on `/run` or `/run-stream` to fail
fast with `409 agent busy` instead of waiting behind another active run.
