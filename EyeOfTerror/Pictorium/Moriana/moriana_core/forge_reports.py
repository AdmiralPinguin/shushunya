from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from DemonsForge.forge_service import config


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


def _report_kind(path: Path, payload: dict[str, Any]) -> str:
    run_id = str(payload.get("run_id") or path.stem)
    if "forge-quality" in run_id:
        return "quality"
    if "forge-cycle" in run_id:
        return "cycle"
    if "forge-self-test" in run_id:
        return "self_test"
    if "forge-long" in run_id:
        return "long"
    if "shushunya-project" in run_id:
        return "shushunya_project"
    return "unknown"


def _json_report_payloads(limit: int) -> list[tuple[Path, dict[str, Any]]]:
    if not REPORTS_DIR.exists():
        return []
    safe_limit = max(1, min(int(limit or 100), 500))
    files = [path for path in REPORTS_DIR.iterdir() if path.is_file() and path.suffix == ".json"]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    payloads: list[tuple[Path, dict[str, Any]]] = []
    for path in files[:safe_limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payloads.append((path, payload))
    return payloads


def summarize_reports(limit: int = 100) -> dict[str, Any]:
    payloads = _json_report_payloads(limit=limit)
    by_kind: dict[str, dict[str, Any]] = {}
    scenario_history: dict[str, list[dict[str, Any]]] = {}
    warning_counts: dict[str, int] = {}
    latest_failures: list[dict[str, Any]] = []

    for path, payload in payloads:
        kind = _report_kind(path, payload)
        kind_summary = by_kind.setdefault(
            kind,
            {
                "count": 0,
                "ok": 0,
                "failed": 0,
                "latest": None,
                "latest_ok": None,
                "latest_duration_sec": None,
            },
        )
        kind_summary["count"] += 1
        if payload.get("ok") is True:
            kind_summary["ok"] += 1
        elif payload.get("ok") is False:
            kind_summary["failed"] += 1
        if kind_summary["latest"] is None:
            kind_summary["latest"] = path.name
            kind_summary["latest_ok"] = payload.get("ok")
            kind_summary["latest_duration_sec"] = payload.get("duration_sec")

        if payload.get("ok") is False and len(latest_failures) < 10:
            latest_failures.append(
                {
                    "filename": path.name,
                    "kind": kind,
                    "run_id": payload.get("run_id"),
                    "duration_sec": payload.get("duration_sec"),
                }
            )

        for scenario in payload.get("scenarios") or []:
            if not isinstance(scenario, dict) or not scenario.get("name"):
                continue
            name = str(scenario["name"])
            verdict = scenario.get("quality_verdict") or {}
            warnings = verdict.get("warnings") or []
            if isinstance(warnings, list):
                for warning in warnings:
                    warning_counts[str(warning)] = warning_counts.get(str(warning), 0) + 1
            scenario_history.setdefault(name, []).append(
                {
                    "filename": path.name,
                    "status": scenario.get("status"),
                    "dry_run_ok": scenario.get("dry_run_ok"),
                    "quality_status": verdict.get("status"),
                    "warnings": warnings if isinstance(warnings, list) else [],
                    "duration_sec": scenario.get("duration_sec"),
                    "artifact_id": scenario.get("artifact_id"),
                }
            )

    scenario_latest: list[dict[str, Any]] = []
    for name, history in sorted(scenario_history.items()):
        latest = dict(history[0])
        latest["name"] = name
        if len(history) > 1:
            current = latest.get("duration_sec")
            previous = history[1].get("duration_sec")
            if isinstance(current, (int, float)) and isinstance(previous, (int, float)) and previous > 0:
                ratio = round(float(current) / float(previous), 3)
                latest["duration_vs_previous"] = ratio
                if ratio >= 1.25:
                    latest.setdefault("warnings", []).append("duration_regression")
        scenario_latest.append(latest)

    return {
        "ok": True,
        "report_count": len(payloads),
        "by_kind": by_kind,
        "warning_counts": dict(sorted(warning_counts.items())),
        "latest_failures": latest_failures,
        "scenario_latest": scenario_latest,
    }


def prune_reports(max_files: int | None = None) -> dict[str, Any]:
    if not REPORTS_DIR.exists():
        return {"ok": True, "deleted": 0, "kept": 0}
    safe_max = max(1, int(max_files or config.REPORT_MAX_FILES))
    files = [
        path
        for path in REPORTS_DIR.iterdir()
        if path.is_file() and path.suffix in ALLOWED_SUFFIXES
    ]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    deleted = 0
    for path in files[safe_max:]:
        try:
            path.unlink()
            deleted += 1
        except OSError:
            pass
    return {"ok": True, "deleted": deleted, "kept": min(len(files), safe_max), "max_files": safe_max}


def report_path(filename: str) -> Path:
    path = _safe_report_path(filename)
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path
