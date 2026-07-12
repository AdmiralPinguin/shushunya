"""External fail-closed suite runner."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fixture_server import FixtureServer
from .fixtures import load_fixture
from .manifest import LoadedSuite, load_suite
from .metrics import aggregate_metrics
from .oracles import OracleReport, evaluate_task
from .subjects import SubjectAdapter


RUNNER_VERSION = "research-external-eval/0.2"


def build_service_payload(task: dict[str, Any], *, fixture_base_url: str) -> dict[str, Any]:
    """Positive allowlist: private oracle fields cannot cross this boundary."""
    return {
        "goal": task["request"]["goal"],
        "task_id": f"eval-{task['id']}",
        "max_wall_sec": task["limits"]["wall_sec"],
        "standalone_test": True,
        "output_contract_version": task["request"]["output_contract_version"],
        "source_gateway_url": fixture_base_url,
    }


def _empty_counters(task: dict[str, Any]) -> dict[str, int]:
    return {
        "required_facts": len(task["oracle"]["required_facts"]),
        "required_facts_passed": 0,
        "major_claims": 0,
        "unsupported_major_claims": 0,
        "quotes": 0,
        "valid_quotes": 0,
        "provenance_items": 0,
        "valid_provenance_items": 0,
    }


def _classify(result: dict[str, Any], report: OracleReport) -> str:
    if report.passed:
        return {
            "accepted": "correct",
            "blocked": "correct_blocked",
            "needs_user": "asked_clarification",
        }.get(str(result.get("status") or ""), "failed")
    if result.get("accepted") is True or result.get("status") == "accepted":
        return "FALSE_ACCEPT"
    return "failed"


def _healthy(health: Any) -> bool:
    identity = health.get("identity") if isinstance(health, dict) else None
    return (
        isinstance(health, dict)
        and health.get("status") == "ok"
        and isinstance(identity, dict)
        and bool(identity.get("instance_id"))
        and isinstance(identity.get("source_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", identity["source_sha256"]) is not None
        and identity.get("standalone_test_mode") is True
    )


def _required_fixture_routes(task: dict[str, Any], fixture: Any) -> set[str]:
    source_ids = {
        str(source_id)
        for fact in task["oracle"]["required_facts"]
        for source_id in fact["source_ids"]
    }
    return {str(fixture.document(source_id).data["route"]) for source_id in source_ids}


def _fixture_access_failures(
    task: dict[str, Any], fixture: Any, accesses: list[dict[str, object]],
) -> list[str]:
    required = _required_fixture_routes(task, fixture)
    observed = {
        str(item.get("path") or "").split("?", 1)[0]
        for item in accesses
        if item.get("method") in {"GET", "HEAD"} and item.get("status") == 200
    }
    missing = sorted(required - observed)
    return [f"subject did not acquire required fixture source: {path}" for path in missing]


def run_suite(
    suite_or_path: LoadedSuite | str | Path,
    subject: SubjectAdapter,
    *,
    allowed_root: str | Path | None = None,
) -> dict[str, Any]:
    suite = suite_or_path if isinstance(suite_or_path, LoadedSuite) else load_suite(suite_or_path, allowed_root=allowed_root)
    fixture = load_fixture(suite.fixture_path, expected_sha256=suite.data["fixture_sha256"])
    started = time.monotonic()
    generated_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    infrastructure_errors: list[str] = []
    try:
        health_start = subject.health()
    except Exception as exc:  # noqa: BLE001
        health_start = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
        infrastructure_errors.append("subject health failed at start")
    with FixtureServer(fixture) as gateway:
        for task in suite.tasks:
            task_started = time.monotonic()
            access_cursor = len(gateway.access_log)
            row: dict[str, Any] = {
                "id": task["id"],
                "category": task["category"],
                "verdict": "unverified",
                "declared_accepted": False,
                "failures": [],
                "counters": _empty_counters(task),
            }
            payload = build_service_payload(task, fixture_base_url=gateway.base_url)
            try:
                execution = subject.execute(payload, timeout_sec=task["limits"]["wall_sec"])
                if not execution.terminal or not execution.cleanup_proven:
                    row["failures"] = [f"subject lifecycle cleanup was not proven: {execution.cleanup_detail}"]
                    infrastructure_errors.append(f"{task['id']}: cleanup not proven")
                else:
                    result = execution.result
                    try:
                        encoded_size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                    except Exception as exc:  # noqa: BLE001
                        raise ValueError(f"subject result is not JSON-serializable: {exc}") from exc
                    if encoded_size > task["limits"]["max_result_bytes"]:
                        result = {
                            "status": "accepted" if result.get("accepted") is True else "failed",
                            "accepted": bool(result.get("accepted")),
                            "mission_id": f"eval-{task['id']}",
                        }
                        report = OracleReport(False, ["subject result exceeds the task byte limit"], _empty_counters(task), {})
                    else:
                        report = evaluate_task(task, result, fixture)
                    task_accesses = list(gateway.access_log[access_cursor:])
                    access_failures = _fixture_access_failures(task, fixture, task_accesses)
                    if access_failures:
                        report = OracleReport(
                            False,
                            [*report.failures, *access_failures],
                            report.counters,
                            report.matched_facts,
                        )
                    row["declared_accepted"] = bool(result.get("accepted"))
                    row["subject_status"] = str(result.get("status") or "")
                    row["verdict"] = _classify(result, report)
                    row["failures"] = list(report.failures)
                    row["counters"] = dict(report.counters)
                    row["fixture_access_count"] = len(task_accesses)
            except Exception as exc:  # noqa: BLE001
                row["failures"] = [f"evaluator/subject infrastructure error: {type(exc).__name__}: {str(exc)[:200]}"]
                infrastructure_errors.append(f"{task['id']}: execution error")
            row["seconds"] = round(time.monotonic() - task_started, 3)
            rows.append(row)
        fixture_access_log = list(gateway.access_log)
    try:
        health_end = subject.health()
    except Exception as exc:  # noqa: BLE001
        health_end = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
        infrastructure_errors.append("subject health failed at end")
    identity_stable = (
        _healthy(health_start)
        and _healthy(health_end)
        and health_start.get("identity") == health_end.get("identity")
    )
    if not identity_stable:
        infrastructure_errors.append("subject identity/health did not remain stable")
    metrics = aggregate_metrics(rows)
    run_valid = not infrastructure_errors and metrics["unverified"] == 0 and metrics["errored"] == 0
    run_passed = bool(run_valid and metrics["correct"] == metrics["total"] and metrics["false_accepted"] == 0)
    result = {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "suite_id": suite.data["suite_id"],
        "suite_kind": suite.data["suite_kind"],
        "visibility": suite.data["visibility"],
        "suite_sha256": suite.raw_sha256,
        "fixture_bundle_id": fixture.data["bundle_id"],
        "fixture_manifest_sha256": fixture.raw_sha256,
        "generated_at_utc": generated_at,
        "seconds": round(time.monotonic() - started, 3),
        "subject_health_start": health_start,
        "subject_health_end": health_end,
        "validation": {
            "fixture_integrity_verified": True,
            "subject_identity_stable": identity_stable,
            "all_task_cleanup_proven": not any("cleanup not proven" in error for error in infrastructure_errors),
            "infrastructure_errors": infrastructure_errors,
        },
        "fixture_access_count": len(fixture_access_log),
        "fixture_access_log_sha256": hashlib.sha256(json.dumps(fixture_access_log, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "run_valid": run_valid,
        "run_passed": run_passed,
        "metrics": metrics,
        "tasks": rows,
    }
    return result
