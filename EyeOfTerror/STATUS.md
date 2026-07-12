# EyeOfTerror Status

## Active Architecture

- Abaddon is the public orchestration boundary on port `7000`.
- Ceraxia is the active code-warband leader on port `7104`. She decides mission
  intent, priorities, constraints, quality gates, and success conditions.
- Skitarii is the autonomous coding warband on port `7200`. It owns repository
  exploration, detailed planning, isolated implementation, review, repair,
  behavioural acceptance, and patch packaging.
- A code run is a native v2 package with exactly one execution step:
  `skitarii_mission` backed by `SkitariiWarband`.
- Native code runs have no `worker_plan`, dispatch directory, synthetic worker
  statuses, or compatibility executor path.

## Enforced Boundaries

- Ceraxia directives are structurally limited to leadership information.
  Detailed steps, commands, dependencies, and file-level implementation plans
  are rejected at the governor boundary.
- Skitarii production endpoints require a valid persisted Ceraxia leadership
  directive. Undirected execution is available only through the explicit
  two-part standalone-test gate.
- Abaddon routes native code runs through one backend switch. Generic local and
  HTTP worker executors reject native packages defensively.
- Skitarii executes in an isolated environment, freezes the candidate before
  private verification, and validates the returned patch in a disposable
  worktree before reporting it ready.
- Terminal mission state dominates stale progress events. A failed finalization
  cannot leave the application presenting a blocked mission as active revision.
- Runs created before the native contract are fail-closed and require a newly
  prepared mission; the system never fabricates a leadership directive for
  historical execution evidence.

## Runtime Registry

- `7000`: Abaddon Gateway
- `7101`: Iskandar Khayon
- `7103`: Moriana
- `7104`: Ceraxia
- `7200`: Skitarii Warband, supervised separately from generic Mechanicum
  workers

The retired code-worker ports `7014-7020` are not registered or started.

## Required Verification

Run the focused Mechanicum barrier:

```bash
EyeOfTerror/Mechanicum/check-mechanicum-local.sh
```

Run the repository integration barrier:

```bash
EyeOfTerror/Warmaster/check-eye-mechanicum.sh
```

The required barrier covers native package integrity, Ceraxia prepare/replay
semantics, centralized backend routing, terminal-state projection, the Ceraxia
leadership facade, and the full Skitarii unit/integration suite.

## Current Operational Work

- Put Abaddon Gateway and Ceraxia under supervised service lifecycle instead of
  detached `nohup` processes.
- Keep a real gateway-to-verdict smoke as a deployment check while ensuring the
  live repository remains unchanged unless patch application is explicit.
- Continue expanding held-out behavioural evaluation across representative real
  repositories and languages.

## Deferred Coding-Warband Capability Roadmap

The current Ceraxia/Skitarii autonomous coding lane is considered sufficient
and is frozen as the production baseline. Further redesign is not active work;
use the warband on real missions while other Shushunya subsystems are built.

A future capability counts as supported only when it has an authority contract,
scoped permissions, machine-checkable result proof, crash-safe recovery, and
truthful user-visible status. Mere access to a command through the Linux shell
does not satisfy that bar.

When this area becomes a priority again, start with a blind 50-100-task
comparison against then-current Claude Code and Codex releases. The deferred
order of work is branch/PR/CI/release and multi-repository workflows;
browser-based behavioural and visual acceptance; deploy, infrastructure, and
database workflows with rollback proof; then dynamic external integrations,
dependency-aware parallel worktrees, cross-warband handoffs, durable
clarification/approval, skills, and technical memory. Generic email, calendar,
office-document, and personal-assistant features remain out of scope unless a
specific engineering mission requires them.

The detailed backlog and invariants are in
[`Mechanicum/ROADMAP.md`](Mechanicum/ROADMAP.md).
