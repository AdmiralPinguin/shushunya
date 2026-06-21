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
```

Search or inspect memory explicitly:

```json
{"action":"archive_search","kind":"focus","query":"active"}
{"action":"archive_search","kind":"vector","query":"search terms"}
{"action":"archive_search","kind":"graph","query":"search terms"}
```

The runner has no long-term memory. It can ask `ArchiveOfHeresy` for memory
through these explicit actions. Normal agent runs also pass every model step
through `ArchiveOfHeresy` with `memory_namespace=agent`, so the agent has a
separate focus bookshelf with the same 10-file limit as the default chat focus
bookshelf, plus namespace-scoped wiki/vector/graph memory.
