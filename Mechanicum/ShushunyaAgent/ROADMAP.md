# ShushunyaAgent Roadmap

## Done

- Model loop through `ArchiveOfHeresy`.
- No persistent runner-owned memory.
- Explicit Archive memory tools: `archive_status`, `archive_search`,
  `archive_memory_gateway`, `archive_memory_catalog`, `archive_memory_search`,
  `archive_memory_read`, `archive_memory_propose`, and filtered
  `archive_memory_events`.
- Archive memory tools use the controlled Memory Gateway and fail soft on
  Archive HTTP errors.
- Automatic Archive memory for agent runs in the `agent` namespace.
- Isolated sandbox execution through `bubblewrap`.
- Default sandbox network isolation.
- Structured file tools.
- Structured Python tool.
- Optional shell fallback with supervisor denylist.
- SearXNG-first web search and guarded public `web_fetch`.
- Stream heartbeat events for long model/tool calls.
- Runtime required-field validation.
- Runtime action schema validation for action allowlist, field types, enums,
  sandbox path shape, and bounded numeric fields.
- Repeated identical action guard.
- JSON output mode for integration.
- Streaming progress events with task id and duration metadata.
- Technical output mode for automation.
- `/run` serialization in the Agent API with process-local and runtime file locks.
- `/state` runtime endpoint for UI/ops checks.
- Process-local quality metrics in `/state` for runs, steps, JSON repair,
  validation rejects, tool failures, timeouts, cancels, and web search sources.
- Task journals and compact `resume_task_id` continuation context.
- Cooperative cancellation through `POST /cancel`.
- Bounded run queue with `SHUSHUNYA_AGENT_MAX_QUEUE`.
- API request guards reject invalid or non-object JSON with `400`.
- `/run-stream` returns queue overflow as HTTP `429` before opening NDJSON.
- Offline self-test and check scripts can validate local agent hardening without
  touching ArchiveOfHeresy.
- Lightweight Agent API watchdog monitors `/state` and can restart without
  touching ArchiveOfHeresy.
- Privileged task journal and resume access behind API-key bearer auth.
- Self-test covering Archive, sandbox paths, file tools, Python, and network isolation.
- Android agent mode calls `/run-stream`, uses the `agent` memory namespace, and
  includes state and cancel controls.

## Next

- Apply hard ext4 project quota for the `ARCHIVE` sandbox. Dry-run helper exists
  at `scripts/setup-hard-quota.sh`; applying it still needs sudo and quota tools
  such as `xfs_quota`.
- Add AppArmor enforcement for the sandbox launcher.
- Add an approval gate for risky tools before enabling broader shell access.
- Add a durable service manager wrapper for boot-time startup and log rotation.
- Add mobile support for bearer auth, then require `SHUSHUNYA_AGENT_API_KEY`
  before exposing broader public agent controls.
- Keep monitoring stale Archive focus files as namespaces accumulate.
- Add richer visible queue controls for long-running phone-driven tasks.
