#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from execution_contract import CONTRACT_VERSION


def is_safe_repo_relative_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return bool(normalized) and not normalized.startswith(("/", "~")) and ".." not in normalized.split("/")


def validate_path_list(paths: Any, label: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(paths, list):
        return [f"diagnostic repair request {label} must be a list"]
    for index, path in enumerate(paths):
        if not isinstance(path, str) or not is_safe_repo_relative_path(path):
            problems.append(f"diagnostic repair request {label}[{index}] must be a safe repo-relative path")
    return problems


def validate_diagnostic_repair_request(request: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if request.get("kind") != "ceraxia_code_brigade_diagnostic_repair_request":
        problems.append("diagnostic repair request kind is unsupported")
    if request.get("contract_version") != CONTRACT_VERSION:
        problems.append("diagnostic repair request contract_version is unsupported")
    if request.get("target") != "CodeBrigade":
        problems.append("diagnostic repair request target must be CodeBrigade")
    if request.get("status") not in {"required", "not_required"}:
        problems.append("diagnostic repair request status must be required or not_required")
    for key in ("run_id", "repo_path", "task", "verification_status", "review_decision"):
        if not isinstance(request.get(key), str):
            problems.append(f"diagnostic repair request {key} must be a string")
    queue = request.get("diagnostic_repair_queue") if isinstance(request.get("diagnostic_repair_queue"), dict) else {}
    if not queue:
        problems.append("diagnostic repair request must include diagnostic_repair_queue")
        return problems
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    item_count = queue.get("item_count")
    if not isinstance(item_count, int) or item_count != len(items):
        problems.append("diagnostic repair queue item_count must match items")
    if queue.get("status") == "queued" and not items:
        problems.append("queued diagnostic repair request must include items")
    if request.get("status") == "required" and queue.get("status") != "queued":
        problems.append("required diagnostic repair request must have queued repair items")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append(f"diagnostic repair queue item {index} must be an object")
            continue
        for key in (
            "command",
            "status",
            "priority",
            "diagnostic_signals",
            "impacted_surfaces",
            "package_ids",
            "read_before_repair",
            "concrete_read_targets",
            "stop_conditions",
            "repair_evidence_required",
        ):
            if key not in item:
                problems.append(f"diagnostic repair queue item {index} missing {key}")
        for key in ("diagnostic_signals", "read_before_repair", "stop_conditions", "repair_evidence_required"):
            if not isinstance(item.get(key), list) or not item.get(key):
                problems.append(f"diagnostic repair queue item {index} {key} must be a non-empty list")
        if not isinstance(item.get("max_repair_attempts"), int) or item.get("max_repair_attempts", 0) < 1:
            problems.append(f"diagnostic repair queue item {index} max_repair_attempts must be positive")
    problems.extend(validate_path_list(request.get("target_files_to_inspect"), "target_files_to_inspect"))
    problems.extend(validate_path_list(request.get("test_files_to_preserve"), "test_files_to_preserve"))
    if not isinstance(request.get("return_contract"), list):
        problems.append("diagnostic repair request return_contract must be a list")
    if not isinstance(request.get("reverse_dependency_index"), dict):
        problems.append("diagnostic repair request reverse_dependency_index must be an object")
    if not isinstance(request.get("scope_budget"), dict):
        problems.append("diagnostic repair request scope_budget must be an object")
    for index, item in enumerate(items):
        if isinstance(item, dict):
            problems.extend(validate_path_list(item.get("concrete_read_targets"), f"diagnostic_repair_queue.items[{index}].concrete_read_targets"))
    return problems


def build_diagnostic_repair_intake(request: dict[str, Any]) -> dict[str, Any]:
    problems = validate_diagnostic_repair_request(request)
    queue = request.get("diagnostic_repair_queue") if isinstance(request.get("diagnostic_repair_queue"), dict) else {}
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    return {
        "kind": "code_brigade_diagnostic_repair_intake",
        "contract_version": CONTRACT_VERSION,
        "status": "blocked" if problems else ("ready" if request.get("status") == "required" else "not_required"),
        "request_status": request.get("status", ""),
        "item_count": len(items),
        "high_priority_count": sum(1 for item in items if isinstance(item, dict) and item.get("priority") == "high"),
        "impacted_surfaces": sorted(
            {
                str(surface)
                for item in items
                if isinstance(item, dict)
                for surface in (item.get("impacted_surfaces") if isinstance(item.get("impacted_surfaces"), list) else [])
                if isinstance(surface, str)
            }
        ),
        "package_ids": sorted(
            {
                str(package_id)
                for item in items
                if isinstance(item, dict)
                for package_id in (item.get("package_ids") if isinstance(item.get("package_ids"), list) else [])
                if isinstance(package_id, str)
            }
        ),
        "target_files_to_inspect": request.get("target_files_to_inspect", []) if isinstance(request.get("target_files_to_inspect"), list) else [],
        "test_files_to_preserve": request.get("test_files_to_preserve", []) if isinstance(request.get("test_files_to_preserve"), list) else [],
        "blockers": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Ceraxia diagnostic repair request for CodeBrigade.")
    parser.add_argument("request", help="Path to diagnostic_repair_request.json")
    args = parser.parse_args()
    payload = json.loads(Path(args.request).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        print(json.dumps({"status": "blocked", "blockers": ["request payload must be an object"]}, ensure_ascii=False, indent=2))
        return 2
    intake = build_diagnostic_repair_intake(payload)
    print(json.dumps(intake, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if intake["status"] in {"ready", "not_required"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
