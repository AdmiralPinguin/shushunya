# Archivist Agent

Isolated memory agents for ArchiveOfHeresy.

The public Shushunya persona does not apply here. These agents use strict
service prompts and only return structured JSON for memory maintenance.

## Librarian

The librarian runs after successful model answers. It coordinates the memory
write path:

- focus files for compact current-topic context;
- vector chunks for similarity lookup;
- wiki pages for durable sorted facts and decisions;
- GraphRAG nodes and edges for entities and relations;
- memory event JSONL records for auditability.

The librarian lives inside ArchiveOfHeresy, but the model side is cut off from
raw memory files. Focus and wiki memory are exposed as controlled bookshelves:

```text
catalog -> tool request -> tool result -> finish action -> bookshelf writes files
```

## Magos

Magos runs before the main model answer. It selects or creates the active focus
file for the request and prepares a compact memory context from focus, wiki,
vector, and graph layers. It is fail-soft: if it fails, ArchiveOfHeresy still
continues the model request.

## Namespaces

Memory can be scoped by `memory_namespace`. The default chat uses `default`;
Warmaster uses `warmaster`. Focus, wiki, vector retrieval, and graph memory are
kept namespace-aware so worker tool loops do not leak into normal chat context.
