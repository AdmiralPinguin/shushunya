from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "runtime" / "forge-test-runners.lock"
LOCK_ENV = "FORGE_TEST_LOCK_HELD"


@contextmanager
def forge_test_lock() -> Iterator[None]:
    if os.environ.get(LOCK_ENV) == "1":
        yield
        return
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
