#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from planning_packet_contract import CONTRACT_VERSION, ROLE_ORDER, validate_planning_packet


def extract_path_hints(task: str) -> list[str]:
    hints: list[str] = []
    for value in re.findall(r"`([^`]+)`", task):
        cleaned = value.strip()
        if cleaned and cleaned not in hints:
            hints.append(cleaned)
    for value in re.findall(r"(?<![\w/.-])([\w./-]+\.(?:py|js|ts|tsx|jsx|kt|java|go|rs|sh|json|toml|ya?ml|md|txt))(?![\w/.-])", task):
        cleaned = value.strip()
        if cleaned and cleaned not in hints:
            hints.append(cleaned)
    return hints


def string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def payload_constraints(payload: dict[str, Any]) -> list[str]:
    constraints: list[str] = []
    for key in ("constraints", "requirements", "preserve"):
        for item in string_list(payload.get(key)):
            if item not in constraints:
                constraints.append(item)
    return constraints


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
        "concurrency": ["concurrency", "parallel", "async", "race", "deadlock", "lock", "cache", "retry", "конкур", "паралл", "гонк", "кэш", "ретра"],
    }
    kinds = [name for name, needles in patterns.items() if any(needle in lowered for needle in needles)]
    if not kinds:
        kinds = ["general_code_change"]
    risk_score = 1
    for kind in kinds:
        if kind == "security":
            risk_score += 3
        elif kind in {"migration", "api_compatibility", "refactor", "concurrency"}:
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


def problem_statement(payload: dict[str, Any], triage: dict[str, Any]) -> dict[str, Any]:
    task = task_text(payload)
    explicit_paths = extract_path_hints(task)
    structured_constraints = payload_constraints(payload)
    return {
        "role": "TaskTriage",
        "intent": task[:500],
        "task_kinds": triage["task_kinds"],
        "risk_level": triage["risk_level"],
        "known_constraints": [
            "preserve public behavior unless the task explicitly asks to change it",
            "prefer repository evidence over guessing candidate files",
            "do not mutate source before survey, design, verification, and risk gates exist",
        ] + structured_constraints,
        "explicit_path_hints": explicit_paths,
        "unknowns": triage["clarifying_questions"] if triage["needs_clarification"] else [],
        "definition_of_done": [
            "the original user-visible request is satisfied",
            "the changed behavior is covered by targeted verification",
            "the final report names evidence, blockers, and any residual risk",
        ],
    }


