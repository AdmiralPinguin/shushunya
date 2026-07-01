#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any


def build_execution_preflight(brief: dict[str, Any]) -> dict[str, Any]:
    repo_path = Path(str(brief.get("repo_path") or ""))
    evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    verification = brief.get("required_verification") if isinstance(brief.get("required_verification"), dict) else {}
    candidate_files = evidence.get("candidate_files") if isinstance(evidence.get("candidate_files"), list) else []
    test_files = evidence.get("test_files") if isinstance(evidence.get("test_files"), list) else []
    targeted_commands = verification.get("targeted_commands") if isinstance(verification.get("targeted_commands"), list) else []
    suggested_commands = brief.get("suggested_verification_commands")
    if not isinstance(suggested_commands, list):
        suggested_commands = []
    existing_candidate_files = [
        path for path in candidate_files if isinstance(path, str) and (repo_path / path).is_file()
    ]
    missing_candidate_files = [
        path for path in candidate_files if isinstance(path, str) and not (repo_path / path).is_file()
    ]
    blockers: list[str] = []
    if not str(brief.get("repo_path") or ""):
        blockers.append("repo_path is missing")
    elif not repo_path.exists():
        blockers.append("repo_path does not exist")
    elif not repo_path.is_dir():
        blockers.append("repo_path is not a directory")
    if not brief.get("allowed_scope"):
        blockers.append("allowed_scope is missing")
    if not candidate_files:
        blockers.append("repository survey has no candidate files")
    elif missing_candidate_files:
        blockers.append("repository survey candidate files are missing")
    if not targeted_commands and not suggested_commands:
        blockers.append("verification strategy has no executable or suggested commands")
    return {
        "kind": "code_brigade_execution_preflight",
        "repo_path": str(repo_path),
        "repo_exists": repo_path.exists(),
        "repo_is_dir": repo_path.is_dir(),
        "allowed_scope_count": len(brief.get("allowed_scope", [])) if isinstance(brief.get("allowed_scope"), list) else 0,
        "candidate_file_count": len(candidate_files),
        "existing_candidate_file_count": len(existing_candidate_files),
        "missing_candidate_files": missing_candidate_files[:20],
        "test_file_count": len(test_files),
        "targeted_command_count": len(targeted_commands),
        "suggested_command_count": len(suggested_commands),
        "blockers": blockers,
        "ok": not blockers,
    }
