# ArchiveOfHeresy Memory Access

This is the quick reference for any chat, agent, or tool that needs memory.
All model-facing memory access must go through ArchiveOfHeresy. Agents must not
read or write memory files directly.

## Base

- Archive URL: `http://127.0.0.1:8090`
- Health: `GET /health`
- Gateway manifest: `GET /archive/memory/gateway`
- Auth: add `Authorization: Bearer $ARCHIVE_API_KEY` when `ARCHIVE_API_KEY` is set.

Known namespaces:

- `default` - normal Telegram/chat memory.
- `agent` - ShushunyaAgent memory.

Read requests for an unknown namespace return 404 unless `create=1` is passed
intentionally. Prefer creating memory through normal chat/proposal paths so the
Librarian can decide how to store it.

## Layers

0. Raw archives: daily JSONL plus SQLite turn archive.
1. Focus: compact current-topic files, max 10 per namespace.
2. Wiki: curated long-term pages updated by the Librarian.
3. Vector: searchable archive chunks.
4. GraphRAG: entities and relations.

Current runtime rule: only focus is injected by default. Lower layers keep
collecting data, but are not injected into the model unless explicitly enabled
or queried through the gateway. Check `magos_context_layers` in `/health` or
`/archive/memory/gateway`.

Current local daemon mode may override that conservative default in
`ArchiveOfHeresy/start-main.sh`: Magos lower layers plus direct vector/graph
injection can be enabled while memory is small. Check `/health` for
`direct_injection`.

ArchiveOfHeresy writes daily memory quality reports at 04:00 when enabled.
Reports are runtime artifacts under `ArchiveOfHeresy/reports/memory_quality/`
and are intentionally ignored by git.

Vector memory prefers local semantic embeddings through the OpenAI-compatible
`/v1/embeddings` endpoint. The local llama.cpp host must be started with
`--embeddings --pooling mean`; current backend/version are visible in
`/health.vector_embedding`.

## HTTP Gateway

Manifest:

```bash
curl -fsS http://127.0.0.1:8090/archive/memory/gateway
```

Catalog:

```bash
curl -fsS -G http://127.0.0.1:8090/archive/memory/catalog \
  --data-urlencode namespace=agent \
  --data-urlencode requester=my-agent
```

Search compact memory:

```bash
curl -fsS -G http://127.0.0.1:8090/archive/memory/search \
  --data-urlencode namespace=agent \
  --data-urlencode requester=my-agent \
  --data-urlencode 'q=memory gateway' \
  --data-urlencode limit=5 \
  --data-urlencode layers=focus,wiki \
  --data-urlencode include_content=0
```

Use `layers=focus,wiki,vector,graph` to choose search scope. Start narrow
(`focus,wiki`) when lower layers are noisy. Set `include_content=1` only after
compact results look relevant and raw vector chunk text is needed.

Read active focus:

```bash
curl -fsS -G http://127.0.0.1:8090/archive/memory/focus \
  --data-urlencode namespace=agent \
  --data-urlencode requester=my-agent \
  --data-urlencode id=active \
  --data-urlencode max_chars=12000
```

Read wiki page:

```bash
curl -fsS -G http://127.0.0.1:8090/archive/memory/wiki \
  --data-urlencode namespace=agent \
  --data-urlencode requester=my-agent \
  --data-urlencode 'title=Page Title' \
  --data-urlencode max_chars=12000
```

Inspect memory gateway events:

```bash
curl -fsS -G http://127.0.0.1:8090/archive/memory/events \
  --data-urlencode namespace=agent \
  --data-urlencode limit=20 \
  --data-urlencode component=memory_gateway \
  --data-urlencode requester=my-agent
```

Propose a memory change:

```bash
curl -fsS -X POST http://127.0.0.1:8090/archive/memory/propose-change \
  -H 'Content-Type: application/json' \
  -d '{
    "namespace": "agent",
    "requester": "my-agent",
    "target": "auto",
    "importance": 3,
    "proposal": "Fact or decision to preserve.",
    "evidence": "Why this is known."
  }'
```

Allowed proposal targets: `auto`, `focus`, `wiki`, `vector`, `graph`.
Proposals are archived as turns. The Librarian decides what actually changes.

## ShushunyaAgent Actions

Use these JSON actions from the agent loop:

```json
{"action":"archive_memory_gateway"}
{"action":"archive_memory_catalog"}
{"action":"archive_memory_search","query":"memory gateway","limit":5,"layers":"focus,wiki","include_content":false}
{"action":"archive_memory_read","kind":"focus","id":"active","max_chars":12000}
{"action":"archive_memory_read","kind":"wiki","title":"Page Title","max_chars":12000}
{"action":"archive_memory_events","limit":20,"component":"memory_gateway","requester":"shushunya-agent"}
{"action":"archive_memory_propose","target":"focus","importance":3,"proposal":"Fact to preserve","evidence":"Tool result or user statement"}
```

Agent rules:

- Treat memory as reference, not proof of current state.
- Current user request and current tool results are fresher than memory.
- Read specific focus/wiki books only after catalog/search shows relevance.
- Never write memory files directly; submit proposals.

## Diagnostics

Run from project root:

```bash
ArchiveOfHeresy/check-memory-gateway.sh --manifest-only
ArchiveOfHeresy/check-memory-gateway.sh agent 'memory gateway'
ArchiveOfHeresy/check-memory.sh agent 'memory gateway'
ArchiveOfHeresy/check-namespace-smoke.py
ArchiveOfHeresy/memory-report.sh agent
ArchiveOfHeresy/memory-quality-report.sh
cd EyeOfTerror/Warmaster/MobileGateway/ShushunyaAgent && ./ShushunyaAgent/bin/python -m shushunya_agent.self_test
```

Services:

```bash
curl -fsS http://127.0.0.1:8090/health
curl -fsS http://127.0.0.1:8095/health
```
