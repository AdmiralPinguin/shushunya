# EyeOfTerror Architecture

EyeOfTerror is the orchestration layer. Mechanicum is the worker layer. A user
client should talk to Warmaster Gateway first and treat workers as internal
services unless it is doing explicit diagnostics.

## Layers

| Layer | Port range | Responsibility |
| --- | --- | --- |
| Warmaster Gateway | 7000 | User-facing chat/task entrypoint, task routing, durable run state, client polling API |
| Mechanicum workers | 7001+ | Focused tools and task executors behind the common worker API |
| Inner Circle governors | 7101+ | Task-class coordinators that plan work and assign Mechanicum workers |
| Legacy/backends | Backend-specific | External engines wrapped by relay workers before Warmaster uses them |

## Control Flow

1. A client sends a user message or task to Warmaster Gateway.
2. Warmaster classifies the request and chooses one active Inner Circle
   governor.
3. The governor builds a task contract, dispatch packets, and verification
   expectations. Warmaster can use an in-process governor path for local
   development or the governor HTTP API for service-separated execution; the
   gateway can be started in either default mode.
4. Warmaster executes or starts the run, records durable ledger state, and
   exposes polling/cancellation endpoints to clients.
5. Mechanicum workers perform focused steps through the common worker API.
6. The governor or verifier checks outputs against the task contract before the
   run is considered complete.

Warmaster should not directly perform specialist worker jobs. Workers should not
route broad user tasks. Governors should coordinate a task class, not own the
user chat surface.

## Registries

- `EyeOfTerror/registry/ports.json` is the stable topology source for ports.
- `EyeOfTerror/registry/governors.json` declares active and planned governors.
- `Mechanicum/*/worker.json` declares worker metadata and capabilities.
- `Mechanicum/worker_services.json` declares runnable worker services.

Rules:

- Active governors must have a service and stable port.
- Governors declare `route_terms` in the registry; Warmaster routing should be
  data-driven from that registry instead of hardcoded per governor.
- Planned governors may have documentation but must not receive live tasks.
- Prototype workers must be listed as runnable services.
- Planned workers must not be listed as runnable services.
- Relay workers adapt legacy engines to the common worker API instead of making
  Warmaster depend on backend-specific protocols.

## Client API

Clients should use:

- `GET /state` after startup or reconnect.
- `GET /state?health=1` for admin startup diagnostics when health checks are
  worth the extra latency.
- `GET /brigade_plan` for expected service topology diagnostics.
- `GET /brigade_health` for expected topology plus best-effort service health.
- `GET /runs/{task_id}/snapshot?events_after=N` for per-run polling.
- `POST /runs/{task_id}/cancel` for cancellation.
- `GET /doctor` for admin diagnostics.

Detailed run endpoints remain available for debugging, but normal clients
should prefer snapshots over many separate requests.

## Service Startup

`EyeOfTerror/start_brigade.py` starts the current service-separated stack:
Mechanicum worker supervisor, `IskandarKhayon` on `7101`, and Warmaster Gateway
on `7000` with default HTTP governor transport. It is a lightweight launcher,
not a durable service manager. `--json` exposes the startup plan for diagnostics
and future admin clients, including the individual Mechanicum worker services
from `Mechanicum/worker_services.json`, top-level service dependencies, and
readiness URLs. `--wait-ready` turns those URLs into a startup readiness gate.
