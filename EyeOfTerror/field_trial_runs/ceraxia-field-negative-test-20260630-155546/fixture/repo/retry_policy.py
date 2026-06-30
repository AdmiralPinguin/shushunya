def parse_retry_count(raw):
    value = int(raw)
    if value < 0 or value > 10:
        raise ValueError('retry count must be between 0 and 10')
    return value
