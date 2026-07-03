#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

GuidanceFn = Callable[[str, dict[str, Any], str], dict[str, Any]]


def build_implementation_worker_plan(
    task: str,
    template_id: str,
    module_contracts: list[Any],
    expected_files: list[str],
    request_guidance: GuidanceFn | None = None,
) -> dict[str, Any]:
    source_files = [
        path
        for path in expected_files
        if path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css"))
        and "/tests/" not in f"/{path}"
        and not Path(path).name.startswith("test_")
    ]
    test_files = [path for path in expected_files if "test" in Path(path).name.lower() or "/tests/" in f"/{path}"]
    rows: list[dict[str, Any]] = []
    for index, contract in enumerate(module_contracts, start=1):
        if not isinstance(contract, dict):
            continue
        path = str(contract.get("path") or "")
        requirements = [str(item) for item in contract.get("requirements", []) if isinstance(item, str)]
        row = {
            "sequence": index,
            "module": str(contract.get("module") or ""),
            "path": path,
            "responsibility": str(contract.get("responsibility") or ""),
            "requirements": requirements,
            "requirement_trace": [
                {
                    "requirement": requirement,
                    "file": path,
                    "function_or_component": infer_symbol_name(path, requirement),
                    "verification_files": test_files,
                }
                for requirement in requirements
            ],
            "paired_tests": [test for test in test_files if paired_test_matches(path, test)] or test_files[:1],
            "code_synthesis_contract": build_module_code_synthesis_contract(
                task=task,
                template_id=template_id,
                module=str(contract.get("module") or ""),
                path=path,
                responsibility=str(contract.get("responsibility") or ""),
                requirements=requirements,
                test_files=[test for test in test_files if paired_test_matches(path, test)] or test_files[:1],
            ),
            "status": "planned_for_implementation",
        }
        rows.append(row)
    if request_guidance is None:
        implementation_guidance: dict[str, Any] = {"status": "not_requested", "reason": "no guidance callback supplied"}
    else:
        implementation_guidance = request_guidance(
            "GreenfieldImplementationWorker",
            {
                "task": task,
                "template_id": template_id,
                "module_contracts": module_contracts,
                "source_files": source_files,
                "test_files": test_files,
            },
            "Plan module-by-module implementation from contracts. Preserve requirement to file/function/test trace and reject empty placeholder work.",
        )
    return {
        "kind": "code_brigade_greenfield_implementation_plan",
        "contract_version": "eye-mechanicum.v1",
        "role": "GreenfieldImplementationWorker",
        "template_id": template_id,
        "module_sequence": rows,
        "milestones": [
            {"name": "scaffold", "exit_gate": "workspace marker, manifests, README, entrypoints, and test folders exist"},
            {"name": "module_implementation", "exit_gate": "each module contract has source code and requirement trace"},
            {"name": "module_synthesis_validation", "exit_gate": "each module has code_synthesis_contract, validation gates, and rollback scope"},
            {"name": "verification", "exit_gate": "allowlisted tests/build/smoke commands pass or return a clear blocker"},
        ],
        "synthesis_policy": {
            "mode": "module_by_module_llm_contract",
            "model_output_format": "json_object",
            "required_output_fields": ["path", "content", "requirements_satisfied", "tests_to_update", "notes"],
            "reject_when": [
                "output path differs from module contract path",
                "requirements_satisfied omits any module requirement",
                "content contains forbidden placeholder markers",
                "tests_to_update omits paired tests when tests exist",
            ],
        },
        "anti_stub_policy": {
            "forbidden_markers": ["TODO", "pass #", "NotImplementedError", "placeholder"],
            "minimum_nonempty_source_files": len(source_files),
            "minimum_test_files": len(test_files),
        },
        "source_files": source_files,
        "test_files": test_files,
        "model_guidance": implementation_guidance,
    }


def build_module_code_synthesis_contract(
    task: str,
    template_id: str,
    module: str,
    path: str,
    responsibility: str,
    requirements: list[str],
    test_files: list[str],
) -> dict[str, Any]:
    return {
        "kind": "code_brigade_greenfield_module_synthesis_contract",
        "contract_version": "eye-mechanicum.v1",
        "role": "GreenfieldImplementationWorker",
        "template_id": template_id,
        "task_excerpt": task[:500],
        "module": module,
        "path": path,
        "responsibility": responsibility,
        "requirements": requirements,
        "paired_tests": test_files,
        "model_request": {
            "instructions": "Implement exactly this module contract. Return JSON only. Do not include unrelated files or placeholders.",
            "output_schema": {
                "type": "object",
                "required": ["path", "content", "requirements_satisfied", "tests_to_update", "notes"],
            },
        },
        "validation_gates": [
            "path matches module contract path",
            "content is non-empty",
            "all requirements are listed in requirements_satisfied",
            "paired tests are listed in tests_to_update when tests exist",
            "forbidden placeholder markers are absent",
        ],
        "rollback_scope": {
            "max_source_files": 1,
            "allowed_source_files": [path] if path else [],
            "allowed_test_files": test_files,
        },
    }


