from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


DEFAULT_TZ = "Asia/Seoul"


def now_iso(tz_name: str = DEFAULT_TZ) -> str:
    return datetime.now(ZoneInfo(tz_name)).replace(microsecond=0).isoformat()


def parse_datetime(value: str | None, tz_name: str = DEFAULT_TZ) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
    return parsed


def interval_delta(value: str | int | float | None) -> timedelta | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return timedelta(seconds=max(1, int(value)))
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"daily", "day", "1d"}:
        return timedelta(days=1)
    if text in {"hourly", "hour", "1h"}:
        return timedelta(hours=1)
    suffixes = {
        "s": 1,
        "sec": 1,
        "m": 60,
        "min": 60,
        "h": 3600,
        "d": 86400,
    }
    for suffix, scale in suffixes.items():
        if text.endswith(suffix):
            number = text[: -len(suffix)].strip()
            if number.isdigit():
                return timedelta(seconds=max(1, int(number) * scale))
    if text.isdigit():
        return timedelta(seconds=max(1, int(text)))
    raise ValueError(f"unsupported interval: {value}")


def next_run_after(current_iso: str | None, interval: str | int | float | None, tz_name: str = DEFAULT_TZ) -> str | None:
    delta = interval_delta(interval)
    if delta is None:
        return None
    base = parse_datetime(current_iso, tz_name) or datetime.now(ZoneInfo(tz_name))
    now = datetime.now(base.tzinfo or timezone.utc)
    candidate = base + delta
    while candidate <= now:
        candidate += delta
    return candidate.replace(microsecond=0).isoformat()
