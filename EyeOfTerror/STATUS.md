# EyeOfTerror Status

## Working

- Iskandar Khayon can build lore reconstruction task contracts and dispatch packets.
- Mechanicum prototype workers cover the current lore pipeline:
  - `Lexmechanic`
  - `AuspexBrowser`
  - `NoosphericExtractor`
  - `Chronologis`
  - `ScriptoriumDaemon`
  - `ReductorVerifier`
  - `FabricatorFinalis`
- Workers can run as local subprocesses through `eye_of_terror.local_executor`.
- Workers can run as HTTP services through `Mechanicum/worker_runtime.py`.
- EyeOfTerror can execute dispatch packets through HTTP services with `eye_of_terror.http_executor`.
- The end-to-end HTTP pipeline test reaches a `ready` final manifest for the Skalathrax test task.

## Main Check

```bash
EyeOfTerror/check-eye-mechanicum.sh
```

This runs contract tests, worker self-tests, local executor tests, service tests,
and the end-to-end HTTP worker pipeline.

## Current Limits

- `Lexmechanic` uses source playbooks plus a generic fallback; live source discovery is not implemented yet.
- `NoosphericExtractor` still uses rule-based event playbooks; Skalathrax rules now live in data, not Python code.
- `AuspexBrowser` performs guarded HTTP text fetches; it does not yet render JavaScript pages or screenshots.
- The pipeline records inaccessible primary books as gaps instead of solving book acquisition.
- Warmaster Gateway is not implemented yet; Iskandar and Mechanicum are ready for it to call.

## Next Good Steps

- Make `Lexmechanic` support pluggable search providers and generic source discovery.
- Add more playbooks only when they are task-class patterns, not one-off hacks.
- Add a persistent task ledger for governor runs and worker artifacts.
- Add Warmaster Gateway only after the Iskandar/Mechanicum boundary remains stable.
