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
- Runtime required-field validation.
- Repeated identical action guard.
- JSON output mode for integration.
- Technical output mode for automation.
- `/run` serialization in the Agent API with process-local and runtime file locks.
- Self-test covering Archive, sandbox paths, file tools, Python, and network isolation.

## Next

- Apply hard ext4 project quota for the `ARCHIVE` sandbox. Dry-run helper exists
  at `scripts/setup-hard-quota.sh`; applying it still needs sudo and quota tools
  such as `xfs_quota`.
- Add AppArmor enforcement for the sandbox launcher.
- Add an approval gate for risky tools before enabling broader shell access.
- Add a browser or HTTP-fetch tool as an explicitly networked, separately gated mode.
- Add a service wrapper for long-running agent sessions.
- Add a UI or Telegram command path that calls `run-agent.sh --json --technical`.
- If binding Agent API outside localhost, require `SHUSHUNYA_AGENT_API_KEY`.
- Keep monitoring stale Archive focus files as namespaces accumulate.
