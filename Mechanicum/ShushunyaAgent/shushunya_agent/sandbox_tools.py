from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import time
from typing import Any, Protocol

from .utils import truncate


class SandboxConfig(Protocol):
    sandbox_shell: str
    sandbox_mode: str
    sandbox_group: str
    sandbox_runner: str
    shell_timeout: int
    max_tool_output_chars: int
    sandbox_storage_limit_bytes: int
    shell_enabled: bool
    shell_approval_required: bool


def run_shell(config: SandboxConfig, cmd: str, timeout: int | None = None, approved: bool = False) -> dict[str, Any]:
    if not config.shell_enabled:
        return {"ok": False, "error": "shell tool is disabled by supervisor policy"}
    if config.shell_approval_required and not approved:
        return {"ok": False, "error": "shell action requires explicit approval", "approval_required": True}
    timeout = min(int(timeout or config.shell_timeout), 300)
    forbidden = ("sudo", "su ", "systemctl", "mount", "umount", "chroot", "nsenter", "docker", "podman", "ssh ")
    lowered = f" {cmd.lower()} "
    if any(token in lowered for token in forbidden):
        return {
            "ok": False,
            "error": "command rejected by supervisor policy",
            "forbidden_tokens": [token.strip() for token in forbidden if token in lowered],
        }

    return run_sandbox_argv(config, ["/usr/bin/bash", "-lc", cmd], timeout)


def sandbox_launcher_argv(config: SandboxConfig, inner_argv: list[str]) -> list[str]:
    if config.sandbox_mode in ("auto", "sg"):
        sandbox_command = " ".join([shlex.quote(config.sandbox_runner), *(shlex.quote(arg) for arg in inner_argv)])
        return ["sg", config.sandbox_group, "-c", sandbox_command]
    if config.sandbox_mode == "direct":
        return [config.sandbox_runner, *inner_argv]
    return [config.sandbox_shell, *inner_argv]


