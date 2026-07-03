#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any


LOCKFILE_NAMES = {"requirements.lock", "uv.lock", "poetry.lock", "Pipfile.lock", "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock"}


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


def package_manager_binary(package_manager: str) -> str:
    if package_manager == "pip":
        return "python"
    if package_manager == "npm":
        return "npm"
    return ""


def dependency_manager_status(package_manager: str) -> dict[str, Any]:
    binary = package_manager_binary(package_manager)
    if package_manager == "none":
        return {"package_manager": package_manager, "required": False, "binary": "", "available": True, "path": ""}
    path = shutil.which(binary) if binary else None
    return {"package_manager": package_manager, "required": True, "binary": binary, "available": bool(path), "path": path or ""}


def command_stays_inside_workspace(repo: Path, command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    for token in tokens:
        if token.startswith("../") or token == ".." or "/../" in token:
            return False
        if token.startswith("/"):
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
        rows.append({"path": rel_path, "exists": path.exists() and path.is_file(), "size_bytes": path.stat().st_size if path.exists() and path.is_file() else 0})
        if not path.exists() or not path.is_file():
            blockers.append(f"dependency manifest is missing: {rel_path}")
    if manager_status["required"] and not manager_status["available"] and install_commands:
        blockers.append(f"package manager is unavailable: {package_manager}")
    elif manager_status["required"] and not manager_status["available"]:
        warnings.append(f"package manager is unavailable until install/run is requested: {package_manager}")
    command_results: list[dict[str, Any]] = []
    allowed_prefixes = [
        ["python", "-m", "pip", "install"],
        ["python3", "-m", "pip", "install"],
        ["npm", "install"],
    ]
    for command in install_commands:
        tokens = shlex.split(command)
        if not command_stays_inside_workspace(repo, command):
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
    return {
        "kind": "code_brigade_greenfield_dependency_report",
        "contract_version": "eye-mechanicum.v1",
        "status": status,
        "package_manager": package_manager,
        "manager_status": manager_status,
        "manifest_files": rows,
        "install_commands": install_commands,
        "command_results": command_results,
        "lockfile_policy": str(dependency_plan.get("lockfile_policy") or ""),
        "dependency_strategy": dependency_plan.get("dependency_strategy", {}),
        "lockfiles_before": lockfiles_before,
        "lockfiles_after": lockfiles_after,
        "new_lockfiles": new_lockfiles,
        "blockers": blockers,
        "warnings": warnings,
    }
