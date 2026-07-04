#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import ast
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
        paired_tests = paired_tests_for_module(path, requirements, test_files)
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
                    "verification_files": paired_tests,
                }
                for requirement in requirements
            ],
            "paired_tests": paired_tests,
            "code_synthesis_contract": build_module_code_synthesis_contract(
                task=task,
                template_id=template_id,
                module=str(contract.get("module") or ""),
                path=path,
                responsibility=str(contract.get("responsibility") or ""),
                requirements=requirements,
                test_files=paired_tests,
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


def generated_file_quality(path: str, content: str, requirements: list[str], project_brief: dict[str, Any] | None = None) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    score = 100
    project_brief = project_brief or {}
    template_id = str(project_brief.get("template_id") or "")
    stripped = content.strip()
    path_lower = path.lower()
    is_test = "test" in Path(path).name.lower() or "/tests/" in f"/{path_lower}"
    if not stripped:
        blockers.append("generated file is empty")
        score -= 80
    if len(stripped.splitlines()) < 2 and requirements:
        warnings.append("generated file is very short for a requirement-bearing contract")
        score -= 20
    if is_test:
        assertion_markers = ("assert", "expect(", "pytest.raises", "unittest")
        test_markers = ("def test_", "it(", "test(")
        if not any(marker in content for marker in assertion_markers):
            blockers.append("generated test file has no assertion")
            score -= 60
        if not any(marker in content for marker in test_markers):
            warnings.append("generated test file has no explicit test case marker")
            score -= 20
    elif path.endswith(".py"):
        try:
            tree = ast.parse(content)
        except SyntaxError:
            blockers.append("generated Python source has syntax error")
            score -= 80
        else:
            executable_nodes = [
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.Return, ast.If, ast.For, ast.While, ast.Try))
            ]
            init_facade = Path(path).name == "__init__.py" and ("import " in content or "__all__" in content)
            if len(executable_nodes) < 2 and requirements and not init_facade:
                blockers.append("generated Python source is semantically too weak")
                score -= 50
    elif path.endswith((".js", ".jsx", ".ts", ".tsx")):
        if all(marker not in content for marker in ("function", "=>", "export", "class", "const ", "let ")):
            blockers.append("generated JavaScript/TypeScript source has no executable structure")
            score -= 50
    elif path.endswith(".html"):
        if "<" not in content or ">" not in content:
            blockers.append("generated HTML has no markup")
            score -= 50
    domain_blockers, domain_warnings, domain_penalty = domain_specific_quality_findings(template_id, path, content, is_test)
    blockers.extend(domain_blockers)
    warnings.extend(domain_warnings)
    score -= domain_penalty
    return {
        "status": "blocked" if blockers or score < 50 else "passed",
        "score": max(0, min(100, score)),
        "blockers": blockers,
        "warnings": warnings,
    }


