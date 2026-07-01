#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROLE_ORDER = [
    "TaskTriage",
    "RepoSurveyor",
    "DesignStrategos",
    "VerificationArchitect",
    "RiskScribe",
]


def task_text(payload: dict[str, Any]) -> str:
    return str(payload.get("task") or payload.get("goal") or payload.get("message") or "").strip()


def normalize_repo_path(payload: dict[str, Any]) -> str:
    value = str(payload.get("repo_path") or payload.get("target_repo") or "").strip()
    if not value:
        match = re.search(r"CERAXIA_TARGET_REPO:\s*(.+)", task_text(payload))
        value = match.group(1).strip() if match else ""
    return value


def classify_task(task: str) -> dict[str, Any]:
    lowered = task.lower()
    patterns = {
        "bugfix": ["bug", "fix", "почини", "исправ", "ошиб", "traceback", "assert"],
        "feature": ["feature", "implement", "add ", "добав", "созда", "реализ"],
        "refactor": ["refactor", "рефактор", "architecture", "архитект"],
        "migration": ["migration", "migrate", "миграц", "legacy", "compat"],
        "security": ["security", "auth", "token", "permission", "path traversal", "безопас"],
        "config_runtime": ["config", "env", "runtime", "timeout", "порт", "настрой"],
        "api_compatibility": ["api", "schema", "endpoint", "response", "request", "contract"],
        "test_repair": ["pytest", "unittest", "test_", "тест"],
    }
    kinds = [name for name, needles in patterns.items() if any(needle in lowered for needle in needles)]
    if not kinds:
        kinds = ["general_code_change"]
    risk_score = 1
    for kind in kinds:
        if kind == "security":
            risk_score += 3
        elif kind in {"migration", "api_compatibility", "refactor"}:
            risk_score += 2
        elif kind in {"config_runtime", "test_repair"}:
            risk_score += 1
    if "multi" in lowered or "несколько" in lowered or len(re.findall(r"`[^`]+`", task)) >= 4:
        risk_score += 1
    if len(task) > 1200:
        risk_score += 1
    risk_level = "high" if risk_score >= 5 else "medium" if risk_score >= 3 else "low"
    return {"kinds": kinds, "risk_score": risk_score, "risk_level": risk_level}


def task_triage(payload: dict[str, Any]) -> dict[str, Any]:
    task = task_text(payload)
    classification = classify_task(task)
    needs_clarification = not task or len(task) < 12
    required_artifacts = [
        "planning_packet.json",
        "repo_survey_request.json",
        "design_options.json",
        "verification_strategy.json",
        "risk_register.json",
    ]
    if "test_repair" in classification["kinds"]:
        required_artifacts.append("failing_test_diagnostic.json")
    if any(kind in classification["kinds"] for kind in ("api_compatibility", "migration", "security")):
        required_artifacts.append("negative_test_plan.json")
    return {
        "role": "TaskTriage",
        "task_kinds": classification["kinds"],
        "risk_level": classification["risk_level"],
        "risk_score": classification["risk_score"],
        "needs_clarification": needs_clarification,
        "clarifying_questions": ["What exact behavior should be preserved or changed?"] if needs_clarification else [],
        "required_artifacts": required_artifacts,
        "handoff_to": "RepoSurveyor",
    }


def repo_survey_request(payload: dict[str, Any], triage: dict[str, Any]) -> dict[str, Any]:
    repo_path = normalize_repo_path(payload)
    focus = [
        "public entrypoints",
        "test surface",
        "import graph around candidate files",
        "configuration and runtime boundaries",
    ]
    if "api_compatibility" in triage["task_kinds"]:
        focus.append("public API request/response compatibility")
    if "security" in triage["task_kinds"]:
        focus.append("security boundary and untrusted input flows")
    if "migration" in triage["task_kinds"]:
        focus.append("old/new data shape readers and writers")
    return {
        "role": "RepoSurveyor",
        "repo_path": repo_path,
        "read_only": True,
        "focus": focus,
        "exclude_patterns": [
            ".git/",
            "__pycache__/",
            ".venv/",
            "node_modules/",
            "runtime/",
            "runs/",
            "models/",
            "videos/",
        ],
        "expected_output": "repo_survey.json",
        "handoff_to": "DesignStrategos",
    }


def design_options(payload: dict[str, Any], triage: dict[str, Any]) -> dict[str, Any]:
    task = task_text(payload)
    selected = "minimal_design"
    options = [
        {
            "name": "hardcode",
            "decision": "reject",
            "reason": "May satisfy one visible case while hiding caller, boundary, or compatibility failures.",
        },
        {
            "name": "broad_rewrite",
            "decision": "reject",
            "reason": "Too much blast radius before repo evidence proves a wide rewrite is necessary.",
        },
        {
            "name": selected,
            "decision": "prefer",
            "reason": "Smallest source change that satisfies the user contract, preserves public behavior, and leaves verification evidence.",
        },
    ]
    if "refactor" in triage["task_kinds"]:
        options[2]["reason"] = "Narrow refactor with behavior-preservation checks before any broad architectural rewrite."
    if "security" in triage["task_kinds"]:
        options.append(
            {
                "name": "boundary_first_patch",
                "decision": "consider",
                "reason": "Security work may need validation before feature behavior changes.",
            }
        )
        selected = "boundary_first_patch"
    return {
        "role": "DesignStrategos",
        "task_excerpt": task[:300],
        "options": options,
        "selected_strategy": selected,
        "requires_ceraxia_approval": True,
        "handoff_to": "VerificationArchitect",
    }


