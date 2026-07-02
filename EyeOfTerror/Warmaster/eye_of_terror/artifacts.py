"""Artifact and final-manifest inspection for run packages."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .run_package import sandbox_artifact_file_status


def artifact_status(ledger: dict[str, Any]) -> dict[str, Any]:
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    workspace_root = str(result.get("workspace_root") or "")
    artifacts = result.get("artifacts", [])
    if not isinstance(artifacts, list):
        artifacts = []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append_artifact(sandbox_path: str, source: str, extra: dict[str, Any] | None = None) -> None:
        if sandbox_path in seen:
            return
        seen.add(sandbox_path)
        item = sandbox_artifact_file_status(workspace_root, sandbox_path)
        item["source"] = source
        if extra:
            item.update(extra)
        items.append(item)

    for artifact in artifacts:
        sandbox_path = str(artifact)
        append_artifact(sandbox_path, "result")
        if workspace_root and sandbox_path.endswith("/final_manifest.json") and sandbox_path.startswith("/work/"):
            manifest_path = Path(workspace_root) / sandbox_path.removeprefix("/work/")
            if manifest_path.exists():
                manifest_error = ""
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    manifest = {}
                    manifest_error = str(exc)
                if isinstance(manifest, dict):
                    for item in items:
                        if item.get("path") == sandbox_path:
                            if manifest_error:
                                item["manifest_error"] = manifest_error
                            else:
                                item["manifest_summary"] = compact_manifest_summary(manifest)
                            break
                files = manifest.get("files") if isinstance(manifest, dict) else []
                for file_item in files if isinstance(files, list) else []:
                    if isinstance(file_item, dict):
                        package_path = str(file_item.get("path") or "")
                        if package_path:
                            append_artifact(package_path, "final_manifest")
    return {"workspace_root": workspace_root, "artifacts": items}


def compact_manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    warnings = manifest.get("warnings", []) if isinstance(manifest.get("warnings"), list) else []
    blockers = manifest.get("blockers", []) if isinstance(manifest.get("blockers"), list) else []
    files = manifest.get("files", []) if isinstance(manifest.get("files"), list) else []
    return {
        "status": manifest.get("status", ""),
        "approved": bool(manifest.get("approved")),
        "critic_status": manifest.get("critic_status", ""),
        "critic_metrics": manifest.get("critic_metrics", {}),
        "event_review": manifest.get("event_review", {}) if isinstance(manifest.get("event_review"), dict) else {},
        "corpus_diagnostics": manifest.get("corpus_diagnostics", {}) if isinstance(manifest.get("corpus_diagnostics"), dict) else {},
        "corpus_requirements": manifest.get("corpus_requirements", {}) if isinstance(manifest.get("corpus_requirements"), dict) else {},
        "package_file_errors": manifest.get("package_file_errors", []) if isinstance(manifest.get("package_file_errors"), list) else [],
        "readiness_checks": manifest.get("readiness_checks", {}) if isinstance(manifest.get("readiness_checks"), dict) else {},
        "revision_focus": manifest.get("revision_focus", {}),
        "task_profile": manifest.get("task_profile", {}) if isinstance(manifest.get("task_profile"), dict) else {},
        "execution_report": manifest.get("execution_report", {}) if isinstance(manifest.get("execution_report"), dict) else {},
        "engineering_investigation": {
            "hypothesis_count": len(
                manifest.get("engineering_investigation", {}).get("hypotheses", [])
                if isinstance(manifest.get("engineering_investigation", {}), dict)
                and isinstance(manifest.get("engineering_investigation", {}).get("hypotheses"), list)
                else []
            ),
            "targeted_read_count": len(
                manifest.get("engineering_investigation", {}).get("targeted_reading_plan", [])
                if isinstance(manifest.get("engineering_investigation", {}), dict)
                and isinstance(manifest.get("engineering_investigation", {}).get("targeted_reading_plan"), list)
                else []
            ),
            "dependency_edge_count": int(
                manifest.get("engineering_investigation", {}).get("dependency_graph", {}).get("edge_count") or 0
            )
            if isinstance(manifest.get("engineering_investigation", {}), dict)
            and isinstance(manifest.get("engineering_investigation", {}).get("dependency_graph"), dict)
            else 0,
        },
        "engineering_readiness": {
            "acceptance_criteria_count": len(
                manifest.get("engineering_readiness", {}).get("acceptance_criteria", [])
                if isinstance(manifest.get("engineering_readiness", {}), dict)
                and isinstance(manifest.get("engineering_readiness", {}).get("acceptance_criteria"), list)
                else []
            ),
            "risk_count": len(
                manifest.get("engineering_readiness", {}).get("risk_register", [])
                if isinstance(manifest.get("engineering_readiness", {}), dict)
                and isinstance(manifest.get("engineering_readiness", {}).get("risk_register"), list)
                else []
            ),
            "impact_file_count": len(
                manifest.get("engineering_readiness", {}).get("impact_matrix", [])
                if isinstance(manifest.get("engineering_readiness", {}), dict)
                and isinstance(manifest.get("engineering_readiness", {}).get("impact_matrix"), list)
                else []
            ),
            "high_risk_count": int(
                manifest.get("engineering_readiness_review", {}).get("high_risk_count") or 0
            )
            if isinstance(manifest.get("engineering_readiness_review", {}), dict)
            else 0,
        },
        "patch_source": str(manifest.get("patch_source") or ""),
        "selected_patch_source": str(
            manifest.get("selected_patch_candidate", {}).get("source") or manifest.get("patch_source") or ""
        )
        if isinstance(manifest.get("selected_patch_candidate", {}), dict)
        else str(manifest.get("patch_source") or ""),
        "patch_candidate_count": len(manifest.get("patch_candidates", []) if isinstance(manifest.get("patch_candidates"), list) else []),
        "source_excerpt_count": len(
            manifest.get("source_excerpt_summary", []) if isinstance(manifest.get("source_excerpt_summary"), list) else []
        ),
        "implementation_decision_count": len(
            manifest.get("implementation_decision_record", [])
            if isinstance(manifest.get("implementation_decision_record"), list)
            else []
        ),
        "operation_count": int(manifest.get("operation_count") or 0),
        "verification_status": str(manifest.get("verification_status") or ""),
        "verification_summary": manifest.get("verification_summary", {}) if isinstance(manifest.get("verification_summary"), dict) else {},
        "review_status": str(manifest.get("review_status") or ""),
        "review_decision_count": len(manifest.get("review_decision_record", []) if isinstance(manifest.get("review_decision_record"), list) else []),
        "next_safe_action": str(manifest.get("next_safe_action") or ""),
        "warning_count": len(warnings),
        "blocker_count": len(blockers),
        "file_count": len(files),
        "warnings": warnings,
        "blockers": blockers,
    }


def final_manifest_summary(result: dict[str, Any]) -> dict[str, Any]:
    workspace_root = str(result.get("workspace_root") or "")
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
    manifest_artifact = next((str(path) for path in artifacts if str(path).endswith("/final_manifest.json")), "")
    if not workspace_root or not manifest_artifact.startswith("/work/"):
        return {}
    manifest_path = Path(workspace_root) / manifest_artifact.removeprefix("/work/")
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return compact_manifest_summary(manifest) if isinstance(manifest, dict) else {}


def resolve_artifact(ledger: dict[str, Any], artifact_path: str) -> Path:
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    workspace_root = str(result.get("workspace_root") or "")
    if not workspace_root:
        raise ValueError("workspace_root is not recorded for this run")
    if not artifact_path.startswith("/work/"):
        raise ValueError("artifact path must start with /work/")
    root = Path(workspace_root).resolve()
    host_path = (root / artifact_path.removeprefix("/work/")).resolve()
    if root not in host_path.parents and host_path != root:
        raise ValueError("artifact path escapes workspace_root")
    return host_path


def artifact_text(ledger: dict[str, Any], artifact_path: str, max_bytes: int = 500000) -> dict[str, Any]:
    host_path = resolve_artifact(ledger, artifact_path)
    if not host_path.exists():
        return {"ok": False, "error": "artifact not found", "path": artifact_path}
    data = host_path.read_bytes()[: max_bytes + 1]
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    return {
        "ok": True,
        "path": artifact_path,
        "host_path": str(host_path),
        "bytes": host_path.stat().st_size,
        "truncated": truncated,
        "text": data.decode("utf-8", errors="replace"),
    }


def final_package(ledger: dict[str, Any], max_bytes: int = 20000) -> dict[str, Any]:
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
    manifest_artifact = next((str(path) for path in artifacts if str(path).endswith("/final_manifest.json")), "")
    if not manifest_artifact:
        return {"ok": False, "error": "final manifest is not recorded"}
    manifest_path = resolve_artifact(ledger, manifest_artifact)
    if not manifest_path.exists():
        return {"ok": False, "error": "final manifest not found", "path": manifest_artifact}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"final manifest is corrupt: {exc}", "path": manifest_artifact}
    if not isinstance(manifest, dict):
        return {"ok": False, "error": "final manifest is not a JSON object", "path": manifest_artifact}
    files: list[dict[str, Any]] = []
    raw_files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            continue
        sandbox_path = str(raw_file.get("path") or "")
        if not sandbox_path:
            continue
        item = {**raw_file, **sandbox_artifact_file_status(str(result.get("workspace_root") or ""), sandbox_path)}
        if item.get("exists"):
            preview = artifact_text(ledger, sandbox_path, max_bytes=max_bytes)
            if preview.get("ok"):
                item["preview"] = {
                    "bytes": preview.get("bytes", 0),
                    "truncated": bool(preview.get("truncated")),
                    "text": preview.get("text", ""),
                }
        files.append(item)
    return {
        "ok": True,
        "manifest_path": manifest_artifact,
        "host_path": str(manifest_path),
        "summary": compact_manifest_summary(manifest),
        "deliverable": str(manifest.get("deliverable") or ""),
        "manifest": manifest,
        "files": files,
    }
