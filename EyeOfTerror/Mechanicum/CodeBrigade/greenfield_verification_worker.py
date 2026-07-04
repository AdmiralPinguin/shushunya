#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from greenfield_architect import request_greenfield_model_guidance
from greenfield_implementation_worker import execute_module_synthesis_contracts, extract_json_object
from verification_adapter import run_verification_commands


def repair_guidance_for_verification(project_brief: dict[str, Any], verification: dict[str, Any], signature: str, request_guidance=request_greenfield_model_guidance) -> dict[str, Any]:
    return request_guidance(
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


def apply_greenfield_synthesis_repair(repo: Path, project_brief: dict[str, Any], verification: dict[str, Any], signature: str, request_guidance=request_greenfield_model_guidance) -> dict[str, Any]:
    return execute_module_synthesis_contracts(
        repo,
        project_brief,
        request_guidance,
        synthesis_stage="verification_repair",
        verification_context={
            "status": verification.get("status", ""),
            "failure_signature": signature,
            "results": verification.get("results", []),
        },
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


def _repo_relative_path(repo: Path, rel_path: str) -> Path | None:
    if not rel_path or rel_path.startswith(("/", "~")):
        return None
    path = (repo / rel_path).resolve()
    try:
        path.relative_to(repo.resolve())
    except ValueError:
        return None
    return path


def _repair_spec_from_guidance(repair_guidance: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(repair_guidance, dict) or not repair_guidance.get("ok"):
        return {}
    content = repair_guidance.get("content")
    if not isinstance(content, str) or not content.strip():
        return {}
    try:
        parsed = extract_json_object(content)
    except (json.JSONDecodeError, ValueError):
        return {}
    hypothesis = parsed.get("repair_hypothesis") if isinstance(parsed.get("repair_hypothesis"), dict) else parsed
    if not isinstance(hypothesis, dict):
        return {}
    evidence = parsed.get("evidence") if isinstance(parsed.get("evidence"), dict) else {}
    if evidence:
        hypothesis = dict(hypothesis)
        hypothesis.setdefault("target_file", evidence.get("traceback_source") or evidence.get("target_file") or evidence.get("path"))
        hypothesis.setdefault("target_line", evidence.get("line_number") or evidence.get("target_line") or evidence.get("line"))
    hypothesis.setdefault("target_file", parsed.get("scope_boundary"))
    hypothesis.setdefault("action", parsed.get("hypothesis") or parsed.get("action") or parsed.get("repair"))
    return hypothesis


def _undefined_name_from_verification(verification: dict[str, Any]) -> str:
    combined = "\n".join(
        f"{item.get('stdout') or ''}\n{item.get('stderr') or ''}"
        for item in verification.get("results", [])
        if isinstance(item, dict)
    )
    match = re.search(r"NameError: name '([A-Za-z_][A-Za-z0-9_]*)' is not defined", combined)
    return match.group(1) if match else ""


def _guided_line_repair(repo: Path, verification: dict[str, Any], repair_guidance: dict[str, Any] | None) -> dict[str, Any] | None:
    spec = _repair_spec_from_guidance(repair_guidance)
    target_file = str(spec.get("target_file") or spec.get("path") or "")
    target_line = spec.get("target_line") or spec.get("line")
    action = " ".join(
        str(spec.get(key) or "")
        for key in ("action", "repair", "hypothesis")
        if spec.get(key)
    ).lower()
    if not target_file or not isinstance(target_line, int):
        return None
    path = _repo_relative_path(repo, target_file)
    if path is None or not path.exists() or not path.is_file():
        return {
            "path": target_file,
            "repair": "guided_line_repair",
            "status": "blocked",
            "blocker": "guided target file is missing or outside workspace",
        }
    undefined_name = _undefined_name_from_verification(verification)
    if not undefined_name:
        return None
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    index = target_line - 1
    if index < 0 or index >= len(lines):
        return {
            "path": target_file,
            "repair": "guided_line_repair",
            "status": "blocked",
            "blocker": "guided target line is outside file",
        }
    line = lines[index]
    if line.strip() != undefined_name:
        return None
    if action and not any(marker in action for marker in ("remove", "delete", "stray", "удал")):
        return None
    del lines[index]
    path.write_text("".join(lines), encoding="utf-8")
    return {
        "path": target_file,
        "repair": "guided_remove_undefined_name_line",
        "status": "applied",
        "target_line": target_line,
        "undefined_name": undefined_name,
    }


def apply_greenfield_repair(repo: Path, project_brief: dict[str, Any], verification: dict[str, Any], repair_guidance: dict[str, Any] | None = None) -> dict[str, Any]:
    template_contents = project_file_content_map(project_brief)
    expected_files = [str(path) for path in project_brief.get("expected_files", []) if isinstance(path, str)]
    repaired_files: list[dict[str, Any]] = []
    blockers: list[str] = []
    guided_repair = _guided_line_repair(repo, verification, repair_guidance)
    if guided_repair is not None:
        if guided_repair.get("status") == "applied":
            repaired_files.append(guided_repair)
        elif guided_repair.get("blocker"):
            blockers.append(str(guided_repair["blocker"]))
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


def run_greenfield_verification_loop(repo: Path, commands: list[str], project_brief: dict[str, Any], max_cycles: int = 2, request_guidance=request_greenfield_model_guidance) -> dict[str, Any]:
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
        repair_guidance = repair_guidance_for_verification(project_brief, verification, signature, request_guidance)
        repair_execution = apply_greenfield_repair(repo, project_brief, verification, repair_guidance)
        if repair_execution.get("status") != "applied":
            synthesis_repair = apply_greenfield_synthesis_repair(repo, project_brief, verification, signature, request_guidance)
            repair_execution = {
                "kind": "code_brigade_greenfield_repair_execution",
                "contract_version": "eye-mechanicum.v1",
                "status": "applied" if synthesis_repair.get("status") == "applied" else "not_applicable",
                "repair_strategy": "module_synthesis_repair",
                "repaired_files": [{"path": path, "repair": "verification_repair_module_synthesis"} for path in synthesis_repair.get("changed_files", []) if isinstance(path, str)],
                "blockers": [
                    *[str(item) for item in repair_execution.get("blockers", []) if isinstance(item, str)],
                    *[
                        f"{row.get('path')}: {'; '.join(str(item) for item in row.get('blockers', []) if isinstance(item, str))}"
                        for row in synthesis_repair.get("rows", [])
                        if isinstance(row, dict) and row.get("blockers")
                    ],
                ],
                "verification_status_before": verification.get("status", ""),
                "synthesis_repair_report": synthesis_repair,
            }
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
    if attempts and isinstance(attempts[-1].get("repair_execution"), dict) and attempts[-1]["repair_execution"].get("status") == "applied":
        verification = run_verification_commands(commands, str(repo), execute=True)
        final_verification = verification
        if verification.get("status") == "passed":
            attempts.append({"cycle": max_cycles + 1, "status": verification.get("status", ""), "failure_signature": "", "repair_guidance": {}, "post_repair_verification": True})
            return verification_loop_result("passed", attempts, verification, "verification passed after final repair")
    return verification_loop_result("blocked", attempts, final_verification, "max verification cycles reached")
