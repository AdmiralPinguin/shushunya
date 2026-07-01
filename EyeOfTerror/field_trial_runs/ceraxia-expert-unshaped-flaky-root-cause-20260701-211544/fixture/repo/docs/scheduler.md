# Scheduler

Root cause: sorting by priority alone left equal-priority items dependent on input order. The deterministic tie-breaker is `id`; tests repeat the check without skip or sleep behavior.