def run_sandbox_argv(
    config: SandboxConfig,
    inner_argv: list[str],
    timeout: int | None = None,
    max_output_chars: int | None = None,
) -> dict[str, Any]:
    timeout = min(int(timeout or config.shell_timeout), 300)
    output_limit = int(max_output_chars or config.max_tool_output_chars)
    argv = sandbox_launcher_argv(config, inner_argv)
    started = time.time()
    process = subprocess.Popen(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        return {
            "ok": False,
            "error": "command timed out",
            "timeout_sec": timeout,
            "killed_process_group": True,
            "stdout": truncate(stdout or "", output_limit),
            "stderr": truncate(stderr or "", output_limit),
        }
    return {
        "ok": process.returncode == 0,
        "returncode": process.returncode,
        "sandbox_mode": config.sandbox_mode,
        "argv": inner_argv,
        "duration_sec": round(time.time() - started, 3),
        "stdout": truncate(stdout, output_limit),
        "stderr": truncate(stderr, output_limit),
    }


FILE_TOOL_SCRIPT = r'''
import base64
import fnmatch
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ALLOWED_ROOTS = [
    Path("/work"),
    Path("/sandbox-tmp"),
    Path("/artifacts"),
    Path("/state"),
    Path("/logs"),
    Path("/models"),
    Path("/tools"),
    Path("/home/agent"),
]


def respond(payload):
    print(json.dumps(payload, ensure_ascii=False))


def decode(value):
    return base64.b64decode(value.encode("ascii")).decode("utf-8")


def safe_path(raw):
    if not raw:
        raw = "/work"
    path = Path(raw)
    if not path.is_absolute():
        path = Path("/work") / path
    resolved = path.resolve(strict=False)
    for root in ALLOWED_ROOTS:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            pass
    raise ValueError(f"path outside sandbox writable roots: {raw}")


def describe(path):
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "type": "dir" if path.is_dir() else "file" if path.is_file() else "other",
        "size": stat.st_size,
        "mode": oct(stat.st_mode & 0o777),
        "mtime": stat.st_mtime,
    }


def file_hash(path, max_bytes):
    digest = hashlib.sha256()
    total = 0
    truncated = False
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(min(1024 * 1024, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                digest.update(chunk[:len(chunk) - (total - max_bytes)])
                truncated = True
                break
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "hash_bytes": min(total, max_bytes), "hash_truncated": truncated}


def usage_bytes():
    total = 0
    seen = set()
    for root in ALLOWED_ROOTS:
        if not root.exists():
            continue
        for current, dirs, files in os.walk(root):
            for name in files:
                child = Path(current) / name
                try:
                    stat = child.stat()
                except OSError:
                    continue
                key = (stat.st_dev, stat.st_ino)
                if key in seen:
                    continue
                seen.add(key)
                total += stat.st_size
    return total


try:
    payload = json.loads(sys.argv[1])
    action = payload.get("action")
    path = safe_path(payload.get("path") or "/work")

    if action == "list_files":
        max_depth = max(0, min(int(payload.get("max_depth", 2)), 8))
        limit = max(1, min(int(payload.get("limit", 500)), 1000))
        offset = max(0, int(payload.get("offset", 0)))
        if not path.exists():
            respond({"ok": False, "error": "path does not exist", "path": str(path)})
        elif not path.is_dir():
            respond({"ok": False, "error": "path is not a directory", "path": str(path)})
        else:
            items = []
            root_depth = len(path.parts)
            for current, dirs, files in os.walk(path):
                current_path = Path(current)
                depth = len(current_path.parts) - root_depth
                if depth >= max_depth:
                    dirs[:] = []
                for name in sorted(dirs + files):
                    child = current_path / name
                    try:
                        items.append(describe(child))
                    except OSError as exc:
                        items.append({"path": str(child), "exists": False, "error": str(exc)})
            page = items[offset:offset + limit]
            next_offset = offset + len(page) if offset + len(page) < len(items) else None
            respond({"ok": True, "path": str(path), "items": page, "total_count": len(items), "offset": offset, "limit": limit, "next_offset": next_offset, "truncated": next_offset is not None})

    elif action == "read_file":
        max_bytes = max(1, min(int(payload.get("max_bytes", 20000)), 200000))
        offset = max(0, int(payload.get("offset", 0)))
        if not path.is_file():
            respond({"ok": False, "error": "path is not a file", "path": str(path)})
        else:
            with path.open("rb") as fh:
                fh.seek(offset)
                data = fh.read(max_bytes + 1)
            truncated = len(data) > max_bytes
            data = data[:max_bytes]
            is_binary = b"\x00" in data
            respond({
                "ok": True,
                "path": str(path),
                "size": path.stat().st_size,
                "offset": offset,
                "bytes_read": len(data),
                "next_offset": offset + len(data) if truncated else None,
                "truncated": truncated,
                "is_binary": is_binary,
                "encoding": "utf-8-replace",
                "content": data.decode("utf-8", errors="replace"),
            })

    elif action in ("write_file", "append_file"):
        content = decode(payload.get("content_b64", ""))
        storage_limit = int(payload.get("storage_limit_bytes", 0))
        existing_size = path.stat().st_size if path.exists() and path.is_file() else 0
        delta = len(content.encode("utf-8")) if action == "append_file" else len(content.encode("utf-8")) - existing_size
        if storage_limit > 0 and usage_bytes() + max(delta, 0) > storage_limit:
            respond({"ok": False, "error": "storage limit would be exceeded", "path": str(path), "storage_limit_bytes": storage_limit})
            raise SystemExit(0)
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if action == "append_file" else "w"
        with path.open(mode, encoding="utf-8") as fh:
            fh.write(content)
        respond({"ok": True, "path": str(path), "size": path.stat().st_size})

    elif action == "replace_in_file":
        old = decode(payload.get("old_b64", ""))
        new = decode(payload.get("new_b64", ""))
        count = int(payload.get("count", -1))
        max_file_bytes = max(1, min(int(payload.get("max_file_bytes", 5000000)), 20000000))
        if not path.is_file():
            respond({"ok": False, "error": "path is not a file", "path": str(path)})
        elif old == "":
            respond({"ok": False, "error": "old text must not be empty", "path": str(path)})
        elif path.stat().st_size > max_file_bytes:
            respond({"ok": False, "error": "file too large for replace_in_file", "path": str(path), "size": path.stat().st_size, "max_file_bytes": max_file_bytes})
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            occurrences = text.count(old)
            if occurrences == 0:
                respond({"ok": False, "error": "old text not found", "path": str(path)})
            else:
                replaced = occurrences if count < 0 else min(count, occurrences)
                path.write_text(text.replace(old, new, count), encoding="utf-8")
                respond({"ok": True, "path": str(path), "replaced": replaced, "size": path.stat().st_size})

    elif action == "mkdir":
        path.mkdir(parents=True, exist_ok=True)
        respond({"ok": True, **describe(path)})

    elif action == "remove_file":
        if not path.exists():
            respond({"ok": True, "path": str(path), "removed": False})
        elif path.is_file() or path.is_symlink():
            path.unlink()
            respond({"ok": True, "path": str(path), "removed": True})
        elif path.is_dir() and payload.get("recursive") is True:
            shutil.rmtree(path)
            respond({"ok": True, "path": str(path), "removed": True})
        else:
            respond({"ok": False, "error": "refusing to remove directory without recursive=true", "path": str(path)})

    elif action == "file_info":
        if path.exists():
            info = {"ok": True, **describe(path)}
            if path.is_file() and payload.get("sha256") is True:
                max_hash_bytes = max(1, min(int(payload.get("max_hash_bytes", 50000000)), 200000000))
                info.update(file_hash(path, max_hash_bytes))
            respond(info)
        else:
            respond({"ok": True, "path": str(path), "exists": False})

    elif action == "find_files":
        pattern = str(payload.get("pattern") or "*")
        max_depth = max(0, min(int(payload.get("max_depth", 4)), 12))
        limit = max(1, min(int(payload.get("limit", 500)), 1000))
        offset = max(0, int(payload.get("offset", 0)))
        if not path.exists():
            respond({"ok": False, "error": "path does not exist", "path": str(path)})
        elif path.is_file():
            match = fnmatch.fnmatch(path.name, pattern)
            items = [describe(path)] if match else []
            page = items[offset:offset + limit]
            next_offset = offset + len(page) if offset + len(page) < len(items) else None
            respond({"ok": True, "path": str(path), "pattern": pattern, "items": page, "total_count": len(items), "offset": offset, "limit": limit, "next_offset": next_offset, "truncated": next_offset is not None})
        elif not path.is_dir():
            respond({"ok": False, "error": "path is not searchable", "path": str(path)})
        else:
            items = []
            root_depth = len(path.parts)
            for current, dirs, files in os.walk(path):
                current_path = Path(current)
                depth = len(current_path.parts) - root_depth
                if depth >= max_depth:
                    dirs[:] = []
                for name in sorted(dirs + files):
                    child = current_path / name
                    if fnmatch.fnmatch(name, pattern):
                        try:
                            items.append(describe(child))
                        except OSError as exc:
                            items.append({"path": str(child), "exists": False, "error": str(exc)})
            page = items[offset:offset + limit]
            next_offset = offset + len(page) if offset + len(page) < len(items) else None
            respond({"ok": True, "path": str(path), "pattern": pattern, "items": page, "total_count": len(items), "offset": offset, "limit": limit, "next_offset": next_offset, "truncated": next_offset is not None})

    elif action == "search_text":
        query = str(payload.get("query") or "")
        case_sensitive = bool(payload.get("case_sensitive", False))
        max_matches = max(1, min(int(payload.get("max_matches", 50)), 500))
        max_bytes_per_file = max(1024, min(int(payload.get("max_bytes_per_file", 200000)), 1000000))
        if query == "":
            respond({"ok": False, "error": "query must not be empty", "path": str(path)})
            raise SystemExit(0)
        if not path.exists():
            respond({"ok": False, "error": "path does not exist", "path": str(path)})
            raise SystemExit(0)
        if not path.is_file() and not path.is_dir():
            respond({"ok": False, "error": "path is not searchable", "path": str(path)})
            raise SystemExit(0)
        files = [path] if path.is_file() else []
        if path.is_dir():
            for current, dirs, names in os.walk(path):
                for name in sorted(names):
                    files.append(Path(current) / name)
                    if len(files) > 2000:
                        break
                if len(files) > 2000:
                    break
        needle = query if case_sensitive else query.lower()
        matches = []
        scanned_files = 0
        truncated_files = 0
        for file_path in files:
            try:
                size = file_path.stat().st_size
                with file_path.open("rb") as fh:
                    data = fh.read(max_bytes_per_file)
            except OSError:
                continue
            scanned_files += 1
            if size > len(data):
                truncated_files += 1
            text = data.decode("utf-8", errors="ignore")
            haystack = text if case_sensitive else text.lower()
            if needle not in haystack:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                checked = line if case_sensitive else line.lower()
                if needle in checked:
                    matches.append({"path": str(file_path), "line": line_no, "text": line[:500]})
                    if len(matches) >= max_matches:
                        respond({"ok": True, "path": str(path), "query": query, "matches": matches, "scanned_files": scanned_files, "truncated_files": truncated_files, "truncated": True})
                        raise SystemExit(0)
        respond({"ok": True, "path": str(path), "query": query, "matches": matches, "scanned_files": scanned_files, "truncated_files": truncated_files, "truncated": False})

    else:
        respond({"ok": False, "error": f"unsupported file tool action: {action}"})
except Exception as exc:
    respond({"ok": False, "error": str(exc)})
'''


FILE_ACTIONS = {
    "list_files",
    "read_file",
    "write_file",
    "append_file",
    "replace_in_file",
    "mkdir",
    "remove_file",
    "file_info",
    "find_files",
    "search_text",
}


def file_tool(config: SandboxConfig, action: dict[str, Any]) -> dict[str, Any]:
    payload = dict(action)
    payload["storage_limit_bytes"] = config.sandbox_storage_limit_bytes
    content = payload.pop("content", None)
    if content is not None:
        payload["content_b64"] = base64.b64encode(str(content).encode("utf-8")).decode("ascii")
    old = payload.pop("old", None)
    if old is not None:
        payload["old_b64"] = base64.b64encode(str(old).encode("utf-8")).decode("ascii")
    new = payload.pop("new", None)
    if new is not None:
        payload["new_b64"] = base64.b64encode(str(new).encode("utf-8")).decode("ascii")
    action_type = str(action.get("action", "")).strip().lower()
    output_limit = config.max_tool_output_chars
    if action_type == "read_file":
        output_limit = max(output_limit, min(int(action.get("max_bytes", 20000) or 20000), 200000) + 5000)
    elif action_type == "list_files" or action_type == "find_files":
        output_limit = max(output_limit, 250000)
    elif action_type == "search_text":
        max_matches = max(1, min(int(action.get("max_matches", 50) or 50), 500))
        output_limit = max(output_limit, min(350000, max_matches * 800 + 5000))
    result = run_sandbox_argv(
        config,
        ["/usr/bin/python3", "-c", FILE_TOOL_SCRIPT, json.dumps(payload, ensure_ascii=False)],
        timeout=30,
        max_output_chars=output_limit,
    )
    if not result.get("ok"):
        return result
    try:
        return json.loads(result.get("stdout", "{}"))
    except json.JSONDecodeError:
        return {"ok": False, "error": "file tool returned invalid JSON", "raw": result}


def python_tool(config: SandboxConfig, action: dict[str, Any]) -> dict[str, Any]:
    code = str(action.get("code", ""))
    if len(code) > 50000:
        return {"ok": False, "error": "python code is too large"}
    timeout = min(int(action.get("timeout") or config.shell_timeout), 300)
    return run_sandbox_argv(config, ["/usr/bin/python3", "-c", code], timeout=timeout)


def sandbox_status(config: SandboxConfig) -> dict[str, Any]:
    script = r'''
import json
import os
import shutil
from pathlib import Path

paths = ["/work", "/sandbox-tmp", "/artifacts", "/state", "/logs", "/models", "/tools", "/home/agent", "/media", "/root"]
usage = shutil.disk_usage("/work")
payload = {
    "cwd": os.getcwd(),
    "uid": os.getuid(),
    "gid": os.getgid(),
    "paths": {path: Path(path).exists() for path in paths},
    "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
}
print(json.dumps(payload, ensure_ascii=False))
'''
    result = run_sandbox_argv(config, ["/usr/bin/python3", "-c", script], timeout=30)
    if not result.get("ok"):
        return result
    try:
        payload = json.loads(result.get("stdout", "{}"))
    except json.JSONDecodeError:
        return {"ok": False, "error": "sandbox status returned invalid JSON", "raw": result}
    payload["ok"] = bool(payload.get("ok", True))
    payload["policy"] = {
        "storage_limit_bytes": config.sandbox_storage_limit_bytes,
        "storage_limit_human": "500G",
        "quota_mode": "soft"
    }
    return payload