def repo_survey_request(payload: dict[str, Any], triage: dict[str, Any]) -> dict[str, Any]:
    task = task_text(payload)
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
    if "concurrency" in triage["task_kinds"]:
        focus.append("concurrency, cache, retry, and shared-state boundaries")
    return {
        "role": "RepoSurveyor",
        "repo_path": repo_path,
        "read_only": True,
        "path_hints": extract_path_hints(task),
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


def dependency_map(triage: dict[str, Any], survey: dict[str, Any]) -> dict[str, Any]:
    nodes = [
        {
            "id": "task_contract",
            "kind": "requirement",
            "description": "User request restated as behavior and constraints.",
        },
        {
            "id": "repo_evidence",
            "kind": "read_only_evidence",
            "description": "Repository entrypoints, tests, candidate files, and dependency edges.",
            "depends_on": ["task_contract"],
        },
        {
            "id": "design_decision",
            "kind": "engineering_decision",
            "description": "Smallest coherent design selected from alternatives.",
            "depends_on": ["repo_evidence"],
        },
        {
            "id": "verification_contract",
            "kind": "quality_gate",
            "description": "Commands, negative tests, and broad verification requirements.",
            "depends_on": ["design_decision"],
        },
        {
            "id": "implementation_brief",
            "kind": "handoff",
            "description": "CodeBrigade receives scoped work only after planning gates exist.",
            "depends_on": ["verification_contract"],
        },
    ]
    if "security" in triage["task_kinds"]:
        nodes.append(
            {
                "id": "security_boundary",
                "kind": "quality_gate",
                "description": "Untrusted input boundary must be proven with negative evidence.",
                "depends_on": ["repo_evidence", "verification_contract"],
            }
        )
    if any(kind in triage["task_kinds"] for kind in ("api_compatibility", "migration")):
        nodes.append(
            {
                "id": "compatibility_boundary",
                "kind": "quality_gate",
                "description": "Old and new caller/data shapes must remain intentionally compatible or explicitly migrated.",
                "depends_on": ["repo_evidence", "design_decision"],
            }
        )
    return {
        "role": "RepoSurveyor",
        "nodes": nodes,
        "critical_path": [
            "task_contract",
            "repo_evidence",
            "design_decision",
            "verification_contract",
            "implementation_brief",
        ],
        "blocked_without": [
            "repo survey candidate files",
            "test surface or explicit no-test blocker",
            "verification commands or explicit execution blocker",
        ],
        "survey_reference": survey["expected_output"],
    }


def work_breakdown(triage: dict[str, Any], dependency: dict[str, Any]) -> dict[str, Any]:
    phases = [
        {
            "id": "frame_task",
            "owner": "PlanningBrigade",
            "depends_on": [],
            "objective": "Restate the task as behavior, constraints, and definition of done.",
            "outputs": ["problem_statement"],
            "exit_gate": "task intent and unknowns are explicit",
        },
        {
            "id": "survey_repo",
            "owner": "Ceraxia",
            "depends_on": ["frame_task"],
            "objective": "Collect read-only repository evidence before selecting files.",
            "outputs": ["repo_survey.json"],
            "exit_gate": "candidate files, tests, entrypoints, or blockers are recorded",
        },
        {
            "id": "choose_design",
            "owner": "PlanningBrigade",
            "depends_on": ["survey_repo"],
            "objective": "Reject unsafe shortcuts and choose the smallest coherent design.",
            "outputs": ["design_options"],
            "exit_gate": "selected strategy is approved by Ceraxia",
        },
        {
            "id": "prepare_verification",
            "owner": "PlanningBrigade",
            "depends_on": ["choose_design"],
            "objective": "Define targeted commands, negative tests, and broad verification requirements.",
            "outputs": ["verification_strategy"],
            "exit_gate": "verification is executable, planned, or explicitly blocked",
        },
        {
            "id": "handoff_to_code_brigade",
            "owner": "Ceraxia",
            "depends_on": ["prepare_verification"],
            "objective": "Build the implementation brief and require CodeBrigade preflight before mutation.",
            "outputs": ["implementation_brief.json"],
            "exit_gate": "brief validates and mutation preconditions are attached",
        },
        {
            "id": "review_result",
            "owner": "Ceraxia",
            "depends_on": ["handoff_to_code_brigade"],
            "objective": "Check worker report, verification evidence, and acceptance contract before finalization.",
            "outputs": ["review_gate.json", "final_report.md"],
            "exit_gate": "final package proves the original request or names concrete blockers",
        },
    ]
    if "test_repair" in triage["task_kinds"]:
        phases.insert(
            2,
            {
                "id": "capture_failing_test",
                "owner": "CodeBrigade",
                "depends_on": ["survey_repo"],
                "objective": "Preserve the failing test diagnostic before source mutation.",
                "outputs": ["failing_test_diagnostic.json"],
                "exit_gate": "failure mode is known and not masked by test edits",
            },
        )
        phases[3]["depends_on"] = ["capture_failing_test"]
    if "security" in triage["task_kinds"]:
        phases.insert(
            -1,
            {
                "id": "prove_boundary",
                "owner": "CodeBrigade",
                "depends_on": ["handoff_to_code_brigade"],
                "objective": "Prove the unsafe path/auth/input boundary cannot be bypassed.",
                "outputs": ["negative_test_evidence.json"],
                "exit_gate": "negative boundary evidence is present or blocked with a reason",
            },
        )
    if any(kind in triage["task_kinds"] for kind in ("api_compatibility", "migration")):
        phases.insert(
            -1,
            {
                "id": "prove_compatibility",
                "owner": "CodeBrigade",
                "depends_on": ["handoff_to_code_brigade"],
                "objective": "Prove old and new public/data shapes are intentionally handled.",
                "outputs": ["compatibility_evidence.json"],
                "exit_gate": "compatibility evidence is present or migration breakage is explicit",
            },
        )
    final_review_dependencies = [
        phase["id"]
        for phase in phases
        if phase["id"] in {"prove_boundary", "prove_compatibility"}
    ] or ["handoff_to_code_brigade"]
    for phase in phases:
        if phase["id"] == "review_result":
            phase["depends_on"] = final_review_dependencies
    return {
        "role": "PlanningBrigade",
        "risk_level": triage["risk_level"],
        "phases": phases,
        "critical_path": dependency["critical_path"],
        "parallelizable_after_survey": [
            "verification planning can proceed beside detailed patch planning",
            "risk register can be updated while CodeBrigade inspects candidate files",
        ],
        "stop_conditions": [
            "repo survey cannot identify candidate files or tests",
            "selected design needs broader rewrite than approved scope",
            "verification cannot prove the requested behavior",
        ],
    }


def impact_analysis(triage: dict[str, Any], problem: dict[str, Any], survey: dict[str, Any]) -> dict[str, Any]:
    kinds = set(triage["task_kinds"])
    surfaces: list[dict[str, Any]] = [
        {
            "surface": "source_behavior",
            "risk": triage["risk_level"],
            "evidence_needed": ["candidate source files", "targeted behavior verification"],
        },
        {
            "surface": "test_surface",
            "risk": "medium" if "test_repair" in kinds else "low",
            "evidence_needed": ["existing tests", "test edits avoided unless explicitly requested"],
        },
    ]
    if "api_compatibility" in kinds:
        surfaces.append(
            {
                "surface": "public_api_contract",
                "risk": "high",
                "evidence_needed": ["request/response compatibility", "caller-facing verification"],
            }
        )
    if "security" in kinds:
        surfaces.append(
            {
                "surface": "security_boundary",
                "risk": "high",
                "evidence_needed": ["negative boundary tests", "untrusted input rejection evidence"],
            }
        )
    if "config_runtime" in kinds:
        surfaces.append(
            {
                "surface": "runtime_configuration",
                "risk": "medium",
                "evidence_needed": ["missing/invalid config behavior", "startup/runtime compatibility"],
            }
        )
    if "migration" in kinds:
        surfaces.append(
            {
                "surface": "data_compatibility",
                "risk": "high",
                "evidence_needed": ["old/new/mixed data shape round trip", "rollback or compatibility note"],
            }
        )
    if "concurrency" in kinds:
        surfaces.append(
            {
                "surface": "concurrency_runtime",
                "risk": "high",
                "evidence_needed": ["parallel/retry behavior", "shared-state or cache invalidation evidence"],
            }
        )
    if "refactor" in kinds:
        surfaces.append(
            {
                "surface": "internal_architecture",
                "risk": "medium",
                "evidence_needed": ["behavior-preserving tests", "dependency edge review"],
            }
        )
    return {
        "role": "DesignStrategos",
        "path_hints": problem.get("explicit_path_hints", []),
        "repo_focus": survey.get("focus", []),
        "surfaces": surfaces,
        "highest_risk_surface": (
            "security_boundary"
            if "security" in kinds
            else "data_compatibility"
            if "migration" in kinds
            else "concurrency_runtime"
            if "concurrency" in kinds
            else surfaces[0]["surface"]
        ),
        "requires_cross_surface_review": len(surfaces) >= 4 or triage["risk_level"] == "high",
    }


def execution_forecast(triage: dict[str, Any], breakdown: dict[str, Any], impact: dict[str, Any]) -> dict[str, Any]:
    phase_count = len(breakdown.get("phases", []))
    surface_count = len(impact.get("surfaces", []))
    complexity_score = triage["risk_score"] + phase_count + surface_count
    if impact.get("requires_cross_surface_review"):
        complexity_score += 2
    if complexity_score >= 15:
        complexity = "high"
        expected_iterations = 4
        recommended_timeout_minutes = 60
    elif complexity_score >= 10:
        complexity = "medium"
        expected_iterations = 2
        recommended_timeout_minutes = 30
    else:
        complexity = "low"
        expected_iterations = 1
        recommended_timeout_minutes = 15
    return {
        "role": "PlanningBrigade",
        "complexity": complexity,
        "complexity_score": complexity_score,
        "expected_code_brigade_iterations": expected_iterations,
        "recommended_timeout_minutes": recommended_timeout_minutes,
        "orchestration_notes": [
            "run repository survey before source mutation",
            "preserve planning packet and implementation brief in the final package",
            "repeat review after every worker report with source changes",
        ],
        "escalation_triggers": [
            "survey quality gate blocks",
            "surface verification matrix is incomplete",
            "verification fails twice on the same behavior",
            "CodeBrigade requests scope outside allowed_scope",
        ],
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
        options[2]["decision"] = "consider"
        options.append(
            {
                "name": "boundary_first_patch",
                "decision": "prefer",
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


def verification_strategy(triage: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
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
    if "concurrency" in triage["task_kinds"]:
        negative_tests.append("parallel/retry behavior preserves state")
    for command in string_list(payload.get("verification_commands")) + string_list(payload.get("required_verification_commands")):
        if command not in commands:
            commands.append(command)
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


def surface_verification_matrix(impact: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    checks = verification.get("checks", []) if isinstance(verification.get("checks"), list) else []
    negative_tests = verification.get("negative_tests", []) if isinstance(verification.get("negative_tests"), list) else []
    rows: list[dict[str, Any]] = []
    for surface in impact.get("surfaces", []):
        name = surface["surface"]
        covered_by: list[str] = []
        blockers: list[str] = []
        if name == "source_behavior":
            covered_by.extend(check for check in checks if "behavior" in check)
        elif name == "test_surface":
            covered_by.extend(check for check in checks if "test" in check)
            if not covered_by:
                covered_by.append("changed-file syntax verification")
        elif name == "public_api_contract":
            covered_by.extend(item for item in [*checks, *negative_tests] if "API" in item or "api" in item or "schema" in item or "compatibility" in item)
        elif name == "security_boundary":
            covered_by.extend(item for item in negative_tests if "input" in item or "path" in item or "auth" in item or "token" in item)
        elif name == "runtime_configuration":
            covered_by.extend(item for item in negative_tests if "config" in item)
        elif name == "data_compatibility":
            covered_by.extend(item for item in negative_tests if "round-trip" in item or "round trip" in item or "mixed records" in item)
        elif name == "concurrency_runtime":
            covered_by.extend(item for item in negative_tests if "parallel" in item or "retry" in item or "state" in item)
        elif name == "internal_architecture":
            covered_by.extend(item for item in checks if "dependency" in item or "behavior" in item)
        if not covered_by:
            blockers.append(f"no planned verification covers {name}")
        rows.append(
            {
                "surface": name,
                "risk": surface["risk"],
                "evidence_needed": surface["evidence_needed"],
                "covered_by": covered_by,
                "blockers": blockers,
            }
        )
    matrix_blockers = [blocker for row in rows for blocker in row["blockers"]]
    return {
        "role": "VerificationArchitect",
        "rows": rows,
        "complete": not matrix_blockers,
        "blockers": matrix_blockers,
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
    if "concurrency" in triage["task_kinds"]:
        risks.append(
            {
                "risk": "nondeterministic_parallel_state_regression",
                "severity": "high",
                "mitigation": "Require parallel/retry verification or an explicit blocker before final acceptance.",
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


def acceptance_contract(
    problem: dict[str, Any],
    triage: dict[str, Any],
    verification: dict[str, Any],
    quality: dict[str, Any],
    surface_matrix: dict[str, Any],
) -> dict[str, Any]:
    must_prove = list(problem["definition_of_done"])
    must_prove.extend(quality["must_have_evidence"])
    if verification["negative_tests"]:
        must_prove.append("required negative tests are present, executed, or explicitly blocked")
    if not surface_matrix["complete"]:
        must_prove.append("surface verification blockers are resolved or explicitly accepted")
    return {
        "role": "PlanningBrigade",
        "risk_level": triage["risk_level"],
        "must_prove": must_prove,
        "must_not_do": [
            "start source mutation before implementation brief validation",
            "treat green syntax checks as sufficient behavior verification",
            "hide skipped, blocked, or planned-only verification",
        ],
        "review_questions": [
            "Does the selected design satisfy the original request rather than a convenient subset?",
            "Are changed files justified by repository evidence?",
            "Would a reasonable maintainer accept the verification evidence?",
        ],
    }


def implementation_brief_blueprint(
    triage: dict[str, Any],
    design: dict[str, Any],
    verification: dict[str, Any],
    risks: dict[str, Any],
    quality: dict[str, Any],
    dependency: dict[str, Any],
    breakdown: dict[str, Any],
    impact: dict[str, Any],
    surface_matrix: dict[str, Any],
    forecast: dict[str, Any],
) -> dict[str, Any]:
    return {
        "role": "PlanningBrigade",
        "target": "CodeBrigade",
        "required_inputs": [
            "planning_packet.json",
            "repo_survey.json",
            "implementation_brief.json",
        ],
        "required_sections": [
            "task",
            "repo_path",
            "task_kinds",
            "risk_level",
            "selected_strategy",
            "allowed_scope",
            "forbidden_approaches",
            "required_verification",
            "surface_verification_matrix",
            "survey_quality_gate",
            "acceptance_gates",
            "quality_bar",
            "acceptance_contract",
            "repo_survey_evidence",
            "work_breakdown",
            "impact_analysis",
            "execution_forecast",
            "planning_review_gate",
        ],
        "strategy": design["selected_strategy"],
        "risk_level": triage["risk_level"],
        "acceptance_gates": risks["acceptance_gates"],
        "required_verification": verification,
        "required_quality_evidence": quality["must_have_evidence"],
        "dependency_critical_path": dependency["critical_path"],
        "work_phases": [phase["id"] for phase in breakdown["phases"]],
        "impact_surfaces": [surface["surface"] for surface in impact["surfaces"]],
        "surface_verification_complete": surface_matrix["complete"],
        "expected_code_brigade_iterations": forecast["expected_code_brigade_iterations"],
        "mutation_preconditions": [
            "implementation brief validates",
            "execution preflight passes",
            "candidate files are repo-relative existing non-symlink paths",
            "verification plan is attached to the worker report",
        ],
    }


def implementation_work_packages(
    triage: dict[str, Any],
    problem: dict[str, Any],
    dependency: dict[str, Any],
    impact: dict[str, Any],
    verification: dict[str, Any],
    risks: dict[str, Any],
    forecast: dict[str, Any],
) -> dict[str, Any]:
    task_kinds = set(triage.get("task_kinds", []))
    surfaces = impact.get("surfaces", []) if isinstance(impact.get("surfaces"), list) else []
    targeted_commands = verification.get("targeted_commands", []) if isinstance(verification.get("targeted_commands"), list) else []
    negative_tests = verification.get("negative_tests", []) if isinstance(verification.get("negative_tests"), list) else []
    risk_items = risks.get("risks", []) if isinstance(risks.get("risks"), list) else []
    packages: list[dict[str, Any]] = [
        {
            "id": "evidence_survey_package",
            "owner": "CodeBrigade",
            "purpose": "Confirm candidate files, dependent callers, and tests before choosing an edit.",
            "impact_surfaces": [surface.get("surface", "") for surface in surfaces if isinstance(surface, dict) and surface.get("surface")],
            "read_scope": [
                "repo_survey_evidence.recommended_read_order",
                "repo_survey_evidence.candidate_files",
                "repo_survey_evidence.test_files",
                "repo_survey_evidence.local_import_edges",
                "repo_survey_evidence.generic_import_edges",
            ],
            "edit_scope": [],
            "verification_scope": ["no mutation; evidence only"],
            "risk_controls": ["block if candidate files or required path hints are missing"],
            "handoff_criteria": ["candidate file decision is grounded in repo_survey.json"],
        },
        {
            "id": "minimal_patch_package",
            "owner": "CodeBrigade",
            "purpose": "Apply the smallest source change that satisfies the selected strategy.",
            "impact_surfaces": ["source_behavior"],
            "read_scope": ["implementation_brief_blueprint", "planning_dependency_map", "impact_analysis"],
            "edit_scope": ["candidate files identified by repository survey"],
            "verification_scope": targeted_commands,
            "risk_controls": [
                "preserve public behavior unless the task explicitly changes it",
                "do not edit tests to mask broken source behavior",
                "rollback or block when patch preflight fails",
            ],
            "handoff_criteria": ["worker_report.json lists changed files, blockers, and execution result"],
        },
        {
            "id": "verification_evidence_package",
            "owner": "CodeBrigade",
            "purpose": "Prove each planned impact surface or return concrete blockers.",
            "impact_surfaces": [surface.get("surface", "") for surface in surfaces if isinstance(surface, dict) and surface.get("surface")],
            "read_scope": ["surface_verification_matrix", "required_verification", "acceptance_contract"],
            "edit_scope": [],
            "verification_scope": targeted_commands + negative_tests,
            "risk_controls": ["do not treat syntax-only checks as behavior proof"],
            "handoff_criteria": ["verification_report.json names executed, skipped, failed, or blocked checks"],
        },
    ]
    if task_kinds & {"api_compatibility", "migration"}:
        packages.append(
            {
                "id": "compatibility_package",
                "owner": "CodeBrigade",
                "purpose": "Protect old/new public or data shapes across callers, readers, and writers.",
                "impact_surfaces": ["public_api_contract", "data_compatibility"],
                "read_scope": ["compatibility_boundary", "entrypoints_to_check", "dependency_edges_to_check"],
                "edit_scope": ["source files required by compatibility evidence"],
                "verification_scope": [item for item in [*targeted_commands, *negative_tests] if "compat" in item.lower() or "api" in item.lower() or "schema" in item.lower() or "round" in item.lower()],
                "risk_controls": ["block if compatibility breakage is not explicitly accepted"],
                "handoff_criteria": ["compatibility evidence is present or the migration break is explicit"],
            }
        )
    if "security" in task_kinds:
        packages.append(
            {
                "id": "security_boundary_package",
                "owner": "CodeBrigade",
                "purpose": "Prove the untrusted input, path, auth, or token boundary cannot be bypassed.",
                "impact_surfaces": ["security_boundary"],
                "read_scope": ["security_boundary", "negative_tests", "untrusted input flows"],
                "edit_scope": ["boundary validation source files only"],
                "verification_scope": [item for item in negative_tests if any(word in item for word in ("input", "path", "auth", "token"))],
                "risk_controls": ["final review blocks without negative boundary evidence"],
                "handoff_criteria": ["negative_test_evidence.json or explicit blocker is returned"],
            }
        )
    if "config_runtime" in task_kinds:
        packages.append(
            {
                "id": "runtime_configuration_package",
                "owner": "CodeBrigade",
                "purpose": "Keep config keys, environment overrides, startup behavior, and runtime defaults aligned.",
                "impact_surfaces": ["runtime_configuration"],
                "read_scope": ["runtime_configuration", "config files", "environment loader", "startup entrypoints"],
                "edit_scope": ["configuration loader, defaults, docs, or entrypoints required by the task"],
                "verification_scope": [item for item in [*targeted_commands, *negative_tests] if any(word in item.lower() for word in ("config", "runtime", "startup", "env"))],
                "risk_controls": ["block if missing/invalid config behavior is not proven or explicitly accepted"],
                "handoff_criteria": ["runtime configuration evidence is present or blocked with a concrete reason"],
            }
        )
    if "concurrency" in task_kinds:
        packages.append(
            {
                "id": "concurrency_runtime_package",
                "owner": "CodeBrigade",
                "purpose": "Protect parallel, retry, cache, and shared-state behavior from nondeterministic regressions.",
                "impact_surfaces": ["concurrency_runtime"],
                "read_scope": ["concurrency_runtime", "shared-state callers", "retry/cache boundaries"],
                "edit_scope": ["state coordination source files only"],
                "verification_scope": [item for item in [*targeted_commands, *negative_tests] if any(word in item.lower() for word in ("parallel", "retry", "state", "cache"))],
                "risk_controls": ["block if the race condition cannot be reproduced or bounded"],
                "handoff_criteria": ["remaining race risk is named in the review evidence"],
            }
        )
    if "refactor" in task_kinds:
        packages.append(
            {
                "id": "architecture_refactor_package",
                "owner": "CodeBrigade",
                "purpose": "Preserve behavior while moving internal boundaries, ownership, or module structure.",
                "impact_surfaces": ["internal_architecture", "source_behavior"],
                "read_scope": ["internal_architecture", "dependency edge review", "public callers"],
                "edit_scope": ["source files justified by the selected refactor strategy"],
                "verification_scope": [item for item in targeted_commands if any(word in item.lower() for word in ("test", "compile", "lint", "type", "dependency", "behavior"))],
                "risk_controls": ["block broad rewrites unless dependency evidence proves the scope"],
                "handoff_criteria": ["refactor evidence shows behavior preservation and dependency impact"],
            }
        )
    return {
        "role": "PlanningBrigade",
        "risk_level": triage["risk_level"],
        "task_kinds": triage["task_kinds"],
        "critical_path": dependency["critical_path"],
        "highest_risk_surface": impact.get("highest_risk_surface", ""),
        "surface_count": len(surfaces),
        "expected_iterations": forecast.get("expected_code_brigade_iterations", 1),
        "packages": packages,
        "package_count": len(packages),
        "review_order": [package["id"] for package in packages],
        "global_handoff_criteria": [
            "each package is passed, blocked, or explicitly deferred",
            "package blockers are reflected in review_gate.json",
            "final report answers the original task rather than only package-local success",
        ],
        "risk_focus": [item.get("risk", "") for item in risk_items if isinstance(item, dict) and item.get("risk")],
        "definition_of_done": problem.get("definition_of_done", []),
    }


def planning_review_gate(
    triage: dict[str, Any],
    problem: dict[str, Any],
    survey: dict[str, Any],
    dependency: dict[str, Any],
    breakdown: dict[str, Any],
    verification: dict[str, Any],
    surface_matrix: dict[str, Any],
    acceptance: dict[str, Any],
    work_packages: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    if triage["needs_clarification"]:
        blockers.append("task requires clarification before reliable implementation planning")
    if not survey.get("repo_path"):
        warnings.append("repo_path is absent; Ceraxia may fall back to the project root")
    if len(problem.get("definition_of_done", [])) < 3:
        blockers.append("definition_of_done is incomplete")
    if dependency.get("critical_path", [])[-1:] != ["implementation_brief"]:
        blockers.append("dependency critical path does not end at implementation brief")
    if len(breakdown.get("phases", [])) < 6:
        blockers.append("work breakdown is incomplete")
    if not verification.get("targeted_commands"):
        blockers.append("verification strategy has no targeted commands")
    if triage["risk_level"] == "high" and not verification.get("broad_verification_required"):
        blockers.append("high-risk task lacks broad verification requirement")
    if surface_matrix.get("complete") is False:
        blockers.extend(str(item) for item in surface_matrix.get("blockers", []))
    if work_packages is not None:
        packages = work_packages.get("packages") if isinstance(work_packages.get("packages"), list) else []
        if len(packages) < 3:
            blockers.append("implementation work packages are incomplete")
        for package in packages:
            if not isinstance(package, dict):
                blockers.append("implementation work package is not an object")
                continue
            for key in ("id", "owner", "purpose", "impact_surfaces", "read_scope", "edit_scope", "verification_scope", "risk_controls", "handoff_criteria"):
                if key not in package:
                    blockers.append(f"implementation work package missing {key}: {package.get('id', '<unknown>')}")
            if package.get("owner") != "CodeBrigade":
                blockers.append(f"implementation work package owner must be CodeBrigade: {package.get('id', '<unknown>')}")
        planned_surfaces = {surface.get("surface") for surface in surface_matrix.get("rows", []) if isinstance(surface, dict)}
        covered_surfaces = {
            surface
            for package in packages
            if isinstance(package, dict)
            for surface in package.get("impact_surfaces", [])
            if isinstance(surface, str)
        }
        missing_package_surfaces = sorted(surface for surface in planned_surfaces if surface and surface not in covered_surfaces)
        if missing_package_surfaces:
            blockers.append("implementation work packages do not cover surfaces: " + ", ".join(missing_package_surfaces))
    if verification.get("negative_tests") and not any("negative" in item for item in acceptance.get("must_prove", [])):
        blockers.append("negative test requirement is not reflected in acceptance contract")
    score = 100
    score -= 25 * len(blockers)
    score -= 5 * len(warnings)
    if triage["risk_level"] == "high":
        score -= 5
    score = max(0, min(100, score))
    if blockers:
        score = min(score, 60)
    if triage["needs_clarification"]:
        score = min(score, 40)
    if blockers:
        decision = "blocked"
    elif score < 80:
        decision = "revise"
    else:
        decision = "ready_for_ceraxia_review"
    return {
        "role": "PlanningBrigade",
        "decision": decision,
        "score": score,
        "blockers": blockers,
        "warnings": warnings,
        "checks": [
            "task clarity",
            "repository target",
            "definition of done",
            "dependency critical path",
            "work phase completeness",
            "verification coverage",
            "implementation work package completeness",
            "acceptance evidence alignment",
        ],
    }


def code_brigade_handoff(triage: dict[str, Any], verification: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    steps = [
        {
            "step": "inspect_repo_evidence",
            "owner": "CodeBrigade",
            "input": "repo_survey.json",
            "output": "candidate_file_decision.json",
            "may_mutate_source": False,
        },
        {
            "step": "prepare_patch_plan",
            "owner": "CodeBrigade",
            "input": "implementation_brief.json",
            "output": "patch_plan.json",
            "may_mutate_source": False,
        },
        {
            "step": "apply_or_block_patch",
            "owner": "CodeBrigade",
            "input": "patch_plan.json",
            "output": "worker_report.json",
            "may_mutate_source": True,
        },
        {
            "step": "verify_patch",
            "owner": "CodeBrigade",
            "input": "worker_report.json",
            "output": "verification_report.json",
            "may_mutate_source": False,
        },
        {
            "step": "return_for_ceraxia_review",
            "owner": "Ceraxia",
            "input": "verification_report.json",
            "output": "review_gate.json",
            "may_mutate_source": False,
        },
    ]
    if "security" in triage["task_kinds"] or verification["negative_tests"]:
        steps.insert(
            3,
            {
                "step": "prove_negative_boundary",
                "owner": "CodeBrigade",
                "input": "patch_plan.json",
                "output": "negative_test_evidence.json",
                "may_mutate_source": False,
            },
        )
    return {
        "role": "PlanningBrigade",
        "target": "CodeBrigade",
        "task_kinds": triage["task_kinds"],
        "steps": steps,
        "required_quality_evidence": quality["must_have_evidence"],
    }


def build_planning_packet(payload: dict[str, Any]) -> dict[str, Any]:
    task = task_text(payload)
    triage = task_triage(payload)
    problem = problem_statement(payload, triage)
    survey = repo_survey_request(payload, triage)
    dependency = dependency_map(triage, survey)
    breakdown = work_breakdown(triage, dependency)
    impact = impact_analysis(triage, problem, survey)
    forecast = execution_forecast(triage, breakdown, impact)
    design = design_options(payload, triage)
    verification = verification_strategy(triage, payload)
    surface_matrix = surface_verification_matrix(impact, verification)
    risks = risk_register(triage, survey, design, verification)
    quality = quality_bar(triage, verification)
    acceptance = acceptance_contract(problem, triage, verification, quality, surface_matrix)
    blueprint = implementation_brief_blueprint(triage, design, verification, risks, quality, dependency, breakdown, impact, surface_matrix, forecast)
    work_packages = implementation_work_packages(triage, problem, dependency, impact, verification, risks, forecast)
    review = planning_review_gate(triage, problem, survey, dependency, breakdown, verification, surface_matrix, acceptance, work_packages)
    handoff = code_brigade_handoff(triage, verification, quality)
    return {
        "ok": bool(task),
        "contract_version": CONTRACT_VERSION,
        "worker": "PlanningBrigade",
        "kind": "ceraxia_planning_packet",
        "task": task,
        "roles_completed": ROLE_ORDER,
        "problem_statement": problem,
        "task_triage": triage,
        "repo_survey_request": survey,
        "dependency_map": dependency,
        "work_breakdown": breakdown,
        "impact_analysis": impact,
        "execution_forecast": forecast,
        "design_options": design,
        "verification_strategy": verification,
        "surface_verification_matrix": surface_matrix,
        "risk_register": risks,
        "quality_bar": quality,
        "acceptance_contract": acceptance,
        "implementation_brief_blueprint": blueprint,
        "implementation_work_packages": work_packages,
        "planning_review_gate": review,
        "code_brigade_handoff": handoff,
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
    parser.add_argument("--validate", action="store_true", help="Exit non-zero when the generated planning packet has contract problems.")
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
    packet = build_planning_packet(payload)
    problems = validate_planning_packet(packet) if args.validate else []
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    if problems:
        print(json.dumps({"ok": False, "validation_problems": problems}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
