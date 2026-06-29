# Local Corpus

Put user-provided primary texts and other local research documents here when a
task needs sources that are not publicly fetchable.

`CorpusIngestor` scans this directory before `Lexmechanic` source discovery.
Matching files become local source candidates in `/work/<slug>/corpus_index.json`
and then flow through the normal pipeline as sources in `source_map.json`,
snapshots in `source_snapshots.json`, and direct-event evidence when markers are
found.

Supported text formats:

- `.epub`
- `.fb2`
- `.txt`
- `.md`
- `.html`
- `.htm`
- `.xhtml`

For lore reconstruction tasks, names matter. If a final manifest reports
`corpus_requirements`, use one of the suggested filenames or a clear equivalent
inside this directory. Example:

```text
Corpus/Kharn_Eater_of_Worlds.epub
Corpus/Lucius_The_Faultless_Blade.epub
Corpus/The_Weakness_of_Others.epub
```

The corpus index records only metadata and short diagnostics for non-matching
files. It does not copy full local text into git.
