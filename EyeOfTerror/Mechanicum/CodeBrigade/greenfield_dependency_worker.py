#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


LOCKFILE_NAMES = {"requirements.lock", "uv.lock", "poetry.lock", "Pipfile.lock", "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock"}
PYTHON_MANIFESTS = {"pyproject.toml", "requirements.txt", "setup.cfg", "setup.py"}
NODE_MANIFESTS = {"package.json"}
ANDROID_MANIFESTS = {"build.gradle", "settings.gradle", "gradle.properties"}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lockfile_snapshot(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or path.name not in LOCKFILE_NAMES:
            continue
        try:
            rel_path = path.relative_to(repo).as_posix()
        except ValueError:
            continue
        rows.append({"path": rel_path, "size_bytes": path.stat().st_size, "sha256": file_sha256(path)})
    return rows


def package_manager_candidates(package_manager: str) -> list[str]:
    if package_manager == "pip":
        candidates = ["python", "python3"]
        if sys.executable:
            candidates.append(sys.executable)
        return list(dict.fromkeys(candidates))
    if package_manager == "npm":
        return ["npm"]
    return []


def package_manager_binary(package_manager: str) -> str:
    candidates = package_manager_candidates(package_manager)
    for binary in candidates:
        if shutil.which(binary):
            return binary
    return candidates[0] if candidates else ""


def dependency_manager_status(package_manager: str) -> dict[str, Any]:
    if package_manager == "none":
        return {"package_manager": package_manager, "required": False, "binary": "", "available": True, "path": "", "candidates": []}
    candidates = package_manager_candidates(package_manager)
    for binary in candidates:
        path = shutil.which(binary)
        if path:
            return {"package_manager": package_manager, "required": True, "binary": binary, "available": True, "path": path, "candidates": candidates}
    binary = candidates[0] if candidates else ""
    return {"package_manager": package_manager, "required": True, "binary": binary, "available": False, "path": "", "candidates": candidates}


def manifest_ecosystem(rel_path: str) -> str:
    name = Path(rel_path).name
    if name in PYTHON_MANIFESTS:
        return "python"
    if name in NODE_MANIFESTS:
        return "node"
    if name in ANDROID_MANIFESTS:
        return "android"
    return "unknown"


def command_stays_inside_workspace(repo: Path, command: str, allowed_absolute_commands: set[str] | None = None) -> bool:
    allowed_absolute_commands = allowed_absolute_commands or set()
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    for index, token in enumerate(tokens):
        if token.startswith("../") or token == ".." or "/../" in token:
            return False
        if token.startswith("/") and not (index == 0 and token in allowed_absolute_commands):
            return False
    try:
        repo.resolve()
    except OSError:
        return False
    return True


def run_dependency_worker(repo: Path, project_brief: dict[str, Any]) -> dict[str, Any]:
    dependency_plan = project_brief.get("dependency_plan") if isinstance(project_brief.get("dependency_plan"), dict) else {}
    package_manager = str(dependency_plan.get("package_manager") or "none")
    manifest_files = [str(path) for path in dependency_plan.get("manifest_files", []) if isinstance(path, str)]
    install_commands = [str(command) for command in dependency_plan.get("install_commands", []) if isinstance(command, str) and command.strip()]
    manager_status = dependency_manager_status(package_manager)
    lockfiles_before = lockfile_snapshot(repo)
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    for rel_path in manifest_files:
        path = repo / rel_path
        exists = path.exists() and path.is_file()
        rows.append(
            {
                "path": rel_path,
                "exists": exists,
                "status": "present" if exists else "missing",
                "ecosystem": manifest_ecosystem(rel_path),
                "size_bytes": path.stat().st_size if exists else 0,
                "sha256": file_sha256(path) if exists else "",
            }
        )
        if not exists:
            blockers.append(f"dependency manifest is missing: {rel_path}")
    if manager_status["required"] and not manager_status["available"] and install_commands:
        blockers.append(f"package manager is unavailable: {package_manager}")
    elif manager_status["required"] and not manager_status["available"]:
        warnings.append(f"package manager is unavailable until install/run is requested: {package_manager}")
    command_results: list[dict[str, Any]] = []
    allowed_prefixes = [
        ["python", "-m", "pip", "install"],
        ["python3", "-m", "pip", "install"],
        [sys.executable, "-m", "pip", "install"],
        ["npm", "install"],
    ]
    for command in install_commands:
        tokens = shlex.split(command)
        if not command_stays_inside_workspace(repo, command, {sys.executable}):
            blockers.append(f"dependency install command uses path outside workspace: {command}")
            command_results.append({"command": command, "status": "blocked", "returncode": None, "stdout": "", "stderr": "path outside workspace"})
            continue
        if not any(tokens[: len(prefix)] == prefix for prefix in allowed_prefixes):
            blockers.append(f"dependency install command is not allowlisted: {command}")
            command_results.append({"command": command, "status": "blocked", "returncode": None, "stdout": "", "stderr": "not allowlisted"})
            continue
        try:
            completed = subprocess.run(tokens, cwd=repo, text=True, capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            blockers.append(f"dependency install command failed to execute: {command}")
            command_results.append({"command": command, "status": "failed", "returncode": None, "stdout": "", "stderr": str(exc)})
            continue
        status = "passed" if completed.returncode == 0 else "failed"
        if status == "failed":
            blockers.append(f"dependency install command failed: {command}")
        command_results.append({"command": command, "status": status, "returncode": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]})
    lockfiles_after = lockfile_snapshot(repo)
    new_lockfiles = [row for row in lockfiles_after if row["path"] not in {before["path"] for before in lockfiles_before}]
    if package_manager != "none" and not install_commands:
        warnings.append("package manager stack recorded without explicit install_commands; dependencies were not installed")
    if blockers:
        status = "blocked"
    elif install_commands:
        status = "installed"
    elif package_manager == "none":
        status = "not_required"
    else:
        status = "manifest_recorded"
    manifest_status = "complete" if rows and all(row["exists"] for row in rows) else "not_required" if not rows else "blocked"
    install_policy_evidence = {
        "explicit_install_requested": bool(install_commands),
        "allowlisted_command_count": sum(1 for row in command_results if row.get("status") in {"passed", "failed"}),
        "blocked_command_count": sum(1 for row in command_results if row.get("status") == "blocked"),
        "execution_policy": "only explicit allowlisted install commands inside greenfield workspace",
    }
    return {
        "kind": "code_brigade_greenfield_dependency_report",
        "contract_version": "eye-mechanicum.v1",
        "status": status,
        "package_manager": package_manager,
        "manager_status": manager_status,
        "manifest_files": rows,
        "manifest_status": manifest_status,
        "manifest_count": len(rows),
        "install_commands": install_commands,
        "command_results": command_results,
        "install_policy_evidence": install_policy_evidence,
        "lockfile_policy": str(dependency_plan.get("lockfile_policy") or ""),
        "dependency_strategy": dependency_plan.get("dependency_strategy", {}),
        "lockfiles_before": lockfiles_before,
        "lockfiles_after": lockfiles_after,
        "new_lockfiles": new_lockfiles,
        "lockfile_status": "new_lockfiles_recorded" if new_lockfiles else "unchanged",
        "blockers": blockers,
        "warnings": warnings,
    }
