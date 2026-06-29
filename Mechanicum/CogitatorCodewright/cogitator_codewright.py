from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


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


WORKER_NAME = "CogitatorCodewright"


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


def output_path_from_request(request: dict[str, Any]) -> str:
    step = request.get("step") if isinstance(request.get("step"), dict) else {}
    expected = step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []
    if not expected or not isinstance(expected[0], str):
        raise ValueError("step.expected_artifacts must contain an output path")
    return expected[0]


def repo_survey(repo_root: Path, goal: str) -> dict[str, Any]:
    extension_counts: Counter[str] = Counter()
    candidate_files: list[str] = []
    test_files: list[str] = []
    config_files: list[str] = []
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
        if path.name in {"pyproject.toml", "package.json", "build.gradle", "settings.gradle", "gradlew", "requirements.txt"}:
            config_files.append(rel)
        goal_tokens = {token for token in goal.lower().replace("/", " ").replace("_", " ").split() if len(token) > 3}
        rel_tokens = set(lowered.replace("/", " ").replace("_", " ").replace("-", " ").split())
        if goal_tokens & rel_tokens:
            candidate_files.append(rel)
    dominant_extensions = [{"extension": ext, "count": count} for ext, count in extension_counts.most_common(12)]
    return {
        "repo_root": str(repo_root),
        "goal": goal,
        "total_files_scanned": total_files,
        "dominant_extensions": dominant_extensions,
        "candidate_files": candidate_files[:80],
        "test_files": test_files[:80],
        "config_files": config_files[:40],
        "excluded_dirs": sorted(EXCLUDED_DIRS),
        "summary": f"Surveyed {total_files} files; found {len(test_files)} test-like files and {len(candidate_files)} goal-matching candidates.",
    }


def run_repository_survey(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    goal = str(request.get("goal") or request.get("task") or "")
    survey = repo_survey(Path.cwd(), goal)
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
    goal = str(request.get("goal") or request.get("task") or survey.get("goal") or "")
    candidates = survey.get("candidate_files") if isinstance(survey.get("candidate_files"), list) else []
    tests = survey.get("test_files") if isinstance(survey.get("test_files"), list) else []
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
            "## Test Surface",
            *[f"- {item}" for item in tests[:30]],
            "",
            "## Implementation Policy",
            "- Produce an auditable patch manifest before mutating source files.",
            "- Require verification commands or explicit blockers before final readiness.",
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
    manifest = {
        "status": "handoff_required",
        "mode": "auditable_handoff",
        "task_id": request.get("task_id"),
        "summary": "Ceraxia prepared implementation intent, but no source files were mutated by this worker.",
        "intended_actions": [
            "read concrete target files before editing",
            "apply minimal scoped patch",
            "run verification commands from verification_report.json",
            "return focused revision steps on failure",
        ],
        "plan_excerpt": plan[:3000],
        "changed_files": [],
        "blockers": [
            "Direct source mutation is not enabled for this worker yet; hand off to a patch/apply worker before claiming the code task complete.",
        ],
        "warnings": [
            "The current package is an auditable implementation handoff, not a completed code change.",
        ],
    }
    write_json(workspace_root, output_path, manifest)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Patch manifest written as auditable handoff; source mutation remains blocked.",
        "artifacts": [output_path],
        "confidence": "medium",
    }


def run_verification(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    patch = load_json_optional(workspace_root, sibling_artifact(output_path, "patch_manifest.json"))
    report = {
        "status": "blocked" if patch.get("blockers") else "ready",
        "task_id": request.get("task_id"),
        "commands": [
            "./EyeOfTerror/check-eye-mechanicum.sh",
            "git diff --check",
        ],
        "executed": [],
        "blockers": patch.get("blockers", []),
        "warnings": patch.get("warnings", []),
        "summary": "Verification commands are identified; execution awaits real source mutation." if patch.get("blockers") else "Verification commands are identified.",
    }
    write_json(workspace_root, output_path, report)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Verification report written.",
        "artifacts": [output_path],
        "confidence": "medium",
    }


def run_code_review(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    verification = load_json_optional(workspace_root, sibling_artifact(output_path, "verification_report.json"))
    blockers = verification.get("blockers") if isinstance(verification.get("blockers"), list) else []
    warnings = verification.get("warnings") if isinstance(verification.get("warnings"), list) else []
    review = {
        "status": "blocked" if blockers else "passed_with_warnings",
        "approved": not blockers,
        "findings": [
            {"severity": "blocker", "message": str(item)}
            for item in blockers
        ],
        "warnings": [
            *[
                {"severity": "warning", "message": str(item)}
                for item in warnings
            ],
            {
                "severity": "warning",
                "message": "Ceraxia skeleton currently prepares code handoff artifacts; direct patch application is not enabled yet.",
            }
        ],
        "revision_plan": {
            "required": bool(blockers),
            "steps": [
                {
                    "step_id": "implementation",
                    "worker": "FerrumPatchwright",
                    "reason": "Enable or hand off to a source mutation worker before claiming implementation complete.",
                    "source": "code_review",
                    "priority": "blocker",
                },
                {
                    "step_id": "verification",
                    "worker": "OrdinatusVerifier",
                    "reason": "Run concrete verification after source mutation.",
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
        "status": "needs_revision" if blockers else "passed_with_warnings",
        "summary": f"Code review written with {len(blockers)} blocker(s).",
        "artifacts": [output_path],
        "revision_plan": review["revision_plan"],
        "confidence": "medium",
    }


def run_finalize(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    review = load_json_optional(workspace_root, sibling_artifact(output_path, "code_review.json"))
    status = "blocked" if review.get("approved") is False else "ready"
    manifest = {
        "status": status,
        "approved": review.get("approved") is True,
        "deliverables": [
            sibling_artifact(output_path, "repo_survey.json"),
            sibling_artifact(output_path, "change_plan.md"),
            sibling_artifact(output_path, "patch_manifest.json"),
            sibling_artifact(output_path, "verification_report.json"),
            sibling_artifact(output_path, "code_review.json"),
        ],
        "review_status": review.get("status", "unknown"),
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