def domain_specific_quality_findings(template_id: str, path: str, content: str, is_test: bool) -> tuple[list[str], list[str], int]:
    blockers: list[str] = []
    warnings: list[str] = []
    penalty = 0
    lowered = content.lower()
    if template_id == "python_fastapi_service" and path == "app/main.py":
        if "fastapi" not in lowered and "app =" not in lowered:
            blockers.append("FastAPI service source does not expose an app")
            penalty += 50
        if "/health" not in content and "health" not in lowered:
            blockers.append("FastAPI service source has no health route or health function")
            penalty += 40
    if template_id in {"static_site", "node_vite_app"} and not is_test:
        if path.endswith(".html") and ("<main" not in lowered and "<body" not in lowered):
            blockers.append("frontend HTML lacks a renderable body/main surface")
            penalty += 40
        ui_script = Path(path).stem.lower() not in {"state", "store", "model", "domain"}
        if ui_script and path.endswith((".js", ".jsx", ".ts", ".tsx")) and all(marker not in content for marker in ("document", "createRoot", "addEventListener", "useState", "render", "return <")):
            blockers.append("frontend script lacks render or interaction behavior")
            penalty += 40
        if path.endswith(".css") and "{" not in content:
            warnings.append("frontend stylesheet has no rule block")
            penalty += 10
    if template_id == "telegram_bot_python" and path.endswith("/bot.py"):
        if "token" not in lowered and "telegram_bot_token" not in lowered:
            blockers.append("Telegram bot runtime does not handle token configuration")
            penalty += 40
        if not any(marker in lowered for marker in ("build_reply", "handle", "/start", "/help", "command")):
            blockers.append("Telegram bot source lacks testable command/reply handling")
            penalty += 40
    if template_id == "data_processing_tool" and (path.endswith("/processor.py") or path.endswith("/cli.py")):
        if path.endswith("/processor.py") and "csv" not in lowered:
            blockers.append("data processor does not parse CSV data")
            penalty += 45
        if path.endswith("/processor.py") and not any(marker in lowered for marker in ("summary", "summarize", "rows", "columns")):
            blockers.append("data processor does not produce a summary")
            penalty += 35
        if path.endswith("/cli.py") and not any(marker in content for marker in ("sys.argv", "argparse", "Path(")):
            warnings.append("data CLI has weak argument/file handling")
            penalty += 15
    if template_id == "local_agent_tool" and (path.endswith("/contract.py") or path.endswith("/tool.py")):
        if path.endswith("/contract.py") and "dict" not in content and "{" not in content:
            blockers.append("local agent contract does not return structured data")
            penalty += 45
        if path.endswith("/contract.py") and not any(marker in lowered for marker in ("status", "action", "task", "result")):
            blockers.append("local agent contract lacks status/action/task/result semantics")
            penalty += 35
        if path.endswith("/tool.py") and not any(marker in lowered for marker in ("main", "print", "json", "build_tool_result")):
            warnings.append("local agent tool entrypoint has weak executable behavior")
            penalty += 15
    return blockers, warnings, penalty


