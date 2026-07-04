#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from greenfield_architect import request_greenfield_model_guidance
from greenfield_implementation_worker import extract_json_object
from greenfield_scenario_worker import review_greenfield_scenarios
from greenfield_templates import GREENFIELD_MARKER


def entrypoint_exists(repo: Path, entrypoint: dict[str, Any]) -> bool:
    path = str(entrypoint.get("path") or "")
    return bool(path) and (repo / path).exists() and (repo / path).is_file()


def semantic_review_greenfield_files(repo: Path, project_brief: dict[str, Any]) -> dict[str, Any]:
    artifact_contract = project_brief.get("artifact_contract") if isinstance(project_brief.get("artifact_contract"), dict) else {}
    implementation_plan = project_brief.get("implementation_plan") if isinstance(project_brief.get("implementation_plan"), dict) else {}
    source_files = [
        str(path)
        for path in artifact_contract.get("source_files", [])
        if isinstance(path, str) and not str(path).startswith("tests/") and "/tests/" not in str(path)
    ]
    test_files = [str(path) for path in artifact_contract.get("test_files", []) if isinstance(path, str)]
    manifest_files = [str(path) for path in artifact_contract.get("manifest_files", []) if isinstance(path, str)]
    forbidden_markers = [str(item) for item in implementation_plan.get("anti_stub_policy", {}).get("forbidden_markers", []) if isinstance(item, str)]
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    ignored_empty = {GREENFIELD_MARKER}
    for rel_path in sorted(set(source_files + test_files + manifest_files + ["README.md"])):
        path = repo / rel_path
        row: dict[str, Any] = {"path": rel_path, "exists": path.exists() and path.is_file()}
        if not path.exists() or not path.is_file():
            row.update({"status": "missing", "size_bytes": 0, "line_count": 0, "forbidden_markers": []})
            blockers.append(f"semantic review file is missing: {rel_path}")
            rows.append(row)
            continue
        text = path.read_text(encoding="utf-8")
        stripped = text.strip()
        markers = forbidden_placeholder_markers_found(text, forbidden_markers)
        status = "ok"
        if not stripped and rel_path not in ignored_empty and Path(rel_path).name != "__init__.py":
            status = "blocked"
            blockers.append(f"semantic review found empty generated file: {rel_path}")
        elif markers:
            status = "blocked"
            blockers.append(f"semantic review found placeholder marker in {rel_path}: {', '.join(markers)}")
        elif rel_path in source_files and rel_path.endswith(".py"):
            status = python_source_semantic_status(text)
            if status == "weak":
                warnings.append(f"semantic review found very weak Python source: {rel_path}")
        row.update(
            {
                "status": status,
                "size_bytes": len(text.encode("utf-8")),
                "line_count": len(text.splitlines()),
                "forbidden_markers": markers,
            }
        )
        rows.append(row)
    module_rows: list[dict[str, Any]] = []
    for contract in project_brief.get("module_contracts", []):
        if not isinstance(contract, dict):
            continue
        rel_path = str(contract.get("path") or "")
        exists = bool(rel_path) and (repo / rel_path).is_file()
        requirements = [str(item) for item in contract.get("requirements", []) if isinstance(item, str)]
        traced = [
            row
            for row in implementation_plan.get("module_sequence", [])
            if isinstance(row, dict) and row.get("path") == rel_path
        ]
        if not exists:
            blockers.append(f"module contract path is missing: {rel_path}")
        if requirements and not traced:
            blockers.append(f"module contract has no implementation trace: {rel_path}")
        for trace_row in traced:
            synthesis_contract = trace_row.get("code_synthesis_contract") if isinstance(trace_row.get("code_synthesis_contract"), dict) else {}
            if synthesis_contract.get("kind") != "code_brigade_greenfield_module_synthesis_contract":
                blockers.append(f"module contract has no module synthesis contract: {rel_path}")
        module_rows.append({"path": rel_path, "exists": exists, "requirement_count": len(requirements), "trace_count": len(traced)})
    trace = project_brief.get("implementation_trace") if isinstance(project_brief.get("implementation_trace"), dict) else {}
    trace_rows_raw = trace.get("rows") if isinstance(trace.get("rows"), list) else []
    trace_rows: list[dict[str, Any]] = []
    if not trace_rows_raw:
        blockers.append("implementation trace has no requirement rows")
    for index, trace_row in enumerate(trace_rows_raw, start=1):
        if not isinstance(trace_row, dict):
            blockers.append(f"implementation trace row {index} is not an object")
            continue
        rel_path = str(trace_row.get("file") or "")
        verification_files = [str(path) for path in trace_row.get("verification_files", []) if isinstance(path, str)]
        source_exists = bool(rel_path) and (repo / rel_path).is_file()
        verification_exists = [path for path in verification_files if (repo / path).is_file()]
        if not trace_row.get("requirement"):
            blockers.append(f"implementation trace row {index} has no requirement")
        if not source_exists:
            blockers.append(f"implementation trace source file is missing: {rel_path}")
        if test_files and not verification_exists:
            blockers.append(f"implementation trace row has no existing verification file: {rel_path}")
        if trace_row.get("synthesis_contract_kind") != "code_brigade_greenfield_module_synthesis_contract":
            blockers.append(f"implementation trace row has no synthesis contract: {rel_path}")
        trace_rows.append(
            {
                "requirement": str(trace_row.get("requirement") or ""),
                "file": rel_path,
                "source_exists": source_exists,
                "verification_file_count": len(verification_files),
                "existing_verification_file_count": len(verification_exists),
            }
        )
    if source_files and not test_files:
        blockers.append("semantic review found source files without test files")
    return {
        "kind": "code_brigade_greenfield_semantic_review",
        "contract_version": "eye-mechanicum.v1",
        "status": "blocked" if blockers else "passed",
        "source_file_count": len(source_files),
        "test_file_count": len(test_files),
        "manifest_file_count": len(manifest_files),
        "rows": rows,
        "module_contract_rows": module_rows,
        "implementation_trace_status": "complete" if trace_rows and not any(not row["source_exists"] or row["existing_verification_file_count"] == 0 for row in trace_rows if test_files) else "blocked",
        "implementation_trace_rows": trace_rows,
        "blockers": blockers,
        "warnings": warnings,
    }


