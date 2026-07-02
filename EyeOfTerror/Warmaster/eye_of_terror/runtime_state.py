"""Shared runtime state and constants for the Warmaster gateway package.

These are imported by the gateway and its extracted submodules. ``ACTIVE_RUNS``
is a single mutable set guarded by ``ACTIVE_RUNS_LOCK``; every module must import
(not re-create) it so process-local active-run tracking stays consistent.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ACTIVE_RUNS: set[str] = set()
ACTIVE_RUNS_LOCK = threading.Lock()
MAX_LIST_LIMIT = 200
MAX_ARTIFACT_TEXT_BYTES = 500000
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ALLOWED_SERVICE_HOSTS = {"127.0.0.1", "localhost", "::1"}
