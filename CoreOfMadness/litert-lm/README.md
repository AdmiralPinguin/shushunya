# LiteRT-LM Host

Project-local LiteRT-LM runtime for Gemma 4 12B.

## Layout

- `venv/` - LiteRT-LM CLI environment.
- `home/.litert-lm/` - LiteRT-LM local model registry.
- `cache/` - Hugging Face/cache files used during import.
- `runtime/` - PID and log files.
- `scripts/` - start, stop, and check helpers.

## Model

Imported model ID:

```bash
gemma4-12b
```

The official OpenAI-compatible server accepts model strings in the form:

```bash
gemma4-12b,gpu,2048
```

## Commands

Start:

```bash
./scripts/start-litert-host.sh
```

Check:

```bash
./scripts/check-litert-host.sh
```

Stop:

```bash
./scripts/stop-litert-host.sh
```

## ArchiveOfHeresy Switch

Use this backend by starting ArchiveOfHeresy with:

```bash
ARCHIVE_LLM_BASE_URL=http://127.0.0.1:9379
ARCHIVE_DEFAULT_MODEL=gemma4-12b,gpu,2048
ARCHIVE_MAGOS_MODEL=gemma4-12b,gpu,2048
ARCHIVE_LIBRARIAN_MODEL=gemma4-12b,gpu,2048
```

Keep llama.cpp on port `8080` as a fallback backend.

## Current Smoke-Test Notes

- `gemma4-12b,cpu,...` fails for this package because the model requires the
  GPU backend.
- `gemma4-12b,gpu,32768` fails on RTX 2060 12GB because LiteRT tries to create
  a single GPU buffer larger than the device limit.
- `gemma4-12b,gpu,2048` loads and generates, but currently returns noisy
  thinking/channel markup in `content`; do not make it the production Archive
  backend until response-format tests pass.
