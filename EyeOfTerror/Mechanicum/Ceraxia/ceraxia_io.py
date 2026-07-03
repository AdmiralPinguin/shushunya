from __future__ import annotations

"""Run-dir and JSON/text IO helpers for the Ceraxia orchestrator."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ceraxia_common import (  # noqa: F401
    CONTRACT_VERSION,
    DIAGNOSTIC_REPAIR_MAX_ATTEMPTS,
    EXECUTION_MODES,
    LIFECYCLE,
    PROJECT_ROOT,
    REQUIRED_RUN_ARTIFACTS,
    RUNS_ROOT,
    CeraxiaInput,
)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def task_slug(task: str, repo_path: str = "") -> str:
    words = re.findall(r"[a-zA-Z0-9а-яА-ЯёЁ]+", task.lower())
    slug = "-".join(words[:6]) or "task"
    digest = hashlib.sha1(f"{task}\n{repo_path}".encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def allocate_run_dir(runs_root: Path, base_run_id: str) -> tuple[str, Path]:
    run_id = base_run_id
    run_dir = runs_root / run_id
    counter = 2
    while run_dir.exists():
        run_id = f"{base_run_id}-{counter}"
        run_dir = runs_root / run_id
        counter += 1
    return run_id, run_dir


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