def validate_module_synthesis_output(
    output: dict[str, Any],
    module_contract: dict[str, Any],
    forbidden_markers: list[str],
    project_brief: dict[str, Any] | None = None,
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
    if isinstance(content, str):
        quality = generated_file_quality(expected_path, content, requirements, project_brief)
        if quality["status"] == "blocked":
            problems.append("semantic quality blocked: " + "; ".join(str(item) for item in quality["blockers"]))
    paired_tests = [str(item) for item in module_contract.get("paired_tests", []) if isinstance(item, str)]
    tests_to_update = [str(item) for item in output.get("tests_to_update", []) if isinstance(item, str)] if isinstance(output.get("tests_to_update"), list) else []
    if paired_tests and not tests_to_update:
        problems.append("tests_to_update is required when paired tests exist")
    unexpected_tests = [path for path in tests_to_update if path not in paired_tests]
    if unexpected_tests:
        problems.append("tests_to_update contains paths outside paired tests: " + ", ".join(unexpected_tests))
    return problems


def file_set_allowed_paths(project_brief: dict[str, Any]) -> set[str]:
    implementation_plan = project_brief.get("implementation_plan") if isinstance(project_brief.get("implementation_plan"), dict) else {}
    source_files = [str(path) for path in implementation_plan.get("source_files", []) if isinstance(path, str)]
    test_files = [str(path) for path in implementation_plan.get("test_files", []) if isinstance(path, str)]
    return set(source_files + test_files)


def module_requirements_by_path(project_brief: dict[str, Any]) -> dict[str, list[str]]:
    implementation_plan = project_brief.get("implementation_plan") if isinstance(project_brief.get("implementation_plan"), dict) else {}
    module_sequence = implementation_plan.get("module_sequence") if isinstance(implementation_plan.get("module_sequence"), list) else []
    requirements: dict[str, list[str]] = {}
    for module in module_sequence:
        if not isinstance(module, dict):
            continue
        path = str(module.get("path") or "")
        if path:
            requirements[path] = [str(item) for item in module.get("requirements", []) if isinstance(item, str)]
    return requirements


def validate_file_set_synthesis_output(
    output: dict[str, Any],
    project_brief: dict[str, Any],
    forbidden_markers: list[str],
) -> list[str]:
    problems: list[str] = []
    rows = output.get("files")
    if not isinstance(rows, list) or not rows:
        return ["files list is required"]
    allowed_paths = file_set_allowed_paths(project_brief)
    requirements_by_path = module_requirements_by_path(project_brief)
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            problems.append(f"file row is not an object: {index}")
            continue
        path = str(row.get("path") or "")
        if path not in allowed_paths:
            problems.append(f"file path is outside file-set synthesis scope: {path}")
        if path in seen:
            problems.append(f"file path is duplicated: {path}")
        seen.add(path)
        content = row.get("content")
        if not isinstance(content, str) or not content.strip():
            problems.append(f"content is required: {path}")
        elif forbidden_markers_found(content, forbidden_markers):
            problems.append(f"content contains forbidden placeholder marker: {path}")
        requirements = requirements_by_path.get(path, [])
        if requirements:
            satisfied = [str(item) for item in row.get("requirements_satisfied", []) if isinstance(item, str)] if isinstance(row.get("requirements_satisfied"), list) else []
            missing = [requirement for requirement in requirements if requirement not in satisfied]
            if missing:
                problems.append(f"requirements_satisfied is incomplete for {path}: " + ", ".join(missing))
        if isinstance(content, str):
            quality = generated_file_quality(path, content, requirements, project_brief)
            if quality["status"] == "blocked":
                problems.append(f"semantic quality blocked for {path}: " + "; ".join(str(item) for item in quality["blockers"]))
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
        problems = validate_module_synthesis_output(output, module, forbidden_markers, project_brief)
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
                "semantic_quality": generated_file_quality(rel_path, rendered_content, [str(item) for item in module.get("requirements", []) if isinstance(item, str)], project_brief),
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


def execute_file_set_synthesis_contract(
    repo: Path,
    project_brief: dict[str, Any],
    request_guidance: GuidanceFn | None = None,
) -> dict[str, Any]:
    implementation_plan = project_brief.get("implementation_plan") if isinstance(project_brief.get("implementation_plan"), dict) else {}
    forbidden_markers = [
        str(item)
        for item in implementation_plan.get("anti_stub_policy", {}).get("forbidden_markers", [])
        if isinstance(item, str)
    ]
    allowed_paths = sorted(file_set_allowed_paths(project_brief))
    if not allowed_paths:
        return {
            "kind": "code_brigade_greenfield_file_set_synthesis_report",
            "contract_version": "eye-mechanicum.v1",
            "status": "skipped",
            "changed_files": [],
            "operation_results": [],
            "blockers": ["no source or test files are available for file-set synthesis"],
            "warnings": [],
        }
    if request_guidance is None:
        return {
            "kind": "code_brigade_greenfield_file_set_synthesis_report",
            "contract_version": "eye-mechanicum.v1",
            "status": "skipped",
            "changed_files": [],
            "operation_results": [],
            "blockers": [],
            "warnings": ["no model guidance callback supplied"],
        }
    existing_files = {
        path: (repo / path).read_text(encoding="utf-8") if (repo / path).exists() and (repo / path).is_file() else ""
        for path in allowed_paths
        if safe_repo_relative_path(repo, path) is not None
    }
    guidance = request_guidance(
        "GreenfieldImplementationWorker",
        {
            "project_name": project_brief.get("project_name"),
            "project_type": project_brief.get("project_type"),
            "template_id": project_brief.get("template_id"),
            "synthesis_contract": {
                "kind": "code_brigade_greenfield_file_set_synthesis_contract",
                "allowed_paths": allowed_paths,
                "module_sequence": implementation_plan.get("module_sequence", []),
                "required_output_fields": ["files", "notes"],
                "file_row_required_fields": ["path", "content", "requirements_satisfied", "notes"],
            },
            "existing_files": existing_files,
        },
        "Implement a coordinated source-and-test file set. Return JSON only with files:[{path, content, requirements_satisfied, notes}] and notes. Do not include paths outside allowed_paths.",
    )
    if not guidance.get("ok"):
        return {
            "kind": "code_brigade_greenfield_file_set_synthesis_report",
            "contract_version": "eye-mechanicum.v1",
            "status": "model_unavailable",
            "changed_files": [],
            "operation_results": [],
            "blockers": [str(guidance.get("error") or "model guidance unavailable")],
            "warnings": [],
            "model_guidance_status": str(guidance.get("status") or ""),
        }
    try:
        output = extract_json_object(str(guidance.get("content") or ""))
    except (ValueError, json.JSONDecodeError) as exc:
        return {
            "kind": "code_brigade_greenfield_file_set_synthesis_report",
            "contract_version": "eye-mechanicum.v1",
            "status": "rejected",
            "changed_files": [],
            "operation_results": [],
            "blockers": [f"model output is not valid JSON object: {exc}"],
            "warnings": [],
            "model_guidance_status": str(guidance.get("status") or ""),
        }
    problems = validate_file_set_synthesis_output(output, project_brief, forbidden_markers)
    if problems:
        return {
            "kind": "code_brigade_greenfield_file_set_synthesis_report",
            "contract_version": "eye-mechanicum.v1",
            "status": "rejected",
            "changed_files": [],
            "operation_results": [],
            "blockers": problems,
            "warnings": [],
            "model_guidance_status": str(guidance.get("status") or ""),
        }
    changed_files: list[str] = []
    operation_results: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    requirements_by_path = module_requirements_by_path(project_brief)
    for row in output.get("files", []):
        path = str(row.get("path") or "")
        target = safe_repo_relative_path(repo, path)
        if target is None:
            continue
        before = target.read_bytes() if target.exists() else b""
        rendered_content = str(row.get("content") or "")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered_content, encoding="utf-8")
        after = rendered_content.encode("utf-8")
        changed_files.append(path)
        quality_rows.append({"path": path, **generated_file_quality(path, rendered_content, requirements_by_path.get(path, []), project_brief)})
        operation_results.append(
            {
                "operation": "greenfield_file_set_synthesis_write",
                "path": path,
                "status": "applied",
                "before_sha256": hashlib.sha256(before).hexdigest() if before else "",
                "after_sha256": hashlib.sha256(after).hexdigest(),
            }
        )
    return {
        "kind": "code_brigade_greenfield_file_set_synthesis_report",
        "contract_version": "eye-mechanicum.v1",
        "status": "applied" if changed_files else "skipped",
        "changed_files": sorted(set(changed_files)),
        "changed_file_count": len(set(changed_files)),
        "allowed_paths": allowed_paths,
        "semantic_quality_rows": quality_rows,
        "semantic_quality_status": "passed" if quality_rows and all(row.get("status") == "passed" for row in quality_rows) else "blocked" if quality_rows else "skipped",
        "operation_results": operation_results,
        "blockers": [],
        "warnings": [],
        "model_guidance_status": str(guidance.get("status") or ""),
        "notes": str(output.get("notes") or ""),
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


def paired_tests_for_module(source_path: str, requirements: list[str], test_files: list[str]) -> list[str]:
    if not test_files:
        return []
    if source_path in test_files:
        return [source_path]
    direct = [test for test in test_files if paired_test_matches(source_path, test)]
    if direct:
        return direct
    integration_tests = [
        test
        for test in test_files
        if any(marker in Path(test).stem.lower() for marker in ("integration", "workflow", "pipeline", "contract", "tracker", "kanban", "board", "operations", "dashboard"))
    ]
    if integration_tests:
        return integration_tests
    requirement_tokens = {
        token
        for requirement in requirements
        for token in requirement.lower().replace("_", " ").replace("-", " ").split()
        if len(token) >= 5 and token not in {"prove", "support", "return", "print", "build", "handle"}
    }
    keyword_matches = [
        test
        for test in test_files
        if any(token in test.lower().replace("_", " ").replace("-", " ") for token in requirement_tokens)
    ]
    if keyword_matches:
        return keyword_matches
    return test_files[:1]
