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


def role_policy_allows_source_mutation(role_policy: dict[str, Any]) -> bool:
    return not role_policy or role_policy.get("may_mutate_source") is not False


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


def source_excerpt_pack(workspace_root: Path, output_path: str, repo_root: Path) -> list[dict[str, Any]]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    investigation = survey.get("engineering_investigation") if isinstance(survey.get("engineering_investigation"), dict) else {}
    readiness = survey.get("engineering_readiness") if isinstance(survey.get("engineering_readiness"), dict) else {}
    targeted = investigation.get("targeted_reading_plan") if isinstance(investigation.get("targeted_reading_plan"), list) else []
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    read_order = repo_map.get("recommended_read_order") if isinstance(repo_map.get("recommended_read_order"), list) else []
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in (targeted, read_order):
        for item in source:
            if not isinstance(item, dict):
                continue
            rel = str(item.get("path") or "")
            if not rel or rel in seen:
                continue
            seen.add(rel)
            candidates.append(item)
            if len(candidates) >= 8:
                break
        if len(candidates) >= 8:
            break
    excerpts: list[dict[str, Any]] = []
    for item in candidates:
        rel = str(item.get("path") or "")
        record: dict[str, Any] = {
            "path": rel,
            "phase": item.get("phase", ""),
            "reason": item.get("reason", ""),
            "question": item.get("question", ""),
            "dependent_count": int(item.get("dependent_count") or 0),
        }
        try:
            path = safe_repo_path(repo_root, rel)
        except ValueError as exc:
            record.update({"status": "blocked", "diagnostic": str(exc)})
            excerpts.append(record)
            continue
        if not path.exists() or not path.is_file():
            record.update({"status": "missing"})
            excerpts.append(record)
            continue
        size = path.stat().st_size
        record["bytes"] = size
        if size > 40_000:
            record.update({"status": "skipped_large_file"})
            excerpts.append(record)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            record.update({"status": "skipped_non_utf8"})
            excerpts.append(record)
            continue
        excerpt = text[:12_000]
        record.update(
            {
                "status": "read",
                "excerpt": excerpt,
                "truncated": len(text) > len(excerpt),
            }
        )
        excerpts.append(record)
    return excerpts


