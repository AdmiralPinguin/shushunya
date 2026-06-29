from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PACKAGE_FILES = [
    "source_map.json",
    "source_snapshots.json",
    "direct_event_notes.json",
    "timeline.json",
    "reconstruction_ru.md",
    "coverage_report.md",
    "critic_report.json",
]

ARTIFACT_REWORK_TARGETS = {
    "source_map.json": ("source_discovery", "Lexmechanic"),
    "source_snapshots.json": ("source_acquisition", "AuspexBrowser"),
    "direct_event_notes.json": ("fact_extraction", "NoosphericExtractor"),
    "timeline.json": ("timeline", "Chronologis"),
    "reconstruction_ru.md": ("draft_reconstruction", "ScriptoriumDaemon"),
    "coverage_report.md": ("draft_reconstruction", "ScriptoriumDaemon"),
    "critic_report.json": ("critic_review", "ReductorVerifier"),
}


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def sibling_artifact(output_path: str, filename: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/{filename}"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"artifact must be an object: {path}")
    return payload


def missing_artifact_revision_steps(missing: list[str]) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for artifact in missing:
        filename = artifact.rsplit("/", 1)[-1]
        target = ARTIFACT_REWORK_TARGETS.get(filename)
        if not target:
            continue
        step_id, worker = target
        reason = f"Missing package file: {artifact}"
        key = (step_id, worker, reason)
        if key in seen:
            continue
        seen.add(key)
        steps.append(
            {
                "step_id": step_id,
                "worker": worker,
                "reason": reason,
                "source": "missing_package_file",
                "priority": "blocker",
            }
        )
    return steps


def merge_revision_plan(critic: dict[str, Any], missing: list[str]) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    critic_plan = critic.get("revision_plan") if isinstance(critic.get("revision_plan"), dict) else {}
    for item in critic_plan.get("steps", []) if isinstance(critic_plan.get("steps"), list) else []:
        if not isinstance(item, dict):
            continue
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if marker not in seen:
            steps.append(item)
            seen.add(marker)
    for item in missing_artifact_revision_steps(missing):
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if marker not in seen:
            steps.append(item)
            seen.add(marker)
    return {"required": bool(steps), "steps": steps}


def quality_expectation_summary(request: dict[str, Any]) -> dict[str, Any]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    step_quality = expectations.get("step_quality") if isinstance(expectations.get("step_quality"), dict) else {}
    return {
        "provided": bool(expectations),
        "step_id": str(step_quality.get("step_id") or ""),
        "worker": str(step_quality.get("worker") or ""),
        "check_count": len(step_quality.get("checks") if isinstance(step_quality.get("checks"), list) else []),
        "blocker_count": len(step_quality.get("blockers") if isinstance(step_quality.get("blockers"), list) else []),
        "revision_targets": step_quality.get("revision_targets", []) if isinstance(step_quality.get("revision_targets"), list) else [],
    }


def quality_expectation_blockers(request: dict[str, Any]) -> list[dict[str, str]]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    step_quality = expectations.get("step_quality") if isinstance(expectations.get("step_quality"), dict) else {}
    if not step_quality:
        return []
    step = request.get("step") if isinstance(request.get("step"), dict) else {}
    blockers: list[dict[str, str]] = []
    if str(step_quality.get("worker") or "") not in {"", "FabricatorFinalis"}:
        blockers.append({"severity": "blocker", "message": f"Quality expectations target another worker: {step_quality.get('worker')}"})
    expected_artifacts = step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []
    if step_quality.get("expected_artifacts") != expected_artifacts:
        blockers.append({"severity": "blocker", "message": "Quality expectations expected_artifacts do not match request.step"})
    return blockers


def build_manifest(workspace_root: Path, manifest_path: str, request: dict[str, Any]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    missing: list[str] = []
    for filename in PACKAGE_FILES:
        artifact_path = sibling_artifact(manifest_path, filename)
        host_path = sandbox_path(workspace_root, artifact_path)
        if not host_path.exists():
            missing.append(artifact_path)
            continue
        files.append(
            {
                "path": artifact_path,
                "bytes": host_path.stat().st_size,
                "kind": "markdown" if filename.endswith(".md") else "json",
            }
        )
    critic_path = sandbox_path(workspace_root, sibling_artifact(manifest_path, "critic_report.json"))
    critic = load_json(critic_path) if critic_path.exists() else {}
    approved = bool(critic.get("approved"))
    quality_blockers = quality_expectation_blockers(request)
    critic_metrics = critic.get("metrics", {}) if isinstance(critic.get("metrics"), dict) else {}
    source_coverage_ready = critic_metrics.get("source_coverage_ready")
    comprehensive_depth = critic_metrics.get("comprehensive_depth") if isinstance(critic_metrics.get("comprehensive_depth"), dict) else {}
    comprehensive_depth_ready = comprehensive_depth.get("passed") if comprehensive_depth.get("mode") == "comprehensive" else True
    readiness_blockers: list[dict[str, str]] = []
    if source_coverage_ready is False:
        readiness_blockers.append({"severity": "blocker", "message": "Final package source coverage is not extraction-ready."})
    if comprehensive_depth_ready is False:
        readiness_blockers.append({"severity": "blocker", "message": "Final package does not satisfy comprehensive depth requirements."})
    readiness_checks = {
        "critic_approved": approved,
        "package_complete": not missing,
        "quality_expectations_ok": not quality_blockers,
        "source_coverage_ready": source_coverage_ready,
        "comprehensive_depth_ready": comprehensive_depth_ready,
    }
    status = "ready" if approved and not missing and not quality_blockers and not readiness_blockers else "blocked"
    revision_plan = merge_revision_plan(critic, missing)
    if quality_blockers:
        revision_plan = {
            "required": True,
            "steps": revision_plan.get("steps", []) + [
                {
                    "step_id": "finalize",
                    "worker": "FabricatorFinalis",
                    "reason": "Finalizer quality expectations failed",
                    "source": "quality_expectations",
                    "priority": "blocker",
                }
            ],
        }
    return {
        "status": status,
        "approved": approved,
        "deliverable": sibling_artifact(manifest_path, "reconstruction_ru.md"),
        "files": files,
        "missing": missing,
        "critic_status": critic.get("status", "missing"),
        "critic_metrics": critic_metrics,
        "readiness_checks": readiness_checks,
        "warnings": critic.get("warnings", []),
        "blockers": critic.get("findings", []) + [{"severity": "blocker", "message": f"Missing package file: {path}"} for path in missing] + quality_blockers + readiness_blockers,
        "revision_plan": revision_plan,
        "revision_focus": critic.get("revision_focus", {"present": False}),
        "quality_expectations": quality_expectation_summary(request),
    }


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    step = request.get("step")
    if not isinstance(step, dict):
        return {"ok": False, "worker": "FabricatorFinalis", "error": "request.step must be an object"}
    expected_artifacts = step.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or not expected_artifacts:
        return {"ok": False, "worker": "FabricatorFinalis", "error": "step.expected_artifacts is empty"}
    manifest_path = str(expected_artifacts[0])
    try:
        manifest = build_manifest(workspace_root, manifest_path, request)
    except (ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "worker": "FabricatorFinalis", "error": str(exc)}
    host_path = sandbox_path(workspace_root, manifest_path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "worker": "FabricatorFinalis",
        "task_id": request.get("task_id"),
        "status": manifest["status"],
        "summary": f"Final manifest written: {manifest['status']}.",
        "artifacts": [manifest_path],
        "gaps": [item["message"] for item in manifest["blockers"]],
        "revision_plan": manifest.get("revision_plan", {}),
        "confidence": "medium",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run FabricatorFinalis on a Worker API request JSON.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/fabricator-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    request = payload.get("request") if isinstance(payload, dict) and isinstance(payload.get("request"), dict) else payload
    result = run(request, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