def python_source_semantic_status(text: str) -> str:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return "blocked"
    executable_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.Return, ast.Expr, ast.If, ast.For, ast.While, ast.Try))
    ]
    return "ok" if len(executable_nodes) >= 2 else "weak"


def forbidden_placeholder_markers_found(text: str, markers: list[str]) -> list[str]:
    found: list[str] = []
    lowered = text.lower()
    for marker in markers:
        if not marker:
            continue
        if marker == "TODO":
            if re.search(r"(?<![A-Za-z0-9_])TODO(?![A-Za-z0-9_])", text):
                found.append(marker)
            continue
        if marker.lower() in lowered:
            found.append(marker)
    return found


def artifact_review_greenfield_project(repo: Path, project_brief: dict[str, Any]) -> dict[str, Any]:
    artifact_contract = project_brief.get("artifact_contract") if isinstance(project_brief.get("artifact_contract"), dict) else {}
    template_id = str(project_brief.get("template_id") or "")
    source_files = [str(path) for path in artifact_contract.get("source_files", []) if isinstance(path, str)]
    test_files = [str(path) for path in artifact_contract.get("test_files", []) if isinstance(path, str)]
    blockers: list[str] = []
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []

    def read(rel_path: str) -> str:
        path = repo / rel_path
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    texts = {path: read(path) for path in source_files + test_files + ["README.md"]}
    contract_source_files = [
        str(contract.get("path") or "")
        for contract in project_brief.get("module_contracts", [])
        if isinstance(contract, dict)
        and isinstance(contract.get("path"), str)
        and not str(contract.get("path")).startswith("tests/")
        and "/tests/" not in str(contract.get("path"))
    ]
    for test_path in test_files:
        text = texts.get(test_path, "")
        has_assertion = any(marker in text for marker in ("self.assert", "assert ", "pytest.raises", "with self.assertRaises"))
        if text and not has_assertion:
            blockers.append(f"artifact review found assertionless test file: {test_path}")
        rows.append({"path": test_path, "kind": "test", "has_assertion": has_assertion})

    for contract in project_brief.get("module_contracts", []):
        if not isinstance(contract, dict):
            continue
        rel_path = str(contract.get("path") or "")
        if not rel_path or rel_path not in texts:
            continue
        text = texts.get(rel_path, "")
        tokens = artifact_requirement_tokens(contract)
        matched = [token for token in tokens if token in text.lower()]
        if tokens and not matched and rel_path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".html")):
            warnings.append(f"artifact review found weak requirement vocabulary in {rel_path}")
        rows.append({"path": rel_path, "kind": "module", "requirement_token_count": len(tokens), "matched_requirement_token_count": len(matched)})

    if template_id == "static_site":
        html = texts.get("index.html", "")
        for asset in [path for path in source_files if path.endswith((".js", ".css")) and "/" not in path]:
            if Path(asset).name not in html:
                blockers.append(f"artifact review found unreferenced static asset: {asset}")
    if template_id == "node_vite_app":
        html = texts.get("index.html", "")
        package_json = read("package.json")
        if "/src/main" not in html:
            blockers.append("artifact review found Vite HTML without src/main entrypoint")
        if package_json and "\"dev\"" not in package_json:
            blockers.append("artifact review found package.json without dev script")
    if template_id == "python_fastapi_service":
        main = texts.get("app/main.py", "")
        routes = texts.get("app/routes.py", "")
        if main and "FastAPI" not in main and "app =" not in main:
            blockers.append("artifact review found FastAPI main without app construction")
        if routes and "APIRouter" in routes and "include_router" not in main:
            blockers.append("artifact review found routes module not included by app/main.py")
    if template_id == "local_agent_tool":
        runner = next((texts[path] for path in source_files if path.endswith("/runner.py")), "")
        contract = next((texts[path] for path in source_files if path.endswith("/contract.py")), "")
        tool = next((texts[path] for path in source_files if path.endswith("/tool.py")), "")
        if runner:
            for marker in (".registry", ".schema", ".session"):
                if marker not in runner:
                    blockers.append(f"artifact review found local agent runner missing import: {marker}")
        if contract and ".runner" not in contract:
            blockers.append("artifact review found local agent contract facade not wired to runner")
        if tool and ".runner" not in tool:
            blockers.append("artifact review found local agent CLI not wired to runner")
    if template_id == "data_processing_tool":
        cli_path = next((path for path in source_files if path.endswith("/cli.py")), "")
        cli = texts.get(cli_path, "")
        package_sources = [path for path in contract_source_files if "/" in path and not path.endswith(("/__init__.py", "/cli.py"))]
        if cli and package_sources:
            missing = [Path(path).stem for path in package_sources if Path(path).stem not in cli]
            if missing:
                blockers.append(f"artifact review found data CLI not wired to modules: {', '.join(missing)}")

    return {
        "kind": "code_brigade_greenfield_artifact_review",
        "contract_version": "eye-mechanicum.v1",
        "status": "blocked" if blockers else "passed",
        "template_id": template_id,
        "source_file_count": len(source_files),
        "test_file_count": len(test_files),
        "rows": rows,
        "blockers": blockers,
        "warnings": warnings,
    }


