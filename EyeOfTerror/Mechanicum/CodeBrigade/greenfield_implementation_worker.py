#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import ast
import re
from pathlib import Path
from typing import Any, Callable

GuidanceFn = Callable[[str, dict[str, Any], str], dict[str, Any]]


def is_test_file_path(path: str) -> bool:
    path_lower = path.lower()
    return "test" in Path(path).name.lower() or "/tests/" in f"/{path_lower}"


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
    test_files = [path for path in expected_files if is_test_file_path(path)]
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
    behavior_markers = task_behavior_markers(task)
    return {
        "kind": "code_brigade_greenfield_module_synthesis_contract",
        "contract_version": "eye-mechanicum.v1",
        "role": "GreenfieldImplementationWorker",
        "template_id": template_id,
        "task_excerpt": task[:500],
        "required_behavior_markers": behavior_markers,
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
            "behavior_marker_policy": "When required_behavior_markers is not empty, source or test output must preserve the user-visible behavior markers relevant to this module.",
        },
        "validation_gates": [
            "path matches module contract path",
            "content is non-empty",
            "all requirements are listed in requirements_satisfied",
            "paired tests are listed in tests_to_update when tests exist",
            "forbidden placeholder markers are absent",
            "required behavior markers from the task are present in generated tests and direct behavior modules",
        ],
        "rollback_scope": {
            "max_source_files": 1,
            "allowed_source_files": [path] if path else [],
            "allowed_test_files": test_files,
        },
    }


def task_behavior_markers(task: str) -> list[str]:
    markers: list[str] = []
    literal_patterns = [
        r"(?:print(?:s|ing)?|prints|return(?:s|ing)?|outputs?|emit(?:s|ting)?|echo(?:es|ing)?|печата(?:ет|ть)|вывод(?:ит|ить)|верн(?:ет|уть|и))\s+([A-Za-z0-9][A-Za-z0-9_.:-]{2,})",
    ]
    for pattern in literal_patterns:
        for match in re.finditer(pattern, task, flags=re.IGNORECASE):
            markers.append(match.group(1).strip(".,;:!?\"'()[]{}"))
    ignored = {"python", "cli", "api", "fastapi", "vite", "react", "telegram"}
    unique: list[str] = []
    for marker in markers:
        normalized = marker.strip()
        if not normalized or normalized.lower() in ignored:
            continue
        if normalized not in unique:
            unique.append(normalized)
    return unique[:8]


