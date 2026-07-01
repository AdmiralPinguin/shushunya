# Cache Store

Cache reads, loads, invalidation, and version updates are protected by an `RLock`. Invalidation is idempotent via `pop(key, None)`; tests avoid sleep-based synchronization.
