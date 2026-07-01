# RepoSurveyor

Defines the read-only repository survey request for Ceraxia.

Responsibilities:

- identify public entrypoints
- map tests and candidate source files
- preserve local dependency/import edges when they can be inferred cheaply
- inspect config/runtime/API/security surfaces when relevant
- exclude generated, model, cache, and runtime trees
- report `truncated=true` when the survey hits the configured file limit

Authority: read-only repository mapping.