def build_implementation_trace(implementation_plan: dict[str, Any]) -> dict[str, Any]:
    module_sequence = implementation_plan.get("module_sequence") if isinstance(implementation_plan.get("module_sequence"), list) else []
    rows: list[dict[str, Any]] = []
    for module in module_sequence:
        if not isinstance(module, dict):
            continue
        trace_rows = module.get("requirement_trace") if isinstance(module.get("requirement_trace"), list) else []
        paired_tests = [str(path) for path in module.get("paired_tests", []) if isinstance(path, str)]
        for trace in trace_rows:
            if not isinstance(trace, dict):
                continue
            rows.append(
                {
                    "module": str(module.get("module") or ""),
                    "requirement": str(trace.get("requirement") or ""),
                    "file": str(trace.get("file") or module.get("path") or ""),
                    "function_or_component": str(trace.get("function_or_component") or ""),
                    "verification_files": [str(path) for path in trace.get("verification_files", []) if isinstance(path, str)],
                    "paired_tests": paired_tests,
                    "synthesis_contract_kind": module.get("code_synthesis_contract", {}).get("kind", "") if isinstance(module.get("code_synthesis_contract"), dict) else "",
                    "status": "planned",
                }
            )
    return {
        "kind": "code_brigade_greenfield_implementation_trace",
        "contract_version": "eye-mechanicum.v1",
        "status": "complete" if rows else "empty",
        "requirement_trace_count": len(rows),
        "module_count": len([row for row in module_sequence if isinstance(row, dict)]),
        "rows": rows,
    }


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty model content")
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model content must be a JSON object")
    return parsed


def forbidden_markers_found(content: str, markers: list[str]) -> list[str]:
    lowered = content.lower()
    found: list[str] = []
    for marker in markers:
        needle = marker.lower()
        if needle == "todo":
            if "todo " in lowered or "todo:" in lowered or "# todo" in lowered or "// todo" in lowered:
                found.append(marker)
        elif needle and needle in lowered:
            found.append(marker)
    return found


def safe_repo_relative_path(repo: Path, rel_path: str) -> Path | None:
    if not rel_path or Path(rel_path).is_absolute():
        return None
    target = (repo / rel_path).resolve()
    try:
        target.relative_to(repo.resolve())
    except ValueError:
        return None
    return target


def validate_module_synthesis_output(
    output: dict[str, Any],
    module_contract: dict[str, Any],
    forbidden_markers: list[str],
) -> list[str]:
    problems: list[str] = []
    expected_path = str(module_contract.get("path") or "")
    if output.get("path") != expected_path:
        problems.append(f"path mismatch: expected {expected_path}")
    content = output.get("content")
    if not isinstance(content, str) or not content.strip():
        problems.append("content is required")
    elif forbidden_markers_found(content, forbidden_markers):
        problems.append("content contains forbidden placeholder marker")
    requirements = [str(item) for item in module_contract.get("requirements", []) if isinstance(item, str)]
    satisfied = [str(item) for item in output.get("requirements_satisfied", []) if isinstance(item, str)] if isinstance(output.get("requirements_satisfied"), list) else []
    missing = [requirement for requirement in requirements if requirement not in satisfied]
    if missing:
        problems.append("requirements_satisfied is incomplete: " + ", ".join(missing))
    paired_tests = [str(item) for item in module_contract.get("paired_tests", []) if isinstance(item, str)]
    tests_to_update = [str(item) for item in output.get("tests_to_update", []) if isinstance(item, str)] if isinstance(output.get("tests_to_update"), list) else []
    if paired_tests and not tests_to_update:
        problems.append("tests_to_update is required when paired tests exist")
    unexpected_tests = [path for path in tests_to_update if path not in paired_tests]
    if unexpected_tests:
        problems.append("tests_to_update contains paths outside paired tests: " + ", ".join(unexpected_tests))
    return problems


