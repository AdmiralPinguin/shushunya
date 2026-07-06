# AgentArena

Historical comparative stress bench for external agent scaffolds.

The arena keeps all downloaded tools, virtual environments, workspaces, runs,
and reports under this directory. It uses the same local OpenAI-compatible
llama.cpp endpoint by default:

- base URL: `http://127.0.0.1:8080/v1`
- model: `gemma-4-12b-it-UD-Q5_K_XL.gguf`

## Candidates

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
totals, pass counts, fail counts, duration, pass rate, failure reasons, and
failed check type/symptom counters.
Per-agent rows also expose `unavailable`, `runnable_total`, and
`runnable_pass_rate`, so missing runtimes do not get confused with poor task
quality.
For historical task journals the summary also includes
`orchestration_quality`: whether code-repair runs showed a failing diagnostic,
performed an edit, verified after the last edit, followed CLI verification
prompts, and recovered from supervisor rejections. `analyze_reports.py`
aggregates the same chain-quality metrics across recent reports.
For data/artifact tasks with seeded input files and checked output files,
reports also include `artifact_quality`, which records whether inputs were read
before output artifacts were written. The artifact analyzer counts direct file
write tools and simple Python file IO (`open(..., "w")`, `Path(...).write_text`)
so data tasks that generate files from scripts are measured correctly.
`analyze_reports.py` classifies failures as `agent_unavailable`, `agent_exit`,
`post_run_checks`, `both`, or `unknown`, so missing runtimes, bad artifacts,
and runtime crashes are separated.
Recent failure entries include compact failed-check summaries for quick triage.
The analyzer also aggregates failed check types, which helps distinguish
artifact/content mistakes from command-level verification failures.
It also reports common failed-check symptoms such as JSON decode, assertion,
type, import, module, and syntax errors.

Runner self-test:

```bash
./scripts/run_arena_self_test.py
```

Summarize recent reports:

```bash
./scripts/analyze_reports.py --limit 30
./scripts/analyze_reports.py --limit 30 --markdown
```
