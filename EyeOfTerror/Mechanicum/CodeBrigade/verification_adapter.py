#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from execution_contract import CONTRACT_VERSION


ALLOWED_PREFIXES = [
    ["python", "-m", "py_compile"],
    ["python3", "-m", "py_compile"],
    ["python", "-m", "pytest"],
    ["python3", "-m", "pytest"],
    ["python", "-m", "unittest"],
    ["python3", "-m", "unittest"],
    ["pytest"],
    ["git", "diff", "--check"],
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


def is_unsafe_path_value(value: str) -> bool:
    return (
        value.startswith("/")
        or value == "~"
        or value.startswith("~/")
        or value == ".."
        or value.startswith("../")
        or "/../" in value
    )


def command_has_unsafe_path_tokens(tokens: list[str]) -> bool:
    for token in tokens:
        if "=" in token:
            _, value = token.split("=", 1)
            if is_unsafe_path_value(value):
                return True
        if token.startswith("-"):
            continue
        if is_unsafe_path_value(token):
            return True
    return False


def normalize_tokens(tokens: list[str]) -> list[str]:
    if tokens and tokens[0] in {"python", "python3"}:
        return [sys.executable, *tokens[1:]]
    return tokens


def is_pytest_command(tokens: list[str]) -> bool:
    return bool(tokens and (tokens[0] == "pytest" or (len(tokens) >= 3 and tokens[1:3] == ["-m", "pytest"])))


def command_kind(command: str) -> str:
    lowered = command.lower()
    if "py_compile" in lowered or "git diff --check" in lowered:
        return "syntax_or_diff"
    if "pytest" in lowered or "unittest" in lowered or "test" in lowered:
        return "behavior"
    return "focused"


def requirement_keywords(requirement: str) -> set[str]:
    words = {
        word
        for word in re.findall(r"[a-zA-Z0-9_]+", requirement.lower())
        if len(word) >= 4 and word not in {"must", "have", "with", "this", "that", "from", "only", "true"}
    }
    synonyms = {
        "api": {"api", "schema", "request", "response", "caller", "compatibility"},
        "compatibility": {"compatibility", "legacy", "migration", "mixed", "shape", "round"},
        "security": {"security", "boundary", "auth", "token", "path", "input", "rejected"},
        "runtime": {"runtime", "config", "startup", "environment"},
        "concurrency": {"concurrency", "parallel", "retry", "cache", "state", "race"},
        "test": {"test", "pytest", "unittest", "behavior", "oracle"},
        "behavior": {"behavior", "visible", "request", "contract"},
    }
    expanded = set(words)
    for word in list(words):
        expanded.update(synonyms.get(word, set()))
    return expanded


def command_matches_requirement(command: str, requirement: str) -> bool:
    command_words = set(re.findall(r"[a-zA-Z0-9_]+", command.lower()))
    keywords = requirement_keywords(requirement)
    return bool(command_words & keywords)


def build_verification_contract_trace(results: list[dict[str, Any]], acceptance_requirements: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for requirement in acceptance_requirements:
        matched = [
            result
            for result in results
            if isinstance(result, dict) and command_matches_requirement(str(result.get("command") or ""), requirement)
        ]
        if not matched:
            matched = [result for result in results if isinstance(result, dict) and command_kind(str(result.get("command") or "")) == "behavior"]
        if not matched:
            matched = [result for result in results if isinstance(result, dict) and result.get("status") in {"blocked", "failed", "skipped"}]
        if not matched:
            matched = [result for result in results if isinstance(result, dict) and command_kind(str(result.get("command") or "")) == "syntax_or_diff" and result.get("status") == "passed"]
        statuses = [str(result.get("status") or "") for result in matched]
        kinds = [command_kind(str(result.get("command") or "")) for result in matched]
        if not matched:
            status = "missing"
        elif any(item == "blocked" for item in statuses):
            status = "blocked"
        elif any(item == "failed" for item in statuses):
            status = "failed"
        elif all(item == "skipped" for item in statuses):
            status = "skipped"
        elif all(item == "planned" for item in statuses):
            status = "planned_only"
        elif any(item == "passed" and kind == "behavior" for item, kind in zip(statuses, kinds)):
            status = "proven"
        elif any(item == "passed" for item in statuses):
            status = "syntax_only"
        else:
            status = "missing"
        rows.append(
            {
                "requirement": requirement,
                "status": status,
                "matched_commands": [str(result.get("command") or "") for result in matched],
                "command_kinds": kinds,
            }
        )
    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    blocking_statuses = {"missing", "blocked", "failed", "skipped", "planned_only", "syntax_only"}
    return {
        "kind": "code_brigade_verification_contract_trace",
        "contract_version": CONTRACT_VERSION,
        "requirement_count": len(rows),
        "status": "proven" if rows and all(row["status"] == "proven" for row in rows) else "incomplete",
        "status_counts": status_counts,
        "focused_evidence_count": sum(1 for result in results if command_kind(str(result.get("command") or "")) == "focused"),
        "behavior_evidence_count": sum(1 for result in results if command_kind(str(result.get("command") or "")) == "behavior"),
        "broad_evidence_count": sum(1 for result in results if "pytest" in str(result.get("command") or "").lower() or "unittest" in str(result.get("command") or "").lower()),
        "blocking_requirement_count": sum(1 for row in rows if row["status"] in blocking_statuses),
        "rows": rows,
    }


def verification_diagnostics(stdout: str, stderr: str, repo: Path) -> dict[str, Any]:
    combined = f"{stdout}\n{stderr}"
    traceback_files: list[str] = []
    for match in re.finditer(r'File "([^"]+)", line (\d+)', combined):
        raw_path = Path(match.group(1))
        try:
            rel_path = raw_path.resolve().relative_to(repo.resolve())
        except ValueError:
            continue
        value = f"{rel_path}:{match.group(2)}"
        if value not in traceback_files:
            traceback_files.append(value)
    missing_imports = sorted(set(re.findall(r"No module named ['\"]([^'\"]+)['\"]", combined)))
    return {
        "has_traceback": "Traceback (most recent call last)" in combined,
        "traceback_files": traceback_files[:20],
        "missing_imports": missing_imports[:20],
        "has_assertion_failure": "AssertionError" in combined or "FAILED" in combined or "FAIL:" in combined,
        "has_syntax_error": "SyntaxError" in combined,
        "has_no_tests_ran": "NO TESTS RAN" in combined or "collected 0 items" in combined,
    }


def run_verification_commands(commands: list[str], repo_path: str, execute: bool = False, timeout_sec: int = 30, acceptance_requirements: list[str] | None = None) -> dict[str, Any]:
    repo = Path(repo_path)
    results: list[dict[str, Any]] = []
    blockers: list[str] = []
    if not repo.exists() or not repo.is_dir():
        return {
            "kind": "code_brigade_verification_execution",
            "contract_version": CONTRACT_VERSION,
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
            stderr = "not allowlisted"
            results.append({"command": command, "status": "blocked", "returncode": None, "stdout": "", "stderr": stderr, "diagnostics": verification_diagnostics("", stderr, repo)})
            continue
        if command_has_unsafe_path_tokens(tokens):
            blockers.append(f"command contains unsafe path token: {command}")
            stderr = "unsafe path token"
            results.append({"command": command, "status": "blocked", "returncode": None, "stdout": "", "stderr": stderr, "diagnostics": verification_diagnostics("", stderr, repo)})
            continue
        if not execute:
            results.append({"command": command, "status": "planned", "returncode": None, "stdout": "", "stderr": "", "diagnostics": verification_diagnostics("", "", repo)})
            continue
        if tokens == ["git", "diff", "--check"] and not (repo / ".git").exists():
            stderr = "not a git repository"
            results.append({"command": command, "status": "skipped", "returncode": None, "stdout": "", "stderr": stderr, "diagnostics": verification_diagnostics("", stderr, repo)})
            continue
        try:
            completed = subprocess.run(normalize_tokens(tokens), cwd=repo, text=True, capture_output=True, timeout=timeout_sec, check=False)
        except FileNotFoundError as exc:
            stderr = str(exc)
            results.append({"command": command, "status": "failed", "returncode": None, "stdout": "", "stderr": stderr, "diagnostics": verification_diagnostics("", stderr, repo)})
            continue
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = "verification command timed out"
            results.append({"command": command, "status": "failed", "returncode": None, "stdout": stdout, "stderr": stderr, "diagnostics": verification_diagnostics(stdout, stderr, repo)})
            continue
        if is_pytest_command(tokens) and completed.returncode == 1 and "No module named pytest" in completed.stderr:
            stdout = completed.stdout[-4000:]
            stderr = "pytest unavailable"
            results.append(
                {
                    "command": command,
                    "status": "skipped",
                    "returncode": completed.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "diagnostics": verification_diagnostics(stdout, stderr, repo),
                }
            )
            continue
        stdout = completed.stdout[-4000:]
        stderr = completed.stderr[-4000:]
        results.append(
            {
                "command": command,
                "status": "passed" if completed.returncode == 0 else "failed",
                "returncode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "diagnostics": verification_diagnostics(stdout, stderr, repo),
            }
        )
    if blockers:
        status = "blocked"
    elif not execute:
        status = "planned"
    else:
        status = "passed" if all(item["status"] in {"passed", "skipped"} for item in results) else "failed"
    return {
        "kind": "code_brigade_verification_execution",
        "contract_version": CONTRACT_VERSION,
        "status": status,
        "execute": execute,
        "repo_path": str(repo),
        "results": results,
        "blockers": blockers,
        "contract_trace": build_verification_contract_trace(results, acceptance_requirements or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run allowlisted CodeBrigade verification commands.")
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--command", action="append", default=[])
    parser.add_argument("--acceptance-requirement", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    report = run_verification_commands(args.command, args.repo_path, execute=args.execute, acceptance_requirements=args.acceptance_requirement)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"planned", "passed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
