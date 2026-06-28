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
| 7012 | OcularisRenderium | Planned JavaScript render/screenshot worker |

## Current Rule

Do not split `ShushunyaAgent` internals immediately. Keep it as the general
worker while new workers are introduced behind the common Worker API.

Extract capabilities gradually only after the worker contract and governor
state machine are stable.

## Standard Worker Runtime

Prototype workers can be served through the shared runtime:

```bash
python3 Mechanicum/start_worker.py Lexmechanic --workspace-root runtime/mechanicum-work
```

Start the current lore pipeline worker set:

```bash
python3 Mechanicum/start_all_workers.py --workspace-root runtime/mechanicum-work
```

The runtime exposes:

- `GET /health`
- `GET /capabilities`
- `POST /run` with either a dispatch packet or raw worker request JSON
