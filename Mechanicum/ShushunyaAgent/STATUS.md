# ShushunyaAgent Status

Last verified: 2026-06-21 12:01 KST.

## Running Services

- LLM host: `http://127.0.0.1:8080`
- ArchiveOfHeresy: `http://127.0.0.1:8090`
- ShushunyaAgent API: `http://127.0.0.1:8095`

## Verified

- Archive health is `ok`.
- Agent API health is `ok`.
- Sandbox hides `/media` and `/root`.
- Sandbox network is blocked by default.
- Structured file tools work.
- `replace_in_file` works.
- Python tool works inside sandbox.
- `shell_enabled=false` blocks shell execution.
- `/run` returns JSON trace and omits stderr unless requested.
- `/run` is serialized by process-local and file locks.

## Known Limits

- The `500G` limit is enforced by structured file tools as a soft policy.
- Shell and Python tools can only be hard-limited by enabling ext4 project quota.
- Hard quota helper is available as `scripts/setup-hard-quota.sh`, but applying
  it requires sudo and quota tools such as `xfs_quota`.
- The API is bound to localhost by default. If exposed beyond localhost, set
  `SHUSHUNYA_AGENT_API_KEY`.
