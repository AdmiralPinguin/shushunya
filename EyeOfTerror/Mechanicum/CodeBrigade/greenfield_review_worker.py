#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from greenfield_architect import request_greenfield_model_guidance
from greenfield_templates import GREENFIELD_MARKER


def entrypoint_exists(repo: Path, entrypoint: dict[str, Any]) -> bool:
    path = str(entrypoint.get("path") or "")
    return bool(path) and (repo / path).exists() and (repo / path).is_file()


def semantic_review_greenfield_files(repo: Path, project_brief: dict[str, Any]) -> dict[str, Any]:
    artifact_contract = project_brief.get("artifact_contract") if isinstance(project_brief.get("artifact_contract"), dict) else {}
    implementation_plan = project_brief.get("implementation_plan") if isinstance(project_brief.get("implementation_plan"), dict) else {}
    source_files = [str(path) for path in artifact_contract.get("source_files", []) if isinstance(path, str)]
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


def review_greenfield_project(repo: Path, project_brief: dict[str, Any], dependency_report: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
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
    reviewer_guidance = request_greenfield_model_guidance(
        "GreenfieldReviewer",
        {
            "project_name": project_brief.get("project_name"),
            "project_type": project_brief.get("project_type"),
            "template_id": project_brief.get("template_id"),
            "expected_files": expected_files,
            "dependency_status": dependency_report.get("status"),
            "verification_status": verification.get("status"),
            "semantic_review": semantic_review,
            "blockers": blockers,
            "warnings": warnings,
        },
        "Critique the finished greenfield project against definition of done. Flag missing launchability, fake stubs, weak tests, and template mismatch.",
    )
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
        "blockers": blockers,
        "warnings": warnings,
        "model_guidance": reviewer_guidance,
    }
