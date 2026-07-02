# HTTP Execution Diagnostics

`http_executor.py` writes `http_execution_report.json` for every HTTP run attempt.
Failures must also be durable ledger events so monitors and clients do not have
to infer execution errors only from the final result object.

Required failure events:

- `http_preflight_failed`: emitted when worker health, identity, or dispatch
  preflight blocks execution. The payload includes `failure_count`, `failures`,
  and the report path.
- `http_step_failed`: emitted when a worker `/run` call fails after preflight.
  The payload includes `step_id`, `worker`, `port`, `status`, and `error`.
