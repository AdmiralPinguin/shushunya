from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config


REPORTS_DIR = config.RUNTIME_DIR / "test-reports"
ALLOWED_SUFFIXES = {".json", ".md", ".png"}


def _safe_report_path(filename: str) -> Path:
    name = Path(str(filename)).name
    if name != filename or not name:
        raise ValueError("report filename must be a basename")
    path = REPORTS_DIR / name
    if path.suffix not in ALLOWED_SUFFIXES:
        raise ValueError("unsupported report file type")
    return path


def list_reports(limit: int = 100) -> list[dict[str, Any]]:
    if not REPORTS_DIR.exists():
        return []
    safe_limit = max(1, min(int(limit or 100), 500))
    files = [
        path
        for path in REPORTS_DIR.iterdir()
        if path.is_file() and path.suffix in ALLOWED_SUFFIXES
    ]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    reports = []
    for path in files[:safe_limit]:
        stat = path.stat()
        item: dict[str, Any] = {
            "filename": path.name,
            "kind": path.suffix.lstrip("."),
            "size_bytes": stat.st_size,
            "modified_at_epoch": stat.st_mtime,
            "url": f"/forge/reports/{path.name}",
        }
        if path.suffix == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict):
                item["run_id"] = payload.get("run_id")
                item["ok"] = payload.get("ok")
                item["duration_sec"] = payload.get("duration_sec")
                item["run_jobs"] = payload.get("run_jobs")
                item["contact_sheet"] = Path(str(payload.get("contact_sheet"))).name if payload.get("contact_sheet") else None
        reports.append(item)
    return reports


def report_path(filename: str) -> Path:
    path = _safe_report_path(filename)
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path