def artifact_requirement_tokens(contract: dict[str, Any]) -> list[str]:
    raw_parts: list[str] = []
    for key in ("responsibility",):
        value = contract.get(key)
        if isinstance(value, str):
            raw_parts.append(value)
    requirements = contract.get("requirements")
    if isinstance(requirements, list):
        raw_parts.extend(str(item) for item in requirements if isinstance(item, str))
    stop_words = {
        "action",
        "actions",
        "behavior",
        "build",
        "command",
        "commands",
        "entrypoint",
        "export",
        "files",
        "handle",
        "module",
        "parse",
        "print",
        "prove",
        "provide",
        "return",
        "source",
        "structured",
        "support",
        "workflow",
    }
    tokens = [
        token
        for part in raw_parts
        for token in re.sub(r"[^a-zA-Z0-9_]+", " ", part.lower().replace("-", "_")).split()
        if len(token) >= 5 and token not in stop_words
    ]
    return list(dict.fromkeys(tokens))


def review_definition_of_done(
    repo: Path,
    project_brief: dict[str, Any],
    dependency_report: dict[str, Any],
    verification: dict[str, Any],
    semantic_review: dict[str, Any],
    scenario_review: dict[str, Any],
    artifact_review: dict[str, Any],
) -> dict[str, Any]:
    items = [str(item) for item in project_brief.get("definition_of_done", []) if isinstance(item, str) and item.strip()]
    expected_files = [str(path) for path in project_brief.get("expected_files", []) if isinstance(path, str)]
    run_commands = [str(command) for command in project_brief.get("run_commands", []) if isinstance(command, str) and command.strip()]
    verification_commands = [str(command) for command in project_brief.get("verification_commands", []) if isinstance(command, str) and command.strip()]
    entrypoints = project_brief.get("entrypoints") if isinstance(project_brief.get("entrypoints"), list) else []
    readme_path = repo / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8") if readme_path.is_file() else ""
    missing_files = [path for path in expected_files if not (repo / path).is_file()]
    missing_entrypoints = [
        str(entrypoint.get("path") or "")
        for entrypoint in entrypoints
        if isinstance(entrypoint, dict) and not entrypoint_exists(repo, entrypoint)
    ]
    missing_readme_commands = [
        command
        for command in [*run_commands, *verification_commands]
        if command and command not in readme_text
    ]
    verification_passed = verification.get("status") in {"passed", "planned"}
    dependency_passed = dependency_report.get("status") != "blocked"
    semantic_passed = semantic_review.get("status") == "passed"
    scenario_passed = scenario_review.get("status") == "passed"
    artifact_passed = artifact_review.get("status") == "passed"
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []

    def add_row(item: str, status: str, evidence: list[str], missing: list[str]) -> None:
        rows.append(
            {
                "item": item,
                "status": status,
                "evidence": evidence,
                "missing_evidence": missing,
            }
        )
        if status != "passed":
            blockers.append(f"definition_of_done item is not proven: {item}")

    for item in items:
        lowered = item.lower()
        evidence: list[str] = []
        missing: list[str] = []
        checks: list[bool] = []
        if any(token in lowered for token in ("expected file", "files are created", "created inside", "artifact")):
            checks.append(not missing_files)
            evidence.append(f"expected_file_count={len(expected_files)}")
            missing.extend(f"missing expected file: {path}" for path in missing_files)
        if any(token in lowered for token in ("entrypoint", "launch", "run command", "запуск")):
            checks.append(not missing_entrypoints)
            evidence.append(f"entrypoint_count={len(entrypoints)}")
            missing.extend(f"missing entrypoint: {path}" for path in missing_entrypoints if path)
        if any(token in lowered for token in ("verification", "test", "tests", "build", "smoke", "провер")):
            checks.append(verification_passed)
            evidence.append(f"verification_status={verification.get('status') or ''}")
            if not verification_passed:
                missing.append(f"verification did not pass: {verification.get('status') or ''}")
        if any(token in lowered for token in ("readme", "document", "documents", "документ")):
            checks.append(bool(readme_text) and not missing_readme_commands)
            evidence.append("README.md")
            missing.extend(f"README missing command: {command}" for command in missing_readme_commands)
            if not readme_text:
                missing.append("README.md is missing or empty")
        if any(token in lowered for token in ("dependency", "dependencies", "package", "install", "завис")):
            checks.append(dependency_passed)
            evidence.append(f"dependency_status={dependency_report.get('status') or ''}")
            if not dependency_passed:
                missing.extend(str(blocker) for blocker in dependency_report.get("blockers", []) if isinstance(blocker, str))
        if any(token in lowered for token in ("behavior", "feature", "workflow", "scenario", "logic", "mvp", "stub", "заглуш")):
            checks.append(semantic_passed and scenario_passed and artifact_passed)
            evidence.extend(
                [
                    f"semantic_review={semantic_review.get('status') or ''}",
                    f"scenario_review={scenario_review.get('status') or ''}",
                    f"artifact_review={artifact_review.get('status') or ''}",
                ]
            )
            if not semantic_passed:
                missing.append(f"semantic review did not pass: {semantic_review.get('status') or ''}")
            if not scenario_passed:
                missing.append(f"scenario review did not pass: {scenario_review.get('status') or ''}")
            if not artifact_passed:
                missing.append(f"artifact review did not pass: {artifact_review.get('status') or ''}")
        if not checks:
            checks.append(verification_passed and semantic_passed and scenario_passed and artifact_passed)
            evidence.extend(
                [
                    "generic DoD proof requires passed verification and all greenfield reviews",
                    f"verification_status={verification.get('status') or ''}",
                    f"semantic_review={semantic_review.get('status') or ''}",
                    f"scenario_review={scenario_review.get('status') or ''}",
                    f"artifact_review={artifact_review.get('status') or ''}",
                ]
            )
            if not verification_passed:
                missing.append(f"verification did not pass: {verification.get('status') or ''}")
            if not semantic_passed:
                missing.append(f"semantic review did not pass: {semantic_review.get('status') or ''}")
            if not scenario_passed:
                missing.append(f"scenario review did not pass: {scenario_review.get('status') or ''}")
            if not artifact_passed:
                missing.append(f"artifact review did not pass: {artifact_review.get('status') or ''}")
        add_row(item, "passed" if all(checks) and not missing else "blocked", evidence, missing)

    if not rows:
        blockers.append("definition_of_done has no items")
    return {
        "kind": "code_brigade_greenfield_definition_of_done_review",
        "contract_version": "eye-mechanicum.v1",
        "status": "blocked" if blockers else "passed",
        "item_count": len(rows),
        "passed_count": sum(1 for row in rows if row["status"] == "passed"),
        "blocked_count": sum(1 for row in rows if row["status"] == "blocked"),
        "rows": rows,
        "blockers": blockers,
    }


