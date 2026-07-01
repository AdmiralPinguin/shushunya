# RepoSurveyor

Defines the read-only repository survey request for Ceraxia.

Responsibilities:

- identify public entrypoints
- map tests and candidate source files
- preserve local dependency/import edges when they can be inferred cheaply
- inspect config/runtime/API/security surfaces when relevant
- exclude generated, model, cache, and runtime trees
- report `truncated=true` when the survey hits the configured file limit
- report `python_symbols_truncated=true` when symbol/import evidence hits its
  separate Python file limit

Quality gates:

- repository survey request is read-only
- explicit path hints are preserved
- critical path includes repo evidence before design

Authority: read-only repository mapping.
