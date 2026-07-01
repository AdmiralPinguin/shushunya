def publish_event(transport, event, max_attempts=3):
    last_error = None
    for _ in range(max_attempts):
        try:
            return transport.send(event)
        except ConnectionError as exc:
            last_error = exc
    raise last_error
