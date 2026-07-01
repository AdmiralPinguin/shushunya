# Client

Retry policy: transient `ConnectionError` transport failures are retried up to `max_attempts`. Validation failures such as `ValueError` are not retried and must surface immediately; no sleep-based waiting is used.
