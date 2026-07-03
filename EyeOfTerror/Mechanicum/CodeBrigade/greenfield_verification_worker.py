#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from greenfield_architect import request_greenfield_model_guidance
from verification_adapter import run_verification_commands


def repair_guidance_for_verification(project_brief: dict[str, Any], verification: dict[str, Any], signature: str) -> dict[str, Any]:
    return request_greenfield_model_guidance(
        "GreenfieldRepairWorker",
        {
            "project_name": project_brief.get("project_name"),
            "template_id": project_brief.get("template_id"),
            "verification_status": verification.get("status"),
            "verification_results": verification.get("results", []),
            "failure_signature": signature,
            "common_failure_fixes": project_brief.get("template_contract", {}).get("common_failure_fixes", []),
        },
        "Given the failed greenfield verification output, propose a bounded repair hypothesis or a blocker. Do not invent unrelated scope.",
    )


def project_file_content_map(project_brief: dict[str, Any]) -> dict[str, str]:
    rows = project_brief.get("files") if isinstance(project_brief.get("files"), list) else []
    contents: dict[str, str] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        content = item.get("content")
        if path and isinstance(content, str):
            contents[path] = content
    return contents


def apply_greenfield_repair(repo: Path, project_brief: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    template_contents = project_file_content_map(project_brief)
    expected_files = [str(path) for path in project_brief.get("expected_files", []) if isinstance(path, str)]
    repaired_files: list[dict[str, Any]] = []
    blockers: list[str] = []
    for rel_path in expected_files:
        if rel_path == "greenfield_project_brief.json":
            continue
        path = repo / rel_path
        if path.exists():
            continue
        if rel_path not in template_contents:
            blockers.append(f"missing file has no template repair content: {rel_path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template_contents[rel_path], encoding="utf-8")
        repaired_files.append({"path": rel_path, "repair": "restored_missing_template_file"})
    readme = repo / "README.md"
    if readme.exists() and readme.is_file():
        text = readme.read_text(encoding="utf-8")
        additions: list[str] = []
        for command in project_brief.get("run_commands", []):
            if isinstance(command, str) and command and command not in text:
                additions.append(f"```bash\n{command}\n```")
        for command in project_brief.get("verification_commands", []):
            if isinstance(command, str) and command and command not in text:
                additions.append(f"```bash\n{command}\n```")
        if additions:
            readme.write_text(text.rstrip() + "\n\n## Repaired Commands\n\n" + "\n\n".join(additions) + "\n", encoding="utf-8")
            repaired_files.append({"path": "README.md", "repair": "added_missing_contract_commands"})
    elif "README.md" in template_contents:
        readme.write_text(template_contents["README.md"], encoding="utf-8")
        repaired_files.append({"path": "README.md", "repair": "restored_missing_template_file"})
    if not repaired_files and not blockers:
        blockers.append("no bounded greenfield repair was applicable")
    return {
        "kind": "code_brigade_greenfield_repair_execution",
        "contract_version": "eye-mechanicum.v1",
        "status": "applied" if repaired_files else "not_applicable",
        "repaired_files": repaired_files,
        "blockers": blockers,
        "verification_status_before": verification.get("status", ""),
    }


def verification_failure_signature(verification: dict[str, Any]) -> str:
    return json.dumps(
        [
            {
                "command": item.get("command"),
                "status": item.get("status"),
                "stderr": str(item.get("stderr") or "")[-500:],
            }
            for item in verification.get("results", [])
            if isinstance(item, dict)
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def build_stop_condition_evidence(
    reason: str,
    attempts: list[dict[str, Any]],
    final_verification: dict[str, Any],
    repeated_signature: bool = False,
) -> dict[str, Any]:
    combined_error = "\n".join(
        str(row.get("stderr") or "")
        for row in final_verification.get("results", [])
        if isinstance(row, dict)
    ).lower()
    return {
        "kind": "code_brigade_greenfield_stop_condition_evidence",
        "reason": reason,
        "attempt_count": len(attempts),
        "repair_attempt_count": sum(1 for attempt in attempts if isinstance(attempt.get("repair_execution"), dict)),
        "repeated_failure_signature": repeated_signature,
        "dependency_unavailable_hint": "module not found" in combined_error or "no module named" in combined_error,
        "secret_required_hint": "token" in combined_error or "api key" in combined_error or "secret" in combined_error,
        "system_package_hint": "command not found" in combined_error or "no such file or directory" in combined_error,
        "final_status": final_verification.get("status", ""),
    }


def verification_loop_result(
    status: str,
    attempts: list[dict[str, Any]],
    final_verification: dict[str, Any],
    stop_reason: str,
    repeated_signature: bool = False,
) -> dict[str, Any]:
    return {
        "kind": "code_brigade_greenfield_verification_loop",
        "status": status,
        "attempts": attempts,
        "final_verification": final_verification,
        "stop_reason": stop_reason,
        "stop_condition_evidence": build_stop_condition_evidence(stop_reason, attempts, final_verification, repeated_signature),
    }


def run_greenfield_verification_loop(repo: Path, commands: list[str], project_brief: dict[str, Any], max_cycles: int = 2) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    previous_signature = ""
    final_verification: dict[str, Any] = {}
    for cycle in range(1, max_cycles + 1):
        verification = run_verification_commands(commands, str(repo), execute=True)
        final_verification = verification
        signature = verification_failure_signature(verification)
        if verification.get("status") == "passed":
            attempts.append({"cycle": cycle, "status": verification.get("status", ""), "failure_signature": "", "repair_guidance": {}})
            return verification_loop_result("passed", attempts, verification, "verification passed")
        if signature and signature == previous_signature:
            attempts.append({"cycle": cycle, "status": verification.get("status", ""), "failure_signature": signature, "repair_guidance": {}, "repair_execution": {"status": "skipped_repeat_failure", "repaired_files": [], "blockers": ["same verification failure repeats"]}})
            return verification_loop_result("blocked", attempts, verification, "same verification failure repeats", repeated_signature=True)
        repair_guidance = repair_guidance_for_verification(project_brief, verification, signature)
        repair_execution = apply_greenfield_repair(repo, project_brief, verification)
        attempts.append(
            {
                "cycle": cycle,
                "status": verification.get("status", ""),
                "failure_signature": signature,
                "repair_guidance": repair_guidance,
                "repair_execution": repair_execution,
            }
        )
        if repair_execution.get("status") != "applied":
            return verification_loop_result("blocked", attempts, verification, "no bounded repair applicable")
        previous_signature = signature
    return verification_loop_result("blocked", attempts, final_verification, "max verification cycles reached")