def execute_module_synthesis_contracts(
    repo: Path,
    project_brief: dict[str, Any],
    request_guidance: GuidanceFn | None = None,
    *,
    synthesis_stage: str = "initial_module_synthesis",
    verification_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    implementation_plan = project_brief.get("implementation_plan") if isinstance(project_brief.get("implementation_plan"), dict) else {}
    module_sequence = implementation_plan.get("module_sequence") if isinstance(implementation_plan.get("module_sequence"), list) else []
    forbidden_markers = [
        str(item)
        for item in implementation_plan.get("anti_stub_policy", {}).get("forbidden_markers", [])
        if isinstance(item, str)
    ]
    rows: list[dict[str, Any]] = []
    operation_results: list[dict[str, Any]] = []
    changed_files: list[str] = []
    for module in module_sequence:
        if not isinstance(module, dict):
            continue
        synthesis_contract = module.get("code_synthesis_contract") if isinstance(module.get("code_synthesis_contract"), dict) else {}
        rel_path = str(module.get("path") or synthesis_contract.get("path") or "")
        row: dict[str, Any] = {
            "module": str(module.get("module") or ""),
            "path": rel_path,
            "synthesis_stage": synthesis_stage,
            "status": "skipped",
            "model_guidance_status": "not_requested",
            "blockers": [],
            "warnings": [],
        }
        target = safe_repo_relative_path(repo, rel_path)
        if target is None:
            row["status"] = "blocked"
            row["blockers"].append("module path is outside workspace")
            rows.append(row)
            continue
        if request_guidance is None:
            row["warnings"].append("no model guidance callback supplied")
            rows.append(row)
            continue
        guidance = request_guidance(
            "GreenfieldImplementationWorker",
            {
                "project_name": project_brief.get("project_name"),
                "project_type": project_brief.get("project_type"),
                "template_id": project_brief.get("template_id"),
                "module_synthesis_contract": synthesis_contract,
                "existing_content": target.read_text(encoding="utf-8") if target.exists() and target.is_file() else "",
                "verification_context": verification_context or {},
            },
            (
                "Repair this single module using the verification failure context. Return JSON only with path, content, "
                "requirements_satisfied, tests_to_update, and notes. Preserve the module contract and do not edit unrelated files."
                if synthesis_stage == "verification_repair"
                else "Implement this single module synthesis contract. Return JSON only with path, content, requirements_satisfied, tests_to_update, and notes."
            ),
        )
        row["model_guidance_status"] = str(guidance.get("status") or "")
        row["model_guidance_ok"] = bool(guidance.get("ok"))
        content = str(guidance.get("content") or "")
        if not guidance.get("ok"):
            row["status"] = "model_unavailable"
            row["blockers"].append(str(guidance.get("error") or "model guidance unavailable"))
            rows.append(row)
            continue
        try:
            output = extract_json_object(content)
        except (ValueError, json.JSONDecodeError) as exc:
            row["status"] = "rejected"
            row["blockers"].append(f"model output is not valid JSON object: {exc}")
            rows.append(row)
            continue
        problems = validate_module_synthesis_output(output, module, forbidden_markers)
        if problems:
            row["status"] = "rejected"
            row["blockers"].extend(problems)
            rows.append(row)
            continue
        before = target.read_bytes() if target.exists() else b""
        rendered_content = str(output["content"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered_content, encoding="utf-8")
        after = rendered_content.encode("utf-8")
        operation_results.append(
            {
                "operation": "greenfield_module_synthesis_write",
                "path": rel_path,
                "status": "applied",
                "before_sha256": hashlib.sha256(before).hexdigest() if before else "",
                "after_sha256": hashlib.sha256(after).hexdigest(),
            }
        )
        changed_files.append(rel_path)
        row.update(
            {
                "status": "applied",
                "requirements_satisfied": output.get("requirements_satisfied", []),
                "tests_to_update": output.get("tests_to_update", []),
                "notes": str(output.get("notes") or ""),
            }
        )
        rows.append(row)
    blocking_statuses = {"blocked", "rejected"}
    applied_count = sum(1 for row in rows if row.get("status") == "applied")
    unavailable_count = sum(1 for row in rows if row.get("status") == "model_unavailable")
    blocked_count = sum(1 for row in rows if row.get("status") in blocking_statuses)
    if blocked_count:
        status = "blocked"
    elif applied_count:
        status = "applied"
    elif unavailable_count:
        status = "model_unavailable"
    else:
        status = "skipped"
    return {
        "kind": "code_brigade_greenfield_module_synthesis_report",
        "contract_version": "eye-mechanicum.v1",
        "synthesis_stage": synthesis_stage,
        "verification_context_status": str((verification_context or {}).get("status") or ""),
        "status": status,
        "module_count": len(rows),
        "applied_count": applied_count,
        "model_unavailable_count": unavailable_count,
        "blocked_count": blocked_count,
        "changed_files": sorted(set(changed_files)),
        "operation_results": operation_results,
        "rows": rows,
    }


def infer_symbol_name(path: str, requirement: str) -> str:
    name = Path(path).stem
    lowered = f"{path} {requirement}".lower()
    if path.endswith((".html", ".css")):
        return name
    if path.endswith((".js", ".jsx", ".ts", ".tsx")):
        if "component" in lowered or "render" in lowered:
            return "component"
        return name
    if "cli" in lowered:
        return "main"
    if "health" in lowered:
        return "health"
    if "reply" in lowered:
        return "build_reply"
    if "summary" in lowered or "csv" in lowered:
        return "summarize_rows"
    if "structured" in lowered:
        return "build_tool_result"
    if "describe" in lowered:
        return "describe"
    if "ready" in lowered or "result" in lowered:
        return "run"
    return name


def paired_test_matches(source_path: str, test_path: str) -> bool:
    source_name = Path(source_path).stem.lower()
    test_name = Path(test_path).stem.lower()
    return source_name in test_name or source_name in test_path.lower()
