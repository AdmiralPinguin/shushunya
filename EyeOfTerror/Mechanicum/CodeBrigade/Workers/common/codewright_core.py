from __future__ import annotations

import json
import ast
import hashlib
import re
import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

MECHANICUM_ROOT = Path(__file__).resolve().parents[1]
if str(MECHANICUM_ROOT) not in sys.path:
    sys.path.insert(0, str(MECHANICUM_ROOT))

from common.swe_guardrails import build_repo_map, python_module_name, source_candidates_from_traceback_text, test_like_path  # noqa: E402


EXCLUDED_DIRS = {
    ".git",
    ".gradle",
    ".venv",
    "__pycache__",
    "node_modules",
    "runtime",
    "tmp",
    "cache",
    ".cache",
    "live_runs",
    "models",
    "outputs",
    "build",
    "dist",
}

MAX_SYMBOL_SCAN_BYTES = 120_000


WORKER_NAME = "CogitatorCodewright"


class PatchApplyError(ValueError):
    def __init__(self, message: str, rolled_back_files: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.rolled_back_files = rolled_back_files


def worker_name() -> str:
    return WORKER_NAME


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def sibling_artifact(output_path: str, filename: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    return f"{output_path.rsplit('/', 1)[0]}/{filename}"


def load_json_optional(workspace_root: Path, path: str) -> dict[str, Any]:
    host_path = sandbox_path(workspace_root, path)
    if not host_path.exists():
        return {}
    payload = json.loads(host_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def read_text_optional(workspace_root: Path, path: str) -> str:
    host_path = sandbox_path(workspace_root, path)
    if not host_path.exists():
        return ""
    return host_path.read_text(encoding="utf-8")


def write_json(workspace_root: Path, path: str, payload: dict[str, Any]) -> None:
    host_path = sandbox_path(workspace_root, path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(workspace_root: Path, path: str, content: str) -> None:
    host_path = sandbox_path(workspace_root, path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(content, encoding="utf-8")


def request_goal(request: dict[str, Any]) -> str:
    contract = request.get("contract") if isinstance(request.get("contract"), dict) else {}
    return str(request.get("goal") or request.get("task") or contract.get("goal") or "")


def role_policy_from_request(request: dict[str, Any]) -> dict[str, Any]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    step_quality = expectations.get("step_quality") if isinstance(expectations.get("step_quality"), dict) else {}
    role_policy = step_quality.get("role_policy") if isinstance(step_quality.get("role_policy"), dict) else {}
    return role_policy


def task_profile_from_request(request: dict[str, Any]) -> dict[str, Any]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    profile = expectations.get("task_profile") if isinstance(expectations.get("task_profile"), dict) else {}
    return profile


def worker_brief_from_request(request: dict[str, Any]) -> dict[str, Any]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    brief = expectations.get("worker_brief") if isinstance(expectations.get("worker_brief"), dict) else {}
    return brief

def repo_grade_workflow_from_request(request: dict[str, Any], changed_files: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    task_profile = task_profile_from_request(request)
    goal = request_goal(request).lower()
    kinds = task_profile.get("kinds") if isinstance(task_profile.get("kinds"), list) else []
    changed_count = len(changed_files or [])
    explicit_repo_grade = any(
        marker in goal
        for marker in ("repo-grade", "real repo", "architecture", "architect", "8-15")
    )
    repo_grade = explicit_repo_grade or changed_count >= 8
    required_passes = [
        "survey",
        "architecture_decision",
        "implementation",
        "focused_verification",
        "broad_verification",
        "self_review",
        "revision_if_needed",
        "final_package",
    ]
    return {
        "mode": "repo_grade" if repo_grade else "focused_fix",
        "required_passes": required_passes if repo_grade else ["survey", "implementation", "verification", "self_review", "final_package"],
        "requires_architecture_decision_record": repo_grade,
        "requires_focused_and_broad_verification": repo_grade,
        "requires_compatibility_and_rollback_notes": repo_grade or any("api" in str(kind) for kind in kinds),
        "requires_pr_summary": True,
    }

def architecture_decision_record_from_evidence(
    request: dict[str, Any],
    survey: dict[str, Any],
    changed_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    readiness = survey.get("engineering_readiness") if isinstance(survey.get("engineering_readiness"), dict) else {}
    investigation = survey.get("engineering_investigation") if isinstance(survey.get("engineering_investigation"), dict) else {}
    decision_seed = investigation.get("design_decision_seed") if isinstance(investigation.get("design_decision_seed"), list) else []
    impact_matrix = readiness.get("impact_matrix") if isinstance(readiness.get("impact_matrix"), list) else []
    changed_paths = [
        str(item.get("path"))
        for item in (changed_files or [])
        if isinstance(item, dict) and item.get("path")
    ]
    return {
        "status": "recorded",
        "decision": "Apply the smallest coherent repo-grade patch across the impacted source, tests, docs, config, and compatibility surfaces.",
        "drivers": [
            "preserve existing public behavior unless the task explicitly changes it",
            "prefer source changes backed by focused tests and broad regression checks",
            "keep rollback evidence and changed-file scope review in the final package",
        ],
        "alternatives_considered": [
            {
                "option": "single-file shortcut",
                "rejected_because": "repo-grade tasks need caller, tests, docs, and compatibility evidence, not only a local source edit",
            },
            {
                "option": "broad rewrite",
                "rejected_because": "larger rewrites increase regression risk and hide the task-specific diff",
            },
        ],
        "seed_rules": [str(item) for item in decision_seed[:8]],
        "impacted_files": changed_paths or [
            str(item.get("path"))
            for item in impact_matrix[:12]
            if isinstance(item, dict) and item.get("path")
        ],
        "rollback": "Revert the changed files listed in patch_package.changed_files; no hidden state mutation is allowed.",
    }














def role_policy_allows_source_mutation(role_policy: dict[str, Any]) -> bool:
    return not role_policy or role_policy.get("may_mutate_source") is not False

def diagnostic_declared_paths(payload: Any) -> set[str]:
    paths: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.endswith("_path") and isinstance(value, str) and value:
                paths.add(value)
            else:
                paths.update(diagnostic_declared_paths(value))
    elif isinstance(payload, list):
        for item in payload:
            paths.update(diagnostic_declared_paths(item))
    return paths


def patch_scope_evidence(
    workspace_root: Path,
    output_path: str,
    changed_files: list[dict[str, Any]],
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    ranked_files = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    ranked_by_path = {
        str(item.get("path") or ""): item
        for item in ranked_files
        if isinstance(item, dict) and item.get("path")
    }
    test_source_links = repo_map.get("test_source_links") if isinstance(repo_map.get("test_source_links"), list) else []
    tests_by_source: dict[str, list[str]] = {}
    sources_by_test: dict[str, list[str]] = {}
    for link in test_source_links:
        if not isinstance(link, dict):
            continue
        test_path = str(link.get("test_path") or "")
        source_paths = [str(item) for item in link.get("source_paths", [])] if isinstance(link.get("source_paths"), list) else []
        if test_path:
            sources_by_test[test_path] = source_paths[:12]
        for source_path in source_paths:
            tests_by_source.setdefault(source_path, [])
            if test_path and test_path not in tests_by_source[source_path]:
                tests_by_source[source_path].append(test_path)
    evidence: list[dict[str, Any]] = []
    unmapped: list[str] = []
    diagnostic_paths = diagnostic_declared_paths(diagnostics or {})
    for item in changed_files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        ranked = ranked_by_path.get(path)
        if ranked:
            evidence.append(
                {
                    "path": path,
                    "in_repo_map": True,
                    "score": ranked.get("score", 0),
                    "reasons": ranked.get("reasons", []),
                    "linked_tests": tests_by_source.get(path, [])[:12],
                    "linked_sources": sources_by_test.get(path, [])[:12],
                }
            )
        else:
            diagnostic_declared = path in diagnostic_paths
            if not diagnostic_declared:
                unmapped.append(path)
            evidence.append(
                {
                    "path": path,
                    "in_repo_map": False,
                    "diagnostic_declared_surface": diagnostic_declared,
                    "score": 0,
                    "reasons": ["diagnostic_declared_surface"] if diagnostic_declared else [],
                    "linked_tests": tests_by_source.get(path, [])[:12],
                    "linked_sources": sources_by_test.get(path, [])[:12],
                }
            )
    return {
        "changed_files_in_repo_map": [item["path"] for item in evidence if item.get("in_repo_map")],
        "changed_files_outside_repo_map": unmapped,
        "changed_sources_with_linked_tests": [
            {"path": item["path"], "tests": item.get("linked_tests", [])}
            for item in evidence
            if item.get("linked_tests")
        ],
        "changed_tests_with_linked_sources": [
            {"path": item["path"], "sources": item.get("linked_sources", [])}
            for item in evidence
            if item.get("linked_sources")
        ],
        "evidence": evidence,
    }

def runtime_evidence_from_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    runtime = diagnostics.get("runtime_diagnostic_extraction") if isinstance(diagnostics.get("runtime_diagnostic_extraction"), dict) else {}
    if not runtime:
        return {}
    return {
        "runtime_failure_count": len(runtime.get("runtime_failures", [])) if isinstance(runtime.get("runtime_failures"), list) else 0,
        "runtime_test_failure_count": len(runtime.get("runtime_test_failures", [])) if isinstance(runtime.get("runtime_test_failures"), list) else 0,
        "runtime_minimal_patch_candidates": runtime.get("runtime_minimal_patch_candidates", [])[:5]
        if isinstance(runtime.get("runtime_minimal_patch_candidates"), list)
        else [],
        "parser_coverage": runtime.get("parser_coverage", {}) if isinstance(runtime.get("parser_coverage"), dict) else {},
    }






def ranked_source_candidates_from_survey(workspace_root: Path, output_path: str) -> list[str]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    ranked_files = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    candidates: list[str] = []
    for item in ranked_files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if path.endswith(".py") and not test_like_path(path) and path not in candidates:
            candidates.append(path)
    return candidates[:20]


def recommended_read_order_from_survey(workspace_root: Path, output_path: str) -> list[dict[str, Any]]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    read_order = repo_map.get("recommended_read_order") if isinstance(repo_map.get("recommended_read_order"), list) else []
    return [item for item in read_order if isinstance(item, dict)][:30]
















def is_unshaped_patch_source(patch_source: str) -> bool:
    return (
        patch_source.startswith("test_inferred_")
        or patch_source.startswith("natural_language_")
        or patch_source.startswith("runtime_diagnostic_")
    )

def ast_patch_plan_required_for_source(patch_source: str) -> bool:
    return patch_source in {
        "test_inferred_arithmetic_return",
        "test_inferred_return_mismatch",
        "test_inferred_self_repair_seed",
        "runtime_diagnostic_return_mismatch",
        "test_inferred_missing_function",
        "test_inferred_security_boundary",
    }





def literal_preview(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return repr(ast.literal_eval(node))
    except (ValueError, TypeError):
        try:
            return ast.unparse(node)
        except Exception:
            return ""


def call_symbol_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def imported_symbol_map_from_tree(tree: ast.Module) -> dict[str, dict[str, str]]:
    imports: dict[str, dict[str, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        for alias in node.names:
            local_name = alias.asname or alias.name
            imports[local_name] = {
                "imported_symbol": alias.name,
                "local_symbol": local_name,
                "module": node.module,
                "source_path": f"{node.module.replace('.', '/')}.py",
            }
    return imports


def test_function_nodes_from_tree(tree: ast.Module) -> list[tuple[str, ast.FunctionDef]]:
    functions: list[tuple[str, ast.FunctionDef]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
            functions.append(("", node))
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name.startswith("test"):
                    functions.append((node.name, child))
    return functions


def assertion_call_nodes(func_node: ast.FunctionDef) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            method = call_symbol_name(node.func)
            if method not in {"assertEqual", "assertNotEqual", "assertTrue", "assertFalse"}:
                continue
            assertions.append(
                {
                    "assertion": method,
                    "actual_node": node.args[0] if node.args else None,
                    "expected_node": node.args[1] if method in {"assertEqual", "assertNotEqual"} and len(node.args) > 1 else None,
                }
            )
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare) and len(node.test.ops) == 1 and len(node.test.comparators) == 1:
            operator = node.test.ops[0]
            if not isinstance(operator, (ast.Eq, ast.NotEq)):
                continue
            assertions.append(
                {
                    "assertion": "assertEqual" if isinstance(operator, ast.Eq) else "assertNotEqual",
                    "actual_node": node.test.left,
                    "expected_node": node.test.comparators[0],
                }
            )
    return assertions[:20]


def test_symbol_links_from_goal(repo_root: Path, goal: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for test_path in discovered_test_paths(repo_root, goal):
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        imports = imported_symbol_map_from_tree(tree)
        for class_name, func_node in test_function_nodes_from_tree(tree):
            for assertion in assertion_call_nodes(func_node):
                actual_node = assertion.get("actual_node")
                actual_symbol = call_symbol_name(actual_node) if isinstance(actual_node, ast.AST) else ""
                imported = imports.get(actual_symbol)
                if not imported:
                    continue
                expected_node = assertion.get("expected_node")
                key = (test_path, func_node.name, imported["source_path"], imported["imported_symbol"])
                if key in seen:
                    continue
                seen.add(key)
                links.append(
                    {
                        "test_path": test_path,
                        "test_class": class_name,
                        "test_function": func_node.name,
                        "assertion": assertion.get("assertion", ""),
                        "actual_expression": literal_preview(actual_node if isinstance(actual_node, ast.AST) else None),
                        "expected_expression": literal_preview(expected_node if isinstance(expected_node, ast.AST) else None),
                        "imported_symbol": imported["imported_symbol"],
                        "local_symbol": imported["local_symbol"],
                        "source_module": imported["module"],
                        "source_path": imported["source_path"],
                        "source_exists": safe_repo_path(repo_root, imported["source_path"]).exists(),
                    }
                )
    return links[:50]


def test_paths_from_goal(goal: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"`([^`]+\.py)`", goal):
        path = match.group(1).strip()
        lowered = path.lower()
        if "test" in lowered and path not in paths:
            paths.append(path)
    return paths


def discovered_test_paths(repo_root: Path, goal: str) -> list[str]:
    paths = test_paths_from_goal(goal)
    lowered = goal.lower()
    if paths or not any(marker in lowered for marker in ("тест", "test", "pytest", "unittest")):
        return paths
    for path in sorted(repo_root.rglob("*.py")):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(repo_root).parts):
            continue
        rel = str(path.relative_to(repo_root))
        if test_like_path(rel):
            paths.append(rel)
    return paths[:20]


def pytest_style_test_file(repo_root: Path, test_path: str) -> bool:
    try:
        text = safe_repo_path(repo_root, test_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    return any(isinstance(node, ast.FunctionDef) and node.name.startswith("test") for node in tree.body)


def test_expectation_candidates(repo_root: Path, goal: str) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for test_path in discovered_test_paths(repo_root, goal):
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", text, flags=re.MULTILINE)
        for module_name, function_name in imports:
            expected_values = re.findall(
                rf"assertEqual\(\s*{re.escape(function_name)}\(\)\s*,\s*([+-]?\d+|True|False|None|'[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\")\s*\)",
                text,
            )
            if len(expected_values) != 1:
                continue
            module_path = f"{module_name.replace('.', '/')}.py"
            source_path = safe_repo_path(repo_root, module_path)
            if not source_path.exists():
                continue
            candidates.append(
                {
                    "test_path": test_path,
                    "module_path": module_path,
                    "function_name": function_name,
                    "literal": safe_return_literal(expected_values[0]),
                }
            )
    return candidates

def safe_return_literal(raw: str) -> str:
    value = raw.strip()
    if re.fullmatch(r"[+-]?\d+", value) or value in {"True", "False", "None"}:
        return value
    if re.fullmatch(r"'[^'\\]*(?:\\.[^'\\]*)*'", value) or re.fullmatch(r'"[^"\\]*(?:\\.[^"\\]*)*"', value):
        return value
    raise ValueError(f"unsupported inferred return literal: {raw}")



























def simple_function_return_segment(source_path: Path, function_name: str) -> dict[str, Any]:
    text = source_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(source_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        returns = [item for item in ast.walk(node) if isinstance(item, ast.Return)]
        if len(returns) != 1 or returns[0].value is None:
            return {}
        args = [arg.arg for arg in node.args.args]
        segment = ast.get_source_segment(text, returns[0].value) or ""
        if not segment or "\n" in segment:
            return {}
        return {"args": args, "return_expr": segment, "line": returns[0].lineno}
    return {}




























def output_path_from_request(request: dict[str, Any]) -> str:
    step = request.get("step") if isinstance(request.get("step"), dict) else {}
    expected = step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []
    if not expected or not isinstance(expected[0], str):
        raise ValueError("step.expected_artifacts must contain an output path")
    return expected[0]


def safe_repo_path(repo_root: Path, raw_path: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("patch path must be a non-empty string")
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"patch path must be relative and stay inside target repo: {raw_path}")
    root = repo_root.resolve()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"patch path escapes target repo: {raw_path}")
    if any(part in EXCLUDED_DIRS for part in resolved.relative_to(root).parts):
        raise ValueError(f"patch path points into an excluded directory: {raw_path}")
    return resolved

def sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invalidate_python_cache(path: Path) -> None:
    if path.suffix != ".py":
        return
    cache_dir = path.parent / "__pycache__"
    if not cache_dir.exists():
        return
    for cached in cache_dir.glob(f"{path.stem}.*.pyc"):
        cached.unlink(missing_ok=True)










def target_repo_root(request: dict[str, Any]) -> Path:
    raw = str(request.get("target_repo_root") or request.get("code_workspace_root") or "").strip()
    if not raw:
        goal = request_goal(request)
        marker = "CERAXIA_TARGET_REPO:"
        marker_at = goal.find(marker)
        if marker_at >= 0:
            raw = goal[marker_at + len(marker):].strip().splitlines()[0].strip()
    if not raw:
        return Path.cwd().resolve()
    return Path(raw).resolve()






















































































































































if __name__ == "__main__":
    raise SystemExit(main())
