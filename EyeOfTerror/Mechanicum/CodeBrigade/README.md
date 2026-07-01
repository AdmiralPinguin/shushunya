# Code Brigade

Ceraxia's implementation-side Mechanicum brigade.

Likely members:

- `CogitatorCodewright`
- `FerrumPatchwright`
- `OrdinatusVerifier`
- `JudicatorCodicis`
- `SealwrightFinalis`

The current folder owns the handoff contract from Ceraxia to code workers. Real
worker implementations still run from the top-level `Mechanicum/` service tree
until the specialized brigade is split into separate services.

## Current Contract

Ceraxia writes `implementation_brief.json` before any code worker may mutate
source. The brief must state:

- task kind and risk level;
- selected strategy from `PlanningBrigade`;
- allowed scope;
- forbidden approaches;
- required verification;
- acceptance gates.

Workers return `worker_report.json` using
`code_brigade_contract.schema.json`. `code_brigade_adapter.py` is the current
local adapter: it validates the implementation brief and can acknowledge a
dry-run handoff, but real source execution remains blocked until an execution
adapter is intentionally wired.
