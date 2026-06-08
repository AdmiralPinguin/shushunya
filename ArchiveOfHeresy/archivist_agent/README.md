# Archivist Agent

Isolated librarian agent for ArchiveOfHeresy focus memory.

This agent lives inside ArchiveOfHeresy, but the model side is cut off from raw memory files. Focus memory is exposed as a controlled bookshelf:

```text
catalog -> tool request -> tool result -> finish action -> bookshelf writes files
```

The public Shushunya persona does not apply here. The archivist uses strict service prompts and only returns JSON actions for maintaining focus books.
