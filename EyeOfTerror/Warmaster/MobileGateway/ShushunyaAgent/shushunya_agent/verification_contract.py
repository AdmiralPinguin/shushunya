from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path
from typing import Any, Iterable


def cli_modules_from_task(task: str) -> list[str]:
    modules: list[str] = []
    invalid_modules = {
        "and",
        "or",
        "the",
        "a",
        "an",
        "cli",
        "json",
        "input",
        "jobs",
        "fallback",
        "pytest",
    }
    for match in re.finditer(r"\bpython3?\s+-m\s+([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)", task or ""):
        module = match.group(1)
        if module not in invalid_modules:
            modules.append(module)
    return list(dict.fromkeys(modules))


def cli_module_from_path(path: str, workspace: str = "") -> str:
    normalized = posixpath.normpath(path or "")
    workspace_norm = posixpath.normpath(workspace or "")
    if not normalized.endswith(".py"):
        return ""
    relative = normalized
    if workspace_norm and normalized.startswith(workspace_norm + "/"):
        relative = normalized[len(workspace_norm) + 1 :]
    parts = relative[:-3].split("/")
    if not parts:
        return ""
    if parts[-1] == "__main__":
        parts = parts[:-1]
    elif parts[-1] not in {"cli", "main"}:
        return ""
    if not parts or not all(re.match(r"^[A-Za-z_]\w*$", part) for part in parts):
        return ""
    return ".".join(parts)


def cli_modules_from_workspace(workspace: str, limit: int = 20) -> list[str]:
    root = Path(workspace or "")
    if not workspace or not root.exists() or not root.is_dir():
        return []
    modules: list[str] = []
    skipped_dirs = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", "tests"}
    visited_dirs = 0
    for current, dirs, files in os.walk(root):
        visited_dirs += 1
        if visited_dirs > 300:
            break
        dirs[:] = [name for name in dirs if name not in skipped_dirs and not name.startswith(".")]
        for filename in files:
            if filename not in {"cli.py", "main.py", "__main__.py"}:
                continue
            module = cli_module_from_path(str(Path(current) / filename), str(root))
            if module:
                modules.append(module)
                if len(modules) >= limit:
                    return list(dict.fromkeys(modules))
    return list(dict.fromkeys(modules))


def cli_modules_from_text_paths(text: str, workspace: str = "") -> list[str]:
    modules: list[str] = []
    for match in re.finditer(r"(/[^\s\"']+/(?:cli|main|__main__)\.py)\b", text or ""):
        module = cli_module_from_path(match.group(1), workspace)
        if module:
            modules.append(module)
    return list(dict.fromkeys(modules))


def _paths_from_listing_text(text: str, workspace: str = "") -> list[str]:
    paths: list[str] = []
    current_dir = ""
    workspace_norm = posixpath.normpath(workspace or "")
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("total "):
            continue
        if line.endswith(":"):
            section = line[:-1].strip()
            if section == ".":
                current_dir = ""
            elif section.startswith("./"):
                current_dir = posixpath.normpath(section[2:])
            elif workspace_norm and section.startswith(workspace_norm + "/"):
                current_dir = posixpath.relpath(posixpath.normpath(section), workspace_norm)
                if current_dir == ".":
                    current_dir = ""
            else:
                current_dir = posixpath.normpath(section)
            continue
        if line.startswith("./"):
            path = posixpath.normpath(line[2:])
        elif line.startswith("/"):
            path = posixpath.normpath(line)
        else:
            path = posixpath.normpath(posixpath.join(current_dir, line)) if current_dir else line
        if workspace_norm and not path.startswith("/"):
            path = posixpath.normpath(posixpath.join(workspace_norm, path))
        paths.append(path)
    return list(dict.fromkeys(paths))


def cli_modules_from_listing_text(text: str, workspace: str = "") -> list[str]:
    modules: list[str] = []
    for path in _paths_from_listing_text(text, workspace):
        module = cli_module_from_path(path, workspace)
        if module:
            modules.append(module)
    return list(dict.fromkeys(modules))


def action_invokes_cli_module(action_type: str, action: dict[str, Any], module: str) -> bool:
    cmd = str(action.get("cmd") or "")
    code = str(action.get("code") or "")
    text = f"{cmd}\n{code}"
    escaped = re.escape(module)
    patterns = (
        rf"\bpython3?\s+-m\s+{escaped}\b",
        rf"['\"]-m['\"]\s*,\s*['\"]{escaped}['\"]",
        rf"runpy\.run_module\(\s*['\"]{escaped}['\"]",
    )
    return any(re.search(pattern, text) for pattern in patterns)


CLI_INPUT_EXTENSIONS = {".csv", ".json", ".jsonl", ".txt", ".tsv", ".yaml", ".yml"}


def cli_input_path_from_listing_item(item: dict[str, Any]) -> str:
    if item.get("type") not in {None, "file"}:
        return ""
    path = str(item.get("path") or "")
    suffix = Path(path).suffix.lower()
    if suffix not in CLI_INPUT_EXTENSIONS:
        return ""
    normalized = posixpath.normpath(path)
    lowered_parts = {part.lower() for part in normalized.split("/")}
    if "tests" in lowered_parts or "__pycache__" in lowered_parts:
        return ""
    return normalized


