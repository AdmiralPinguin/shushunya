# ArchiveOfHeresy Status

## Current Memory Activation

The local daemon is intentionally configured for active memory while the archive
is still small:

- Magos is enabled with lower layers: `ARCHIVE_MAGOS_CONTEXT_LAYERS=wiki,vector,graph`.
- Direct vector prompt injection is enabled: `ARCHIVE_VECTOR_INJECTION_ENABLED=1`.
- Direct graph prompt injection is enabled: `ARCHIVE_GRAPH_INJECTION_ENABLED=1`.
- Vector and graph startup backfill are enabled.

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
```

`check-main.sh` checks `/health`, `/v1/models`, catalogs for `default` and
`agent`, unified gateway search across focus/wiki/vector/graph,
`/archive/vector/search`, and `/archive/graph/search`.

`memory-report.sh` prints active focus title, focus count, wiki page count,
vector chunk/turn counts, graph node/edge counts, recent memory events, and
recent Magos/Librarian errors from the runtime log.

## Known Weak Spots

- Vector memory currently uses stable hashed sparse token vectors, not true
  semantic embeddings. Retrieval quality is closer to keyword/similarity search
  than semantic recall. Later, replace it with local embeddings without external
  APIs.
- Focus and wiki search currently use token overlap. Rephrased ideas can be
  missed until a stronger retrieval layer is added.
- Vector backfill currently scans the archive SQLite on startup. This is fine
  while the archive is small, but later it should become incremental and track
  already indexed turn IDs.
- `CHAT_QUEUE_LOCK` serializes the whole flow, including Librarian/wiki/graph
  maintenance. This preserves consistency now, but answers may wait as memory
  grows. Later, move maintenance into a background queue with observable pending
  status.
