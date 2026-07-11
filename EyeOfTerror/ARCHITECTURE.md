# EyeOfTerror Architecture

EyeOfTerror is the orchestration layer. Mechanicum is the worker layer. A user
client should talk to Abaddon first and treat workers as internal services
unless it is doing explicit diagnostics. `Warmaster` remains only in internal
paths and machine contracts for compatibility.

## Layers

| Layer | Port range | Responsibility |
| --- | --- | --- |
| Abaddon | 7000 | User-facing chat/task entrypoint, task routing, durable run state, client polling API |
| Mechanicum workers | 7001+ | Focused tools and task executors behind the common worker API |
| Inner Circle governors | 7101+ | Task-class leaders that make domain decisions and command subordinate warbands |
| Legacy/backends | Backend-specific | External engines wrapped by relay workers before Abaddon uses them |

## Command Hierarchy Invariant

- **Abaddon** owns strategic intake, routing, cross-warband arbitration, and final escalation.
- **Ceraxia** owns coding-warband leadership decisions and engineering tradeoffs.
- **Skitarii** own the detailed repository plan, implementation, tests, and repair loops.

Abaddon, Moriana, other brigadiers, and public clients must not dispatch work
directly to Skitarii. Code requests go to Ceraxia; Ceraxia commands her
subordinate Skitarii warband.

## Control Flow

1. A client sends a user message or task to Abaddon.
2. Abaddon classifies the request and chooses one active Inner Circle
   governor.
3. The governor sets warband-level decisions, constraints, tradeoffs, and
   acceptance boundaries, then delegates detailed planning, execution, and
   checks to the subordinate warband. Abaddon can use an
   in-process governor path for local development or the governor HTTP API for
   service-separated execution; the gateway can be started in either default
   mode.
4. Abaddon executes or starts the run, records durable ledger state, and
   exposes polling/cancellation endpoints to clients.
5. Mechanicum workers perform focused steps through the common worker API.
6. The governor or verifier checks outputs against the task contract before the
   run is considered complete.

Abaddon should not directly perform specialist worker jobs. Workers should not
route broad user tasks. Governors should coordinate a task class, not own the
user chat surface.

## Registries

- `EyeOfTerror/Warmaster/registry/ports.json` is the stable topology source for ports.
- `EyeOfTerror/Warmaster/registry/governors.json` declares active and planned governors.
- Worker `worker.json` manifests live under their owning active department
  paths; legacy relay/runtime manifests can live under `LegacyMechanicum/`.
- `LegacyMechanicum/worker_services.json` declares runnable worker services.

Rules:

- Active governors must have a service and stable port.
- Governors declare `route_terms` in the registry; Abaddon routing should be
  data-driven from that registry instead of hardcoded per governor.
- Planned governors may have documentation but must not receive live tasks.
- Prototype workers must be listed as runnable services.
- Planned workers must not be listed as runnable services.
- Relay workers adapt legacy engines to the common worker API instead of making
  Abaddon depend on backend-specific protocols.

## Client API

Clients should use:

- `GET /state` after startup or reconnect.
- `GET /state?health=1` for admin startup diagnostics when health checks are
  worth the extra latency.
- `GET /brigade_plan` for expected service topology diagnostics.
- `GET /brigade_health` for expected topology, best-effort service health, and
  governor worker requirement diagnostics.
- `GET /events?after=N` for one aggregate event cursor across all runs.
- `GET /runs/{task_id}/snapshot?events_after=N` for per-run polling.
- `GET /recovery` for interrupted-run recovery queues and readiness
  diagnostics.
- `POST /runs/{task_id}/cancel` for cancellation.
- `GET /doctor` for admin diagnostics.

Detailed run endpoints remain available for debugging, but normal clients
should prefer snapshots over many separate requests.

Each prepared run package should include:

- `contract.json` for the user task contract.
- `oversight.json` for the governor's run-specific supervision plan.
- `status.json` for pipeline status and file locations.
- `dispatch/*.json` for worker requests.

## Service Startup

`EyeOfTerror/Warmaster/start_brigade.py` starts the current service-separated stack:
Mechanicum worker supervisor, `IskandarKhayon` on `7101`, and Abaddon
on `7000` with default HTTP governor transport. It is a lightweight launcher,
not a durable service manager. `--json` exposes the startup plan for diagnostics
and future admin clients, including the individual Mechanicum worker services
from `LegacyMechanicum/worker_services.json`, top-level service dependencies, and
readiness URLs. `--wait-ready` turns Abaddon, governor, and worker health URLs
into a startup readiness gate.
The launcher fails fast if any managed top-level process exits and terminates
the remaining managed processes. It also checks managed port availability before
starting unless `--skip-port-check` is passed.
