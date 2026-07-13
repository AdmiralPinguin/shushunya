"""Artifact and final-manifest inspection for run packages."""
from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator

MAX_MANIFEST_BYTES = 5_000_000
MAX_ARTIFACT_CATALOG_ITEMS = 4096
MAX_ARTIFACT_CATALOG_ERRORS = 32
MAX_ARTIFACT_CATALOG_PATH_CHARS = 1024
MAX_ARTIFACT_CATALOG_MANIFESTS = 16
MAX_ARTIFACT_MANIFEST_SUMMARY_BYTES = 64_000
VCS_DIR_NAMES = {".git", ".hg", ".svn"}


def _safe_artifact_root(value: str, label: str) -> Path:
    if not value:
        raise ValueError(f"{label} is not recorded for this run")
    root = Path(value)
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError(f"{label} must be an absolute non-traversing path")
    if any(part.casefold() in VCS_DIR_NAMES for part in root.parts):
        raise ValueError(f"{label} may not enter version-control metadata")
    return root


def _open_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    else:
        # A malicious recorded path must not block a request thread on a FIFO.
        flags |= getattr(os, "O_NONBLOCK", 0)
    return flags


def _open_regular_beneath(root: Path, parts: tuple[str, ...]) -> tuple[int, os.stat_result]:
    """Open one regular, single-link artifact without following any symlink.

    Every lookup after the trusted root is relative to an already-open directory
    descriptor.  This makes the validation and final open one race-safe operation
    instead of a vulnerable ``resolve()`` followed by ``open()`` sequence.
    """
    if not parts or any(part.casefold() in VCS_DIR_NAMES for part in parts):
        raise ValueError("artifact path enters version-control metadata")
    directory_fds: list[int] = []
    try:
        current_fd = os.open("/", _open_flags(directory=True))
        directory_fds.append(current_fd)
        for root_part in root.parts[1:]:
            current_fd = os.open(
                root_part, _open_flags(directory=True), dir_fd=current_fd,
            )
            directory_fds.append(current_fd)
        for part in parts[:-1]:
            current_fd = os.open(part, _open_flags(directory=True), dir_fd=current_fd)
            directory_fds.append(current_fd)
        file_fd = os.open(parts[-1], _open_flags(), dir_fd=current_fd)
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("artifact must be a regular, non-linked file")
        except Exception:
            os.close(file_fd)
            raise
        return file_fd, info
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("artifact path is missing, linked, or has an unsafe component") from exc
    finally:
        for descriptor in reversed(directory_fds):
            os.close(descriptor)


def _regular_size_beneath(root: Path, parts: tuple[str, ...]) -> int:
    descriptor, info = _open_regular_beneath(root, parts)
    os.close(descriptor)
    return int(info.st_size)