def module_required_behavior_markers(module_contract: dict[str, Any], project_brief: dict[str, Any] | None = None) -> list[str]:
    synthesis_contract = module_contract.get("code_synthesis_contract") if isinstance(module_contract.get("code_synthesis_contract"), dict) else {}
    markers = [
        str(item)
        for item in synthesis_contract.get("required_behavior_markers", [])
        if isinstance(item, str) and item.strip()
    ]
    if not markers and project_brief is not None:
        markers = task_behavior_markers(str(project_brief.get("task") or ""))
    return markers


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
        candidate = stripped[start : end + 1]
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as nested_exc:
            if "Invalid \\escape" not in str(nested_exc) and "Invalid control character" not in str(nested_exc):
                parsed = extract_module_synthesis_fields(candidate)
            else:
                try:
                    parsed = json.loads(repair_model_json_code_payload(candidate))
                except json.JSONDecodeError:
                    parsed = extract_module_synthesis_fields(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("model content must be a JSON object")
    return parsed


def repair_model_json_code_payload(text: str) -> str:
    valid_escapes = {'"', "\\", "/", "b", "f", "n", "r", "t", "u"}
    repaired: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if not in_string:
            repaired.append(char)
            if char == '"':
                in_string = True
            continue
        if escaped:
            if char in valid_escapes:
                repaired.append(char)
            else:
                repaired.append("\\")
                repaired.append(char)
            escaped = False
            continue
        if char == "\\":
            repaired.append("\\")
            escaped = True
            continue
        if char == '"':
            repaired.append(char)
            in_string = False
            continue
        if char == "\n":
            repaired.append("\\n")
        elif char == "\r":
            repaired.append("\\r")
        elif char == "\t":
            repaired.append("\\t")
        elif ord(char) < 0x20:
            repaired.append(f"\\u{ord(char):04x}")
        else:
            repaired.append(char)
    if escaped:
        repaired.append("\\")
    return "".join(repaired)


def extract_module_synthesis_fields(text: str) -> dict[str, Any]:
    if '"content"' not in text or '"requirements_satisfied"' not in text or '"tests_to_update"' not in text:
        raise ValueError("model content is not a recognizable module synthesis object")
    path_match = re.search(r'"path"\s*:\s*"([^"]+)"', text, flags=re.DOTALL)
    content_match = re.search(r'"content"\s*:\s*"', text, flags=re.DOTALL)
    requirements_match = re.search(r'"\s*,\s*"requirements_satisfied"\s*:', text, flags=re.DOTALL)
    if not path_match or not content_match or not requirements_match or requirements_match.start() <= content_match.end():
        raise ValueError("module synthesis fields are not recoverable")
    content_raw = text[content_match.end() : requirements_match.start()]
    suffix = text[requirements_match.end() :]
    requirements = extract_json_array_field(suffix)
    tests_key = re.search(r'"tests_to_update"\s*:', suffix, flags=re.DOTALL)
    if not tests_key:
        raise ValueError("tests_to_update field is not recoverable")
    tests_suffix = suffix[tests_key.end() :]
    tests_to_update = extract_json_array_field(tests_suffix)
    notes = ""
    notes_match = re.search(r'"notes"\s*:\s*"(.*)"\s*\}?[\s`]*$', tests_suffix, flags=re.DOTALL)
    if notes_match:
        notes = decode_json_string_fragment(notes_match.group(1))
    return {
        "path": path_match.group(1),
        "content": decode_json_string_fragment(content_raw),
        "requirements_satisfied": requirements,
        "tests_to_update": tests_to_update,
        "notes": notes,
    }


def extract_json_array_field(text: str) -> list[Any]:
    start = text.find("[")
    if start < 0:
        raise ValueError("JSON array field is missing")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                parsed = json.loads(repair_model_json_code_payload(text[start : index + 1]))
                return parsed if isinstance(parsed, list) else []
    raise ValueError("JSON array field is unterminated")


def decode_json_string_fragment(text: str) -> str:
    repaired = repair_model_json_code_payload('"' + text + '"')
    try:
        decoded = json.loads(repaired)
    except json.JSONDecodeError:
        decoded = text
    return str(decoded)


def forbidden_markers_found(content: str, markers: list[str]) -> list[str]:
    lowered = content.lower()
    found: list[str] = []
    for marker in markers:
        needle = marker.lower()
        if needle == "todo":
            if "todo:" in lowered or "# todo" in lowered or "// todo" in lowered:
                found.append(marker)
        elif needle and needle in lowered:
            found.append(marker)
    return found


def source_narration_comments(content: str) -> list[str]:
    narration_markers = (
        "test oracle",
        "verification failure",
        "previous implementation",
        "current implementation",
        "match the oracle",
        "match the test",
        "to satisfy the test",
        "test expects",
    )
    findings: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("#", "//")):
            continue
        lowered = stripped.lower()
        if any(marker in lowered for marker in narration_markers):
            findings.append(stripped[:160])
    return findings


def safe_repo_relative_path(repo: Path, rel_path: str) -> Path | None:
    if not rel_path or Path(rel_path).is_absolute():
        return None
    target = (repo / rel_path).resolve()
    try:
        target.relative_to(repo.resolve())
    except ValueError:
        return None
    return target


def test_oracle_snapshots(repo: Path, module_contract: dict[str, Any], *, max_chars_per_file: int = 6000) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for rel_path in module_contract.get("paired_tests", []):
        if not isinstance(rel_path, str) or not rel_path:
            continue
        target = safe_repo_relative_path(repo, rel_path)
        if target is None or not target.exists() or not target.is_file():
            snapshots.append({"path": rel_path, "status": "missing"})
            continue
        content = target.read_text(encoding="utf-8")
        snapshots.append(
            {
                "path": rel_path,
                "status": "captured",
                "content": content[:max_chars_per_file],
                "truncated": len(content) > max_chars_per_file,
            }
        )
    return snapshots


