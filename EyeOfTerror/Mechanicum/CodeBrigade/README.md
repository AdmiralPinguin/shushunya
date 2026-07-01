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
The worker report must include `implementation_plan`, which preserves survey
candidate files, test files, local dependency edges, handoff steps,
verification commands, acceptance gates, and refusal conditions for the future
real executor.
It also includes `execution_policy_status`; this must remain
`blocked_until_adapter_is_wired` until a real source-mutation adapter is
implemented and covered by the local gate.
When that adapter is added, its result must satisfy
`execution_result.schema.json`: status, changed files, patch summary, executed
verification commands, blockers, and rollback notes.

`verification_adapter.py` can run a narrow allowlist of verification commands
without a shell. It is safe enough for explicit verification wiring, but source
mutation remains outside this adapter.
