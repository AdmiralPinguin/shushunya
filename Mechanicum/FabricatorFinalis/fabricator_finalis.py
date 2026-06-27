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


def build_manifest(workspace_root: Path, manifest_path: str) -> dict[str, Any]:
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
    status = "ready" if approved and not missing else "blocked"
    return {
        "status": status,
        "approved": approved,
        "deliverable": sibling_artifact(manifest_path, "reconstruction_ru.md"),
        "files": files,
        "missing": missing,
        "critic_status": critic.get("status", "missing"),
        "warnings": critic.get("warnings", []),
        "blockers": critic.get("findings", []) + [{"severity": "blocker", "message": f"Missing package file: {path}"} for path in missing],
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
        manifest = build_manifest(workspace_root, manifest_path)
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