def _read_regular_beneath(root: Path, parts: tuple[str, ...], max_bytes: int) -> tuple[bytes, int]:
    descriptor, info = _open_regular_beneath(root, parts)
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            return handle.read(max(0, int(max_bytes)) + 1), int(info.st_size)
    except Exception:
        # fdopen owns the descriptor once it succeeds; close only if construction
        # itself failed before ownership transferred.
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _decode_json_object_bounded(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("final manifest exceeds the byte limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"final manifest is corrupt: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("final manifest is not a JSON object")
    return payload


def _is_skitarii_result(result: dict[str, Any]) -> bool:
    return str(result.get("final_step") or "") == "skitarii"


def _native_artifact_location(
    result: dict[str, Any], artifact_path: str,
) -> tuple[Path, tuple[str, ...], Path]:
    root_text = str(result.get("artifact_root") or "")
    if not root_text:
        raise ValueError("artifact_root is not recorded for this Skitarii run")
    value = str(artifact_path).replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not value or "\x00" in value or path.is_absolute() or not path.parts
        or any(part in ("", ".", "..", ".git") for part in path.parts)
        or path.parts[0].endswith(":")
    ):
        raise ValueError("artifact path must be a safe run-relative path")
    normalized = path.as_posix()
    recorded = {
        PurePosixPath(str(item).replace("\\", "/")).as_posix()
        for item in (result.get("artifacts") or [])
        if isinstance(item, str) and item
    }
    if normalized not in recorded:
        raise ValueError("artifact path is not recorded in the run result")
    root = _safe_artifact_root(root_text, "artifact_root")
    parts = tuple(path.parts)
    return root, parts, root.joinpath(*parts)


def _native_artifact_status(result: dict[str, Any], artifact_path: str) -> dict[str, Any]:
    try:
        root, parts, _host_path = _native_artifact_location(result, artifact_path)
        size = _regular_size_beneath(root, parts)
    except ValueError as exc:
        return {"path": artifact_path, "exists": False, "errors": [str(exc)]}
    return {
        "path": artifact_path,
        "exists": True,
        "bytes": size,
        "errors": [],
    }


def _generic_artifact_path(value: str) -> str:
    raw = str(value)
    path = PurePosixPath(raw)
    if (
        not raw.startswith("/work/")
        or "\\" in raw
        or "\x00" in raw
        or any(part in {"", ".", "..", ".git", ".hg", ".svn"} for part in path.parts)
    ):
        raise ValueError("artifact path must be a safe /work path")
    return path.as_posix()


def _generic_relative_parts(artifact_path: str) -> tuple[str, ...]:
    normalized = _generic_artifact_path(artifact_path)
    return tuple(PurePosixPath(normalized.removeprefix("/work/")).parts)


def _read_generic_manifest(root: Path, manifest_artifact: str) -> dict[str, Any]:
    raw, _size = _read_regular_beneath(
        root, _generic_relative_parts(manifest_artifact), MAX_MANIFEST_BYTES,
    )
    return _decode_json_object_bounded(raw)


def _generic_recorded_artifact_paths(result: dict[str, Any]) -> set[str]:
    recorded: set[str] = set()
    for item in result.get("artifacts") or []:
        if not isinstance(item, str):
            continue
        try:
            recorded.add(_generic_artifact_path(item))
        except ValueError:
            continue
    workspace_root = str(result.get("workspace_root") or "")
    if not workspace_root:
        return recorded
    try:
        root = _safe_artifact_root(workspace_root, "workspace_root")
    except ValueError:
        return recorded
    manifests = [path for path in recorded if path.endswith("/final_manifest.json")]
    for manifest_artifact in manifests:
        try:
            manifest = _read_generic_manifest(root, manifest_artifact)
        except ValueError:
            continue
        for raw_file in manifest.get("files") or []:
            if not isinstance(raw_file, dict):
                continue
            try:
                recorded.add(_generic_artifact_path(str(raw_file.get("path") or "")))
            except ValueError:
                continue
    return recorded


def _generic_artifact_location(
    result: dict[str, Any], artifact_path: str,
) -> tuple[Path, tuple[str, ...], Path]:
    workspace_root = str(result.get("workspace_root") or "")
    root = _safe_artifact_root(workspace_root, "workspace_root")
    normalized = _generic_artifact_path(artifact_path)
    if normalized not in _generic_recorded_artifact_paths(result):
        raise ValueError("artifact path is not recorded in the run result or final manifest")
    parts = _generic_relative_parts(normalized)
    return root, parts, root.joinpath(*parts)


def _artifact_location(
    ledger: dict[str, Any], artifact_path: str,
) -> tuple[Path, tuple[str, ...], Path]:
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    if _is_skitarii_result(result):
        return _native_artifact_location(result, artifact_path)
    return _generic_artifact_location(result, artifact_path)


def _bounded_catalog_manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    summary = compact_manifest_summary(manifest)
    encoded = json.dumps(
        summary,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    if len(encoded) <= MAX_ARTIFACT_MANIFEST_SUMMARY_BYTES:
        return summary

    def bounded_strings(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        return [" ".join(str(value)[:2_000].split())[:500] for value in values[:8]]

    return {
        "status": str(summary.get("status") or "")[:120],
        "approved": bool(summary.get("approved")),
        "critic_status": str(summary.get("critic_status") or "")[:120],
        "warning_count": int(summary.get("warning_count") or 0),
        "blocker_count": int(summary.get("blocker_count") or 0),
        "file_count": int(summary.get("file_count") or 0),
        "warnings": bounded_strings(summary.get("warnings")),
        "blockers": bounded_strings(summary.get("blockers")),
        "summary_truncated": True,
        "summary_byte_limit": MAX_ARTIFACT_MANIFEST_SUMMARY_BYTES,
    }


def artifact_status(
    ledger: dict[str, Any],
    *,
    max_items: int = MAX_ARTIFACT_CATALOG_ITEMS,
) -> dict[str, Any]:
    """Return a bounded, explicitly complete artifact catalog.

    Catalog construction deliberately validates each recorded location directly.
    Calling ``_artifact_location`` for every manifest member would rebuild and
    reparse the complete manifest on every iteration, turning an N-file package
    into O(N²) work.  The bounded protocol also prevents a consumer from treating
    a truncated or unparseable manifest as a complete list of accepted outputs.
    """
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    native_skitarii = _is_skitarii_result(result)
    workspace_root = str(result.get("workspace_root") or "")
    artifacts = result.get("artifacts", [])
    if not isinstance(artifacts, list):
        artifacts = []
    try:
        safe_limit = max(1, min(int(max_items), MAX_ARTIFACT_CATALOG_ITEMS))
    except (TypeError, ValueError):
        safe_limit = MAX_ARTIFACT_CATALOG_ITEMS
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    catalog_errors: list[str] = []
    catalog_error_count = 0
    truncated = False
    manifest_count = 0

    native_root: Path | None = None
    generic_root: Path | None = None
    root_error = ""
    try:
        if native_skitarii:
            native_root = _safe_artifact_root(
                str(result.get("artifact_root") or ""),
                "artifact_root",
            )
        else:
            generic_root = _safe_artifact_root(workspace_root, "workspace_root")
    except ValueError as exc:
        root_error = str(exc)

    def catalog_error(message: str) -> None:
        nonlocal catalog_error_count
        catalog_error_count += 1
        clean = " ".join(str(message)[:2_000].split())[:500]
        if clean and len(catalog_errors) < MAX_ARTIFACT_CATALOG_ERRORS:
            catalog_errors.append(clean)

    def direct_artifact_status(sandbox_path: str) -> dict[str, Any]:
        try:
            if root_error:
                raise ValueError(root_error)
            if native_skitarii:
                value = str(sandbox_path).replace("\\", "/")
                path = PurePosixPath(value)
                if (
                    not value
                    or "\x00" in value
                    or path.is_absolute()
                    or not path.parts
                    or any(part in ("", ".", "..", ".git") for part in path.parts)
                    or path.parts[0].endswith(":")
                ):
                    raise ValueError("artifact path must be a safe run-relative path")
                if native_root is None:
                    raise ValueError("artifact_root is unavailable")
                size = _regular_size_beneath(native_root, tuple(path.parts))
            else:
                normalized = _generic_artifact_path(sandbox_path)
                if generic_root is None:
                    raise ValueError("workspace_root is unavailable")
                size = _regular_size_beneath(
                    generic_root,
                    _generic_relative_parts(normalized),
                )
        except ValueError as exc:
            return {
                "path": sandbox_path,
                "exists": False,
                "bytes": 0,
                "errors": [str(exc)],
            }
        return {
            "path": sandbox_path,
            "exists": True,
            "bytes": size,
            "errors": [],
        }

    def append_artifact(
        raw_path: Any,
        source: str,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        nonlocal truncated
        if not isinstance(raw_path, str):
            catalog_error(f"{source} artifact path is not a string")
            return True
        if len(raw_path) > MAX_ARTIFACT_CATALOG_PATH_CHARS:
            catalog_error(
                f"{source} artifact path exceeds the {MAX_ARTIFACT_CATALOG_PATH_CHARS}-character catalog limit"
            )
            return True
        sandbox_path = raw_path
        if sandbox_path in seen:
            return True
        if len(items) >= safe_limit:
            truncated = True
            return False
        seen.add(sandbox_path)
        item = direct_artifact_status(sandbox_path)
        # Filesystem locations are an implementation detail and can disclose the
        # host layout to any gateway caller. Public artifact APIs use logical paths.
        item.pop("host_path", None)
        item["source"] = source
        if extra:
            item.update(extra)
        items.append(item)
        return True

    for artifact_index, artifact in enumerate(artifacts):
        if len(items) >= safe_limit:
            truncated = True
            break
        sandbox_path = artifact
        append_artifact(sandbox_path, "result")
        if (
            not native_skitarii
            and isinstance(sandbox_path, str)
            and len(sandbox_path) <= MAX_ARTIFACT_CATALOG_PATH_CHARS
            and sandbox_path.endswith("/final_manifest.json")
            and sandbox_path.startswith("/work/")
        ):
            manifest_count += 1
            if manifest_count > MAX_ARTIFACT_CATALOG_MANIFESTS:
                truncated = True
                catalog_error(
                    "artifact catalog contains more than "
                    f"{MAX_ARTIFACT_CATALOG_MANIFESTS} final manifests"
                )
                break
            manifest_error = ""
            try:
                if generic_root is None:
                    raise ValueError(root_error or "workspace_root is unavailable")
                raw, _size = _read_regular_beneath(
                    generic_root,
                    _generic_relative_parts(sandbox_path),
                    MAX_MANIFEST_BYTES,
                )
                manifest = _decode_json_object_bounded(raw)
            except ValueError as exc:
                manifest = {}
                manifest_error = str(exc)
                catalog_error(
                    f"could not enumerate package files from {sandbox_path}: {manifest_error}"
                )
            for item in items:
                if item.get("path") == sandbox_path:
                    if manifest_error:
                        item["manifest_error"] = manifest_error
                    else:
                        item["manifest_summary"] = _bounded_catalog_manifest_summary(
                            manifest
                        )
                    break
            files = manifest.get("files", []) if isinstance(manifest, dict) else []
            if not isinstance(files, list):
                catalog_error(f"{sandbox_path} files field is not a list")
                files = []
            for file_index, file_item in enumerate(files):
                if not isinstance(file_item, dict):
                    catalog_error(
                        f"{sandbox_path} files[{file_index}] is not an object"
                    )
                    continue
                package_path = file_item.get("path")
                if not isinstance(package_path, str) or not package_path:
                    catalog_error(
                        f"{sandbox_path} files[{file_index}] has no path"
                    )
                    continue
                if not append_artifact(package_path, "final_manifest"):
                    break
        if artifact_index + 1 < len(artifacts) and len(items) >= safe_limit:
            truncated = True
            break
    complete = not truncated and catalog_error_count == 0
    return {
        "artifacts": items,
        "artifact_catalog": {
            "schema_version": 1,
            "complete": complete,
            "truncated": truncated,
            "limit": safe_limit,
            "returned": len(items),
            "errors": catalog_errors,
            "error_count": catalog_error_count,
        },
    }


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
    try:
        root = _safe_artifact_root(workspace_root, "workspace_root")
        manifest = _read_generic_manifest(root, manifest_artifact)
    except ValueError:
        return {}
    return compact_manifest_summary(manifest)


def resolve_artifact(ledger: dict[str, Any], artifact_path: str) -> Path:
    root, parts, host_path = _artifact_location(ledger, artifact_path)
    _regular_size_beneath(root, parts)
    return host_path


@contextmanager
def open_artifact_binary(
    ledger: dict[str, Any], artifact_path: str,
) -> Iterator[tuple[BinaryIO, int]]:
    """Open one recorded artifact and keep the proven descriptor alive.

    Callers must consume the yielded stream inside the context.  Returning a
    host path after validation would reintroduce a symlink-swap race between
    validation and the eventual read; this API deliberately exposes no path.
    """
    root, parts, _host_path = _artifact_location(ledger, artifact_path)
    descriptor, info = _open_regular_beneath(root, parts)
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            yield handle, int(info.st_size)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def artifact_text(ledger: dict[str, Any], artifact_path: str, max_bytes: int = 500000) -> dict[str, Any]:
    max_bytes = max(0, int(max_bytes))
    root, parts, _host_path = _artifact_location(ledger, artifact_path)
    data, file_size = _read_regular_beneath(root, parts, max_bytes)
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    return {
        "ok": True,
        "path": artifact_path,
        "bytes": file_size,
        "truncated": truncated,
        "text": data.decode("utf-8", errors="replace"),
    }


def final_package(ledger: dict[str, Any], max_bytes: int = 20000) -> dict[str, Any]:
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    if _is_skitarii_result(result):
        status_payload = artifact_status(ledger)
        artifact_items: list[dict[str, Any]] = []
        for raw_item in status_payload.get("artifacts") or []:
            item = dict(raw_item) if isinstance(raw_item, dict) else {}
            if item.get("exists") and item.get("path"):
                preview = artifact_text(ledger, str(item["path"]), max_bytes=max_bytes)
                if preview.get("ok"):
                    item["preview"] = {
                        "bytes": preview.get("bytes", 0),
                        "truncated": bool(preview.get("truncated")),
                        "text": preview.get("text", ""),
                    }
            artifact_items.append(item)
        status = str(result.get("status") or "blocked")
        public_stage = dict(result.get("patch_stage")) if isinstance(result.get("patch_stage"), dict) else {}
        public_stage.pop("patch_file", None)
        return {
            "kind": "skitarii_bridge_result",
            "ok": bool(result.get("ok")),
            "phase": str(result.get("phase") or status),
            "status": status,
            "summary": str(result.get("summary") or ""),
            "artifacts": [str(path) for path in (result.get("artifacts") or [])],
            "artifact_status": artifact_items,
            "patch_stage": public_stage,
            "ready_to_apply": bool(result.get("ready_to_apply")),
            "next_action": result.get("next_action") if isinstance(result.get("next_action"), dict) else {},
        }
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
    manifest_artifact = next((str(path) for path in artifacts if str(path).endswith("/final_manifest.json")), "")
    if not manifest_artifact:
        return {"ok": False, "error": "final manifest is not recorded"}
    try:
        root, parts, _host_path = _artifact_location(ledger, manifest_artifact)
        raw, _size = _read_regular_beneath(root, parts, MAX_MANIFEST_BYTES)
        manifest = _decode_json_object_bounded(raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "path": manifest_artifact}
    files: list[dict[str, Any]] = []
    raw_files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            continue
        sandbox_path = str(raw_file.get("path") or "")
        if not sandbox_path:
            continue
        try:
            root, parts, _host_path = _artifact_location(ledger, sandbox_path)
            size = _regular_size_beneath(root, parts)
        except ValueError:
            continue
        item = {
            **raw_file,
            "path": sandbox_path,
            "exists": True,
            "bytes": size,
        }
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
        "summary": compact_manifest_summary(manifest),
        "deliverable": str(manifest.get("deliverable") or ""),
        "manifest": manifest,
        "files": files,
    }