def verification_strategy(triage: dict[str, Any]) -> dict[str, Any]:
    commands = ["python -m py_compile <changed .py files>", "git diff --check"]
    checks = ["targeted behavior verification", "changed-file syntax verification"]
    negative_tests: list[str] = []
    if "test_repair" in triage["task_kinds"]:
        commands.insert(0, "rerun failing test command")
        checks.append("failing test proves fixed behavior")
    if "api_compatibility" in triage["task_kinds"]:
        checks.append("public caller or schema compatibility check")
        negative_tests.append("old and new API shape compatibility")
    if "security" in triage["task_kinds"]:
        negative_tests.append("untrusted input is rejected")
        negative_tests.append("path/auth/token boundary cannot be bypassed")
    if "config_runtime" in triage["task_kinds"]:
        negative_tests.append("missing/invalid config fails safely")
    if "migration" in triage["task_kinds"]:
        negative_tests.append("old, new, and mixed records round-trip correctly")
    broad_required = triage["risk_level"] == "high"
    return {
        "role": "VerificationArchitect",
        "targeted_commands": commands,
        "checks": checks,
        "negative_tests": negative_tests,
        "broad_verification_required": broad_required,
        "broad_verification_or_blocker": broad_required,
        "handoff_to": "RiskScribe",
    }


def risk_register(triage: dict[str, Any], survey: dict[str, Any], design: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    risks = [
        {
            "risk": "test_green_but_behavior_wrong",
            "severity": "high",
            "mitigation": "Require source correctness, unchanged tests, and meaningful verification evidence.",
        },
        {
            "risk": "hidden_public_caller_breakage",
            "severity": "medium" if triage["risk_level"] != "high" else "high",
            "mitigation": "RepoSurveyor must identify entrypoints and dependent callers before mutation.",
        },
    ]
    if verification["negative_tests"]:
        risks.append(
            {
                "risk": "missing_negative_boundary_test",
                "severity": "high",
                "mitigation": "Do not accept final package until negative tests are run or explicitly blocked.",
            }
        )
    if design["selected_strategy"] == "boundary_first_patch":
        risks.append(
            {
                "risk": "security_patch_changes_user_visible_behavior",
                "severity": "high",
                "mitigation": "Document compatibility impact and add caller-facing verification.",
            }
        )
    return {
        "role": "RiskScribe",
        "risks": risks,
        "acceptance_gates": [
            "planning packet includes all five planning roles",
            "Ceraxia approves selected strategy before implementation",
            "tests are not edited to fit the patch unless the user explicitly requested test changes",
            "negative tests are present or blocker is explicit for security/config/API/migration work",
        ],
        "handoff_to": "Ceraxia",
        "survey_reference": survey["expected_output"],
    }


def quality_bar(triage: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    kinds = set(triage["task_kinds"])
    must_have = [
        "task intent is restated in implementable terms",
        "candidate files are chosen from repository evidence",
        "final report names changed files, verification, blockers, and next action",
    ]
    if "test_repair" in kinds:
        must_have.append("failing test diagnostic is preserved before source mutation")
    if "api_compatibility" in kinds or "migration" in kinds:
        must_have.append("backward compatibility evidence is present")
    if "security" in kinds:
        must_have.append("negative boundary test or explicit blocker is present")
    if verification["broad_verification_required"]:
        must_have.append("broad verification is executed or blocked with a concrete reason")
    return {
        "role": "PlanningBrigade",
        "risk_level": triage["risk_level"],
        "must_have_evidence": must_have,
        "forbidden_shortcuts": [
            "claiming success without verification evidence",
            "changing tests before source evidence supports the fix",
            "broad rewrite without explicit repo evidence",
            "hiding blocked or skipped checks",
        ],
        "success_definition": "Ceraxia can hand the task to CodeBrigade with scoped files, verification expectations, risk gates, and an auditable final package.",
    }


def build_planning_packet(payload: dict[str, Any]) -> dict[str, Any]:
    task = task_text(payload)
    triage = task_triage(payload)
    survey = repo_survey_request(payload, triage)
    design = design_options(payload, triage)
    verification = verification_strategy(triage)
    risks = risk_register(triage, survey, design, verification)
    quality = quality_bar(triage, verification)
    return {
        "ok": bool(task),
        "worker": "PlanningBrigade",
        "kind": "ceraxia_planning_packet",
        "task": task,
        "roles_completed": ROLE_ORDER,
        "task_triage": triage,
        "repo_survey_request": survey,
        "design_options": design,
        "verification_strategy": verification,
        "risk_register": risks,
        "quality_bar": quality,
        "next_action": {
            "owner": "Ceraxia",
            "action": "approve_or_revise_plan",
            "reason": "PlanningBrigade is advisory and cannot replace the responsible code brigadier.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Ceraxia planning packet.")
    parser.add_argument("--task", default="")
    parser.add_argument("--repo-path", default="")
    parser.add_argument("--input-json", type=Path)
    args = parser.parse_args()
    payload: dict[str, Any] = {}
    if args.input_json:
        loaded = json.loads(args.input_json.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise SystemExit("input JSON must be an object")
        payload.update(loaded)
    if args.task:
        payload["task"] = args.task
    if args.repo_path:
        payload["repo_path"] = args.repo_path
    print(json.dumps(build_planning_packet(payload), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