def cli_input_paths_from_listing_text(text: str, workspace: str = "") -> list[str]:
    paths: list[str] = []
    for path in _paths_from_listing_text(text, workspace):
        detected = cli_input_path_from_listing_item({"path": path, "type": "file"})
        if detected:
            paths.append(detected)
    return list(dict.fromkeys(paths))


def cli_input_paths_from_text_paths(text: str, workspace: str = "") -> list[str]:
    paths = cli_input_paths_from_task(text, workspace)
    extension_pattern = "|".join(re.escape(ext.lstrip(".")) for ext in sorted(CLI_INPUT_EXTENSIONS))
    for match in re.finditer(rf"(/[^\s\"'<>]+\.({extension_pattern}))\b", text or "", flags=re.IGNORECASE):
        raw_path = match.group(1).rstrip(".,;:!?)]}»”")
        detected = cli_input_path_from_listing_item({"path": raw_path, "type": "file"})
        if detected:
            paths.append(detected)
    return list(dict.fromkeys(paths))


def action_uses_cli_input_path(action: dict[str, Any], input_paths: Iterable[str]) -> bool:
    cmd = str(action.get("cmd") or "")
    code = str(action.get("code") or "")
    text = f"{cmd}\n{code}"
    for path in input_paths:
        normalized = posixpath.normpath(str(path))
        basename = posixpath.basename(normalized)
        if normalized and normalized in text:
            return True
        if basename and re.search(r"(?<![\w.-])" + re.escape(basename) + r"(?![\w.-])", text):
            return True
    return False


def cli_input_paths_from_task(task: str, workspace: str = "") -> list[str]:
    paths: list[str] = []
    extension_pattern = "|".join(re.escape(ext.lstrip(".")) for ext in sorted(CLI_INPUT_EXTENSIONS))
    pattern = rf"(?<![\\\w./-])((?:[\w.-]+/)*[\w.-]+\.({extension_pattern}))(?![\w/-])"
    for match in re.finditer(pattern, task or "", flags=re.IGNORECASE):
        raw_path = match.group(1)
        normalized = posixpath.normpath(raw_path)
        lowered_parts = {part.lower() for part in normalized.split("/")}
        if "tests" in lowered_parts or "__pycache__" in lowered_parts:
            continue
        if workspace and not normalized.startswith("/"):
            normalized = posixpath.normpath(posixpath.join(workspace, normalized))
        paths.append(normalized)
    return list(dict.fromkeys(paths))


def cli_semantic_markers_from_task(task: str) -> list[str]:
    if "resume context" in (task or "").lower():
        return []
    markers: list[str] = []
    for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", task or ""):
        marker = match.group(0)
        lowered = marker.lower()
        if lowered in {"python", "pytest", "json", "cli", "datetime", "fallback", "true", "false"}:
            continue
        if "_" in marker or lowered in {"owners", "owner", "rejected", "scheduled", "reason", "summary", "plan", "count"}:
            markers.append(marker)
    return list(dict.fromkeys(markers))[:12]


def action_checks_cli_semantics(action: dict[str, Any], markers: Iterable[str]) -> bool:
    marker_list = list(dict.fromkeys(markers))
    if not marker_list:
        return True
    cmd = str(action.get("cmd") or "")
    code = str(action.get("code") or "")
    text = f"{cmd}\n{code}".lower()
    matched = sum(1 for marker in marker_list if marker.lower() in text)
    required = min(2, len(marker_list))
    return matched >= required


def task_requires_cli_output_semantics(task: str) -> bool:
    lowered = (task or "").lower()
    return any(
        marker in lowered
        for marker in (
            "cli должен вернуть",
            "cli должна вернуть",
            "cli должен содерж",
            "cli output",
            "cli stdout",
            "command output",
            "stdout должен",
        )
    )


def action_is_cli_verification(
    action_type: str,
    action: dict[str, Any],
    task: str = "",
    expected_modules: Iterable[str] | None = None,
    expected_input_paths: Iterable[str] | None = None,
) -> bool:
    if action_type not in {"shell", "python"}:
        return False
    required_modules = list(dict.fromkeys([*cli_modules_from_task(task), *(expected_modules or [])]))
    if required_modules and not any(action_invokes_cli_module(action_type, action, module) for module in required_modules):
        return False
    required_inputs = list(dict.fromkeys(expected_input_paths or []))
    if required_inputs and not action_uses_cli_input_path(action, required_inputs):
        return False
    has_explicit_cli_contract = bool(required_modules or required_inputs)
    semantic_markers = cli_semantic_markers_from_task(task)
    should_check_semantics = (not has_explicit_cli_contract) or task_requires_cli_output_semantics(task)
    if should_check_semantics and semantic_markers and not action_checks_cli_semantics(action, semantic_markers):
        return False
    cmd = str(action.get("cmd") or "").lower()
    code = str(action.get("code") or "").lower()
    text = f"{cmd}\n{code}"
    validation_markers = ("assert ", "json.load", "json.loads")
    entrypoint_markers = ("run_check", ".cli", "/cli", " cli", "main(", "runpy.run_module")
    if "pytest" in text:
        return "run_check" in text
    if action_type == "shell":
        return "run_check" in cmd or (
            ("python -m" in cmd or "python3 -m" in cmd)
            and any(marker in cmd for marker in validation_markers)
        )
    return "run_check" in code or (
        any(marker in code for marker in ("subprocess.run", "subprocess.check", "runpy.run_module"))
        and any(marker in code for marker in validation_markers)
        and any(marker in code for marker in entrypoint_markers)
    )