def reviewer_model_findings(guidance: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(guidance, dict) or not guidance.get("ok"):
        return {
            "kind": "code_brigade_greenfield_reviewer_model_findings",
            "contract_version": "eye-mechanicum.v1",
            "status": "unavailable",
            "blockers": [],
            "warnings": [],
            "parse_error": str(guidance.get("error") or "") if isinstance(guidance, dict) else "missing guidance",
        }
    content = str(guidance.get("content") or "").strip()
    if not content:
        return {
            "kind": "code_brigade_greenfield_reviewer_model_findings",
            "contract_version": "eye-mechanicum.v1",
            "status": "empty",
            "blockers": [],
            "warnings": [],
            "parse_error": "empty reviewer guidance",
        }
    try:
        parsed = extract_json_object(content)
    except ValueError as exc:
        return {
            "kind": "code_brigade_greenfield_reviewer_model_findings",
            "contract_version": "eye-mechanicum.v1",
            "status": "advisory_unparsed",
            "blockers": [],
            "warnings": ["GreenfieldReviewer model guidance was not structured JSON"],
            "parse_error": str(exc),
        }
    raw_status = str(parsed.get("status") or parsed.get("decision") or "advisory").lower()
    raw_blockers = parsed.get("blockers") if isinstance(parsed.get("blockers"), list) else []
    raw_warnings = parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else []
    blockers = [str(item) for item in raw_blockers if isinstance(item, str) and item.strip()]
    warnings = [str(item) for item in raw_warnings if isinstance(item, str) and item.strip()]
    blocking_status = raw_status in {"blocked", "reject", "rejected", "fail", "failed"}
    return {
        "kind": "code_brigade_greenfield_reviewer_model_findings",
        "contract_version": "eye-mechanicum.v1",
        "status": "blocked" if blocking_status and blockers else "passed" if raw_status in {"passed", "accepted", "ok"} else "advisory",
        "blockers": blockers if blocking_status else [],
        "warnings": warnings,
        "parse_error": "",
    }


def review_greenfield_project(
    repo: Path,
    project_brief: dict[str, Any],
    dependency_report: dict[str, Any],
    verification: dict[str, Any],
    request_guidance=request_greenfield_model_guidance,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    expected_files = [str(path) for path in project_brief.get("expected_files", []) if isinstance(path, str)]
    for rel_path in expected_files:
        path = repo / rel_path
        if not path.exists() or not path.is_file():
            blockers.append(f"expected file is missing: {rel_path}")
    readme = repo / "README.md"
    readme_text = readme.read_text(encoding="utf-8") if readme.exists() and readme.is_file() else ""
    if not readme_text:
        blockers.append("README.md is missing or empty")
    for command in project_brief.get("run_commands", []):
        if isinstance(command, str) and command and command not in readme_text:
            blockers.append(f"README.md does not document run command: {command}")
    for command in project_brief.get("verification_commands", []):
        if isinstance(command, str) and command and command not in readme_text:
            warnings.append(f"README.md does not document verification command: {command}")
    entrypoints = project_brief.get("entrypoints") if isinstance(project_brief.get("entrypoints"), list) else []
    if not entrypoints:
        blockers.append("greenfield project has no entrypoints")
    for entrypoint in entrypoints:
        if isinstance(entrypoint, dict) and not entrypoint_exists(repo, entrypoint):
            blockers.append(f"entrypoint file is missing: {entrypoint.get('path')}")
    if dependency_report.get("status") == "blocked":
        blockers.extend(str(item) for item in dependency_report.get("blockers", []))
    if verification.get("status") not in {"passed", "planned"}:
        blockers.append(f"verification did not pass: {verification.get('status')}")
    module_contracts = project_brief.get("module_contracts") if isinstance(project_brief.get("module_contracts"), list) else []
    if len(module_contracts) < 2 and project_brief.get("project_type") not in {"web_app"}:
        blockers.append("non-trivial greenfield project must not collapse to a single module contract")
    semantic_review = semantic_review_greenfield_files(repo, project_brief)
    if semantic_review.get("status") == "blocked":
        blockers.extend(str(item) for item in semantic_review.get("blockers", []))
    warnings.extend(str(item) for item in semantic_review.get("warnings", []))
    scenario_review = review_greenfield_scenarios(repo, project_brief)
    if scenario_review.get("status") == "blocked":
        blockers.extend(str(item) for item in scenario_review.get("blockers", []))
    warnings.extend(str(item) for item in scenario_review.get("warnings", []))
    artifact_review = artifact_review_greenfield_project(repo, project_brief)
    if artifact_review.get("status") == "blocked":
        blockers.extend(str(item) for item in artifact_review.get("blockers", []))
    warnings.extend(str(item) for item in artifact_review.get("warnings", []))
    definition_of_done_review = review_definition_of_done(
        repo,
        project_brief,
        dependency_report,
        verification,
        semantic_review,
        scenario_review,
        artifact_review,
    )
    if definition_of_done_review.get("status") == "blocked":
        blockers.extend(str(item) for item in definition_of_done_review.get("blockers", []))
    reviewer_guidance = request_guidance(
        "GreenfieldReviewer",
        {
            "project_name": project_brief.get("project_name"),
            "project_type": project_brief.get("project_type"),
            "template_id": project_brief.get("template_id"),
            "expected_files": expected_files,
            "dependency_status": dependency_report.get("status"),
            "verification_status": verification.get("status"),
            "semantic_review": semantic_review,
            "scenario_review": scenario_review,
            "artifact_review": artifact_review,
            "definition_of_done_review": definition_of_done_review,
            "blockers": blockers,
            "warnings": warnings,
        },
        "Critique the finished greenfield project against definition of done. Return JSON only with status, blockers, warnings, and evidence_notes. Use status=blocked only for concrete missing launchability, fake stubs, weak tests, template mismatch, or incomplete definition_of_done evidence.",
    )
    model_findings = reviewer_model_findings(reviewer_guidance)
    if model_findings.get("status") == "blocked":
        blockers.extend(f"GreenfieldReviewer model blocker: {item}" for item in model_findings.get("blockers", []) if isinstance(item, str))
    warnings.extend(str(item) for item in model_findings.get("warnings", []) if isinstance(item, str))
    return {
        "kind": "code_brigade_greenfield_review",
        "contract_version": "eye-mechanicum.v1",
        "status": "blocked" if blockers else "passed",
        "definition_of_done": project_brief.get("definition_of_done", []),
        "expected_file_count": len(expected_files),
        "entrypoint_count": len(entrypoints),
        "module_contract_count": len(module_contracts),
        "dependency_status": dependency_report.get("status", ""),
        "verification_status": verification.get("status", ""),
        "semantic_review": semantic_review,
        "scenario_review": scenario_review,
        "artifact_review": artifact_review,
        "definition_of_done_review": definition_of_done_review,
        "model_findings": model_findings,
        "blockers": blockers,
        "warnings": warnings,
        "model_guidance": reviewer_guidance,
    }