def generated_file_quality(path: str, content: str, requirements: list[str], project_brief: dict[str, Any] | None = None) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    score = 100
    project_brief = project_brief or {}
    template_id = str(project_brief.get("template_id") or "")
    stripped = content.strip()
    path_lower = path.lower()
    is_test = is_test_file_path(path)
    if not stripped:
        blockers.append("generated file is empty")
        score -= 80
    if not is_test:
        narration = source_narration_comments(content)
        if narration:
            blockers.append("generated source contains test/repair narration comments: " + "; ".join(narration[:3]))
            score -= 50
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
        contract_delegates_to_structured_runner = "run_action(" in content or "run_sequence(" in content
        if path.endswith("/contract.py") and "dict" not in content and "{" not in content and not contract_delegates_to_structured_runner:
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
        is_test = is_test_file_path(expected_path)
        required_markers = module_required_behavior_markers(module_contract, project_brief)
        if is_test and required_markers:
            missing_markers = [marker for marker in required_markers if marker not in content]
            if missing_markers:
                problems.append("generated test omits task behavior markers: " + ", ".join(missing_markers))
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


def source_to_paired_tests(project_brief: dict[str, Any]) -> dict[str, list[str]]:
    implementation_plan = project_brief.get("implementation_plan") if isinstance(project_brief.get("implementation_plan"), dict) else {}
    module_sequence = implementation_plan.get("module_sequence") if isinstance(implementation_plan.get("module_sequence"), list) else []
    test_files = {str(path) for path in implementation_plan.get("test_files", []) if isinstance(path, str)}
    pairs: dict[str, list[str]] = {}
    for module in module_sequence:
        if not isinstance(module, dict):
            continue
        path = str(module.get("path") or "")
        if not path or path in test_files:
            continue
        paired_tests = [str(item) for item in module.get("paired_tests", []) if isinstance(item, str)]
        if paired_tests:
            pairs[path] = paired_tests
    return pairs


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
    paired_tests_by_source = source_to_paired_tests(project_brief)
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
            is_test = is_test_file_path(path)
            required_markers = task_behavior_markers(str(project_brief.get("task") or ""))
            if is_test and required_markers:
                missing_markers = [marker for marker in required_markers if marker not in content]
                if missing_markers:
                    problems.append(f"generated test omits task behavior markers for {path}: " + ", ".join(missing_markers))
    changed_sources = sorted(path for path in seen if path in paired_tests_by_source)
    missing_paired_tests = sorted(
        {
            test_path
            for source_path in changed_sources
            for test_path in paired_tests_by_source.get(source_path, [])
            if test_path not in seen
        }
    )
    if missing_paired_tests:
        problems.append("file-set synthesis omitted paired tests for changed source files: " + ", ".join(missing_paired_tests))
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
    write_snapshots: dict[str, tuple[Path, bool, bytes]] = {}
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
        if synthesis_stage == "verification_repair" and is_test_file_path(rel_path):
            row["status"] = "skipped_test_oracle"
            row["warnings"].append("verification repair preserves test-oracle files")
            rows.append(row)
            continue
        if request_guidance is None:
            row["warnings"].append("no model guidance callback supplied")
            rows.append(row)
            continue
        guidance_payload = {
            "project_name": project_brief.get("project_name"),
            "project_type": project_brief.get("project_type"),
            "template_id": project_brief.get("template_id"),
            "module_synthesis_contract": synthesis_contract,
            "existing_content": target.read_text(encoding="utf-8") if target.exists() and target.is_file() else "",
            "verification_context": verification_context or {},
        }
        if synthesis_stage == "verification_repair":
            guidance_payload["test_oracle_snapshots"] = test_oracle_snapshots(repo, synthesis_contract)
            guidance_payload["repair_invariants"] = [
                "test_oracle_snapshots are read-only acceptance evidence",
                "do not change tests, expected literals, assertions, or verification commands",
                "source changes must satisfy exact expected keys, values, and strings shown by the test oracle",
            ]
        guidance = request_guidance(
            "GreenfieldImplementationWorker",
            guidance_payload,
            (
                "Repair this single module using the verification failure context. Return JSON only with path, content, "
                "requirements_satisfied, tests_to_update, and notes. Preserve the module contract and do not edit unrelated files. "
                "Test files are verification oracles and must not be edited during verification repair. Use paired test oracle "
                "snapshots to satisfy exact expected keys, values, and string literals."
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
            reformat_guidance = request_guidance(
                "GreenfieldImplementationWorker",
                {
                    "project_name": project_brief.get("project_name"),
                    "project_type": project_brief.get("project_type"),
                    "template_id": project_brief.get("template_id"),
                    "module_synthesis_contract": synthesis_contract,
                    "invalid_model_content": content,
                    "parse_error": str(exc),
                },
                "The previous module implementation response was not valid JSON. Reformat the same implementation as valid JSON only with path, content, requirements_satisfied, tests_to_update, and notes. Do not change the module contract or add unrelated files.",
            )
            row["reformat_guidance_status"] = str(reformat_guidance.get("status") or "")
            row["reformat_guidance_ok"] = bool(reformat_guidance.get("ok"))
            if not reformat_guidance.get("ok"):
                row["status"] = "rejected"
                row["blockers"].append(f"model output is not valid JSON object: {exc}")
                row["blockers"].append(str(reformat_guidance.get("error") or "model reformat guidance unavailable"))
                rows.append(row)
                continue
            try:
                output = extract_json_object(str(reformat_guidance.get("content") or ""))
            except (ValueError, json.JSONDecodeError) as reformat_exc:
                row["status"] = "rejected"
                row["blockers"].append(f"model output is not valid JSON object: {exc}")
                row["blockers"].append(f"model reformat output is not valid JSON object: {reformat_exc}")
                rows.append(row)
                continue
            row["warnings"].append("model output required JSON reformat retry")
        problems = validate_module_synthesis_output(output, module, forbidden_markers, project_brief)
        if problems:
            validation_retry_payload = {
                "project_name": project_brief.get("project_name"),
                "project_type": project_brief.get("project_type"),
                "template_id": project_brief.get("template_id"),
                "module_synthesis_contract": synthesis_contract,
                "previous_module_output": output,
                "validation_problems": problems,
                "existing_content": target.read_text(encoding="utf-8") if target.exists() and target.is_file() else "",
                "verification_context": verification_context or {},
            }
            if synthesis_stage == "verification_repair":
                validation_retry_payload["test_oracle_snapshots"] = test_oracle_snapshots(repo, synthesis_contract)
                validation_retry_payload["repair_invariants"] = [
                    "test_oracle_snapshots are read-only acceptance evidence",
                    "retry must fix validation blockers without weakening tests or changing path",
                    "source changes must satisfy exact expected imports, keys, values, and strings shown by the test oracle",
                ]
            validation_retry_guidance = request_guidance(
                "GreenfieldImplementationWorker",
                validation_retry_payload,
                "The previous module implementation JSON was parseable but failed validation. Correct the same module only. Return JSON only with the same path, valid content, complete requirements_satisfied, tests_to_update, and notes. Do not change unrelated files or weaken tests. During verification repair, use test_oracle_snapshots as read-only acceptance evidence.",
            )
            row["validation_retry_guidance_status"] = str(validation_retry_guidance.get("status") or "")
            row["validation_retry_guidance_ok"] = bool(validation_retry_guidance.get("ok"))
            if validation_retry_guidance.get("ok"):
                try:
                    retry_output = extract_json_object(str(validation_retry_guidance.get("content") or ""))
                except (ValueError, json.JSONDecodeError) as retry_exc:
                    row["validation_retry_error"] = f"validation retry output is not valid JSON object: {retry_exc}"
                else:
                    retry_problems = validate_module_synthesis_output(retry_output, module, forbidden_markers, project_brief)
                    if not retry_problems:
                        output = retry_output
                        row["warnings"].append("model output required validation retry")
                        problems = []
                    else:
                        row["validation_retry_blockers"] = retry_problems
            if problems:
                row["status"] = "rejected"
                row["blockers"].extend(problems)
                if isinstance(row.get("validation_retry_blockers"), list):
                    row["blockers"].extend(f"validation retry: {item}" for item in row["validation_retry_blockers"] if isinstance(item, str))
                if row.get("validation_retry_error"):
                    row["blockers"].append(str(row["validation_retry_error"]))
                rows.append(row)
                continue
        before = target.read_bytes() if target.exists() else b""
        rendered_content = str(output["content"])
        if rel_path not in write_snapshots:
            write_snapshots[rel_path] = (target, target.exists(), before)
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
    if blocked_count and write_snapshots:
        for rel_path, (target, existed, before_bytes) in write_snapshots.items():
            if existed:
                target.write_bytes(before_bytes)
            elif target.exists():
                target.unlink()
            operation_results.append(
                {
                    "operation": "greenfield_module_synthesis_rollback",
                    "path": rel_path,
                    "status": "rolled_back_after_blocked_synthesis",
                }
            )
        for row in rows:
            if row.get("status") == "applied":
                row["status"] = "rolled_back"
                row.setdefault("warnings", []).append("module write rolled back because another module failed synthesis validation")
        changed_files = []
        applied_count = 0
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
