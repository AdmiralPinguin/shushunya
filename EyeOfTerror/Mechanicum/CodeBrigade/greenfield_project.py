#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from execution_contract import build_blocked_execution_result, build_implemented_execution_result, build_patch_manifest
from greenfield_architect import build_greenfield_project_brief, request_greenfield_model_guidance
from greenfield_dependency_worker import run_dependency_worker
from greenfield_implementation_worker import execute_file_set_synthesis_contract, execute_module_synthesis_contracts
from greenfield_memory_worker import build_greenfield_memory_record
from greenfield_review_worker import forbidden_placeholder_markers_found, review_greenfield_project
from greenfield_scaffold_worker import greenfield_workspace_status, normalize_project_file_rows, scaffold_greenfield_files
from greenfield_templates import GREENFIELD_MARKER, PROJECT_TYPES
from greenfield_verification_worker import run_greenfield_verification_loop


def write_greenfield_json_artifact(repo: Path, rel_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = repo / rel_path
    before = path.read_bytes() if path.exists() else b""
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    after = content.encode("utf-8")
    return {
        "operation": "write_greenfield_report_artifact",
        "path": rel_path,
        "status": "applied",
        "before_sha256": hashlib.sha256(before).hexdigest() if before else "",
        "after_sha256": hashlib.sha256(after).hexdigest(),
    }


def build_greenfield_run_report(
    project_brief: dict[str, Any],
    file_set_synthesis_report: dict[str, Any],
    implementation_synthesis_report: dict[str, Any],
    dependency_report: dict[str, Any],
    verification_loop: dict[str, Any],
    greenfield_review: dict[str, Any],
    memory_record: dict[str, Any],
    model_guidance_ledger: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "code_brigade_greenfield_run_report",
        "contract_version": "eye-mechanicum.v1",
        "project_name": project_brief.get("project_name", ""),
        "project_type": project_brief.get("project_type", ""),
        "template_id": project_brief.get("template_id", ""),
        "acceptance_feature_ids": memory_record.get("acceptance_feature_ids", []),
        "file_set_synthesis_status": file_set_synthesis_report.get("status", ""),
        "file_set_synthesis_changed_file_count": file_set_synthesis_report.get("changed_file_count", 0),
        "file_set_synthesis_semantic_quality_status": file_set_synthesis_report.get("semantic_quality_status", ""),
        "implementation_synthesis_status": implementation_synthesis_report.get("status", ""),
        "implementation_synthesis_applied_count": implementation_synthesis_report.get("applied_count", 0),
        "implementation_synthesis_model_unavailable_count": implementation_synthesis_report.get("model_unavailable_count", 0),
        "definition_of_done_status": memory_record.get("definition_of_done_status", {}),
        "dependency_status": dependency_report.get("status", ""),
        "verification_status": verification_loop.get("status", ""),
        "verification_stop_reason": verification_loop.get("stop_reason", ""),
        "verification_stop_condition_evidence": verification_loop.get("stop_condition_evidence", {}),
        "review_status": greenfield_review.get("status", ""),
        "semantic_review_status": greenfield_review.get("semantic_review", {}).get("status", ""),
        "scenario_review_status": greenfield_review.get("scenario_review", {}).get("status", ""),
        "scenario_count": greenfield_review.get("scenario_review", {}).get("scenario_count", 0),
        "scenario_blocked_count": greenfield_review.get("scenario_review", {}).get("blocked_count", 0),
        "commands": memory_record.get("commands", {}),
        "model_guidance_ledger_status": model_guidance_ledger.get("status", ""),
        "model_guidance_role_count": model_guidance_ledger.get("role_count", 0),
        "reusable_learnings": memory_record.get("reusable_learnings", []),
    }


def _guidance_status(payload: Any) -> str:
    if not isinstance(payload, dict) or not payload:
        return "missing"
    return str(payload.get("status") or payload.get("decision") or "recorded")


def build_greenfield_model_guidance_ledger(
    project_brief: dict[str, Any],
    file_set_synthesis_report: dict[str, Any],
    implementation_synthesis_report: dict[str, Any],
    verification_loop: dict[str, Any],
    greenfield_review: dict[str, Any],
) -> dict[str, Any]:
    repair_entries = [
        attempt.get("repair_guidance")
        for attempt in verification_loop.get("attempts", [])
        if isinstance(attempt, dict) and isinstance(attempt.get("repair_guidance"), dict) and attempt.get("repair_guidance")
    ]
    entries = [
        {
            "role": "GreenfieldArchitect",
            "stage": "architecture_plan",
            "status": _guidance_status(project_brief.get("architecture_plan", {}).get("model_guidance", {})),
        },
        {
            "role": "GreenfieldImplementationWorker",
            "stage": "implementation_plan",
            "status": _guidance_status(project_brief.get("implementation_plan", {}).get("model_guidance", {})),
        },
        {
            "role": "GreenfieldImplementationWorker",
            "stage": "implementation_feature_report",
            "status": _guidance_status(project_brief.get("implementation_feature_report", {}).get("model_guidance", {})),
        },
        {
            "role": "GreenfieldImplementationWorker",
            "stage": "file_set_synthesis_execution",
            "status": str(file_set_synthesis_report.get("status") or "missing"),
        },
        {
            "role": "GreenfieldImplementationWorker",
            "stage": "module_synthesis_execution",
            "status": str(implementation_synthesis_report.get("status") or "missing"),
        },
        {
            "role": "GreenfieldReviewer",
            "stage": "greenfield_review",
            "status": _guidance_status(greenfield_review.get("model_guidance", {})),
        },
    ]
    for index, repair_guidance in enumerate(repair_entries, start=1):
        entries.append(
            {
                "role": "GreenfieldRepairWorker",
                "stage": f"repair_attempt_{index}",
                "status": _guidance_status(repair_guidance),
            }
        )
    roles = sorted({entry["role"] for entry in entries})
    missing = [entry for entry in entries if entry["status"] == "missing"]
    return {
        "kind": "code_brigade_greenfield_model_guidance_ledger",
        "contract_version": "eye-mechanicum.v1",
        "status": "complete" if not missing else "partial",
        "role_count": len(roles),
        "roles": roles,
        "entries": entries,
        "missing_entry_count": len(missing),
    }


def extract_project_spec(task: str, request_guidance=request_greenfield_model_guidance) -> dict[str, Any]:
    marker = "CERAXIA_PROJECT:"
    if marker not in task:
        brief = build_greenfield_project_brief(task, request_guidance=request_guidance)
        return {
            "source": "greenfield_project_brief",
            "files": brief["files"],
            "verification_commands": brief["verification_commands"],
            "summary": brief["architecture_plan"]["summary"],
            "greenfield_project_brief": brief,
        }
    raw = task.split(marker, 1)[1].strip()
    payload, _ = json.JSONDecoder().raw_decode(raw)
    if not isinstance(payload, dict):
        raise ValueError("CERAXIA_PROJECT payload must be a JSON object")
    brief = build_greenfield_project_brief(task, payload, request_guidance)
    return {
        "source": "explicit_ceraxia_project",
        "files": brief["files"],
        "verification_commands": brief["verification_commands"],
        "summary": brief["architecture_plan"]["summary"],
        "greenfield_project_brief": brief,
    }


def infer_minimal_project_spec(task: str) -> dict[str, Any]:
    lowered = task.lower()
    project_name = "ceraxia_project"
    name_match = re.search(r"`([^`/\\]+)`", task)
    if name_match:
        project_name = re.sub(r"[^A-Za-z0-9_-]+", "-", name_match.group(1)).strip("-") or project_name
    if any(word in lowered for word in ("api", "http", "fastapi", "server", "сервер", "апи")):
        app_content = (
            "def health():\n"
            "    return {\"ok\": True}\n\n"
            "if __name__ == \"__main__\":\n"
            "    print(health())\n"
        )
        test_content = (
            "import unittest\n"
            "import app\n\n"
            "class AppTests(unittest.TestCase):\n"
            "    def test_health(self):\n"
            "        self.assertEqual(app.health(), {\"ok\": True})\n"
        )
        summary = "minimal Python service scaffold"
    else:
        app_content = (
            "def main():\n"
            "    return \"ready\"\n\n"
            "if __name__ == \"__main__\":\n"
            "    print(main())\n"
        )
        test_content = (
            "import unittest\n"
            "import app\n\n"
            "class AppTests(unittest.TestCase):\n"
            "    def test_main(self):\n"
            "        self.assertEqual(app.main(), \"ready\")\n"
        )
        summary = "minimal Python application scaffold"
    return {
        "source": "inferred_minimal_python_project",
        "summary": summary,
        "files": [
            {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
            {"path": "README.md", "content": f"# {project_name}\n\nGenerated by Ceraxia CodeBrigade greenfield project mode.\n"},
            {"path": "app.py", "content": app_content},
            {"path": "test_app.py", "content": test_content},
        ],
        "verification_commands": ["python -m unittest test_app.py", "python -m py_compile app.py"],
    }


def validate_greenfield_project_brief(brief: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if brief.get("kind") != "code_brigade_greenfield_project_brief":
        problems.append("greenfield_project_brief kind is required")
    if brief.get("contract_version") != "eye-mechanicum.v1":
        problems.append("greenfield_project_brief contract_version is unsupported")
    if brief.get("project_type") not in PROJECT_TYPES:
        problems.append("greenfield_project_brief project_type is unsupported")
    if not isinstance(brief.get("stack"), dict) or not brief.get("stack"):
        problems.append("greenfield_project_brief stack is required")
    if not isinstance(brief.get("entrypoints"), list) or not brief.get("entrypoints"):
        problems.append("greenfield_project_brief entrypoints are required")
    if not isinstance(brief.get("expected_files"), list) or not brief.get("expected_files"):
        problems.append("greenfield_project_brief expected_files are required")
    if not isinstance(brief.get("verification_commands"), list):
        problems.append("greenfield_project_brief verification_commands must be a list")
    if not isinstance(brief.get("run_commands"), list) or not brief.get("run_commands"):
        problems.append("greenfield_project_brief run_commands are required")
    if not isinstance(brief.get("artifact_contract"), dict) or not brief.get("artifact_contract"):
        problems.append("greenfield_project_brief artifact_contract is required")
    if not isinstance(brief.get("workspace_policy"), dict) or brief.get("workspace_policy", {}).get("marker") != GREENFIELD_MARKER:
        problems.append("greenfield_project_brief workspace_policy marker is required")
    if not isinstance(brief.get("definition_of_done"), list) or not brief.get("definition_of_done"):
        problems.append("greenfield_project_brief definition_of_done is required")
    if not isinstance(brief.get("architecture_plan"), dict) or not brief.get("architecture_plan"):
        problems.append("greenfield_project_brief architecture_plan is required")
    if not isinstance(brief.get("file_tree_plan"), list) or not brief.get("file_tree_plan"):
        problems.append("greenfield_project_brief file_tree_plan is required")
    if not isinstance(brief.get("module_contracts"), list) or not brief.get("module_contracts"):
        problems.append("greenfield_project_brief module_contracts are required")
    implementation_plan = brief.get("implementation_plan") if isinstance(brief.get("implementation_plan"), dict) else {}
    if not implementation_plan:
        problems.append("greenfield_project_brief implementation_plan is required")
    elif implementation_plan.get("role") != "GreenfieldImplementationWorker":
        problems.append("greenfield_project_brief implementation_plan role is required")
    else:
        synthesis_policy = implementation_plan.get("synthesis_policy") if isinstance(implementation_plan.get("synthesis_policy"), dict) else {}
        if synthesis_policy.get("mode") != "module_by_module_llm_contract":
            problems.append("greenfield_project_brief implementation_plan synthesis_policy is required")
        module_sequence = implementation_plan.get("module_sequence") if isinstance(implementation_plan.get("module_sequence"), list) else []
        for module in module_sequence:
            if not isinstance(module, dict):
                continue
            path = str(module.get("path") or "")
            synthesis_contract = module.get("code_synthesis_contract") if isinstance(module.get("code_synthesis_contract"), dict) else {}
            if synthesis_contract.get("kind") != "code_brigade_greenfield_module_synthesis_contract":
                problems.append(f"greenfield_project_brief module synthesis contract is required: {path}")
            elif synthesis_contract.get("path") != path:
                problems.append(f"greenfield_project_brief module synthesis contract path mismatch: {path}")
    implementation_trace = brief.get("implementation_trace") if isinstance(brief.get("implementation_trace"), dict) else {}
    if not implementation_trace:
        problems.append("greenfield_project_brief implementation_trace is required")
    elif implementation_trace.get("kind") != "code_brigade_greenfield_implementation_trace":
        problems.append("greenfield_project_brief implementation_trace kind is required")
    elif not isinstance(implementation_trace.get("rows"), list):
        problems.append("greenfield_project_brief implementation_trace rows are required")
    else:
        for row in implementation_trace.get("rows", []):
            if not isinstance(row, dict):
                continue
            if row.get("synthesis_contract_kind") != "code_brigade_greenfield_module_synthesis_contract":
                problems.append(f"greenfield_project_brief implementation_trace synthesis contract is required: {row.get('file') or ''}")
    if not isinstance(brief.get("implementation_feature_report"), dict) or not brief.get("implementation_feature_report"):
        problems.append("greenfield_project_brief implementation_feature_report is required")
    scenario_plan = brief.get("scenario_plan") if isinstance(brief.get("scenario_plan"), dict) else {}
    if not scenario_plan:
        problems.append("greenfield_project_brief scenario_plan is required")
    elif scenario_plan.get("kind") != "code_brigade_greenfield_scenario_plan":
        problems.append("greenfield_project_brief scenario_plan kind is required")
    elif not isinstance(scenario_plan.get("rows"), list) or not scenario_plan.get("rows"):
        problems.append("greenfield_project_brief scenario_plan rows are required")
    if not isinstance(brief.get("verification_plan"), dict) or not brief.get("verification_plan"):
        problems.append("greenfield_project_brief verification_plan is required")
    return problems


def execute_greenfield_project_brief(brief: dict[str, Any], request_guidance=request_greenfield_model_guidance) -> dict[str, Any]:
    repo = Path(str(brief.get("repo_path") or ""))
    workspace = greenfield_workspace_status(repo)
    if not workspace["repo_exists"] or not workspace["repo_is_dir"]:
        return build_blocked_execution_result(["greenfield repo_path must be an existing directory"], workspace)
    if not workspace["greenfield_allowed"]:
        return build_blocked_execution_result(
            ["greenfield project creation requires an empty directory or .ceraxia_greenfield_workspace marker"],
            workspace,
        )
    try:
        spec = extract_project_spec(str(brief.get("task") or ""), request_guidance)
        project_brief = spec.get("greenfield_project_brief") if isinstance(spec.get("greenfield_project_brief"), dict) else {}
        contract_problems = validate_greenfield_project_brief(project_brief)
        if contract_problems:
            return build_blocked_execution_result(contract_problems, workspace)
        files = list(spec.get("files") if isinstance(spec.get("files"), list) else [])
        if project_brief and not any(isinstance(item, dict) and item.get("path") == "greenfield_project_brief.json" for item in files):
            files.append({"path": "greenfield_project_brief.json", "content": json.dumps(project_brief, ensure_ascii=False, indent=2, sort_keys=True) + "\n"})
        rows = normalize_project_file_rows(files)
    except (ValueError, SyntaxError, json.JSONDecodeError) as exc:
        return build_blocked_execution_result([str(exc)], workspace)
    scaffold_report = scaffold_greenfield_files(repo, rows, workspace)
    if scaffold_report.get("status") != "implemented":
        return scaffold_report
    operation_results = scaffold_report.get("operation_results", []) if isinstance(scaffold_report.get("operation_results"), list) else []
    changed_files = [str(path) for path in scaffold_report.get("changed_files", []) if isinstance(path, str)]
    file_set_synthesis_report = execute_file_set_synthesis_contract(repo, project_brief, request_guidance)
    operation_results.extend(
        item
        for item in file_set_synthesis_report.get("operation_results", [])
        if isinstance(item, dict)
    )
    changed_files = sorted(set(changed_files + [str(path) for path in file_set_synthesis_report.get("changed_files", []) if isinstance(path, str)]))
    operation_results.append(write_greenfield_json_artifact(repo, "greenfield_file_set_synthesis_report.json", file_set_synthesis_report))
    if "greenfield_file_set_synthesis_report.json" not in changed_files:
        changed_files.append("greenfield_file_set_synthesis_report.json")
    implementation_synthesis_report = execute_module_synthesis_contracts(repo, project_brief, request_guidance)
    operation_results.extend(
        item
        for item in implementation_synthesis_report.get("operation_results", [])
        if isinstance(item, dict)
    )
    changed_files = sorted(set(changed_files + [str(path) for path in implementation_synthesis_report.get("changed_files", []) if isinstance(path, str)]))
    operation_results.append(write_greenfield_json_artifact(repo, "greenfield_module_synthesis_report.json", implementation_synthesis_report))
    if "greenfield_module_synthesis_report.json" not in changed_files:
        changed_files.append("greenfield_module_synthesis_report.json")
    dependency_report = run_dependency_worker(repo, project_brief)
    commands = spec.get("verification_commands") if isinstance(spec.get("verification_commands"), list) else []
    verification_loop = run_greenfield_verification_loop(repo, [str(command) for command in commands if isinstance(command, str)], project_brief, request_guidance=request_guidance)
    verification = verification_loop.get("final_verification", {}) if isinstance(verification_loop.get("final_verification"), dict) else {}
    greenfield_review = review_greenfield_project(repo, project_brief, dependency_report, verification, request_guidance)
    greenfield_memory_record = build_greenfield_memory_record(project_brief, dependency_report, verification_loop, greenfield_review, implementation_synthesis_report, file_set_synthesis_report)
    greenfield_model_guidance_ledger = build_greenfield_model_guidance_ledger(project_brief, file_set_synthesis_report, implementation_synthesis_report, verification_loop, greenfield_review)
    greenfield_run_report = build_greenfield_run_report(project_brief, file_set_synthesis_report, implementation_synthesis_report, dependency_report, verification_loop, greenfield_review, greenfield_memory_record, greenfield_model_guidance_ledger)
    for artifact_path, artifact_payload in (
        ("greenfield_memory_record.json", greenfield_memory_record),
        ("greenfield_model_guidance_ledger.json", greenfield_model_guidance_ledger),
        ("greenfield_run_report.json", greenfield_run_report),
    ):
        operation_results.append(write_greenfield_json_artifact(repo, artifact_path, artifact_payload))
    result = build_implemented_execution_result(
        changed_files,
        f"created {len(changed_files)} greenfield project files from {spec.get('source')}",
        workspace,
        operation_results,
        scaffold_report.get("patch_manifest", build_patch_manifest(changed_files, operation_results, "")),
    )
    result["greenfield_project"] = {
        "kind": "code_brigade_greenfield_project_result",
        "spec_source": spec.get("source", ""),
        "summary": spec.get("summary", ""),
        "workspace": workspace,
        "greenfield_project_brief": project_brief,
        "architecture_plan": project_brief.get("architecture_plan", {}) if isinstance(project_brief, dict) else {},
        "file_tree_plan": project_brief.get("file_tree_plan", []) if isinstance(project_brief, dict) else [],
        "module_contracts": project_brief.get("module_contracts", []) if isinstance(project_brief, dict) else [],
        "implementation_plan": project_brief.get("implementation_plan", {}) if isinstance(project_brief, dict) else {},
        "implementation_trace": project_brief.get("implementation_trace", {}) if isinstance(project_brief, dict) else {},
        "implementation_feature_report": project_brief.get("implementation_feature_report", {}) if isinstance(project_brief, dict) else {},
        "scenario_plan": project_brief.get("scenario_plan", {}) if isinstance(project_brief, dict) else {},
        "file_set_synthesis_report": file_set_synthesis_report,
        "implementation_synthesis_report": implementation_synthesis_report,
        "dependency_plan": project_brief.get("dependency_plan", {}) if isinstance(project_brief, dict) else {},
        "verification_plan": project_brief.get("verification_plan", {}) if isinstance(project_brief, dict) else {},
        "dependency_report": dependency_report,
        "verification_loop": verification_loop,
        "greenfield_review": greenfield_review,
        "greenfield_memory_record": greenfield_memory_record,
        "greenfield_model_guidance_ledger": greenfield_model_guidance_ledger,
        "greenfield_run_report": greenfield_run_report,
        "verification": verification,
    }
    result["verification_commands_executed"] = [
        str(item.get("command") or "")
        for item in verification.get("results", [])
        if isinstance(item, dict) and item.get("status") in {"passed", "failed", "blocked", "skipped"}
    ]
    if dependency_report.get("status") == "blocked":
        result["status"] = "blocked"
        result["blockers"] = ["greenfield dependency worker blocked"]
    elif greenfield_review.get("status") == "blocked":
        result["status"] = "blocked"
        result["blockers"] = ["greenfield review blocked"]
    elif verification.get("status") not in {"passed", "planned"}:
        result["status"] = "blocked"
        result["blockers"] = ["greenfield project verification failed"]
    return result
