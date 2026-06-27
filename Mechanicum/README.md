# Mechanicum

Mechanicum contains internal workers. Workers should be callable services, not
user-facing chat personalities.

The public entrypoint remains EyeOfTerror. Mechanicum workers receive contracts
from governors and return structured outputs.

## Worker Ports

| Port | Worker | Role |
| --- | --- | --- |
| 7001 | ShushunyaAgent | General worker |
| 7002 | Lexmechanic | Source researcher |
| 7003 | NoosphericExtractor | Fact extractor |
| 7004 | Chronologis | Timeline builder |
| 7005 | ScriptoriumDaemon | Writer |
| 7006 | ReductorVerifier | Critic/verifier |
| 7007 | FabricatorFinalis | Finalizer/packager/Telegram |
| 7008 | CogitatorCodewright | Code worker |
| 7009 | AuspexBrowser | Browser worker |
| 7010 | ForgeRelay | DemonsForge adapter |
| 7011 | MnemosyneRelay | ArchiveOfHeresy adapter |

## Current Rule

Do not split `ShushunyaAgent` internals immediately. Keep it as the general
worker while new workers are introduced behind the common Worker API.

Extract capabilities gradually only after the worker contract and governor
state machine are stable.

## Standard Worker Runtime

Prototype workers can be served through the shared runtime:

```bash
python3 Mechanicum/worker_runtime.py \
  --worker Lexmechanic \
  --module-path Mechanicum/Lexmechanic \
  --module lexmechanic \
  --port 7002 \
  --workspace-root runtime/mechanicum-work
```

The runtime exposes:

- `GET /health`
- `POST /run` with either a dispatch packet or raw worker request JSON
