# AgentArena

Comparative stress bench for ShushunyaAgent and external agent scaffolds.

The arena keeps all downloaded tools, virtual environments, workspaces, runs,
and reports under this directory. It uses the same local OpenAI-compatible
llama.cpp endpoint by default:

- base URL: `http://127.0.0.1:8080/v1`
- model: `gemma-4-12b-it-UD-Q5_K_XL.gguf`

## Candidates

- `shushunya`: current local ShushunyaAgent API at `http://127.0.0.1:8095`.
- `aider`: terminal coding assistant. Official docs describe OpenAI-compatible
  setup with `OPENAI_API_BASE`, `OPENAI_API_KEY`, and `--model openai/<model>`.
- `mini-swe-agent`: compact bash-based SWE agent using LiteLLM/OpenAI-compatible
  providers.
- `openhands`: downloaded for comparison, but full local operation normally
  requires Docker/Podman or its own runtime. This machine currently has neither,
  so it is tracked as a candidate and reported as unavailable until the runtime
  exists.

## Usage

Install candidates:

```bash
./scripts/install_candidates.sh
```

Run a small tournament:

```bash
./scripts/run_arena.py --suite smoke
```

Reports are written to `reports/`, raw run logs to `runs/`, and per-agent task
workspaces to `workspaces/`.

Reports are written atomically and include a `summary` block with per-agent
totals, pass counts, fail counts, duration, and pass rate.

Runner self-test:

```bash
./scripts/run_arena_self_test.py
```

Summarize recent reports:

```bash
./scripts/analyze_reports.py --limit 30
```