def patch_scope_evidence(workspace_root: Path, output_path: str, changed_files: list[dict[str, Any]]) -> dict[str, Any]:
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
            unmapped.append(path)
            evidence.append(
                {
                    "path": path,
                    "in_repo_map": False,
                    "score": 0,
                    "reasons": [],
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


def patch_scope_review(scope: dict[str, Any]) -> dict[str, Any]:
    in_map = [str(item) for item in scope.get("changed_files_in_repo_map", [])] if isinstance(scope.get("changed_files_in_repo_map"), list) else []
    outside_map = [str(item) for item in scope.get("changed_files_outside_repo_map", [])] if isinstance(scope.get("changed_files_outside_repo_map"), list) else []
    evidence = scope.get("evidence") if isinstance(scope.get("evidence"), list) else []
    source_without_linked_tests = [
        str(item.get("path"))
        for item in evidence
        if (
            isinstance(item, dict)
            and str(item.get("path") or "").endswith(".py")
            and not test_like_path(str(item.get("path") or ""))
            and not item.get("linked_tests")
        )
    ]
    total = len(in_map) + len(outside_map)
    return {
        "status": "needs_attention" if outside_map or source_without_linked_tests else "covered",
        "changed_file_count": total,
        "mapped_changed_file_count": len(in_map),
        "unmapped_changed_file_count": len(outside_map),
        "unmapped_changed_files": outside_map,
        "source_without_linked_tests": source_without_linked_tests[:12],
    }


def repository_investigation_review(
    survey: dict[str, Any],
    patch: dict[str, Any],
    scope_review: dict[str, Any],
) -> dict[str, Any]:
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    investigation = survey.get("engineering_investigation") if isinstance(survey.get("engineering_investigation"), dict) else {}
    readiness = survey.get("engineering_readiness") if isinstance(survey.get("engineering_readiness"), dict) else {}
    ranked_files = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    read_order = repo_map.get("recommended_read_order") if isinstance(repo_map.get("recommended_read_order"), list) else []
    targeted_reads = investigation.get("targeted_reading_plan") if isinstance(investigation.get("targeted_reading_plan"), list) else []
    hypotheses = investigation.get("hypotheses") if isinstance(investigation.get("hypotheses"), list) else []
    impact_matrix = readiness.get("impact_matrix") if isinstance(readiness.get("impact_matrix"), list) else []
    source_excerpt_summary = (
        patch.get("source_excerpt_summary")
        if isinstance(patch.get("source_excerpt_summary"), list)
        else []
    )
    read_excerpts = [
        item
        for item in source_excerpt_summary
        if isinstance(item, dict) and item.get("status") == "read"
    ]
    changed_files = patch.get("changed_files") if isinstance(patch.get("changed_files"), list) else []
    concrete_changes = [item for item in changed_files if isinstance(item, dict) and item.get("path")]
    changed_file_count = len(concrete_changes)
    preexisting_changed_file_count = len([item for item in concrete_changes if not item.get("created")])
    mapped_changed_file_count = int(scope_review.get("mapped_changed_file_count") or 0)
    diagnostics = patch.get("diagnostics") if isinstance(patch.get("diagnostics"), dict) else {}
    planned_output_paths = [
        str(value)
        for key, value in diagnostics.items()
        if key.endswith("_path") and isinstance(value, str) and value
    ]
    marker_write_outputs = [
        str(item.get("path") or "")
        for item in concrete_changes
        if item.get("operation") == "write_file" and (item.get("created") or item.get("idempotent"))
    ]
    for path in marker_write_outputs:
        if path and path not in planned_output_paths:
            planned_output_paths.append(path)
    planned_output_path_set = set(planned_output_paths)
    explicit_output_surface = bool(planned_output_paths) and all(
        str(item.get("path") or "") in planned_output_path_set
        for item in concrete_changes
        if item.get("operation") == "write_file" and (item.get("created") or item.get("idempotent"))
    ) and all(
        item.get("operation") == "write_file" and (item.get("created") or item.get("idempotent"))
        for item in concrete_changes
    )
    raw_unmapped_changed_files = (
        scope_review.get("unmapped_changed_files")
        if isinstance(scope_review.get("unmapped_changed_files"), list)
        else []
    )
    unmapped_changed_files = {str(path) for path in raw_unmapped_changed_files}
    unmapped_preexisting = [
        str(item.get("path") or "")
        for item in concrete_changes
        if not item.get("created")
        and not item.get("idempotent")
        and str(item.get("path") or "") not in planned_output_path_set
        and str(item.get("path") or "") in unmapped_changed_files
    ]
    created_changes = [str(item.get("path") or "") for item in concrete_changes if item.get("created")]
    checks = [
        {
            "check": "ranked_repo_map_present",
            "status": "pass" if ranked_files or explicit_output_surface else "blocker",
            "evidence": {
                "ranked_file_count": len(ranked_files),
                "explicit_output_surface": explicit_output_surface,
                "planned_output_paths": planned_output_paths[:12],
            },
        },
        {
            "check": "targeted_reading_plan_present",
            "status": "pass" if (targeted_reads and read_order) or explicit_output_surface else "blocker",
            "evidence": {
                "targeted_read_count": len(targeted_reads),
                "recommended_read_count": len(read_order),
                "explicit_output_surface": explicit_output_surface,
            },
        },
        {
            "check": "hypotheses_present",
            "status": "pass" if hypotheses else "blocker",
            "evidence": {"hypothesis_count": len(hypotheses)},
        },
        {
            "check": "impact_matrix_present",
            "status": "pass" if impact_matrix or explicit_output_surface else "blocker",
            "evidence": {
                "impact_file_count": len(impact_matrix),
                "explicit_output_surface": explicit_output_surface,
            },
        },
        {
            "check": "pre_mutation_source_reads_present",
            "status": "pass" if read_excerpts or explicit_output_surface else "blocker",
            "evidence": {
                "read_excerpt_count": len(read_excerpts),
                "read_paths": [str(item.get("path") or "") for item in read_excerpts[:12]],
                "explicit_output_surface": explicit_output_surface,
            },
        },
        {
            "check": "changed_files_mapped_to_survey",
            "status": "pass" if preexisting_changed_file_count == 0 or not unmapped_preexisting else "blocker",
            "evidence": {
                "changed_file_count": changed_file_count,
                "preexisting_changed_file_count": preexisting_changed_file_count,
                "mapped_changed_file_count": mapped_changed_file_count,
                "created_changes_allowed_as_explicit_outputs": created_changes[:12],
                "unmapped_preexisting_changed_files": unmapped_preexisting[:12],
            },
        },
    ]
    blockers = [
        check
        for check in checks
        if check.get("status") == "blocker"
    ]
    return {
        "status": "blocked" if blockers else "covered",
        "checks": checks,
        "blockers": blockers,
        "summary": "Repository investigation evidence is sufficient." if not blockers else "Repository investigation evidence is incomplete.",
    }


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


def git_dirty_target_evidence(repo_root: Path, operations: list[Any]) -> dict[str, Any]:
    if not (repo_root / ".git").exists():
        return {"git_repo": False, "dirty_targets": []}
    target_paths: list[str] = []
    seen: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        try:
            path = safe_repo_path(repo_root, str(operation.get("path") or ""))
        except ValueError:
            continue
        rel = str(path.relative_to(repo_root))
        if rel not in seen:
            seen.add(rel)
            target_paths.append(rel)
    dirty_targets: list[dict[str, Any]] = []
    for rel in target_paths:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--", rel],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            dirty_targets.append({"path": rel, "status": "unknown", "diagnostic": completed.stderr[-1000:]})
            continue
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if lines:
            dirty_targets.append({"path": rel, "status": "dirty", "porcelain": lines})
    return {"git_repo": True, "target_paths": target_paths, "dirty_targets": dirty_targets}


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


def extract_json_after_marker(text: str, marker: str) -> dict[str, Any]:
    start = text.find(marker)
    if start < 0:
        return {}
    payload_text = text[start + len(marker):].strip()
    if payload_text.startswith("```"):
        lines = payload_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if "```" in lines:
            lines = lines[:lines.index("```")]
        payload_text = "\n".join(lines).strip()
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(payload_text)
    except json.JSONDecodeError as exc:
        label = marker.rstrip(":")
        raise ValueError(f"{label} JSON is invalid: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def marker_value(text: str, marker: str) -> str:
    marker_at = text.find(marker)
    if marker_at < 0:
        return ""
    return text[marker_at + len(marker):].strip().splitlines()[0].strip()


def marker_block(text: str, marker: str) -> str:
    marker_at = text.find(marker)
    if marker_at < 0:
        return ""
    block = text[marker_at + len(marker):]
    stop_markers = [
        "\nCERAXIA_TARGET_REPO:",
        "\nCERAXIA_PATCH:",
        "\nCERAXIA_FEATURE:",
        "\nCERAXIA_INTEGRATION_CONTRACT:",
        "\nCERAXIA_PUBLIC_API_COMPAT:",
        "\nCERAXIA_CONFIG_RUNTIME:",
        "\nCERAXIA_REFACTOR:",
        "\nCERAXIA_EDGE_FIX:",
        "\nCERAXIA_DATA_MIGRATION:",
        "\nCERAXIA_FILES:",
        "\nCERAXIA_CREATE_FILE:",
        "\nCERAXIA_FILE_CONTENT:",
        "\nCERAXIA_REPLACE_IN_FILE:",
        "\nCERAXIA_OLD:",
        "\nCERAXIA_NEW:",
        "\nCERAXIA_VERIFY:",
    ]
    stop_positions = [pos for marker_item in stop_markers if (pos := block.find(marker_item)) >= 0]
    if stop_positions:
        block = block[: min(stop_positions)]
    return block.strip("\n")


def verification_commands_from_markers(goal: str) -> list[str]:
    commands: list[str] = []
    for line in goal.splitlines():
        stripped = line.strip()
        if stripped.startswith("CERAXIA_VERIFY:"):
            command = stripped.removeprefix("CERAXIA_VERIFY:").strip()
            if command:
                commands.append(command)
    return commands


def verification_commands_from_natural_goal(goal: str) -> list[str]:
    commands = verification_commands_from_markers(goal)
    for match in re.finditer(r"(?:проверь|запусти|run|verify|test)\s+`([^`]+)`", goal, flags=re.IGNORECASE):
        command = match.group(1).strip()
        if command and command not in commands:
            commands.append(command)
    return commands


def ambiguity_analysis_from_goal(goal: str, repo_root: Path) -> dict[str, Any]:
    lowered = goal.lower()
    hard_ambiguity_markers = [
        "не задан",
        "не указ",
        "если вариантов несколько",
        "ambiguous",
    ]
    soft_ambiguity_markers = [
        "не угадывай",
        "улучши",
        "improve",
    ]
    hard_ambiguity = any(marker in lowered for marker in hard_ambiguity_markers)
    soft_ambiguity = any(marker in lowered for marker in soft_ambiguity_markers)
    if not hard_ambiguity and not soft_ambiguity:
        return {}
    test_files = [
        str(path.relative_to(repo_root))
        for path in sorted(repo_root.rglob("*.py"))
        if test_like_path(str(path.relative_to(repo_root))) and not any(part in EXCLUDED_DIRS for part in path.relative_to(repo_root).parts)
    ][:8]
    verification_commands = verification_commands_from_natural_goal(goal)
    if soft_ambiguity and not hard_ambiguity and test_files and verification_commands:
        return {}
    candidates: list[dict[str, str]] = []
    if any(marker in lowered for marker in ("ошиб", "error", "exception")):
        candidates.extend(
            [
                {
                    "interpretation": "raise_exception",
                    "risk": "callers may expect exceptions and HTTP/API layers may need mapping",
                },
                {
                    "interpretation": "return_error_object",
                    "risk": "changes return shape and may break existing callers",
                },
                {
                    "interpretation": "fallback_default",
                    "risk": "can hide invalid input and corrupt downstream data",
                },
            ]
        )
    if not candidates:
        candidates.append(
            {
                "interpretation": "multiple_valid_implementations",
                "risk": "task lacks an acceptance criterion that distinguishes correct behavior",
            }
        )
    return {
        "status": "ambiguous",
        "reason": "task does not provide enough acceptance criteria for safe source mutation",
        "candidate_interpretations": candidates,
        "safe_next_question": "Specify the expected behavior, error shape, and verification command before source mutation.",
        "available_test_files": test_files,
    }


def infer_simple_replace_patch_spec(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    patterns = [
        r"(?:в\s+файле|в|in)\s+`(?P<path>[^`]+)`.*?(?:замени|replace)\s+`(?P<old>[^`]+)`\s+(?:на|with)\s+`(?P<new>[^`]+)`",
        r"(?:замени|replace)\s+`(?P<old>[^`]+)`\s+(?:на|with)\s+`(?P<new>[^`]+)`.*?(?:в\s+файле|в|in)\s+`(?P<path>[^`]+)`",
    ]
    for pattern in patterns:
        match = re.search(pattern, goal, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        raw_path = match.group("path").strip()
        old = match.group("old")
        new = match.group("new")
        if "\x00" in old or "\x00" in new:
            raise ValueError("inferred replace patch cannot contain NUL bytes")
        return {
            "source": "natural_language_simple_replace",
            "operations": [
                {
                    "type": "replace",
                    "path": raw_path,
                    "old": old,
                    "new": new,
                }
            ],
            "verification_commands": verification_commands_from_natural_goal(goal),
        }
    return {}


def safe_return_literal(raw: str) -> str:
    value = raw.strip()
    if re.fullmatch(r"[+-]?\d+", value) or value in {"True", "False", "None"}:
        return value
    if re.fullmatch(r"'[^'\\]*(?:\\.[^'\\]*)*'", value) or re.fullmatch(r'"[^"\\]*(?:\\.[^"\\]*)*"', value):
        return value
    raise ValueError(f"unsupported inferred return literal: {raw}")


def infer_add_function_patch_spec(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    patterns = [
        r"(?:в\s+файле|в|in)\s+`(?P<path>[^`]+)`.*?(?:добавь|add).*?(?:функц\w*|function)\s+`(?P<function>[A-Za-z_][A-Za-z0-9_]*)`.*?(?:возвращ\w*|return(?:ing)?)\s+`(?P<literal>[^`]+)`",
        r"(?:добавь|add).*?(?:функц\w*|function)\s+`(?P<function>[A-Za-z_][A-Za-z0-9_]*)`.*?(?:в\s+файле|в|in)\s+`(?P<path>[^`]+)`.*?(?:возвращ\w*|return(?:ing)?)\s+`(?P<literal>[^`]+)`",
    ]
    for pattern in patterns:
        match = re.search(pattern, goal, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        function_name = match.group("function")
        literal = safe_return_literal(match.group("literal"))
        content = f"\n\ndef {function_name}():\n    return {literal}\n"
        return {
            "source": "natural_language_add_function",
            "operations": [
                {
                    "type": "append",
                    "path": match.group("path").strip(),
                    "content": content,
                    "python_function_name": function_name,
                }
            ],
            "verification_commands": verification_commands_from_natural_goal(goal),
        }
    return {}


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


def ast_return_literal_for_function(source_path: Path, function_name: str) -> str:
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ""
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        returns = [item for item in ast.walk(node) if isinstance(item, ast.Return)]
        if len(returns) != 1:
            return ""
        value = returns[0].value
        if isinstance(value, ast.Constant):
            if isinstance(value.value, bool):
                return "True" if value.value else "False"
            if value.value is None:
                return "None"
            if isinstance(value.value, int):
                return str(value.value)
            if isinstance(value.value, str):
                return repr(value.value)
    return ""


def infer_return_mismatch_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    candidates: list[dict[str, str]] = []
    for candidate in test_expectation_candidates(repo_root, goal):
        source_path = safe_repo_path(repo_root, candidate["module_path"])
        current = source_path.read_text(encoding="utf-8")
        function_name = candidate["function_name"]
        if not re.search(rf"^\s*def\s+{re.escape(function_name)}\s*\(", current, flags=re.MULTILINE):
            continue
        actual = ast_return_literal_for_function(source_path, function_name)
        expected = candidate["literal"]
        if not actual or actual == expected:
            continue
        if current.count(f"return {actual}") != 1:
            continue
        candidates.append({**candidate, "actual": actual})
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred return mismatch requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    commands = verification_commands_from_natural_goal(goal)
    if not commands:
        test_module = candidate["test_path"][:-3].replace("/", ".")
        commands = [f"python -m unittest {test_module}"]
    return {
        "source": "test_inferred_return_mismatch",
        "diagnostics": {
            "kind": "test_inferred_return_mismatch",
            "test_path": candidate["test_path"],
            "module_path": candidate["module_path"],
            "function_name": candidate["function_name"],
            "actual": candidate["actual"],
            "expected": candidate["literal"],
        },
        "operations": [
            {
                "type": "replace",
                "path": candidate["module_path"],
                "old": f"return {candidate['actual']}",
                "new": f"return {candidate['literal']}",
            }
        ],
        "verification_commands": commands,
    }


def arithmetic_test_expectation_candidates(repo_root: Path, goal: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for test_path in discovered_test_paths(repo_root, goal):
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", text, flags=re.MULTILINE)
        imported_modules = {function_name: module_name for module_name, function_name in imports}
        for match in re.finditer(
            r"assertEqual\(\s*([A-Za-z_][A-Za-z0-9_]*)\(\s*([+-]?\d+)\s*,\s*([+-]?\d+)\s*\)\s*,\s*([+-]?\d+)\s*\)",
            text,
        ):
            function_name, left_raw, right_raw, expected_raw = match.groups()
            module_name = imported_modules.get(function_name, "")
            if not module_name:
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
                    "left": int(left_raw),
                    "right": int(right_raw),
                    "expected": int(expected_raw),
                }
            )
            delegated = delegated_arithmetic_candidate(
                repo_root,
                test_path,
                module_path,
                function_name,
                int(left_raw),
                int(right_raw),
                int(expected_raw),
            )
            if delegated:
                candidates.append(delegated)
    return candidates


def delegated_arithmetic_candidate(
    repo_root: Path,
    test_path: str,
    module_path: str,
    function_name: str,
    left: int,
    right: int,
    expected: int,
) -> dict[str, Any]:
    source_path = safe_repo_path(repo_root, module_path)
    try:
        text = source_path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(source_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {}
    imports: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        for alias in node.names:
            imports[alias.asname or alias.name] = f"{node.module.replace('.', '/')}.py"
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        args = [arg.arg for arg in node.args.args]
        returns = [item for item in ast.walk(node) if isinstance(item, ast.Return)]
        if len(args) != 2 or len(returns) != 1:
            return {}
        value = returns[0].value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
            return {}
        if len(value.args) != 2 or not all(isinstance(item, ast.Name) for item in value.args):
            return {}
        call_args = [item.id for item in value.args if isinstance(item, ast.Name)]
        if call_args != args:
            return {}
        target_module_path = imports.get(value.func.id)
        if not target_module_path:
            return {}
        target_path = safe_repo_path(repo_root, target_module_path)
        if not target_path.exists():
            return {}
        return {
            "test_path": test_path,
            "module_path": target_module_path,
            "function_name": value.func.id,
            "left": left,
            "right": right,
            "expected": expected,
            "delegated_from": {
                "module_path": module_path,
                "function_name": function_name,
            },
        }


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


def infer_arithmetic_return_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    candidates: list[dict[str, Any]] = []
    for candidate in arithmetic_test_expectation_candidates(repo_root, goal):
        source_path = safe_repo_path(repo_root, candidate["module_path"])
        function = simple_function_return_segment(source_path, str(candidate["function_name"]))
        args = function.get("args") if isinstance(function.get("args"), list) else []
        if len(args) != 2:
            continue
        left_name, right_name = str(args[0]), str(args[1])
        left = int(candidate["left"])
        right = int(candidate["right"])
        expected = int(candidate["expected"])
        options = [
            (f"{left_name} + {right_name}", left + right),
            (f"{left_name} - {right_name}", left - right),
            (f"{right_name} - {left_name}", right - left),
            (f"{left_name} * {right_name}", left * right),
            (f"{left_name} - ({left_name} * {right_name} / 100)", left - (left * right / 100)),
        ]
        matching = [expr for expr, value in options if value == expected]
        if len(matching) != 1:
            continue
        new_expr = matching[0]
        old_expr = str(function.get("return_expr") or "")
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(", old_expr):
            continue
        if old_expr == new_expr:
            continue
        content = source_path.read_text(encoding="utf-8")
        old = f"return {old_expr}"
        new = f"return {new_expr}"
        if content.count(old) != 1:
            continue
        candidates.append({**candidate, "actual_expression": old_expr, "replacement_expression": new_expr})
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred arithmetic return requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    commands = verification_commands_from_natural_goal(goal)
    if not commands:
        test_module = str(candidate["test_path"])[:-3].replace("/", ".")
        commands = [f"python -m unittest {test_module}"]
    return {
        "source": "test_inferred_arithmetic_return",
        "diagnostics": {
            "kind": "test_inferred_arithmetic_return",
            "test_path": candidate["test_path"],
            "module_path": candidate["module_path"],
            "function_name": candidate["function_name"],
            "actual_expression": candidate["actual_expression"],
            "replacement_expression": candidate["replacement_expression"],
            "example": {
                "left": candidate["left"],
                "right": candidate["right"],
                "expected": candidate["expected"],
            },
            "delegated_from": candidate.get("delegated_from", {}),
        },
        "operations": [
            {
                "type": "replace",
                "path": candidate["module_path"],
                "old": f"return {candidate['actual_expression']}",
                "new": f"return {candidate['replacement_expression']}",
            }
        ],
        "verification_commands": commands,
    }


def infer_missing_function_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    candidates: list[dict[str, str]] = []
    for candidate in test_expectation_candidates(repo_root, goal):
        source_path = safe_repo_path(repo_root, candidate["module_path"])
        current = source_path.read_text(encoding="utf-8")
        if re.search(rf"^\s*def\s+{re.escape(candidate['function_name'])}\s*\(", current, flags=re.MULTILINE):
            continue
        candidates.append(candidate)
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred missing function requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    function_name = candidate["function_name"]
    content = f"\n\ndef {function_name}():\n    return {candidate['literal']}\n"
    commands = verification_commands_from_natural_goal(goal)
    if not commands:
        test_module = candidate["test_path"][:-3].replace("/", ".")
        commands = [f"python -m unittest {test_module}"]
    return {
        "source": "test_inferred_missing_function",
        "diagnostics": {
            "kind": "test_inferred_missing_function",
            "test_path": candidate["test_path"],
            "module_path": candidate["module_path"],
            "function_name": function_name,
            "expected": candidate["literal"],
        },
        "operations": [
            {
                "type": "append",
                "path": candidate["module_path"],
                "content": content,
                "python_function_name": function_name,
            }
        ],
        "verification_commands": commands,
    }


def patch_spec_from_feature_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_FEATURE:")
    if not payload:
        return {}
    module_path = str(payload.get("module_path") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    docs_path = str(payload.get("docs_path") or "").strip()
    caller_path = str(payload.get("caller_path") or "").strip()
    if not module_path or not function_name or not test_path or not docs_path or not caller_path:
        raise ValueError("CERAXIA_FEATURE requires module_path, function_name, test_path, docs_path, and caller_path")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name):
        raise ValueError("CERAXIA_FEATURE function_name must be a valid Python identifier")
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in arguments):
        raise ValueError("CERAXIA_FEATURE arguments must be a non-empty list of Python identifiers")
    expression = str(payload.get("return_expression") or "").strip()
    if not expression or "\n" in expression or not re.fullmatch(r"[A-Za-z0-9_ +\-*/().]+", expression):
        raise ValueError("CERAXIA_FEATURE return_expression must be a simple arithmetic expression")
    test_cases = payload.get("test_cases")
    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("CERAXIA_FEATURE test_cases must be a non-empty list")
    rendered_cases: list[str] = []
    for index, item in enumerate(test_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_FEATURE test case {index} must be an object")
        inputs = item.get("inputs")
        expected = item.get("expected")
        if not isinstance(inputs, list) or len(inputs) != len(arguments):
            raise ValueError(f"CERAXIA_FEATURE test case {index} inputs must match arguments")
        if not all(isinstance(value, (int, float)) for value in inputs) or not isinstance(expected, (int, float)):
            raise ValueError(f"CERAXIA_FEATURE test case {index} supports only numeric inputs and expected values")
        rendered_cases.append(f"        self.assertEqual({function_name}({', '.join(str(value) for value in inputs)}), {expected})")
    module_content = f"def {function_name}({', '.join(arguments)}):\n    return {expression}\n"
    class_name = "".join(part.capitalize() for part in function_name.split("_")) + "Test"
    test_content = (
        f"import unittest\nfrom {module_path[:-3].replace('/', '.')} import {function_name}\n\n"
        f"class {class_name}(unittest.TestCase):\n"
        f"    def test_{function_name}(self):\n"
        + "\n".join(rendered_cases)
        + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    docs_title = str(payload.get("docs_title") or function_name.replace("_", " ").title())
    docs_content = f"# {docs_title}\n\nFunction `{function_name}` is available in `{module_path}` and is covered by `{test_path}`.\n"
    caller_function = str(payload.get("caller_function") or f"use_{function_name}").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", caller_function):
        raise ValueError("CERAXIA_FEATURE caller_function must be a valid Python identifier")
    caller_content = (
        f"from {module_path[:-3].replace('/', '.')} import {function_name}\n\n"
        f"def {caller_function}({', '.join(arguments)}):\n"
        f"    return {function_name}({', '.join(arguments)})\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_FEATURE verification_commands must be a list of strings")
    return {
        "source": "feature_marker_synthesis",
        "diagnostics": {
            "kind": "feature_marker_synthesis",
            "function_name": function_name,
            "module_path": module_path,
            "test_path": test_path,
            "docs_path": docs_path,
            "caller_path": caller_path,
        },
        "operations": [
            {"type": "write_file", "path": module_path, "content": module_content},
            {"type": "write_file", "path": test_path, "content": test_content},
            {"type": "write_file", "path": docs_path, "content": docs_content},
            {"type": "write_file", "path": caller_path, "content": caller_content},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_integration_contract_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_INTEGRATION_CONTRACT:")
    if not payload:
        return {}
    contract_path = str(payload.get("contract_path") or "").strip()
    implementation_path = str(payload.get("implementation_path") or "").strip()
    caller_path = str(payload.get("caller_path") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    report_path = str(payload.get("report_path") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    caller_function = str(payload.get("caller_function") or "").strip()
    response_field = str(payload.get("response_field") or "").strip()
    expression = str(payload.get("return_expression") or "").strip()
    required = [contract_path, implementation_path, caller_path, test_path, report_path, function_name, caller_function, response_field, expression]
    if not all(required):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT requires contract, implementation, caller, test, report, function, caller_function, response_field, and return_expression")
    if not implementation_path.endswith(".py") or not caller_path.endswith(".py") or not test_path.endswith(".py"):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT implementation, caller, and test paths must be Python files")
    identifiers = [function_name, caller_function, response_field]
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in identifiers):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT function and field names must be simple identifiers")
    request_fields = payload.get("request_fields")
    if not isinstance(request_fields, list) or not request_fields or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in request_fields):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT request_fields must be a non-empty list of identifiers")
    if "\n" in expression or not re.fullmatch(r"[A-Za-z0-9_ +\-*/().]+", expression):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT return_expression must be a simple arithmetic expression")
    test_cases = payload.get("test_cases")
    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT test_cases must be a non-empty list")
    contract_content = json.dumps(
        {
            "endpoint": function_name,
            "request_fields": request_fields,
            "response_fields": [response_field],
            "caller": caller_function,
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    assignments = "".join(f"    {field} = payload['{field}']\n" for field in request_fields)
    implementation_content = (
        f"def {function_name}(payload):\n"
        f"{assignments}"
        f"    return {{'{response_field}': {expression}}}\n"
    )
    implementation_module = implementation_path[:-3].replace("/", ".")
    caller_args = ", ".join(request_fields)
    caller_payload = ", ".join(f"'{field}': {field}" for field in request_fields)
    caller_content = (
        f"from {implementation_module} import {function_name}\n\n"
        f"def {caller_function}({caller_args}):\n"
        f"    return {function_name}({{{caller_payload}}})['{response_field}']\n"
    )
    rendered_cases: list[str] = []
    for index, item in enumerate(test_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_INTEGRATION_CONTRACT test case {index} must be an object")
        inputs = item.get("inputs")
        expected = item.get("expected")
        if not isinstance(inputs, dict) or set(inputs) != set(request_fields):
            raise ValueError(f"CERAXIA_INTEGRATION_CONTRACT test case {index} inputs must match request_fields")
        if not all(isinstance(inputs[field], (int, float)) for field in request_fields) or not isinstance(expected, (int, float)):
            raise ValueError(f"CERAXIA_INTEGRATION_CONTRACT test case {index} supports only numeric values")
        payload_literal = "{" + ", ".join(f"{field!r}: {inputs[field]!r}" for field in request_fields) + "}"
        args_literal = ", ".join(repr(inputs[field]) for field in request_fields)
        rendered_cases.append(f"        self.assertEqual({function_name}({payload_literal})['{response_field}'], {expected!r})")
        rendered_cases.append(f"        self.assertEqual({caller_function}({args_literal}), {expected!r})")
    caller_module = caller_path[:-3].replace("/", ".")
    class_name = "".join(part.capitalize() for part in function_name.split("_")) + "ContractTest"
    test_content = (
        f"import json\nimport unittest\nfrom pathlib import Path\nfrom {implementation_module} import {function_name}\nfrom {caller_module} import {caller_function}\n\n"
        f"class {class_name}(unittest.TestCase):\n"
        "    def test_contract_declares_response_field(self):\n"
        f"        contract = json.loads(Path('{contract_path}').read_text(encoding='utf-8'))\n"
        f"        self.assertIn('{response_field}', contract['response_fields'])\n\n"
        "    def test_implementation_and_caller_follow_contract(self):\n"
        + "\n".join(rendered_cases)
        + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    report_content = (
        "# Integration Contract Update\n\n"
        f"- Contract: `{contract_path}`\n"
        f"- Implementation: `{implementation_path}`\n"
        f"- Caller: `{caller_path}`\n"
        f"- Tests: `{test_path}`\n"
        f"- Response field: `{response_field}`\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT verification_commands must be a list of strings")
    return {
        "source": "integration_contract_marker_synthesis",
        "diagnostics": {
            "kind": "integration_contract_marker_synthesis",
            "contract_path": contract_path,
            "implementation_path": implementation_path,
            "caller_path": caller_path,
            "test_path": test_path,
            "report_path": report_path,
            "request_fields": request_fields,
            "response_field": response_field,
        },
        "operations": [
            {"type": "write_file", "path": contract_path, "content": contract_content, "overwrite": True},
            {"type": "write_file", "path": implementation_path, "content": implementation_content, "overwrite": True},
            {"type": "write_file", "path": caller_path, "content": caller_content, "overwrite": True},
            {"type": "write_file", "path": test_path, "content": test_content, "overwrite": True},
            {"type": "write_file", "path": report_path, "content": report_content, "overwrite": True},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_public_api_compat_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_PUBLIC_API_COMPAT:")
    if not payload:
        return {}
    source_path = str(payload.get("source_path") or "").strip()
    caller_path = str(payload.get("caller_path") or "").strip()
    docs_path = str(payload.get("docs_path") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    caller_function = str(payload.get("caller_function") or "").strip()
    expression = str(payload.get("return_expression") or "").strip()
    if not all([source_path, caller_path, docs_path, test_path, function_name, caller_function, expression]):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT requires source_path, caller_path, docs_path, test_path, function_name, caller_function, and return_expression")
    if not source_path.endswith(".py") or not caller_path.endswith(".py") or not test_path.endswith(".py"):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT source, caller, and test paths must be Python files")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", caller_function):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT function names must be valid identifiers")
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in arguments):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT arguments must be a non-empty list of identifiers")
    if "\n" in expression or not re.fullmatch(r"[A-Za-z0-9_ +\-*/().]+", expression):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT return_expression must be a simple arithmetic expression")
    test_cases = payload.get("test_cases")
    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT test_cases must be a non-empty list")
    signature = f"{function_name}({', '.join(arguments)})"
    source_content = (
        f"def {signature}:\n"
        f"    \"\"\"Public API: keep signature `{signature}` stable.\"\"\"\n"
        f"    return {expression}\n"
    )
    source_module = source_path[:-3].replace("/", ".")
    caller_content = (
        f"from {source_module} import {function_name}\n\n"
        f"def {caller_function}({', '.join(arguments)}):\n"
        f"    return {function_name}({', '.join(arguments)})\n"
    )
    docs_content = (
        f"# Public API Compatibility\n\n"
        f"`{signature}` is the stable public function. Callers must keep using the same positional arguments.\n"
    )
    rendered_cases: list[str] = []
    for index, item in enumerate(test_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_PUBLIC_API_COMPAT test case {index} must be an object")
        inputs = item.get("inputs")
        expected = item.get("expected")
        if not isinstance(inputs, list) or len(inputs) != len(arguments):
            raise ValueError(f"CERAXIA_PUBLIC_API_COMPAT test case {index} inputs must match arguments")
        if not all(isinstance(value, (int, float)) for value in inputs) or not isinstance(expected, (int, float)):
            raise ValueError(f"CERAXIA_PUBLIC_API_COMPAT test case {index} supports only numeric values")
        args_literal = ", ".join(repr(value) for value in inputs)
        rendered_cases.append(f"        self.assertEqual({function_name}({args_literal}), {expected!r})")
        rendered_cases.append(f"        self.assertEqual({caller_function}({args_literal}), {expected!r})")
    caller_module = caller_path[:-3].replace("/", ".")
    class_name = "".join(part.capitalize() for part in function_name.split("_")) + "CompatTest"
    test_content = (
        f"import inspect\nimport unittest\nfrom {source_module} import {function_name}\nfrom {caller_module} import {caller_function}\n\n"
        f"class {class_name}(unittest.TestCase):\n"
        "    def test_public_signature_stays_compatible(self):\n"
        f"        self.assertEqual(list(inspect.signature({function_name}).parameters), {arguments!r})\n\n"
        "    def test_behavior_and_callers(self):\n"
        + "\n".join(rendered_cases)
        + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT verification_commands must be a list of strings")
    return {
        "source": "public_api_compat_marker_synthesis",
        "diagnostics": {
            "kind": "public_api_compat_marker_synthesis",
            "source_path": source_path,
            "caller_path": caller_path,
            "docs_path": docs_path,
            "test_path": test_path,
            "function_name": function_name,
            "public_signature": signature,
            "caller_function": caller_function,
        },
        "operations": [
            {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True},
            {"type": "write_file", "path": caller_path, "content": caller_content, "overwrite": True},
            {"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True},
            {"type": "write_file", "path": test_path, "content": test_content, "overwrite": True},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_config_runtime_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_CONFIG_RUNTIME:")
    if not payload:
        return {}
    config_path = str(payload.get("config_path") or "").strip()
    loader_path = str(payload.get("loader_path") or "").strip()
    entrypoint_path = str(payload.get("entrypoint_path") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    setting_key = str(payload.get("setting_key") or "").strip()
    env_var = str(payload.get("env_var") or "").strip()
    default_value = payload.get("default_value")
    if not all([config_path, loader_path, entrypoint_path, test_path, setting_key, env_var]):
        raise ValueError("CERAXIA_CONFIG_RUNTIME requires config_path, loader_path, entrypoint_path, test_path, setting_key, and env_var")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", setting_key):
        raise ValueError("CERAXIA_CONFIG_RUNTIME setting_key must be a simple identifier")
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env_var):
        raise ValueError("CERAXIA_CONFIG_RUNTIME env_var must be an uppercase environment variable name")
    if not isinstance(default_value, (str, int, float, bool)):
        raise ValueError("CERAXIA_CONFIG_RUNTIME default_value must be a JSON scalar")
    config_content = json.dumps({setting_key: default_value}, ensure_ascii=False, indent=2) + "\n"
    loader_module = loader_path[:-3].replace("/", ".")
    config_literal = repr(config_path)
    loader_parent_depth = len(PurePosixPath(loader_path).parent.parts)
    config_root_steps = "\n".join(["CONFIG_ROOT = CONFIG_ROOT.parent" for _ in range(loader_parent_depth)])
    if config_root_steps:
        config_root_steps += "\n"
    loader_content = (
        "import json\n"
        "import os\n"
        "from pathlib import Path\n\n"
        "CONFIG_ROOT = Path(__file__).resolve().parent\n"
        f"{config_root_steps}"
        f"CONFIG_PATH = CONFIG_ROOT / {config_literal}\n\n"
        "def load_settings():\n"
        "    data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))\n"
        f"    value = os.environ.get('{env_var}', data.get('{setting_key}', {default_value!r}))\n"
        f"    return {{'{setting_key}': value}}\n"
    )
    entrypoint_content = (
        "#!/usr/bin/env sh\n"
        "set -eu\n"
        f"export {env_var}=\"${{{env_var}:-{default_value}}}\"\n"
        f"python -m {loader_module}\n"
    )
    test_content = (
        f"import os\nimport unittest\nfrom {loader_module} import load_settings\n\n"
        "class ConfigRuntimeTest(unittest.TestCase):\n"
        "    def test_default_setting(self):\n"
        f"        os.environ.pop('{env_var}', None)\n"
        f"        self.assertEqual(load_settings()['{setting_key}'], {default_value!r})\n\n"
        "    def test_env_override(self):\n"
        f"        os.environ['{env_var}'] = 'override-value'\n"
        "        try:\n"
        f"            self.assertEqual(load_settings()['{setting_key}'], 'override-value')\n"
        "        finally:\n"
        f"            os.environ.pop('{env_var}', None)\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_CONFIG_RUNTIME verification_commands must be a list of strings")
    return {
        "source": "config_runtime_marker_synthesis",
        "diagnostics": {
            "kind": "config_runtime_marker_synthesis",
            "config_path": config_path,
            "loader_path": loader_path,
            "entrypoint_path": entrypoint_path,
            "test_path": test_path,
            "setting_key": setting_key,
            "env_var": env_var,
        },
        "operations": [
            {"type": "write_file", "path": config_path, "content": config_content},
            {"type": "write_file", "path": loader_path, "content": loader_content},
            {"type": "write_file", "path": entrypoint_path, "content": entrypoint_content},
            {"type": "write_file", "path": test_path, "content": test_content},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_refactor_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_REFACTOR:")
    if not payload:
        return {}
    helper_path = str(payload.get("helper_path") or "").strip()
    helper_function = str(payload.get("helper_function") or "").strip()
    expression = str(payload.get("return_expression") or "").strip()
    if not helper_path or not helper_function or not expression:
        raise ValueError("CERAXIA_REFACTOR requires helper_path, helper_function, and return_expression")
    if not helper_path.endswith(".py"):
        raise ValueError("CERAXIA_REFACTOR helper_path must be a Python file")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", helper_function):
        raise ValueError("CERAXIA_REFACTOR helper_function must be a valid Python identifier")
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in arguments):
        raise ValueError("CERAXIA_REFACTOR arguments must be a non-empty list of Python identifiers")
    if "\n" in expression or not re.fullmatch(r"[A-Za-z0-9_ +\-*/().]+", expression):
        raise ValueError("CERAXIA_REFACTOR return_expression must be a simple arithmetic expression")
    replacements = payload.get("replacements")
    if not isinstance(replacements, list) or len(replacements) < 2:
        raise ValueError("CERAXIA_REFACTOR requires at least two replacements")
    operations: list[dict[str, Any]] = [
        {
            "type": "write_file",
            "path": helper_path,
            "content": f"def {helper_function}({', '.join(arguments)}):\n    return {expression}\n",
        }
    ]
    public_functions: list[str] = []
    touched_paths: list[str] = [helper_path]
    for index, item in enumerate(replacements):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_REFACTOR replacement {index} must be an object")
        path = str(item.get("path") or "").strip()
        old = item.get("old")
        new = item.get("new")
        public_function = str(item.get("public_function") or "").strip()
        if not path or not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError(f"CERAXIA_REFACTOR replacement {index} requires path, old, and new")
        if public_function and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", public_function):
            raise ValueError(f"CERAXIA_REFACTOR replacement {index} public_function must be a valid identifier")
        if public_function:
            public_functions.append(public_function)
        touched_paths.append(path)
        operations.append({"type": "replace", "path": path, "old": old, "new": new})
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = ["python -m unittest discover"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_REFACTOR verification_commands must be a list of strings")
    baseline_commands = payload.get("baseline_verification_commands", [])
    if baseline_commands is None:
        baseline_commands = []
    if not isinstance(baseline_commands, list) or not all(isinstance(item, str) for item in baseline_commands):
        raise ValueError("CERAXIA_REFACTOR baseline_verification_commands must be a list of strings")
    return {
        "source": "refactor_marker_synthesis",
        "diagnostics": {
            "kind": "refactor_marker_synthesis",
            "helper_path": helper_path,
            "helper_function": helper_function,
            "public_functions": public_functions,
            "touched_paths": touched_paths,
            "baseline_verification_commands": baseline_commands,
        },
        "operations": operations,
        "verification_commands": verification_commands,
    }


def patch_spec_from_edge_fix_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_EDGE_FIX:")
    if not payload:
        return {}
    source_path = str(payload.get("source_path") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    if not source_path or not function_name or not test_path:
        raise ValueError("CERAXIA_EDGE_FIX requires source_path, function_name, and test_path")
    if not source_path.endswith(".py") or not test_path.endswith(".py"):
        raise ValueError("CERAXIA_EDGE_FIX source_path and test_path must be Python files")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name):
        raise ValueError("CERAXIA_EDGE_FIX function_name must be a valid Python identifier")
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in arguments):
        raise ValueError("CERAXIA_EDGE_FIX arguments must be a non-empty list of Python identifiers")
    body_lines = payload.get("body_lines")
    if not isinstance(body_lines, list) or not body_lines or not all(isinstance(item, str) and item.strip() for item in body_lines):
        raise ValueError("CERAXIA_EDGE_FIX body_lines must be a non-empty list of strings")
    forbidden_body = re.compile(r"\b(import|open|exec|eval|subprocess|socket|requests)\b")
    if any(forbidden_body.search(line) for line in body_lines):
        raise ValueError("CERAXIA_EDGE_FIX body_lines contain unsafe statements")
    positive_cases = payload.get("positive_cases")
    negative_cases = payload.get("negative_cases")
    if not isinstance(positive_cases, list) or not positive_cases:
        raise ValueError("CERAXIA_EDGE_FIX positive_cases must be a non-empty list")
    if not isinstance(negative_cases, list) or not negative_cases:
        raise ValueError("CERAXIA_EDGE_FIX negative_cases must be a non-empty list")
    source_content = f"def {function_name}({', '.join(arguments)}):\n" + "".join(f"    {line}\n" for line in body_lines)
    ast.parse(source_content)
    test_module = source_path[:-3].replace("/", ".")
    class_name = "".join(part.capitalize() for part in function_name.split("_")) + "EdgeTest"
    rendered_positive: list[str] = []
    for index, item in enumerate(positive_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_EDGE_FIX positive case {index} must be an object")
        inputs = item.get("inputs")
        expected = item.get("expected")
        if not isinstance(inputs, list) or len(inputs) != len(arguments):
            raise ValueError(f"CERAXIA_EDGE_FIX positive case {index} inputs must match arguments")
        rendered_positive.append(f"        self.assertEqual({function_name}({', '.join(repr(value) for value in inputs)}), {expected!r})")
    rendered_negative: list[str] = []
    for index, item in enumerate(negative_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_EDGE_FIX negative case {index} must be an object")
        inputs = item.get("inputs")
        exception = str(item.get("exception") or "ValueError")
        if not isinstance(inputs, list) or len(inputs) != len(arguments):
            raise ValueError(f"CERAXIA_EDGE_FIX negative case {index} inputs must match arguments")
        if exception not in {"ValueError", "TypeError", "KeyError"}:
            raise ValueError(f"CERAXIA_EDGE_FIX negative case {index} uses unsupported exception")
        rendered_negative.append(
            f"        with self.assertRaises({exception}):\n"
            f"            {function_name}({', '.join(repr(value) for value in inputs)})"
        )
    test_content = (
        f"import unittest\nfrom {test_module} import {function_name}\n\n"
        f"class {class_name}(unittest.TestCase):\n"
        "    def test_positive_cases(self):\n"
        + "\n".join(rendered_positive)
        + "\n\n"
        "    def test_negative_cases(self):\n"
        + "\n".join(rendered_negative)
        + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_EDGE_FIX verification_commands must be a list of strings")
    return {
        "source": "edge_fix_marker_synthesis",
        "diagnostics": {
            "kind": "edge_fix_marker_synthesis",
            "source_path": source_path,
            "test_path": test_path,
            "function_name": function_name,
            "positive_case_count": len(positive_cases),
            "negative_case_count": len(negative_cases),
        },
        "operations": [
            {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True},
            {"type": "write_file", "path": test_path, "content": test_content, "overwrite": True},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_data_migration_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_DATA_MIGRATION:")
    if not payload:
        return {}
    source_path = str(payload.get("source_path") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    read_function = str(payload.get("read_function") or "").strip()
    write_function = str(payload.get("write_function") or "").strip()
    id_field = str(payload.get("id_field") or "").strip()
    old_field = str(payload.get("old_field") or "").strip()
    new_field = str(payload.get("new_field") or "").strip()
    if not all([source_path, test_path, read_function, write_function, id_field, old_field, new_field]):
        raise ValueError("CERAXIA_DATA_MIGRATION requires source_path, test_path, read_function, write_function, id_field, old_field, and new_field")
    if not source_path.endswith(".py") or not test_path.endswith(".py"):
        raise ValueError("CERAXIA_DATA_MIGRATION source_path and test_path must be Python files")
    identifiers = [read_function, write_function, id_field, old_field, new_field]
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in identifiers):
        raise ValueError("CERAXIA_DATA_MIGRATION function and field names must be simple identifiers")
    if old_field == new_field:
        raise ValueError("CERAXIA_DATA_MIGRATION old_field and new_field must differ")
    source_module = source_path[:-3].replace("/", ".")
    source_content = (
        f"def {read_function}(record):\n"
        f"    if '{new_field}' in record:\n"
        f"        value = record['{new_field}']\n"
        f"    elif '{old_field}' in record:\n"
        f"        value = record['{old_field}']\n"
        "    else:\n"
        f"        raise KeyError('{new_field}')\n"
        f"    return {{'{id_field}': record['{id_field}'], '{new_field}': value}}\n\n"
        f"def {write_function}(record):\n"
        f"    normalized = {read_function}(record)\n"
        f"    return {{'{id_field}': normalized['{id_field}'], '{new_field}': normalized['{new_field}']}}\n"
    )
    test_content = (
        f"import unittest\nfrom {source_module} import {read_function}, {write_function}\n\n"
        "class DataMigrationTest(unittest.TestCase):\n"
        "    def test_reads_old_shape(self):\n"
        f"        self.assertEqual({read_function}({{'{id_field}': 'a1', '{old_field}': 12}}), {{'{id_field}': 'a1', '{new_field}': 12}})\n\n"
        "    def test_reads_new_shape(self):\n"
        f"        self.assertEqual({read_function}({{'{id_field}': 'b2', '{new_field}': 20}}), {{'{id_field}': 'b2', '{new_field}': 20}})\n\n"
        "    def test_writer_emits_new_shape_only(self):\n"
        f"        self.assertEqual({write_function}({{'{id_field}': 'c3', '{old_field}': 7}}), {{'{id_field}': 'c3', '{new_field}': 7}})\n\n"
        "    def test_missing_value_is_rejected(self):\n"
        f"        with self.assertRaises(KeyError):\n"
        f"            {read_function}({{'{id_field}': 'd4'}})\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_DATA_MIGRATION verification_commands must be a list of strings")
    return {
        "source": "data_migration_marker_synthesis",
        "diagnostics": {
            "kind": "data_migration_marker_synthesis",
            "source_path": source_path,
            "test_path": test_path,
            "read_function": read_function,
            "write_function": write_function,
            "old_field": old_field,
            "new_field": new_field,
            "compatibility": "reader accepts old and new shapes; writer emits new shape",
        },
        "operations": [
            {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True},
            {"type": "write_file", "path": test_path, "content": test_content, "overwrite": True},
        ],
        "verification_commands": verification_commands,
    }


def infer_api_deprecation_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    commands = verification_commands_from_natural_goal(goal)
    test_paths = discovered_test_paths(repo_root, goal)
    candidates: list[dict[str, Any]] = []
    for test_path in test_paths:
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "DeprecationWarning" not in text:
            continue
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", text, flags=re.MULTILINE)
        imported_modules = {function_name: module_name for module_name, function_name in imports}
        for function_name, module_name in ((name, module) for name, module in imported_modules.items()):
            old_call = re.search(rf"{re.escape(function_name)}\(\s*([A-Za-z0-9_'.\"]+)\s*,\s*([A-Za-z0-9_'.\"]+)\s*\)", text)
            keyword_calls = re.findall(rf"{re.escape(function_name)}\([^)]*\b([A-Za-z_][A-Za-z0-9_]*)\s*=", text)
            if not old_call or not keyword_calls:
                continue
            source_path = f"{module_name.replace('.', '/')}.py"
            source = safe_repo_path(repo_root, source_path)
            if not source.exists():
                continue
            try:
                tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            function_node = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name), None)
            if not function_node or len(function_node.args.args) != 2:
                continue
            first_arg = function_node.args.args[0].arg
            old_param = function_node.args.args[1].arg
            new_param = keyword_calls[0]
            caller_matches: list[dict[str, str]] = []
            for caller_name, caller_module in imported_modules.items():
                if caller_name == function_name:
                    continue
                if re.search(rf"{re.escape(caller_name)}\([^)]*\b{re.escape(new_param)}\s*=", text):
                    caller_matches.append(
                        {
                            "caller_name": caller_name,
                            "caller_path": f"{caller_module.replace('.', '/')}.py",
                        }
                    )
            if len(caller_matches) > 1:
                continue
            docs_path = ""
            for docs in sorted(repo_root.rglob("*.md")):
                if any(part in EXCLUDED_DIRS for part in docs.relative_to(repo_root).parts):
                    continue
                docs_text = docs.read_text(encoding="utf-8")
                if function_name in docs_text or source_path.rsplit("/", 1)[0] in str(docs.relative_to(repo_root)):
                    docs_path = str(docs.relative_to(repo_root))
                    break
            if not docs_path:
                continue
            candidates.append(
                {
                    "test_path": test_path,
                    "source_path": source_path,
                    "function_name": function_name,
                    "first_arg": first_arg,
                    "old_param": old_param,
                    "new_param": new_param,
                    "caller": caller_matches[0] if caller_matches else {},
                    "docs_path": docs_path,
                }
            )
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred API deprecation requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    function_name = str(candidate["function_name"])
    first_arg = str(candidate["first_arg"])
    old_param = str(candidate["old_param"])
    new_param = str(candidate["new_param"])
    source_path = str(candidate["source_path"])
    source_content = (
        "import warnings\n\n"
        f"def {function_name}({first_arg}, {old_param}=0, *, {new_param}=None):\n"
        f"    if {new_param} is None:\n"
        f"        {new_param} = {old_param}\n"
        f"        if {old_param} != 0:\n"
        f"            warnings.warn('{old_param} is deprecated; use {new_param}', DeprecationWarning, stacklevel=2)\n"
        f"    return {first_arg} - {new_param}\n"
    )
    operations: list[dict[str, Any]] = [
        {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True}
    ]
    caller = candidate.get("caller") if isinstance(candidate.get("caller"), dict) else {}
    if caller:
        caller_path = str(caller.get("caller_path") or "")
        caller_name = str(caller.get("caller_name") or "")
        if caller_path and caller_name:
            source_module = source_path[:-3].replace("/", ".")
            caller_content = (
                f"from {source_module} import {function_name}\n\n"
                f"def {caller_name}({first_arg}, {new_param}):\n"
                f"    return {function_name}({first_arg}, {new_param}={new_param})\n"
            )
            operations.append({"type": "write_file", "path": caller_path, "content": caller_content, "overwrite": True})
    docs_path = str(candidate["docs_path"])
    docs_content = (
        "# Payments API\n\n"
        f"`{function_name}({first_arg}, {new_param}=...)` is the preferred call style. "
        f"The legacy positional `{old_param}` argument remains supported temporarily and emits `DeprecationWarning`.\n"
    )
    operations.append({"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True})
    if not commands:
        commands = [f"python -m unittest {str(candidate['test_path'])[:-3].replace('/', '.')}"]
    return {
        "source": "test_inferred_api_deprecation",
        "diagnostics": {
            "kind": "test_inferred_api_deprecation",
            "test_path": candidate["test_path"],
            "source_path": source_path,
            "function_name": function_name,
            "old_param": old_param,
            "new_param": new_param,
            "caller": caller,
            "docs_path": docs_path,
        },
        "operations": operations,
        "verification_commands": commands,
    }


def infer_data_migration_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    commands = verification_commands_from_natural_goal(goal)
    test_paths = discovered_test_paths(repo_root, goal)
    candidates: list[dict[str, Any]] = []
    for test_path in test_paths:
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "serialize_record" not in text:
            continue
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+(.+?)\s*$", text, flags=re.MULTILINE)
        imported: dict[str, str] = {}
        for module_name, names_raw in imports:
            for name in names_raw.split(","):
                function_name = name.strip()
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name):
                    imported[function_name] = module_name
        read_function = "normalize_record" if "normalize_record" in imported else ""
        write_function = "serialize_record" if "serialize_record" in imported else ""
        if not read_function or not write_function:
            continue
        if imported[read_function] != imported[write_function]:
            continue
        source_path = f"{imported[read_function].replace('.', '/')}.py"
        source = safe_repo_path(repo_root, source_path)
        if not source.exists():
            continue
        source_text = source.read_text(encoding="utf-8")
        current_fields = re.findall(r"record\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]", source_text)
        if len(current_fields) < 2:
            continue
        id_field = "id" if "id" in current_fields else current_fields[0]
        old_field_candidates = [field for field in current_fields if field != id_field]
        if len(set(old_field_candidates)) != 1:
            continue
        old_field = old_field_candidates[0]
        test_fields = set(re.findall(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*:", text))
        docs_fields: set[str] = set()
        docs_path = ""
        for docs in sorted(repo_root.rglob("*.md")):
            if any(part in EXCLUDED_DIRS for part in docs.relative_to(repo_root).parts):
                continue
            docs_text = docs.read_text(encoding="utf-8")
            if old_field in docs_text or source_path.rsplit("/", 1)[0] in str(docs.relative_to(repo_root)):
                docs_path = str(docs.relative_to(repo_root))
                docs_fields.update(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", docs_text))
                docs_fields.update(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", docs_text))
                break
        new_candidates = sorted((test_fields | docs_fields) - {id_field, old_field})
        new_field = next((field for field in new_candidates if field.endswith(old_field) or old_field in field), "")
        if not new_field and len(new_candidates) == 1:
            new_field = new_candidates[0]
        if not new_field:
            continue
        candidates.append(
            {
                "test_path": test_path,
                "source_path": source_path,
                "read_function": read_function,
                "write_function": write_function,
                "id_field": id_field,
                "old_field": old_field,
                "new_field": new_field,
                "docs_path": docs_path,
            }
        )
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred data migration requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    source_path = str(candidate["source_path"])
    read_function = str(candidate["read_function"])
    write_function = str(candidate["write_function"])
    id_field = str(candidate["id_field"])
    old_field = str(candidate["old_field"])
    new_field = str(candidate["new_field"])
    source_content = (
        f"def {read_function}(record):\n"
        f"    if '{new_field}' in record:\n"
        f"        value = record['{new_field}']\n"
        f"    elif '{old_field}' in record:\n"
        f"        value = record['{old_field}']\n"
        "    else:\n"
        f"        raise KeyError('{new_field}')\n"
        f"    return {{'{id_field}': record['{id_field}'], '{new_field}': value}}\n\n"
        f"def {write_function}(record):\n"
        f"    normalized = {read_function}(record)\n"
        f"    return {{'{id_field}': normalized['{id_field}'], '{new_field}': normalized['{new_field}']}}\n"
    )
    operations: list[dict[str, Any]] = [
        {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True}
    ]
    docs_path = str(candidate.get("docs_path") or "")
    if docs_path:
        docs_content = (
            "# Records\n\n"
            f"Legacy records with `{old_field}` remain readable. Writers emit `{new_field}` so rollback can still read old stored data while new outputs use the new shape.\n"
        )
        operations.append({"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True})
    if not commands:
        commands = [f"python -m unittest {str(candidate['test_path'])[:-3].replace('/', '.')}"]
    return {
        "source": "test_inferred_data_migration",
        "diagnostics": {
            "kind": "test_inferred_data_migration",
            "test_path": candidate["test_path"],
            "source_path": source_path,
            "read_function": read_function,
            "write_function": write_function,
            "id_field": id_field,
            "old_field": old_field,
            "new_field": new_field,
            "docs_path": docs_path,
        },
        "operations": operations,
        "verification_commands": commands,
    }


def infer_security_boundary_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    commands = verification_commands_from_natural_goal(goal)
    test_paths = discovered_test_paths(repo_root, goal)
    candidates: list[dict[str, Any]] = []
    for test_path in test_paths:
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "assertRaises(ValueError)" not in text:
            continue
        if ".." not in text or "/" not in text:
            continue
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", text, flags=re.MULTILINE)
        if len(imports) != 1:
            continue
        module_name, function_name = imports[0]
        source_path = f"{module_name.replace('.', '/')}.py"
        source = safe_repo_path(repo_root, source_path)
        if not source.exists():
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        function_node = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name), None)
        if not function_node or len(function_node.args.args) != 1:
            continue
        arg_name = function_node.args.args[0].arg
        positive_cases = re.findall(
            rf"assertEqual\(\s*{re.escape(function_name)}\(\s*(['\"][^'\"]+['\"])\s*\)\s*,\s*(['\"][^'\"]+['\"])\s*\)",
            text,
        )
        malicious_literals = re.findall(r"['\"]([^'\"]*(?:\.\.|/etc/passwd)[^'\"]*)['\"]", text)
        if not positive_cases or not malicious_literals:
            continue
        docs_path = ""
        for docs in sorted(repo_root.rglob("*.md")):
            if any(part in EXCLUDED_DIRS for part in docs.relative_to(repo_root).parts):
                continue
            docs_text = docs.read_text(encoding="utf-8")
            if function_name in docs_text or "archive" in docs_text.lower() or "path" in docs_text.lower():
                docs_path = str(docs.relative_to(repo_root))
                break
        candidates.append(
            {
                "test_path": test_path,
                "source_path": source_path,
                "function_name": function_name,
                "argument": arg_name,
                "positive_case_count": len(positive_cases),
                "malicious_case_count": len(set(malicious_literals)),
                "docs_path": docs_path,
            }
        )
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred security boundary requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    source_path = str(candidate["source_path"])
    function_name = str(candidate["function_name"])
    argument = str(candidate["argument"])
    source_content = (
        f"def {function_name}({argument}):\n"
        f"    candidate = str({argument}).replace('\\\\\\\\', '/')\n"
        "    parts = [part for part in candidate.split('/') if part not in ('', '.')]\n"
        "    if candidate.startswith('/') or '..' in parts:\n"
        "        raise ValueError('archive path escapes root')\n"
        "    if not parts:\n"
        "        raise ValueError('archive path is empty')\n"
        "    return '/'.join(parts)\n"
    )
    operations: list[dict[str, Any]] = [
        {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True}
    ]
    docs_path = str(candidate.get("docs_path") or "")
    if docs_path:
        docs_content = (
            "# Archive Paths\n\n"
            "Paths are normalized as relative archive-root paths. Absolute paths and parent traversal segments are rejected with `ValueError`.\n"
        )
        operations.append({"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True})
    if not commands:
        commands = [f"python -m unittest {str(candidate['test_path'])[:-3].replace('/', '.')}"]
    return {
        "source": "test_inferred_security_boundary",
        "diagnostics": {
            "kind": "test_inferred_security_boundary",
            "test_path": candidate["test_path"],
            "source_path": source_path,
            "function_name": function_name,
            "argument": argument,
            "positive_case_count": candidate["positive_case_count"],
            "malicious_case_count": candidate["malicious_case_count"],
            "docs_path": docs_path,
        },
        "operations": operations,
        "verification_commands": commands,
    }


def patch_spec_from_multi_file_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_FILES:")
    if not payload:
        return {}
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("CERAXIA_FILES must contain a non-empty files list")
    operations: list[dict[str, Any]] = []
    planned_paths: list[str] = []
    overwrite_paths: list[str] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_FILES item {index} must be an object")
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"CERAXIA_FILES item {index} requires a non-empty string path")
        if not isinstance(content, str):
            raise ValueError(f"CERAXIA_FILES item {index} requires string content")
        operation: dict[str, Any] = {
            "type": "write_file",
            "path": path,
            "content": content,
        }
        if "overwrite" in item:
            operation["overwrite"] = bool(item.get("overwrite"))
        planned_paths.append(path)
        if operation.get("overwrite") is True:
            overwrite_paths.append(path)
        operations.append(operation)
    verification_commands = payload.get("verification_commands", [])
    if verification_commands is None:
        verification_commands = []
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_FILES verification_commands must be a list of strings")
    return {
        "source": "multi_file_marker_synthesis",
        "diagnostics": {
            "kind": "multi_file_marker_synthesis",
            "file_count": len(operations),
            "planned_paths": planned_paths,
            "overwrite_paths": overwrite_paths,
            "created_or_updated_paths": planned_paths,
        },
        "operations": operations,
        "verification_commands": verification_commands,
    }


def synthesized_patch_spec_from_markers(goal: str) -> dict[str, Any]:
    integration_contract = patch_spec_from_integration_contract_marker(goal)
    if integration_contract:
        return integration_contract
    public_api_compat = patch_spec_from_public_api_compat_marker(goal)
    if public_api_compat:
        return public_api_compat
    config_runtime = patch_spec_from_config_runtime_marker(goal)
    if config_runtime:
        return config_runtime
    refactor = patch_spec_from_refactor_marker(goal)
    if refactor:
        return refactor
    edge_fix = patch_spec_from_edge_fix_marker(goal)
    if edge_fix:
        return edge_fix
    data_migration = patch_spec_from_data_migration_marker(goal)
    if data_migration:
        return data_migration
    feature = patch_spec_from_feature_marker(goal)
    if feature:
        return feature
    multi_file = patch_spec_from_multi_file_marker(goal)
    if multi_file:
        return multi_file
    create_path = marker_value(goal, "CERAXIA_CREATE_FILE:")
    if create_path:
        content = marker_block(goal, "CERAXIA_FILE_CONTENT:")
        return {
            "source": "marker_synthesis",
            "operations": [
                {
                    "type": "write_file",
                    "path": create_path,
                    "content": content,
                }
            ],
            "verification_commands": verification_commands_from_markers(goal),
        }
    replace_path = marker_value(goal, "CERAXIA_REPLACE_IN_FILE:")
    if replace_path:
        old = marker_block(goal, "CERAXIA_OLD:")
        new = marker_block(goal, "CERAXIA_NEW:")
        return {
            "source": "marker_synthesis",
            "operations": [
                {
                    "type": "replace",
                    "path": replace_path,
                    "old": old,
                    "new": new,
                }
            ],
            "verification_commands": verification_commands_from_markers(goal),
        }
    return {}


def normalize_patch_payload(payload: dict[str, Any], source: str) -> dict[str, Any]:
    if isinstance(payload.get("ceraxia_patch"), dict):
        payload = payload["ceraxia_patch"]
    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError(f"{source} must contain a non-empty operations list")
    return payload


def patch_spec_resolution_from_request(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    candidate_builders: list[tuple[str, Any]] = [
        ("explicit_json_patch", lambda: extract_json_after_marker(goal, "CERAXIA_PATCH:")),
        ("marker_synthesis", lambda: synthesized_patch_spec_from_markers(goal)),
        ("test_inferred_api_deprecation", lambda: infer_api_deprecation_from_tests(request)),
        ("test_inferred_data_migration", lambda: infer_data_migration_from_tests(request)),
        ("test_inferred_security_boundary", lambda: infer_security_boundary_from_tests(request)),
        ("natural_language_simple_replace", lambda: infer_simple_replace_patch_spec(request)),
        ("natural_language_add_function", lambda: infer_add_function_patch_spec(request)),
        ("test_inferred_arithmetic_return", lambda: infer_arithmetic_return_from_tests(request)),
        ("test_inferred_return_mismatch", lambda: infer_return_mismatch_from_tests(request)),
        ("test_inferred_missing_function", lambda: infer_missing_function_from_tests(request)),
    ]
    candidates: list[dict[str, Any]] = []
    for source, builder in candidate_builders:
        try:
            payload = builder()
            if not payload:
                candidates.append({"source": source, "status": "unavailable", "diagnostic": "no matching evidence found"})
                continue
            normalized = normalize_patch_payload(payload, source)
        except ValueError as exc:
            candidates.append({"source": source, "status": "blocked", "diagnostic": str(exc)})
            continue
        operations = normalized.get("operations") if isinstance(normalized.get("operations"), list) else []
        verification_commands = (
            normalized.get("verification_commands") if isinstance(normalized.get("verification_commands"), list) else []
        )
        diagnostics = normalized.get("diagnostics") if isinstance(normalized.get("diagnostics"), dict) else {}
        candidates.append(
            {
                "source": source,
                "status": "selected",
                "operation_count": len(operations),
                "verification_command_count": len(verification_commands),
                "diagnostics": diagnostics,
            }
        )
        return {"patch_spec": normalized, "candidates": candidates, "selected_candidate": candidates[-1]}
    return {"patch_spec": {}, "candidates": candidates, "selected_candidate": {}}


def patch_spec_from_request(request: dict[str, Any]) -> dict[str, Any]:
    return patch_spec_resolution_from_request(request)["patch_spec"]


def apply_patch_operation(repo_root: Path, operation: dict[str, Any]) -> dict[str, Any]:
    op_type = str(operation.get("type") or "").strip()
    path = safe_repo_path(repo_root, str(operation.get("path") or ""))
    before_exists = path.exists()
    before_hash = sha256_text(path) if before_exists else ""
    if op_type == "replace":
        if not before_exists:
            raise ValueError(f"replace target does not exist: {operation.get('path')}")
        old = operation.get("old")
        new = operation.get("new")
        if not isinstance(old, str) or old == "":
            raise ValueError("replace operation requires non-empty old text")
        if not isinstance(new, str):
            raise ValueError("replace operation requires new text")
        content = path.read_text(encoding="utf-8")
        count = content.count(old)
        if count != 1:
            raise ValueError(f"replace operation requires exactly one match in {operation.get('path')}, found {count}")
        path.write_text(content.replace(old, new, 1), encoding="utf-8")
    elif op_type == "write_file":
        content = operation.get("content")
        if not isinstance(content, str):
            raise ValueError("write_file operation requires string content")
        overwrite = bool(operation.get("overwrite"))
        if before_exists and path.read_text(encoding="utf-8") == content:
            return {
                "path": str(path.relative_to(repo_root)),
                "operation": op_type,
                "created": False,
                "before_sha256": before_hash,
                "after_sha256": before_hash,
                "changed": False,
                "idempotent": True,
            }
        if before_exists and not overwrite:
            raise ValueError(f"write_file target exists and overwrite is false: {operation.get('path')}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    elif op_type == "append":
        if not before_exists:
            raise ValueError(f"append target does not exist: {operation.get('path')}")
        content = operation.get("content")
        if not isinstance(content, str) or content == "":
            raise ValueError("append operation requires non-empty string content")
        current = path.read_text(encoding="utf-8")
        if content in current:
            return {
                "path": str(path.relative_to(repo_root)),
                "operation": op_type,
                "created": False,
                "before_sha256": before_hash,
                "after_sha256": before_hash,
                "changed": False,
                "idempotent": True,
            }
        function_name = str(operation.get("python_function_name") or "").strip()
        if function_name:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name):
                raise ValueError("append operation python_function_name must be a valid identifier")
            if re.search(rf"^\s*def\s+{re.escape(function_name)}\s*\(", current, flags=re.MULTILINE):
                raise ValueError(f"append operation would duplicate existing function: {function_name}")
        separator = "" if current.endswith("\n") or not current else "\n"
        path.write_text(f"{current}{separator}{content}", encoding="utf-8")
    else:
        raise ValueError(f"unsupported patch operation type: {op_type}")
    invalidate_python_cache(path)
    after_hash = sha256_text(path)
    return {
        "path": str(path.relative_to(repo_root)),
        "operation": op_type,
        "created": not before_exists,
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "changed": before_hash != after_hash,
    }


def restore_path_snapshot(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        invalidate_python_cache(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    invalidate_python_cache(path)


def apply_patch_operations_atomically(repo_root: Path, operations: list[Any]) -> list[dict[str, Any]]:
    changed_files: list[dict[str, Any]] = []
    snapshots: dict[Path, bytes | None] = {}
    try:
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError("each patch operation must be an object")
            path = safe_repo_path(repo_root, str(operation.get("path") or ""))
            if path not in snapshots:
                snapshots[path] = path.read_bytes() if path.exists() else None
            changed_files.append(apply_patch_operation(repo_root, operation))
    except ValueError as exc:
        rolled_back_files: list[dict[str, Any]] = []
        mutated_paths = {
            safe_repo_path(repo_root, str(item.get("path") or ""))
            for item in changed_files
            if isinstance(item, dict) and item.get("changed")
        }
        for path, content in reversed(list(snapshots.items())):
            restore_path_snapshot(path, content)
            if path in mutated_paths:
                rolled_back_files.append(
                    {
                        "path": str(path.relative_to(repo_root)),
                        "restored": content is not None,
                        "removed": content is None,
                    }
                )
        raise PatchApplyError(str(exc), rolled_back_files) from exc
    return changed_files


def command_allowed(command: list[str]) -> bool:
    if not command:
        return False
    if command[0] == "pytest":
        return True
    if command[0] in {"python", "python3", sys.executable} and len(command) >= 3 and command[1] == "-m":
        return command[2] in {"py_compile", "pytest", "unittest"}
    return False


def run_verification_command(repo_root: Path, raw_command: str) -> dict[str, Any]:
    try:
        command = shlex.split(raw_command)
    except ValueError as exc:
        return {"command": raw_command, "returncode": 2, "stdout": "", "stderr": f"invalid command syntax: {exc}"}
    if not command_allowed(command):
        return {
            "command": raw_command,
            "returncode": 126,
            "stdout": "",
            "stderr": "verification command is outside Ceraxia's allowlist",
        }
    if command[0] in {"python", "python3"}:
        command[0] = sys.executable
    completed = subprocess.run(command, cwd=repo_root, text=True, capture_output=True, timeout=120, check=False)
    return {
        "command": raw_command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def repair_expected_colon(repo_root: Path, py_file: str, stderr: str) -> dict[str, Any]:
    if "SyntaxError: expected ':'" not in stderr:
        return {"applied": False, "reason": "not an expected-colon SyntaxError"}
    match = re.search(r'File "([^"]+)", line (\d+)', stderr)
    if not match:
        return {"applied": False, "reason": "could not locate failing file and line"}
    failing_path = Path(match.group(1))
    if failing_path.name != Path(py_file).name:
        return {"applied": False, "reason": "failing file does not match changed file"}
    line_number = int(match.group(2))
    path = safe_repo_path(repo_root, py_file)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if line_number < 1 or line_number > len(lines):
        return {"applied": False, "reason": "failing line is out of range"}
    original = lines[line_number - 1]
    line_without_newline = original.rstrip("\n")
    if line_without_newline.rstrip().endswith(":"):
        return {"applied": False, "reason": "failing line already ends with colon"}
    newline = "\n" if original.endswith("\n") else ""
    lines[line_number - 1] = f"{line_without_newline.rstrip()}:{newline}"
    before_hash = sha256_text(path)
    path.write_text("".join(lines), encoding="utf-8")
    invalidate_python_cache(path)
    return {
        "applied": True,
        "kind": "expected_colon",
        "path": py_file,
        "line": line_number,
        "before_sha256": before_hash,
        "after_sha256": sha256_text(path),
    }


def repair_assertion_return_mismatch(repo_root: Path, py_files: list[str], output: str) -> dict[str, Any]:
    match = re.search(r"AssertionError: ([+-]?\d+) != ([+-]?\d+)", output)
    if not match:
        return {"applied": False, "reason": "no simple integer AssertionError mismatch found"}
    actual, expected = match.groups()
    needle = f"return {actual}"
    replacement = f"return {expected}"
    candidates: list[tuple[Path, str]] = []
    for py_file in py_files:
        path = safe_repo_path(repo_root, py_file)
        content = path.read_text(encoding="utf-8")
        if content.count(needle) == 1:
            candidates.append((path, content))
    if len(candidates) != 1:
        return {"applied": False, "reason": f"expected one changed file with {needle!r}, found {len(candidates)}"}
    path, content = candidates[0]
    before_hash = sha256_text(path)
    path.write_text(content.replace(needle, replacement, 1), encoding="utf-8")
    invalidate_python_cache(path)
    return {
        "applied": True,
        "kind": "assertion_return_mismatch",
        "path": str(path.relative_to(repo_root)),
        "actual": actual,
        "expected": expected,
        "before_sha256": before_hash,
        "after_sha256": sha256_text(path),
    }


def repair_name_error_return_literal(repo_root: Path, py_files: list[str], output: str) -> dict[str, Any]:
    match = re.search(r"NameError: name '([A-Za-z_][A-Za-z0-9_]*)' is not defined", output)
    if not match:
        return {"applied": False, "reason": "no simple NameError found"}
    name = match.group(1)
    expected_match = re.search(r"assertEqual\([^,\n]+,\s*([+-]?\d+|True|False|None)\)", output)
    if not expected_match:
        return {"applied": False, "reason": "could not infer a literal expected value from assertEqual"}
    expected = expected_match.group(1)
    needle = f"return {name}"
    candidates: list[tuple[Path, str]] = []
    for py_file in py_files:
        path = safe_repo_path(repo_root, py_file)
        content = path.read_text(encoding="utf-8")
        if content.count(needle) == 1:
            candidates.append((path, content))
    if len(candidates) != 1:
        return {"applied": False, "reason": f"expected one changed file with {needle!r}, found {len(candidates)}"}
    path, content = candidates[0]
    before_hash = sha256_text(path)
    path.write_text(content.replace(needle, f"return {expected}", 1), encoding="utf-8")
    invalidate_python_cache(path)
    return {
        "applied": True,
        "kind": "name_error_return_literal",
        "path": str(path.relative_to(repo_root)),
        "name": name,
        "expected": expected,
        "before_sha256": before_hash,
        "after_sha256": sha256_text(path),
    }


def repair_import_error_missing_function(repo_root: Path, py_files: list[str], output: str) -> dict[str, Any]:
    import_match = re.search(
        r"ImportError: cannot import name '([A-Za-z_][A-Za-z0-9_]*)' from '([A-Za-z_][A-Za-z0-9_\.]*)'",
        output,
    )
    if not import_match:
        return {"applied": False, "reason": "no simple import-name ImportError found"}
    function_name, module_name = import_match.groups()
    expected_values = re.findall(
        rf"assertEqual\(\s*{re.escape(function_name)}\(\)\s*,\s*([+-]?\d+|True|False|None)\s*\)",
        output,
    )
    if not expected_values:
        for test_file in sorted(repo_root.glob("test*.py")) + sorted(repo_root.glob("*_test.py")):
            text = test_file.read_text(encoding="utf-8")
            expected_values.extend(
                re.findall(
                    rf"assertEqual\(\s*{re.escape(function_name)}\(\)\s*,\s*([+-]?\d+|True|False|None)\s*\)",
                    text,
                )
            )
    if len(expected_values) != 1:
        return {"applied": False, "reason": f"could not infer exactly one expected literal for missing function, found {len(expected_values)}"}
    expected = expected_values[0]
    module_path = f"{module_name.replace('.', '/')}.py"
    if module_path not in py_files:
        return {"applied": False, "reason": f"missing function module is not a changed file: {module_path}"}
    path = safe_repo_path(repo_root, module_path)
    content = path.read_text(encoding="utf-8")
    if re.search(rf"^\s*def\s+{re.escape(function_name)}\s*\(", content, flags=re.MULTILINE):
        return {"applied": False, "reason": f"function already exists: {function_name}"}
    before_hash = sha256_text(path)
    prefix = "" if not content or content.endswith("\n") else "\n"
    suffix = "\n" if content else ""
    addition = f"{prefix}{suffix}def {function_name}():\n    return {expected}\n"
    path.write_text(content + addition, encoding="utf-8")
    invalidate_python_cache(path)
    return {
        "applied": True,
        "kind": "import_error_missing_function",
        "path": module_path,
        "function": function_name,
        "expected": expected,
        "before_sha256": before_hash,
        "after_sha256": sha256_text(path),
    }


def python_file_summary(repo_root: Path, path: Path) -> dict[str, Any]:
    rel = str(path.relative_to(repo_root))
    try:
        if path.stat().st_size > MAX_SYMBOL_SCAN_BYTES:
            return {"path": rel, "skipped": "file_too_large_for_symbol_scan"}
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return {"path": rel, "skipped": f"python_parse_failed: {exc.__class__.__name__}"}
    functions: list[str] = []
    classes: list[str] = []
    imports: list[str] = []
    calls: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.extend(f"{module}.{alias.name}" if module else alias.name for alias in node.names)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            calls.append(target.id)
        elif isinstance(target, ast.Attribute):
            calls.append(target.attr)
    return {
        "path": rel,
        "module": python_module_name(rel),
        "functions": functions[:40],
        "classes": classes[:40],
        "imports": imports[:40],
        "calls": sorted(set(calls))[:80],
    }


def import_dependency_graph(python_symbols: list[dict[str, Any]]) -> dict[str, Any]:
    modules_by_name = {
        str(item.get("module") or ""): str(item.get("path") or "")
        for item in python_symbols
        if isinstance(item, dict) and item.get("module") and item.get("path")
    }
    edges: list[dict[str, str]] = []
    reverse: dict[str, list[str]] = {}
    for item in python_symbols:
        if not isinstance(item, dict):
            continue
        source_path = str(item.get("path") or "")
        imports = item.get("imports") if isinstance(item.get("imports"), list) else []
        for imported in imports:
            text = str(imported)
            candidate_modules = [text, text.rsplit(".", 1)[0] if "." in text else text]
            target_path = next((modules_by_name[module] for module in candidate_modules if module in modules_by_name), "")
            if not target_path or target_path == source_path:
                continue
            edge = {"from": source_path, "to": target_path, "import": text}
            if edge not in edges:
                edges.append(edge)
                reverse.setdefault(target_path, [])
                if source_path not in reverse[target_path]:
                    reverse[target_path].append(source_path)
    return {
        "edges": edges[:200],
        "reverse_dependents": {key: value[:20] for key, value in sorted(reverse.items())[:80]},
        "edge_count": len(edges),
    }


def call_graph_summary(python_symbols: list[dict[str, Any]]) -> dict[str, Any]:
    function_defs: dict[str, str] = {}
    for item in python_symbols:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        for name in item.get("functions", []) if isinstance(item.get("functions"), list) else []:
            function_defs.setdefault(str(name), path)
    edges: list[dict[str, str]] = []
    for item in python_symbols:
        if not isinstance(item, dict):
            continue
        source_path = str(item.get("path") or "")
        for call in item.get("calls", []) if isinstance(item.get("calls"), list) else []:
            target_path = function_defs.get(str(call), "")
            if target_path and target_path != source_path:
                edge = {"from": source_path, "to": target_path, "call": str(call)}
                if edge not in edges:
                    edges.append(edge)
    return {
        "edges": edges[:200],
        "edge_count": len(edges),
        "known_function_count": len(function_defs),
    }


def targeted_reading_plan(repo_map: dict[str, Any], dependency_graph: dict[str, Any]) -> list[dict[str, Any]]:
    read_order = repo_map.get("recommended_read_order") if isinstance(repo_map.get("recommended_read_order"), list) else []
    reverse = dependency_graph.get("reverse_dependents") if isinstance(dependency_graph.get("reverse_dependents"), dict) else {}
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in read_order[:20]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if not path or path in seen:
            continue
        seen.add(path)
        dependents = [str(value) for value in reverse.get(path, [])] if isinstance(reverse.get(path), list) else []
        plan.append(
            {
                "path": path,
                "phase": item.get("phase", ""),
                "reason": item.get("reason", ""),
                "dependent_count": len(dependents),
                "sample_dependents": dependents[:5],
                "question": "What contract does this file expose, and what tests or dependents would break if it changes?",
            }
        )
    return plan[:20]


def engineering_hypotheses(goal: str, repo_map: dict[str, Any], dependency_graph: dict[str, Any]) -> list[dict[str, Any]]:
    ranked = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    reverse = dependency_graph.get("reverse_dependents") if isinstance(dependency_graph.get("reverse_dependents"), dict) else {}
    hypotheses: list[dict[str, Any]] = []
    for item in ranked[:8]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if not path:
            continue
        reasons = item.get("reasons") if isinstance(item.get("reasons"), list) else []
        dependents = reverse.get(path, []) if isinstance(reverse.get(path), list) else []
        hypotheses.append(
            {
                "hypothesis": f"{path} is likely relevant to the requested code change.",
                "confidence": "high" if int(item.get("score") or 0) >= 8 else "medium",
                "evidence": reasons[:5],
                "risk": "public behavior may affect dependents" if dependents else "local change risk appears limited",
                "next_read": path,
            }
        )
    if not hypotheses:
        hypotheses.append(
            {
                "hypothesis": "No strong source candidate was found from filenames, symbols, or tests.",
                "confidence": "low",
                "evidence": ["repository map has no high-signal ranked files"],
                "risk": "manual task clarification or broader survey may be required",
                "next_read": "",
            }
        )
    return hypotheses


def suggested_verification_commands(test_files: list[str]) -> list[str]:
    commands: list[str] = []
    py_tests = [item for item in test_files if item.endswith(".py")]
    if py_tests:
        commands.append("python -m unittest discover")
        commands.extend(f"python -m unittest {item[:-3].replace('/', '.')}" for item in py_tests[:5])
    return commands[:8]


def engineering_readiness_model(goal: str, repo_map: dict[str, Any], dependency_graph: dict[str, Any], test_files: list[str]) -> dict[str, Any]:
    ranked_files = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    links = repo_map.get("test_source_links") if isinstance(repo_map.get("test_source_links"), list) else []
    reverse = dependency_graph.get("reverse_dependents") if isinstance(dependency_graph.get("reverse_dependents"), dict) else {}
    linked_tests_by_source: dict[str, list[str]] = {}
    for link in links:
        if not isinstance(link, dict):
            continue
        test_path = str(link.get("test_path") or "")
        for source_path in link.get("source_paths", []) if isinstance(link.get("source_paths"), list) else []:
            linked_tests_by_source.setdefault(str(source_path), [])
            if test_path and test_path not in linked_tests_by_source[str(source_path)]:
                linked_tests_by_source[str(source_path)].append(test_path)
    impact_matrix: list[dict[str, Any]] = []
    for item in ranked_files[:12]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if not path:
            continue
        dependents = [str(value) for value in reverse.get(path, [])] if isinstance(reverse.get(path), list) else []
        linked_tests = linked_tests_by_source.get(path, [])
        score = int(item.get("score") or 0)
        if dependents and not linked_tests:
            impact_level = "high"
        elif dependents or score >= 8:
            impact_level = "medium"
        else:
            impact_level = "low"
        impact_matrix.append(
            {
                "path": path,
                "impact_level": impact_level,
                "rank_score": score,
                "dependent_count": len(dependents),
                "linked_tests": linked_tests[:8],
                "reason": "public dependency surface" if dependents else "ranked task relevance",
            }
        )
    risk_register: list[dict[str, Any]] = []
    if not ranked_files:
        risk_register.append(
            {
                "risk": "no_ranked_source_candidate",
                "severity": "high",
                "mitigation": "block broad source mutation until a focused file or failing test identifies the target",
            }
        )
    uncovered_public = [item for item in impact_matrix if item.get("dependent_count", 0) and not item.get("linked_tests")]
    if uncovered_public:
        risk_register.append(
            {
                "risk": "public_surface_without_static_test_link",
                "severity": "medium",
                "affected_paths": [str(item.get("path")) for item in uncovered_public[:8]],
                "mitigation": "run broader verification or require manual coverage review before approval",
            }
        )
    if not test_files:
        risk_register.append(
            {
                "risk": "no_test_surface_detected",
                "severity": "medium",
                "mitigation": "require syntax checks and task-specific verification commands",
            }
        )
    acceptance_criteria = [
        {"criterion": "requested_behavior_addressed", "verification": "patch candidate selected from explicit contract, task text, or test evidence"},
        {"criterion": "source_scope_is_explained", "verification": "changed files map back to repo survey or review warns about drift"},
        {"criterion": "changed_python_compiles", "verification": "py_compile runs for changed Python files"},
        {"criterion": "task_verification_passes", "verification": "requested or inferred verification commands return zero"},
        {"criterion": "review_has_no_blockers", "verification": "code_review decision record approves final package"},
    ]
    test_strategy = {
        "primary_commands": suggested_verification_commands(test_files),
        "linked_test_targets": sorted({test for tests in linked_tests_by_source.values() for test in tests})[:12],
        "fallback_checks": ["python -m py_compile <changed .py files>", "git diff --check"],
        "coverage_note": "Prefer linked tests for changed sources; use broader discovery when public dependents are present.",
    }
    return {
        "impact_matrix": impact_matrix,
        "risk_register": risk_register,
        "acceptance_criteria": acceptance_criteria,
        "test_strategy": test_strategy,
        "readiness_checks": {
            "has_ranked_sources": bool(ranked_files),
            "has_acceptance_criteria": bool(acceptance_criteria),
            "has_test_strategy": bool(test_strategy.get("primary_commands") or test_strategy.get("fallback_checks")),
            "high_risk_count": sum(1 for item in risk_register if item.get("severity") == "high"),
        },
    }


def repo_survey(repo_root: Path, goal: str) -> dict[str, Any]:
    extension_counts: Counter[str] = Counter()
    candidate_files: list[str] = []
    test_files: list[str] = []
    config_files: list[str] = []
    python_symbols: list[dict[str, Any]] = []
    total_files = 0
    for path in sorted(repo_root.rglob("*")):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(repo_root).parts):
            continue
        if not path.is_file():
            continue
        rel = str(path.relative_to(repo_root))
        if rel.endswith((".pyc", ".sqlite3", ".gguf", ".safetensors", ".bin", ".apk")):
            continue
        total_files += 1
        suffix = path.suffix.lower() or "[no_ext]"
        extension_counts[suffix] += 1
        lowered = rel.lower()
        if any(marker in lowered for marker in ("test", "self_test", "spec")):
            test_files.append(rel)
        if path.suffix == ".py" and len(python_symbols) < 80:
            python_symbols.append(python_file_summary(repo_root, path))
        if path.name in {"pyproject.toml", "package.json", "build.gradle", "settings.gradle", "gradlew", "requirements.txt"}:
            config_files.append(rel)
        goal_tokens = {token for token in goal.lower().replace("/", " ").replace("_", " ").split() if len(token) > 3}
        rel_tokens = set(lowered.replace("/", " ").replace("_", " ").replace("-", " ").split())
        if goal_tokens & rel_tokens:
            candidate_files.append(rel)
    dominant_extensions = [{"extension": ext, "count": count} for ext, count in extension_counts.most_common(12)]
    repo_map = build_repo_map(goal, candidate_files[:80], test_files[:80], python_symbols)
    dependency_graph = import_dependency_graph(python_symbols)
    call_graph = call_graph_summary(python_symbols)
    reading_plan = targeted_reading_plan(repo_map, dependency_graph)
    hypotheses = engineering_hypotheses(goal, repo_map, dependency_graph)
    readiness_model = engineering_readiness_model(goal, repo_map, dependency_graph, test_files[:80])
    return {
        "repo_root": str(repo_root),
        "goal": goal,
        "total_files_scanned": total_files,
        "dominant_extensions": dominant_extensions,
        "candidate_files": candidate_files[:80],
        "test_files": test_files[:80],
        "python_symbols": python_symbols,
        "suggested_verification_commands": suggested_verification_commands(test_files),
        "repo_map": repo_map,
        "engineering_investigation": {
            "dependency_graph": dependency_graph,
            "call_graph": call_graph,
            "targeted_reading_plan": reading_plan,
            "hypotheses": hypotheses,
            "design_decision_seed": [
                "Prefer the smallest patch that satisfies the failing test or explicit user contract.",
                "Inspect dependents before changing public functions or modules with reverse dependencies.",
                "If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.",
            ],
        },
        "engineering_readiness": readiness_model,
        "config_files": config_files[:40],
        "excluded_dirs": sorted(EXCLUDED_DIRS),
        "summary": f"Surveyed {total_files} files; found {len(test_files)} test-like files and {len(candidate_files)} goal-matching candidates.",
    }


def run_repository_survey(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    goal = request_goal(request)
    survey = repo_survey(target_repo_root(request), goal)
    survey["role_policy"] = role_policy_from_request(request)
    survey["task_profile"] = task_profile_from_request(request)
    survey["worker_brief"] = worker_brief_from_request(request)
    write_json(workspace_root, output_path, survey)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": survey["summary"],
        "artifacts": [output_path],
        "confidence": "medium",
    }


def run_change_planning(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    goal = request_goal(request) or str(survey.get("goal") or "")
    role_policy = role_policy_from_request(request)
    task_profile = task_profile_from_request(request)
    worker_brief = worker_brief_from_request(request)
    candidates = survey.get("candidate_files") if isinstance(survey.get("candidate_files"), list) else []
    tests = survey.get("test_files") if isinstance(survey.get("test_files"), list) else []
    symbols = survey.get("python_symbols") if isinstance(survey.get("python_symbols"), list) else []
    suggested_commands = survey.get("suggested_verification_commands") if isinstance(survey.get("suggested_verification_commands"), list) else []
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    investigation = survey.get("engineering_investigation") if isinstance(survey.get("engineering_investigation"), dict) else {}
    readiness = survey.get("engineering_readiness") if isinstance(survey.get("engineering_readiness"), dict) else {}
    ranked_files = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    test_source_links = repo_map.get("test_source_links") if isinstance(repo_map.get("test_source_links"), list) else []
    read_order = repo_map.get("recommended_read_order") if isinstance(repo_map.get("recommended_read_order"), list) else []
    targeted_reads = investigation.get("targeted_reading_plan") if isinstance(investigation.get("targeted_reading_plan"), list) else []
    hypotheses = investigation.get("hypotheses") if isinstance(investigation.get("hypotheses"), list) else []
    decision_seed = investigation.get("design_decision_seed") if isinstance(investigation.get("design_decision_seed"), list) else []
    impact_matrix = readiness.get("impact_matrix") if isinstance(readiness.get("impact_matrix"), list) else []
    risk_register = readiness.get("risk_register") if isinstance(readiness.get("risk_register"), list) else []
    acceptance_criteria = readiness.get("acceptance_criteria") if isinstance(readiness.get("acceptance_criteria"), list) else []
    test_strategy = readiness.get("test_strategy") if isinstance(readiness.get("test_strategy"), dict) else {}
    symbol_lines: list[str] = []
    for item in symbols[:20]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        functions = ", ".join(str(name) for name in item.get("functions", [])[:8]) if isinstance(item.get("functions"), list) else ""
        classes = ", ".join(str(name) for name in item.get("classes", [])[:8]) if isinstance(item.get("classes"), list) else ""
        skipped = str(item.get("skipped") or "")
        detail = skipped or f"functions=[{functions}] classes=[{classes}]"
        symbol_lines.append(f"- {path}: {detail}")
    ranked_lines: list[str] = []
    for item in ranked_files[:20]:
        if not isinstance(item, dict):
            continue
        reasons = ", ".join(str(reason) for reason in item.get("reasons", [])[:4]) if isinstance(item.get("reasons"), list) else ""
        ranked_lines.append(f"- {item.get('path')}: score={item.get('score')} reasons=[{reasons}]")
    link_lines: list[str] = []
    for item in test_source_links[:20]:
        if not isinstance(item, dict):
            continue
        sources = ", ".join(str(path) for path in item.get("source_paths", [])[:8]) if isinstance(item.get("source_paths"), list) else ""
        link_lines.append(f"- {item.get('test_path')} -> {sources}")
    read_order_lines: list[str] = []
    for item in read_order[:20]:
        if not isinstance(item, dict):
            continue
        read_order_lines.append(f"- {item.get('phase')}: {item.get('path')} ({item.get('reason')})")
    targeted_read_lines: list[str] = []
    for item in targeted_reads[:20]:
        if not isinstance(item, dict):
            continue
        targeted_read_lines.append(
            f"- {item.get('path')}: {item.get('question')} dependents={item.get('dependent_count', 0)}"
        )
    hypothesis_lines: list[str] = []
    for item in hypotheses[:12]:
        if not isinstance(item, dict):
            continue
        evidence = ", ".join(str(value) for value in item.get("evidence", [])[:4]) if isinstance(item.get("evidence"), list) else ""
        hypothesis_lines.append(f"- [{item.get('confidence')}] {item.get('hypothesis')} evidence=[{evidence}] risk={item.get('risk')}")
    impact_lines: list[str] = []
    for item in impact_matrix[:12]:
        if not isinstance(item, dict):
            continue
        tests_for_item = ", ".join(str(value) for value in item.get("linked_tests", [])[:5]) if isinstance(item.get("linked_tests"), list) else ""
        impact_lines.append(
            f"- {item.get('path')}: impact={item.get('impact_level')} dependents={item.get('dependent_count', 0)} tests=[{tests_for_item}]"
        )
    risk_lines: list[str] = []
    for item in risk_register[:12]:
        if not isinstance(item, dict):
            continue
        risk_lines.append(f"- [{item.get('severity')}] {item.get('risk')}: {item.get('mitigation')}")
    acceptance_lines: list[str] = []
    for item in acceptance_criteria[:12]:
        if not isinstance(item, dict):
            continue
        acceptance_lines.append(f"- {item.get('criterion')}: {item.get('verification')}")
    test_strategy_lines: list[str] = []
    if isinstance(test_strategy.get("primary_commands"), list):
        test_strategy_lines.extend(f"- primary: {item}" for item in test_strategy.get("primary_commands", [])[:8])
    if isinstance(test_strategy.get("linked_test_targets"), list):
        test_strategy_lines.extend(f"- linked: {item}" for item in test_strategy.get("linked_test_targets", [])[:8])
    if isinstance(test_strategy.get("fallback_checks"), list):
        test_strategy_lines.extend(f"- fallback: {item}" for item in test_strategy.get("fallback_checks", [])[:8])
    content = "\n".join(
        [
            "# Ceraxia Change Plan",
            "",
            f"Goal: {goal}",
            "",
            "## Scope",
            "- Inspect the named task and constrain edits to the smallest coherent module set.",
            "- Preserve user changes and expose blockers instead of guessing.",
            "",
            "## Candidate Files",
            *[f"- {item}" for item in candidates[:30]],
            "",
            "## Ranked Repo Map",
            *ranked_lines,
            "",
            "## Test Source Links",
            *link_lines,
            "",
            "## Recommended Read Order",
            *read_order_lines,
            "",
            "## Targeted Reading Plan",
            *targeted_read_lines,
            "",
            "## Hypothesis Log",
            *hypothesis_lines,
            "",
            "## Design Decision Seed",
            *[f"- {item}" for item in decision_seed[:12]],
            "",
            "## File Impact Matrix",
            *impact_lines,
            "",
            "## Risk Register",
            *risk_lines,
            "",
            "## Acceptance Criteria",
            *acceptance_lines,
            "",
            "## Test Strategy",
            *test_strategy_lines,
            "",
            "## Test Surface",
            *[f"- {item}" for item in tests[:30]],
            "",
            "## Python Symbol Surface",
            *symbol_lines,
            "",
            "## Suggested Verification",
            *[f"- {item}" for item in suggested_commands[:8]],
            "",
            "## Implementation Policy",
            "- Produce an auditable patch manifest before mutating source files.",
            "- Require verification commands or explicit blockers before final readiness.",
            "",
            "## Task Profile",
            f"- kinds: {', '.join(str(item) for item in task_profile.get('kinds', [])) if isinstance(task_profile.get('kinds'), list) else ''}",
            f"- complexity: {task_profile.get('complexity', '')}",
            *[
                f"- risk: {item}"
                for item in (task_profile.get("risk_flags") if isinstance(task_profile.get("risk_flags"), list) else [])
            ],
            "",
            "## Worker Brief",
            f"- brief: {worker_brief.get('brief', '')}",
            f"- handoff_question: {worker_brief.get('handoff_question', '')}",
            *[
                f"- must_produce: {item}"
                for item in (worker_brief.get("must_produce") if isinstance(worker_brief.get("must_produce"), list) else [])
            ],
            "",
            "## Role Policy",
            f"- role: {role_policy.get('role', '')}",
            f"- authority: {role_policy.get('authority', '')}",
            f"- may_mutate_source: {role_policy.get('may_mutate_source', False)}",
            *[
                f"- required_evidence: {item}"
                for item in (role_policy.get("required_evidence") if isinstance(role_policy.get("required_evidence"), list) else [])
            ],
        ]
    )
    write_text(workspace_root, output_path, content + "\n")
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Code change plan written.",
        "artifacts": [output_path],
        "confidence": "medium",
    }


def run_implementation(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    plan = read_text_optional(workspace_root, sibling_artifact(output_path, "change_plan.md"))
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    readiness = survey.get("engineering_readiness") if isinstance(survey.get("engineering_readiness"), dict) else {}
    role_policy = role_policy_from_request(request)
    task_profile = task_profile_from_request(request)
    worker_brief = worker_brief_from_request(request)
    blockers: list[str] = []
    changed_files: list[dict[str, Any]] = []
    rolled_back_files: list[dict[str, Any]] = []
    patch_spec: dict[str, Any] = {}
    repo_root = target_repo_root(request)
    excerpts = source_excerpt_pack(workspace_root, output_path, repo_root)
    patch_resolution = {"patch_spec": {}, "candidates": [], "selected_candidate": {}}
    dirty_worktree = {"git_repo": False, "dirty_targets": []}
    ambiguity_analysis: dict[str, Any] = {}
    try:
        patch_resolution = patch_spec_resolution_from_request(request)
        patch_spec = patch_resolution["patch_spec"] if isinstance(patch_resolution.get("patch_spec"), dict) else {}
        if patch_spec:
            if not role_policy_allows_source_mutation(role_policy):
                blockers.append("role_policy forbids source mutation for this step")
            else:
                operations = patch_spec["operations"] if isinstance(patch_spec.get("operations"), list) else []
                dirty_worktree = git_dirty_target_evidence(repo_root, operations)
                dirty_targets = dirty_worktree.get("dirty_targets") if isinstance(dirty_worktree.get("dirty_targets"), list) else []
                if dirty_targets:
                    dirty_paths = ", ".join(str(item.get("path")) for item in dirty_targets if isinstance(item, dict))
                    blockers.append(f"target file has uncommitted user changes; refusing source mutation: {dirty_paths}")
                else:
                    changed_files.extend(apply_patch_operations_atomically(repo_root, operations))
        else:
            ambiguity_analysis = ambiguity_analysis_from_goal(request_goal(request), repo_root)
            if ambiguity_analysis:
                blockers.append("Ambiguous code task requires clarification before source mutation.")
            else:
                blockers.append(
                    "No patch candidate could be selected from explicit contract, task text, or test evidence."
                )
    except PatchApplyError as exc:
        blockers.append(str(exc))
        rolled_back_files = exc.rolled_back_files
    except ValueError as exc:
        blockers.append(str(exc))
    status = "applied" if changed_files and not blockers else "handoff_required"
    manifest = {
        "status": status,
        "mode": "explicit_patch_apply" if status == "applied" else "auditable_handoff",
        "task_id": request.get("task_id"),
        "summary": "Ceraxia applied explicit patch operations." if status == "applied" else "Ceraxia prepared implementation intent, but no source files were mutated by this worker.",
        "intended_actions": [
            "read concrete target files before editing",
            "apply minimal scoped patch",
            "run verification commands from verification_report.json",
            "return focused revision steps on failure",
        ],
        "plan_excerpt": plan[:3000],
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "dirty_worktree": dirty_worktree,
        "ambiguity_analysis": ambiguity_analysis,
        "patch_spec_present": bool(patch_spec),
        "patch_source": str(patch_spec.get("source") or "explicit_json_patch") if patch_spec else "",
        "patch_candidates": patch_resolution.get("candidates", []) if isinstance(patch_resolution.get("candidates"), list) else [],
        "selected_patch_candidate": patch_resolution.get("selected_candidate", {})
        if isinstance(patch_resolution.get("selected_candidate"), dict)
        else {},
        "source_excerpt_pack": excerpts,
        "source_excerpt_summary": [
            {
                "path": item.get("path", ""),
                "status": item.get("status", ""),
                "bytes": item.get("bytes", 0),
                "truncated": item.get("truncated", False),
            }
            for item in excerpts
        ],
        "implementation_decision_record": [
            {
                "check": "source_evidence_loaded",
                "status": "pass" if any(item.get("status") == "read" for item in excerpts) else "warn",
                "detail": f"{sum(1 for item in excerpts if item.get('status') == 'read')} targeted files read",
            },
            {
                "check": "patch_candidate_selected",
                "status": "pass" if patch_spec else "fail",
                "detail": str(
                    (
                        patch_resolution.get("selected_candidate", {})
                        if isinstance(patch_resolution.get("selected_candidate"), dict)
                        else {}
                    ).get("source")
                    or "none"
                ),
            },
            {
                "check": "mutation_authority",
                "status": "pass" if role_policy_allows_source_mutation(role_policy) else "blocked",
                "detail": str(role_policy.get("authority") or "default_source_mutation_allowed"),
            },
        ],
        "diagnostics": patch_spec.get("diagnostics", {}) if isinstance(patch_spec.get("diagnostics"), dict) else {},
        "operation_count": len(patch_spec.get("operations", [])) if isinstance(patch_spec.get("operations"), list) else 0,
        "changed_files": changed_files,
        "recommended_read_order": recommended_read_order_from_survey(workspace_root, output_path),
        "engineering_readiness": readiness,
        "patch_scope_evidence": patch_scope_evidence(workspace_root, output_path, changed_files),
        "rollback": {
            "applied": bool(rolled_back_files),
            "files": rolled_back_files,
        },
        "verification_commands": patch_spec.get("verification_commands", []) if isinstance(patch_spec.get("verification_commands"), list) else [],
        "blockers": blockers,
        "warnings": [
            "Only explicit CERAXIA_PATCH operations are supported by this prototype patch worker.",
        ] if status == "applied" else [
            "The current package is an auditable implementation handoff, not a completed code change.",
        ]
    }
    write_json(workspace_root, output_path, manifest)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Patch manifest written with applied changes." if status == "applied" else "Patch manifest written as auditable handoff; source mutation remains blocked.",
        "artifacts": [output_path],
        "confidence": "medium",
    }


def run_verification(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    patch = load_json_optional(workspace_root, sibling_artifact(output_path, "patch_manifest.json"))
    role_policy = role_policy_from_request(request)
    task_profile = task_profile_from_request(request)
    worker_brief = worker_brief_from_request(request)
    blockers = [str(item) for item in patch.get("blockers", [])] if isinstance(patch.get("blockers"), list) else []
    executed: list[dict[str, Any]] = []
    repo_root = target_repo_root(request)
    changed_files = patch.get("changed_files") if isinstance(patch.get("changed_files"), list) else []
    repairs: list[dict[str, Any]] = []
    blocked_repairs: list[dict[str, Any]] = []
    candidate_source_paths: list[str] = []
    ranked_survey_sources = ranked_source_candidates_from_survey(workspace_root, output_path)
    repairs_allowed = role_policy_allows_source_mutation(role_policy)
    if patch.get("status") == "applied":
        py_files = [
            str(item.get("path"))
            for item in changed_files
            if isinstance(item, dict) and str(item.get("path") or "").endswith(".py")
        ]
        if py_files:
            cmd = [sys.executable, "-m", "py_compile", *py_files]
            completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
            executed.append(
                {
                    "command": " ".join(cmd),
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                }
            )
            if completed.returncode != 0:
                for candidate in source_candidates_from_traceback_text(completed.stderr, repo_root):
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                for candidate in ranked_survey_sources:
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                repaired_any = False
                for py_file in py_files:
                    if not repairs_allowed:
                        blockers.append("role_policy forbids source mutation repair")
                        blocked_repairs.append({"kind": "py_compile_repair", "path": py_file, "reason": "role_policy forbids source mutation repair"})
                        break
                    repair = repair_expected_colon(repo_root, py_file, completed.stderr)
                    if repair.get("applied"):
                        repairs.append(repair)
                        repaired_any = True
                if repaired_any:
                    completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
                    executed.append(
                        {
                            "command": " ".join(cmd),
                            "returncode": completed.returncode,
                            "stdout": completed.stdout[-4000:],
                            "stderr": completed.stderr[-4000:],
                            "after_repair": True,
                        }
                    )
                if completed.returncode != 0:
                    blockers.append("py_compile failed for changed Python files")
        if (repo_root / ".git").exists():
            cmd = ["git", "diff", "--check"]
            completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
            executed.append(
                {
                    "command": "git diff --check",
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                }
            )
            if completed.returncode != 0:
                blockers.append("git diff --check failed")
        raw_commands = patch.get("verification_commands") if isinstance(patch.get("verification_commands"), list) else []
        for raw_command in raw_commands:
            if not isinstance(raw_command, str) or not raw_command.strip():
                blockers.append("verification command must be a non-empty string")
                continue
            try:
                result = run_verification_command(repo_root, raw_command)
            except subprocess.TimeoutExpired:
                result = {"command": raw_command, "returncode": 124, "stdout": "", "stderr": "verification command timed out"}
            executed.append(result)
            if result.get("returncode") != 0:
                output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
                for candidate in source_candidates_from_traceback_text(output, repo_root):
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                for candidate in ranked_survey_sources:
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                if not repairs_allowed:
                    repair = {"applied": False, "blocked": "role_policy forbids source mutation repair"}
                    blockers.append("role_policy forbids source mutation repair")
                    blocked_repairs.append({"kind": "command_repair", "command": raw_command, "reason": "role_policy forbids source mutation repair"})
                else:
                    repair = repair_import_error_missing_function(repo_root, py_files, output)
                if not repair.get("applied") and repairs_allowed:
                    repair = repair_name_error_return_literal(repo_root, py_files, output)
                if not repair.get("applied") and repairs_allowed:
                    repair = repair_assertion_return_mismatch(repo_root, py_files, output)
                if repair.get("applied"):
                    repairs.append(repair)
                    try:
                        result = run_verification_command(repo_root, raw_command)
                    except subprocess.TimeoutExpired:
                        result = {"command": raw_command, "returncode": 124, "stdout": "", "stderr": "verification command timed out"}
                    result["after_repair"] = True
                    executed.append(result)
                if result.get("returncode") != 0:
                    blockers.append(f"verification command failed: {raw_command}")
    report = {
        "status": "blocked" if blockers else "passed",
        "task_id": request.get("task_id"),
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "commands": [
            "python -m py_compile <changed .py files>",
            "git diff --check",
        ],
        "executed": executed,
        "repairs": repairs,
        "blockers": blockers,
        "warnings": patch.get("warnings", []),
        "summary": "Verification passed for applied changes." if not blockers else "Verification is blocked or failed.",
    }
    failed_commands = [
        item
        for item in executed
        if isinstance(item, dict) and int(item.get("returncode") or 0) != 0
    ]
    repair_state = {
        "status": "blocked" if blockers else "passed",
        "task_id": request.get("task_id"),
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "repairs_allowed": repairs_allowed,
        "repair_attempts": repairs,
        "blocked_repairs": blocked_repairs,
        "commands_executed_count": len(executed),
        "failed_commands": failed_commands,
        "candidate_source_paths": candidate_source_paths[:20],
        "pending_blockers": blockers,
        "next_action": "inspect_blockers_or_revision_plan" if blockers else "continue_to_code_review",
        "summary": "Repair loop state recorded for verification step.",
    }
    write_json(workspace_root, output_path, report)
    write_json(workspace_root, sibling_artifact(output_path, "repair_loop_state.json"), repair_state)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Verification report written.",
        "artifacts": [output_path, sibling_artifact(output_path, "repair_loop_state.json")],
        "confidence": "medium",
    }


def run_code_review(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    patch = load_json_optional(workspace_root, sibling_artifact(output_path, "patch_manifest.json"))
    verification = load_json_optional(workspace_root, sibling_artifact(output_path, "verification_report.json"))
    repair_state = load_json_optional(workspace_root, sibling_artifact(output_path, "repair_loop_state.json"))
    role_policy = role_policy_from_request(request)
    task_profile = task_profile_from_request(request)
    worker_brief = worker_brief_from_request(request)
    blockers = verification.get("blockers") if isinstance(verification.get("blockers"), list) else []
    warnings = verification.get("warnings") if isinstance(verification.get("warnings"), list) else []
    scope = patch.get("patch_scope_evidence") if isinstance(patch.get("patch_scope_evidence"), dict) else {}
    readiness = patch.get("engineering_readiness") if isinstance(patch.get("engineering_readiness"), dict) else {}
    readiness_checks = readiness.get("readiness_checks") if isinstance(readiness.get("readiness_checks"), dict) else {}
    acceptance_criteria = readiness.get("acceptance_criteria") if isinstance(readiness.get("acceptance_criteria"), list) else []
    risk_register = readiness.get("risk_register") if isinstance(readiness.get("risk_register"), list) else []
    impact_matrix = readiness.get("impact_matrix") if isinstance(readiness.get("impact_matrix"), list) else []
    scope_review = patch_scope_review(scope)
    investigation_review = repository_investigation_review(survey, patch, scope_review)
    patch_source = str(patch.get("patch_source") or "")
    diagnostics = patch.get("diagnostics") if isinstance(patch.get("diagnostics"), dict) else {}
    changed_files = patch.get("changed_files") if isinstance(patch.get("changed_files"), list) else []
    failed_commands = repair_state.get("failed_commands") if isinstance(repair_state.get("failed_commands"), list) else []
    candidate_source_paths = repair_state.get("candidate_source_paths") if isinstance(repair_state.get("candidate_source_paths"), list) else []
    decision_record: list[dict[str, Any]] = [
        {
            "check": "patch_applied",
            "status": "pass" if patch.get("status") == "applied" else "blocker",
            "evidence": patch.get("status", "unknown"),
        },
        {
            "check": "verification_passed",
            "status": "pass" if verification.get("status") == "passed" else "blocker",
            "evidence": verification.get("status", "unknown"),
        },
        {
            "check": "scope_review",
            "status": str(scope_review.get("status") or "unknown"),
            "evidence": scope_review,
        },
        {
            "check": "diagnostic_linkage",
            "status": "pass" if (not patch_source.startswith("test_inferred_") or diagnostics) else "blocker",
            "evidence": diagnostics,
        },
        {
            "check": "readiness_model_present",
            "status": "pass" if readiness_checks.get("has_acceptance_criteria") and readiness_checks.get("has_test_strategy") else "blocker",
            "evidence": readiness_checks,
        },
        {
            "check": "impact_matrix_present",
            "status": "pass" if impact_matrix else "warning",
            "evidence": {"impact_file_count": len(impact_matrix)},
        },
        {
            "check": "repository_investigation_review",
            "status": "pass" if investigation_review.get("status") == "covered" else "blocker",
            "evidence": investigation_review,
        },
    ]
    review_warnings = [
        {"severity": "warning", "message": str(item)}
        for item in warnings
    ]
    if scope_review.get("unmapped_changed_file_count", 0):
        files = ", ".join(scope_review.get("unmapped_changed_files", [])[:5])
        review_warnings.append(
            {
                "severity": "warning",
                "message": f"Changed file(s) outside ranked repo map should be manually checked for scope drift: {files}",
            }
        )
    if scope_review.get("source_without_linked_tests"):
        files = ", ".join(scope_review.get("source_without_linked_tests", [])[:5])
        review_warnings.append(
            {
                "severity": "warning",
                "message": f"Changed source file(s) have no static linked tests in repo map; verify coverage manually: {files}",
            }
        )
    if patch.get("status") != "applied":
        blockers = [*blockers, "Patch manifest was not applied."]
    if verification.get("status") != "passed":
        blockers = [*blockers, "Verification did not pass."]
    if patch_source.startswith("test_inferred_") and not diagnostics:
        blockers = [*blockers, "Test-inferred patch lacks diagnostics linking test evidence to source mutation."]
    if investigation_review.get("status") != "covered":
        for item in investigation_review.get("blockers", []) if isinstance(investigation_review.get("blockers"), list) else []:
            check_name = str(item.get("check") or "repository_investigation")
            blockers = [*blockers, f"Repository investigation is incomplete: {check_name}."]
    if not acceptance_criteria:
        blockers = [*blockers, "Engineering readiness model lacks acceptance criteria."]
    if not readiness_checks.get("has_test_strategy"):
        blockers = [*blockers, "Engineering readiness model lacks test strategy."]
    high_risks = [item for item in risk_register if isinstance(item, dict) and item.get("severity") == "high"]
    if high_risks and not changed_files:
        blockers = [*blockers, "High-risk task has no applied source change or explicit handoff resolution."]
    focused_revision_context = {
        "candidate_source_paths": [str(item) for item in candidate_source_paths[:12]],
        "changed_files": [
            str(item.get("path"))
            for item in changed_files
            if isinstance(item, dict) and item.get("path")
        ][:12],
        "failed_commands": [
            str(item.get("command"))
            for item in failed_commands
            if isinstance(item, dict) and item.get("command")
        ][:8],
        "patch_source": patch_source,
        "diagnostics": diagnostics,
        "engineering_readiness": {
            "acceptance_criteria": acceptance_criteria,
            "risk_register": risk_register,
            "impact_matrix": impact_matrix,
            "readiness_checks": readiness_checks,
        },
        "repository_investigation_review": investigation_review,
    }
    review = {
        "status": "blocked" if blockers else "passed",
        "approved": not blockers,
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "repair_loop_status": repair_state.get("status", "unknown"),
        "patch_scope_review": scope_review,
        "engineering_readiness_review": {
            "readiness_checks": readiness_checks,
            "acceptance_criteria_count": len(acceptance_criteria),
            "risk_count": len(risk_register),
            "high_risk_count": len(high_risks),
            "impact_file_count": len(impact_matrix),
        },
        "repository_investigation_review": investigation_review,
        "decision_record": decision_record,
        "findings": [
            {"severity": "blocker", "message": str(item)}
            for item in blockers
        ],
        "warnings": [
            *review_warnings,
            {
                "severity": "warning",
                "message": "Ceraxia currently supports only explicit patch operations; autonomous code synthesis is not enabled yet.",
            }
        ],
        "revision_plan": {
            "required": bool(blockers),
            "focused_context": focused_revision_context if blockers else {},
            "steps": [
                {
                    "step_id": "implementation",
                    "worker": "FerrumPatchwright",
                    "reason": "Rebuild the patch from focused_context and preserve diagnostic linkage.",
                    "source": "code_review",
                    "priority": "blocker",
                },
                {
                    "step_id": "verification",
                    "worker": "OrdinatusVerifier",
                    "reason": "Rerun allowlisted verification and preserve failed command output if it still fails.",
                    "source": "code_review",
                    "priority": "blocker",
                },
            ] if blockers else [],
        },
    }
    write_json(workspace_root, output_path, review)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "needs_revision" if blockers else "passed",
        "summary": f"Code review written with {len(blockers)} blocker(s).",
        "artifacts": [output_path],
        "revision_plan": review["revision_plan"],
        "confidence": "medium",
    }


def run_finalize(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    patch = load_json_optional(workspace_root, sibling_artifact(output_path, "patch_manifest.json"))
    verification = load_json_optional(workspace_root, sibling_artifact(output_path, "verification_report.json"))
    repair_state = load_json_optional(workspace_root, sibling_artifact(output_path, "repair_loop_state.json"))
    review = load_json_optional(workspace_root, sibling_artifact(output_path, "code_review.json"))
    role_policy = role_policy_from_request(request)
    task_profile = task_profile_from_request(request)
    worker_brief = worker_brief_from_request(request)
    status = "blocked" if review.get("approved") is False else "ready"
    manifest = {
        "status": status,
        "approved": review.get("approved") is True,
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "role_policies": {
            "implementation": patch.get("role_policy", {}),
            "verification": verification.get("role_policy", {}),
            "code_review": review.get("role_policy", {}),
            "finalize": role_policy,
        },
        "deliverables": [
            sibling_artifact(output_path, "repo_survey.json"),
            sibling_artifact(output_path, "change_plan.md"),
            sibling_artifact(output_path, "patch_manifest.json"),
            sibling_artifact(output_path, "verification_report.json"),
            sibling_artifact(output_path, "repair_loop_state.json"),
            sibling_artifact(output_path, "code_review.json"),
        ],
        "changed_files": patch.get("changed_files", []),
        "recommended_read_order": patch.get("recommended_read_order", []),
        "engineering_investigation": survey.get("engineering_investigation", {}) if isinstance(survey.get("engineering_investigation"), dict) else {},
        "engineering_readiness": patch.get("engineering_readiness", {}),
        "engineering_readiness_review": review.get("engineering_readiness_review", {}),
        "repository_investigation_review": review.get("repository_investigation_review", {}),
        "patch_scope_evidence": patch.get("patch_scope_evidence", {}),
        "patch_source": patch.get("patch_source", ""),
        "patch_candidates": patch.get("patch_candidates", []),
        "selected_patch_candidate": patch.get("selected_patch_candidate", {}),
        "dirty_worktree": patch.get("dirty_worktree", {}),
        "ambiguity_analysis": patch.get("ambiguity_analysis", {}),
        "source_excerpt_summary": patch.get("source_excerpt_summary", []),
        "implementation_decision_record": patch.get("implementation_decision_record", []),
        "diagnostics": patch.get("diagnostics", {}),
        "operation_count": patch.get("operation_count", 0),
        "verification_status": verification.get("status", "unknown"),
        "verification_executed": verification.get("executed", []),
        "verification_repairs": verification.get("repairs", []),
        "repair_loop_state": repair_state,
        "verification_blockers": verification.get("blockers", []),
        "verification_summary": {
            "executed_count": len(verification.get("executed", [])) if isinstance(verification.get("executed"), list) else 0,
            "repair_count": len(verification.get("repairs", [])) if isinstance(verification.get("repairs"), list) else 0,
            "blocker_count": len(verification.get("blockers", [])) if isinstance(verification.get("blockers"), list) else 0,
        },
        "execution_report": {
            "task_profile": task_profile,
            "worker_briefs_present": {
                "repository_survey": bool(load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json")).get("worker_brief")),
                "implementation": bool(patch.get("worker_brief")),
                "verification": bool(verification.get("worker_brief")),
                "code_review": bool(review.get("worker_brief")),
                "finalize": bool(worker_brief),
            },
            "changed_file_count": len(patch.get("changed_files", [])) if isinstance(patch.get("changed_files"), list) else 0,
            "verification_command_count": len(verification.get("executed", [])) if isinstance(verification.get("executed"), list) else 0,
            "repair_attempt_count": len(repair_state.get("repair_attempts", [])) if isinstance(repair_state.get("repair_attempts"), list) else 0,
            "patch_candidate_count": len(patch.get("patch_candidates", [])) if isinstance(patch.get("patch_candidates"), list) else 0,
            "source_excerpt_count": len(patch.get("source_excerpt_summary", [])) if isinstance(patch.get("source_excerpt_summary"), list) else 0,
            "acceptance_criteria_count": len(
                patch.get("engineering_readiness", {}).get("acceptance_criteria", [])
                if isinstance(patch.get("engineering_readiness", {}), dict)
                and isinstance(patch.get("engineering_readiness", {}).get("acceptance_criteria"), list)
                else []
            ),
            "risk_count": len(
                patch.get("engineering_readiness", {}).get("risk_register", [])
                if isinstance(patch.get("engineering_readiness", {}), dict)
                and isinstance(patch.get("engineering_readiness", {}).get("risk_register"), list)
                else []
            ),
            "impact_file_count": len(
                patch.get("engineering_readiness", {}).get("impact_matrix", [])
                if isinstance(patch.get("engineering_readiness", {}), dict)
                and isinstance(patch.get("engineering_readiness", {}).get("impact_matrix"), list)
                else []
            ),
            "blocker_count": len([item.get("message") for item in review.get("findings", []) if isinstance(item, dict)]),
            "revision_required": bool(review.get("revision_plan", {}).get("required")) if isinstance(review.get("revision_plan"), dict) else False,
        },
        "review_status": review.get("status", "unknown"),
        "patch_scope_review": review.get("patch_scope_review", {}),
        "review_decision_record": review.get("decision_record", []),
        "blockers": [item.get("message") for item in review.get("findings", []) if isinstance(item, dict)],
        "next_safe_action": "handoff_to_patch_worker" if status == "blocked" else "inspect_final_package",
        "summary": "Ceraxia code task package finalized.",
        "revision_plan": review.get("revision_plan", {"required": False, "steps": []}),
    }
    write_json(workspace_root, output_path, manifest)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": status,
        "summary": manifest["summary"],
        "artifacts": [output_path],
        "revision_plan": manifest["revision_plan"],
        "confidence": "medium",
    }


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    step = request.get("step") if isinstance(request.get("step"), dict) else {}
    step_id = str(step.get("step_id") or "")
    output_path = output_path_from_request(request)
    handlers = {
        "repository_survey": run_repository_survey,
        "change_planning": run_change_planning,
        "implementation": run_implementation,
        "verification": run_verification,
        "code_review": run_code_review,
        "finalize": run_finalize,
    }
    handler = handlers.get(step_id)
    if handler is None:
        return {"ok": False, "worker": worker_name(), "error": f"unsupported step_id: {step_id}"}
    return handler(request, workspace_root, output_path)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run CogitatorCodewright code worker.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/mechanicum-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    result = run(payload.get("request") if isinstance(payload.get("request"), dict) else payload, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") or result.get("status") in {"blocked", "needs_revision", "passed_with_warnings"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
