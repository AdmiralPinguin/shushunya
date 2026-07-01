#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


ALLOWED_PREFIXES = [
    ["python", "-m", "py_compile"],
    ["python3", "-m", "py_compile"],
    ["python", "-m", "pytest"],
    ["python3", "-m", "pytest"],
    ["pytest"],
]


def split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def command_allowed(tokens: list[str]) -> bool:
    if not tokens:
        return False
    return any(tokens[: len(prefix)] == prefix for prefix in ALLOWED_PREFIXES)


def normalize_tokens(tokens: list[str]) -> list[str]:
    if tokens and tokens[0] in {"python", "python3"}:
        return [sys.executable, *tokens[1:]]
    return tokens


def run_verification_commands(commands: list[str], repo_path: str, execute: bool = False, timeout_sec: int = 30) -> dict[str, Any]:
    repo = Path(repo_path)
    results: list[dict[str, Any]] = []
    blockers: list[str] = []
    if not repo.exists() or not repo.is_dir():
        return {
            "kind": "code_brigade_verification_execution",
            "status": "blocked",
            "execute": execute,
            "repo_path": str(repo),
            "results": [],
            "blockers": ["repo_path is missing or not a directory"],
        }
    for command in commands:
        tokens = split_command(command)
        if not command_allowed(tokens):
            blockers.append(f"command is not allowlisted: {command}")
            results.append({"command": command, "status": "blocked", "returncode": None, "stdout": "", "stderr": "not allowlisted"})
            continue
        if not execute:
            results.append({"command": command, "status": "planned", "returncode": None, "stdout": "", "stderr": ""})
            continue
        try:
            completed = subprocess.run(normalize_tokens(tokens), cwd=repo, text=True, capture_output=True, timeout=timeout_sec, check=False)
        except FileNotFoundError as exc:
            results.append({"command": command, "status": "failed", "returncode": None, "stdout": "", "stderr": str(exc)})
            continue
        except subprocess.TimeoutExpired as exc:
            results.append({"command": command, "status": "failed", "returncode": None, "stdout": exc.stdout or "", "stderr": "verification command timed out"})
            continue
        results.append(
            {
                "command": command,
                "status": "passed" if completed.returncode == 0 else "failed",
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
        )
    status = "blocked" if blockers else ("passed" if all(item["status"] in {"planned", "passed"} for item in results) else "failed")
    return {
        "kind": "code_brigade_verification_execution",
        "status": status,
        "execute": execute,
        "repo_path": str(repo),
        "results": results,
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run allowlisted CodeBrigade verification commands.")
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--command", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    report = run_verification_commands(args.command, args.repo_path, execute=args.execute)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"planned", "passed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
