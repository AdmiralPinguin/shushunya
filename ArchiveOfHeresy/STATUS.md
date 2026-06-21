# ArchiveOfHeresy Status

## Current Memory Activation

The local daemon is intentionally configured for active memory while the archive
is still small:

- Magos is enabled with lower layers: `ARCHIVE_MAGOS_CONTEXT_LAYERS=wiki,vector,graph`.
- Direct vector prompt injection is enabled: `ARCHIVE_VECTOR_INJECTION_ENABLED=1`.
- Direct graph prompt injection is enabled: `ARCHIVE_GRAPH_INJECTION_ENABLED=1`.
- Vector and graph startup backfill are enabled.
- Daily memory quality reporting is enabled at 04:00.

This means the model can receive active focus, Magos lower-layer context, direct
vector context, and direct graph context. If prompt noise becomes visible, first
reduce `ARCHIVE_MAGOS_CONTEXT_LAYERS`, then disable direct vector/graph injection.

The architecture remains unchanged: Magos runs before the answer, the Librarian
runs after the answer, and external agents only propose writes through Memory
Gateway.

## Diagnostics

Use:

```bash
ArchiveOfHeresy/check-main.sh 'memory query'
ArchiveOfHeresy/check-memory-gateway.sh agent 'memory query'
ArchiveOfHeresy/memory-report.sh default
ArchiveOfHeresy/memory-report.sh agent
ArchiveOfHeresy/memory-quality-report.sh
```

`check-main.sh` checks `/health`, `/v1/models`, catalogs for `default` and
`agent`, unified gateway search across focus/wiki/vector/graph,
`/archive/vector/search`, and `/archive/graph/search`.

`memory-report.sh` prints active focus title, focus count, wiki page count,
vector chunk/turn counts, graph node/edge counts, recent memory events, and
recent Magos/Librarian errors from the runtime log.

`memory-quality-report.sh` manually runs the same Librarian quality audit that
the daemon schedules daily at 04:00. Reports are written to
`reports/memory_quality/YYYY/MM/`.

## Known Weak Spots

- Vector memory now prefers local semantic embeddings from the OpenAI-compatible
  `/v1/embeddings` endpoint and falls back to stable hashed sparse token and
  character n-gram vectors when the local host does not expose embeddings. Check
  `/health.vector_embedding` to confirm whether the resolved version is
  `openai:*` or `sparse:*`. The local llama.cpp host must run with
  `--embeddings --pooling mean` for the semantic path.
- Focus and wiki search currently use token plus character n-gram overlap.
  Rephrased ideas are still harder than exact lexical matches until a stronger
  retrieval layer is added.
- Vector backfill is incremental by indexed turn ID and embedding version, and
  capped by `ARCHIVE_VECTOR_BACKFILL_MAX_TURNS` per start. A future migration
  should add explicit migration tooling and progress reporting for very large
  archives.
- `CHAT_QUEUE_LOCK` now covers the main request/response path, while
  Librarian/wiki/graph maintenance runs after the answer under
  `MAINTENANCE_LOCK`. This reduces response blocking, but maintenance is still
  synchronous after the response write. Later, move maintenance into a background
  queue with observable pending status.
