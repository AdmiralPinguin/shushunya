def safe_archive_path(raw):
    candidate = str(raw).replace('\\\\', '/')
    parts = [part for part in candidate.split('/') if part not in ('', '.')]
    if candidate.startswith('/') or '..' in parts:
        raise ValueError('archive path escapes root')
    if not parts:
        raise ValueError('archive path is empty')
    return '/'.join(parts)
