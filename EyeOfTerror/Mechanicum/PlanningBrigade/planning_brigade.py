#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from planning_feedback_contract import build_planning_feedback_intake
from planning_packet_contract import CONTRACT_VERSION, ROLE_ORDER, validate_planning_packet
from roles import design_strategos, repo_surveyor, risk_scribe, task_triage as task_triage_role, verification_architect


PATH_HINT_PATTERN = re.compile(r"(?<![\w/.-])([\w./-]+\.(?:py|js|ts|tsx|jsx|kt|java|go|rs|sh|json|toml|ya?ml|md|txt))(?![\w/.-])")


def looks_like_path_hint(value: str) -> bool:
    cleaned = value.strip()
    if not cleaned or any(character.isspace() for character in cleaned):
        return False
    return bool(PATH_HINT_PATTERN.fullmatch(cleaned) or "/" in cleaned)


def patch_operation_path_hints(task: str) -> list[str]:
    marker = "CERAXIA_PATCH:"
    if marker not in task:
        return []
    raw = task.split(marker, 1)[1].strip()
    try:
        payload, _ = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError:
        return []
    operations = payload.get("operations") if isinstance(payload, dict) else []
    if not isinstance(operations, list):
        return []
    hints: list[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        for key in ("path", "old_path", "new_path"):
            value = str(operation.get(key) or "").strip()
            if looks_like_path_hint(value) and value not in hints:
                hints.append(value)
    return hints


def extract_path_hints(task: str) -> list[str]:
    task_without_patch = task.split("CERAXIA_PATCH:", 1)[0]
    hints: list[str] = []
    for value in re.findall(r"`([^`]+)`", task_without_patch):
        cleaned = value.strip()
        if looks_like_path_hint(cleaned) and cleaned not in hints:
            hints.append(cleaned)
    for value in PATH_HINT_PATTERN.findall(task_without_patch):
        cleaned = value.strip()
        if cleaned and cleaned not in hints:
            hints.append(cleaned)
    for value in patch_operation_path_hints(task):
        if value not in hints:
            hints.append(value)
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


def assumption_register(triage: dict[str, Any], problem: dict[str, Any], survey: dict[str, Any]) -> dict[str, Any]:
    kinds = set(triage.get("task_kinds", []))
    assumptions: list[dict[str, Any]] = [
        {
            "id": "task_contract_is_sufficient",
            "assumption": "the task text and structured constraints are sufficient to choose a narrow implementation strategy",
            "risk_if_false": "CodeBrigade may solve a convenient subset instead of the user request",
            "validation_source": "problem_statement.definition_of_done",
            "blocks_when_false": "task requires clarification before source mutation",
            "owner": "PlanningBrigade",
        },
        {
            "id": "repo_survey_can_find_relevant_surface",
            "assumption": "read-only repository survey can identify candidate files, entrypoints, tests, or concrete blockers",
            "risk_if_false": "implementation scope would be guessed from task wording",
            "validation_source": "repo_survey.json",
            "blocks_when_false": "survey_quality_gate blocks implementation brief",
            "owner": "Ceraxia",
        },
        {
            "id": "verification_can_prove_user_visible_behavior",
            "assumption": "planned verification can prove the changed user-visible behavior or name a concrete blocker",
            "risk_if_false": "syntax-only checks could be mistaken for behavior proof",
            "validation_source": "verification_report.json",
            "blocks_when_false": "review_gate blocks finalization or requests replan",
            "owner": "CodeBrigade",
        },
    ]
    if "security" in kinds:
        assumptions.append(
            {
                "id": "security_boundary_is_traceable",
                "assumption": "untrusted input, boundary enforcement, and negative bypass case can be traced before mutation",
                "risk_if_false": "security patch may close the wrong path",
                "validation_source": "investigation_playbook.security_boundary_trace",
                "blocks_when_false": "negative boundary work package blocks handoff",
                "owner": "CodeBrigade",
            }
        )
    if kinds & {"api_compatibility", "migration"}:
        assumptions.append(
            {
                "id": "compatibility_expectation_is_known",
                "assumption": "old, new, mixed, and caller-facing shapes can be identified before deciding compatibility behavior",
                "risk_if_false": "migration may silently break existing callers or data",
                "validation_source": "investigation_playbook.compatibility_shape_trace",
                "blocks_when_false": "compatibility package blocks or returns to PlanningBrigade",
                "owner": "CodeBrigade",
            }
        )
    if "concurrency" in kinds:
        assumptions.append(
            {
                "id": "state_transition_risk_is_bounded",
                "assumption": "shared state, retry/cache boundary, and nondeterministic failure mode can be named",
                "risk_if_false": "race-condition fix may be unverifiable or introduce a hidden state regression",
                "validation_source": "investigation_playbook.state_transition_trace",
                "blocks_when_false": "concurrency runtime package blocks final review",
                "owner": "CodeBrigade",
            }
        )
    return {
        "role": "PlanningBrigade",
        "risk_level": triage["risk_level"],
        "path_hints": problem.get("explicit_path_hints", []),
        "repo_focus": survey.get("focus", []),
        "assumptions": assumptions,
        "replan_when_false": [item["blocks_when_false"] for item in assumptions],
        "handoff_to": "CodeBrigade",
    }


def investigation_playbook(triage: dict[str, Any], problem: dict[str, Any], survey: dict[str, Any]) -> dict[str, Any]:
    kinds = set(triage.get("task_kinds", []))
    stages: list[dict[str, Any]] = [
        {
            "stage": "entrypoints_first",
            "must_collect": ["public entrypoints", "runtime or CLI/API boundaries"],
            "blocks_mutation_until": "the user-visible surface is named or explicitly absent",
        },
        {
            "stage": "candidate_source_second",
            "must_collect": ["candidate source files", "reason each candidate is in scope"],
            "blocks_mutation_until": "candidate files are justified by repo evidence, not only task wording",
        },
        {
            "stage": "callers_and_dependencies",
            "must_collect": ["direct callers", "local import edges", "reverse dependency impact"],
            "blocks_mutation_until": "caller impact is mapped or a concrete blocker is recorded",
        },
        {
            "stage": "tests_and_oracles",
            "must_collect": ["existing tests", "failing diagnostics or explicit no-test blocker", "behavior oracle"],
            "blocks_mutation_until": "verification can prove the requested behavior or is explicitly blocked",
        },
        {
            "stage": "contract_and_risk_review",
            "must_collect": ["public contract assumptions", "highest-risk surface", "rollback or refusal condition"],
            "blocks_mutation_until": "risk controls and acceptance evidence are attached to the brief",
        },
    ]
    if "security" in kinds:
        stages.append(
            {
                "stage": "security_boundary_trace",
                "must_collect": ["untrusted input source", "boundary enforcement point", "negative bypass case"],
                "blocks_mutation_until": "the bypass path is traced from input to rejection point",
            }
        )
    if kinds & {"api_compatibility", "migration"}:
        stages.append(
            {
                "stage": "compatibility_shape_trace",
                "must_collect": ["old shape", "new shape", "mixed/caller compatibility expectation"],
                "blocks_mutation_until": "compatibility breakage is proven intentional or prevented",
            }
        )
    if "concurrency" in kinds:
        stages.append(
            {
                "stage": "state_transition_trace",
                "must_collect": ["shared state owner", "retry/cache boundary", "nondeterministic failure mode"],
                "blocks_mutation_until": "parallel state risk is named and bounded",
            }
        )
    return {
        "role": "PlanningBrigade",
        "target": "CodeBrigade",
        "path_hints": problem.get("explicit_path_hints", []),
        "repo_focus": survey.get("focus", []),
        "read_stages": stages,
        "evidence_questions": [
            "Which file or contract proves this is the right behavior to change?",
            "Which callers, entrypoints, or schemas could break if the patch is too narrow?",
            "Which existing or planned command proves the user-visible behavior rather than only syntax?",
            "What concrete blocker should stop mutation if repository evidence is missing?",
        ],
        "mutation_blockers": [
            "candidate files are absent or not justified by repository evidence",
            "public caller or test surface is unknown for medium/high risk work",
            "verification would be syntax-only for a behavior, security, compatibility, migration, or concurrency task",
        ],
        "replan_triggers": [
            "new impacted surface appears outside the impact analysis",
            "source edit scope exceeds the execution forecast budget",
            "verification cannot be mapped to every high-risk surface",
            "CodeBrigade needs to edit tests without explicit user permission",
        ],
        "handoff_to": "CodeBrigade",
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
        max_source_files_to_edit = 6
    elif complexity_score >= 10:
        complexity = "medium"
        expected_iterations = 2
        recommended_timeout_minutes = 30
        max_source_files_to_edit = 4
    else:
        complexity = "low"
        expected_iterations = 1
        recommended_timeout_minutes = 15
        max_source_files_to_edit = 2
    return {
        "role": "PlanningBrigade",
        "complexity": complexity,
        "complexity_score": complexity_score,
        "expected_code_brigade_iterations": expected_iterations,
        "recommended_timeout_minutes": recommended_timeout_minutes,
        "scope_budget": {
            "max_source_files_to_edit": max_source_files_to_edit,
            "max_test_files_to_edit_without_explicit_user_request": 0,
            "max_docs_files_to_edit": 2 if complexity != "low" else 1,
            "requires_ceraxia_replan_when": [
                "needed source edits exceed max_source_files_to_edit",
                "test edits are needed but were not explicitly requested by the user",
                "new public contract breakage appears outside the planned impact surfaces",
            ],
        },
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


def expert_quality_plan(
    triage: dict[str, Any],
    impact: dict[str, Any],
    forecast: dict[str, Any],
) -> dict[str, Any]:
    kinds = set(triage.get("task_kinds", []))
    surfaces = [
        str(surface.get("surface") or "")
        for surface in impact.get("surfaces", [])
        if isinstance(surface, dict) and surface.get("surface")
    ]
    expert_required = triage.get("risk_level") == "high" or forecast.get("complexity") == "high" or impact.get("requires_cross_surface_review") is True
    tradeoffs = [
        {
            "decision": "minimal_patch_vs_broad_rewrite",
            "prefer": "minimal_patch",
            "reason": "Preserve public behavior until repository evidence proves a wider rewrite is necessary.",
        },
        {
            "decision": "fast_green_checks_vs_behavior_proof",
            "prefer": "behavior_proof",
            "reason": "Syntax or smoke checks are not enough for user-visible, compatibility, security, or concurrency behavior.",
        },
    ]
    rollback = [
        "keep the changed-file set small enough to revert as one package",
        "name the previous behavior or data shape that must still be readable after the patch",
    ]
    observability = [
        "record executed, skipped, failed, and blocked verification commands",
        "preserve changed files, candidate files, and package-level blockers in the worker report",
    ]
    review_checklist = [
        "does the final package satisfy the original task rather than a convenient subset",
        "are changed files justified by repository evidence and package ownership",
        "does every high-risk surface have executed evidence or a concrete blocker",
        "are compatibility, security, runtime, and concurrency risks named when present",
    ]
    escalation = [
        "return to Ceraxia when implementation scope exceeds the selected strategy",
        "return to PlanningBrigade when verification cannot prove the acceptance contract",
    ]
    if "migration" in kinds or "api_compatibility" in kinds:
        tradeoffs.append(
            {
                "decision": "strict_new_shape_vs_backward_compatibility",
                "prefer": "backward_compatibility",
                "reason": "Public/data shape changes require explicit migration evidence before old callers are broken.",
            }
        )
        rollback.append("document how old and mixed records can still be read or intentionally rejected")
        review_checklist.append("old, new, and mixed data/API shapes are tested or explicitly blocked")
    if "security" in kinds:
        tradeoffs.append(
            {
                "decision": "boundary_patch_vs_feature_shortcut",
                "prefer": "boundary_patch",
                "reason": "Security work must close the untrusted-input path before expanding feature behavior.",
            }
        )
        observability.append("capture negative boundary evidence for untrusted input, path, auth, or token flows")
        review_checklist.append("negative boundary evidence proves the bypass is closed")
    if "concurrency" in kinds:
        tradeoffs.append(
            {
                "decision": "deterministic_state_vs_fast_shared_cache",
                "prefer": "deterministic_state",
                "reason": "Parallel/retry behavior must prefer correctness over a faster but race-prone shared state path.",
            }
        )
        rollback.append("preserve a single-thread or stale-cache fallback when a race cannot be fully bounded")
        observability.append("record remaining race risk and any non-deterministic verification limitations")
        review_checklist.append("parallel or retry evidence covers shared-state and cache invalidation behavior")
    if "refactor" in kinds:
        rollback.append("keep old public entrypoints available until behavior-preservation evidence passes")
        review_checklist.append("dependency edge review proves the refactor does not silently move public contracts")
    return {
        "role": "DesignStrategos",
        "level": "expert" if expert_required else "standard",
        "required_for_expert_gate": expert_required,
        "impact_surfaces": surfaces,
        "tradeoff_register": tradeoffs,
        "rollback_strategy": rollback,
        "observability_plan": observability,
        "review_checklist": review_checklist,
        "escalation_policy": escalation,
    }


def change_control_plan(
    triage: dict[str, Any],
    impact: dict[str, Any],
    verification: dict[str, Any],
    expert_plan: dict[str, Any],
) -> dict[str, Any]:
    kinds = set(triage.get("task_kinds", []))
    surfaces = [
        str(surface.get("surface") or "")
        for surface in impact.get("surfaces", [])
        if isinstance(surface, dict) and surface.get("surface")
    ]
    allowed_intents = [
        "change only behavior required by the original task contract",
        "touch source files only when repo evidence links them to the impacted surface",
        "adjust configuration or docs only when required to preserve the implemented contract",
    ]
    protected_invariants = [
        "public behavior not named by the task remains compatible",
        "tests are not changed to make a broken source patch pass",
        "verification evidence must stay tied to every high-risk surface",
    ]
    post_change_proofs = [
        "changed files are listed with repo evidence rationale",
        "targeted verification command is executed or concretely blocked",
        "final report answers every definition_of_done item",
    ]
    if "security" in kinds:
        protected_invariants.append("negative security boundary remains closed for bypass inputs")
        post_change_proofs.append("negative boundary evidence is executed or blocked with a concrete reason")
    if kinds & {"api_compatibility", "migration"}:
        protected_invariants.append("old callers, old data, and mixed shapes remain intentionally handled")
        post_change_proofs.append("compatibility evidence covers old, new, and mixed caller/data shapes")
    if "concurrency" in kinds:
        protected_invariants.append("parallel, retry, cache, and shared-state behavior remains deterministic or explicitly bounded")
        post_change_proofs.append("remaining race risk is reproduced, bounded, or explicitly blocked")
    if "refactor" in kinds:
        protected_invariants.append("public entrypoints and dependency edges remain behavior-preserving")
        post_change_proofs.append("dependency-edge review proves the refactor did not silently move public contracts")
    return {
        "role": "PlanningBrigade",
        "target": "CodeBrigade",
        "risk_level": triage["risk_level"],
        "impacted_surfaces": surfaces,
        "allowed_change_intents": allowed_intents,
        "protected_invariants": protected_invariants,
        "mutation_requires": [
            "implementation brief validates",
            "investigation playbook evidence has been acknowledged",
            "candidate file and caller impact are named",
            "rollback trigger is known before source mutation",
        ],
        "diff_review_questions": [
            "Does each changed file map to a planned impact surface?",
            "Does the diff preserve protected invariants outside the requested change?",
            "Does the diff avoid broad rewrite, hardcode, and test-masking shortcuts?",
            "Would a maintainer understand the patch from the evidence and verification trail?",
        ],
        "rollback_triggers": [
            "changed-file set exceeds the forecast scope budget",
            "verification cannot prove the changed behavior",
            "new public contract breakage appears outside the planned impact surfaces",
        ],
        "post_change_proofs": post_change_proofs,
        "expert_review_required": bool(expert_plan.get("required_for_expert_gate")),
        "handoff_to": "CodeBrigade",
    }


def design_options(payload: dict[str, Any], triage: dict[str, Any]) -> dict[str, Any]:
    task = task_text(payload)
    selected = "minimal_design"
    option_contract = {
        "acceptance_impact": "must satisfy definition_of_done with focused behavior evidence",
        "rollback_impact": "single scoped rollback is expected",
        "replan_trigger": "repo evidence shows the option cannot satisfy the acceptance contract safely",
    }
    options = [
        {
            "name": "hardcode",
            "decision": "reject",
            "reason": "May satisfy one visible case while hiding caller, boundary, or compatibility failures.",
            "acceptance_impact": "fails general user-visible behavior beyond one case",
            "rollback_impact": "low diff size but high false-success risk",
            "replan_trigger": "only accepted behavior proof is a literal or fixture-only match",
        },
        {
            "name": "broad_rewrite",
            "decision": "reject",
            "reason": "Too much blast radius before repo evidence proves a wide rewrite is necessary.",
            "acceptance_impact": "can satisfy many surfaces but risks unrequested behavior drift",
            "rollback_impact": "large rollback surface and high review burden",
            "replan_trigger": "only valid after dependency map proves narrow options cannot work",
        },
        {
            "name": selected,
            "decision": "prefer",
            "reason": "Smallest source change that satisfies the user contract, preserves public behavior, and leaves verification evidence.",
            **option_contract,
        },
    ]
    if "refactor" in triage["task_kinds"]:
        options[2]["reason"] = "Narrow refactor with behavior-preservation checks before any broad architectural rewrite."
        options[2]["rollback_impact"] = "must be reversible without public interface changes"
    if "security" in triage["task_kinds"]:
        options[2]["decision"] = "consider"
        options.append(
            {
                "name": "boundary_first_patch",
                "decision": "prefer",
                "reason": "Security work may need validation before feature behavior changes.",
                "acceptance_impact": "negative boundary proof has priority over convenience behavior",
                "rollback_impact": "rollback must restore boundary behavior and remove partial bypass fixes",
                "replan_trigger": "negative boundary proof cannot be executed or remains ambiguous",
            }
        )
        selected = "boundary_first_patch"
    tradeoff_matrix = [
        {
            "criterion": "acceptance_proof",
            "selected_strategy_effect": next(option for option in options if option["name"] == selected)["acceptance_impact"],
            "rejected_shortcut_risk": "hardcode can pass a fixture while missing real behavior",
        },
        {
            "criterion": "blast_radius",
            "selected_strategy_effect": next(option for option in options if option["name"] == selected)["rollback_impact"],
            "rejected_shortcut_risk": "broad rewrite expands review and rollback without repo proof",
        },
        {
            "criterion": "replan_safety",
            "selected_strategy_effect": next(option for option in options if option["name"] == selected)["replan_trigger"],
            "rejected_shortcut_risk": "continuing after missing evidence turns uncertainty into false success",
        },
    ]
    return {
        "role": "DesignStrategos",
        "task_excerpt": task[:300],
        "options": options,
        "tradeoff_matrix": tradeoff_matrix,
        "selected_strategy": selected,
        "requires_ceraxia_approval": True,
        "safe_block_when": [
            "selected strategy lacks executable or explicitly blocked acceptance proof",
            "repo evidence cannot identify candidate files, callers, tests, or compatibility surface",
            "rollback impact cannot be bounded before mutation",
        ],
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


def diagnostic_repair_plan(
    triage: dict[str, Any],
    verification: dict[str, Any],
    impact: dict[str, Any],
    forecast: dict[str, Any],
) -> dict[str, Any]:
    stop_conditions = [
        "same verification failure repeats after a mutation",
        "diagnostics identify no repo-local source or test surface",
        "repair would exceed execution_forecast.scope_budget",
        "zero-test diagnostics indicate wrong test runner or command mismatch",
        "missing import cannot be mapped to an allowed existing or planned file",
    ]
    repair_evidence = [
        "diagnostic_summary",
        "changed files mapped to impact surfaces",
        "verification commands rerun after final mutation",
        "residual blockers when repair stops",
    ]
    if verification.get("broad_verification_required"):
        stop_conditions.append("broad verification cannot be executed or explicitly blocked after repair")
        repair_evidence.append("broad verification result or explicit blocker")
    if triage.get("risk_level") == "high" or impact.get("requires_cross_surface_review"):
        repair_evidence.append("Ceraxia review checkpoint after each repair attempt")
    return {
        "role": "VerificationArchitect",
        "target": "CodeBrigade",
        "max_repair_attempts": 3,
        "diagnostic_inputs_required": [
            "latest verification_execution.results[].diagnostics",
            "verification_execution.results[].diagnostics.traceback_files",
            "verification_execution.results[].diagnostics.missing_imports",
            "verification_execution.results[].diagnostics.has_assertion_failure",
            "verification_execution.results[].diagnostics.has_syntax_error",
            "verification_execution.results[].diagnostics.has_no_tests_ran",
        ],
        "read_before_repair": [
            "traceback_files",
            "target_files_to_inspect",
            "test_files_to_preserve",
            "reverse_dependency_index",
            "changed-file verification output",
        ],
        "stop_conditions": stop_conditions,
        "repair_evidence_required": repair_evidence,
        "scope_budget": forecast.get("scope_budget", {}) if isinstance(forecast.get("scope_budget"), dict) else {},
        "requires_ceraxia_review_after_each_attempt": triage.get("risk_level") == "high" or bool(impact.get("requires_cross_surface_review")),
        "handoff_to": "CodeBrigade",
    }


def surface_verification_matrix(impact: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    checks = verification.get("checks", []) if isinstance(verification.get("checks"), list) else []
    negative_tests = verification.get("negative_tests", []) if isinstance(verification.get("negative_tests"), list) else []
    rows: list[dict[str, Any]] = []
    for surface in impact.get("surfaces", []):
        name = surface["surface"]
        covered_by: list[str] = []
        output_evidence_required = ["command status is recorded", "output signal is classified"]
        blockers: list[str] = []
        if name == "source_behavior":
            covered_by.extend(check for check in checks if "behavior" in check)
            output_evidence_required.append("source behavior command output is linked to this surface")
        elif name == "test_surface":
            covered_by.extend(check for check in checks if "test" in check)
            if not covered_by:
                covered_by.append("changed-file syntax verification")
            output_evidence_required.append("test command output is linked to this surface")
        elif name == "public_api_contract":
            covered_by.extend(item for item in [*checks, *negative_tests] if "API" in item or "api" in item or "schema" in item or "compatibility" in item)
            output_evidence_required.append("API or schema command output is linked to this surface")
        elif name == "security_boundary":
            covered_by.extend(item for item in negative_tests if "input" in item or "path" in item or "auth" in item or "token" in item)
            output_evidence_required.append("negative boundary output or explicit blocker is linked to this surface")
        elif name == "runtime_configuration":
            covered_by.extend(item for item in negative_tests if "config" in item)
            output_evidence_required.append("runtime configuration output or explicit blocker is linked to this surface")
        elif name == "data_compatibility":
            covered_by.extend(item for item in negative_tests if "round-trip" in item or "round trip" in item or "mixed records" in item)
            output_evidence_required.append("compatibility output or explicit blocker is linked to this surface")
        elif name == "concurrency_runtime":
            covered_by.extend(item for item in negative_tests if "parallel" in item or "retry" in item or "state" in item)
            output_evidence_required.append("parallel or retry output or explicit blocker is linked to this surface")
        elif name == "internal_architecture":
            covered_by.extend(item for item in checks if "dependency" in item or "behavior" in item)
            output_evidence_required.append("dependency or behavior output is linked to this surface")
        if not covered_by:
            blockers.append(f"no planned verification covers {name}")
        rows.append(
            {
                "surface": name,
                "risk": surface["risk"],
                "evidence_needed": surface["evidence_needed"],
                "covered_by": covered_by,
                "output_evidence_required": output_evidence_required,
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
    if "config_runtime" in kinds:
        must_have.append("runtime configuration evidence is present")
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
    expert_plan: dict[str, Any],
) -> dict[str, Any]:
    must_prove = list(problem["definition_of_done"])
    must_prove.extend(quality["must_have_evidence"])
    if verification["negative_tests"]:
        must_prove.append("required negative tests are present, executed, or explicitly blocked")
    if not surface_matrix["complete"]:
        must_prove.append("surface verification blockers are resolved or explicitly accepted")
    if expert_plan.get("required_for_expert_gate") is True:
        must_prove.append("expert quality plan tradeoffs, rollback, observability, and review checklist are satisfied or explicitly blocked")
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
            "Do rollback, observability, and review evidence match the task risk level?",
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
    expert_plan: dict[str, Any],
    playbook: dict[str, Any],
    change_control: dict[str, Any],
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
            "surface_package_matrix",
            "investigation_playbook",
            "survey_quality_gate",
            "acceptance_gates",
            "quality_bar",
            "acceptance_contract",
            "acceptance_trace_matrix",
            "constraint_trace_matrix",
            "assumption_register",
            "repo_survey_evidence",
            "work_breakdown",
            "impact_analysis",
            "execution_forecast",
            "expert_quality_plan",
            "implementation_work_packages",
            "worker_output_contract",
            "planning_review_gate",
            "change_control_plan",
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
        "expert_quality_level": expert_plan.get("level", "standard"),
        "expert_review_checklist": expert_plan.get("review_checklist", []),
        "change_control_invariants": change_control.get("protected_invariants", []),
        "change_control_post_change_proofs": change_control.get("post_change_proofs", []),
        "investigation_stages": [
            str(stage.get("stage") or "")
            for stage in playbook.get("read_stages", [])
            if isinstance(stage, dict) and stage.get("stage")
        ],
        "mutation_preconditions": [
            "implementation brief validates",
            "investigation playbook read stages are acknowledged",
            "change control plan protected invariants are acknowledged",
            "execution preflight passes",
            "candidate files are repo-relative existing non-symlink paths",
            "verification plan is attached to the worker report",
            "expert quality plan is attached for high-risk or cross-surface work",
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
    evidence_package = {
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
        "blocking_policy": [
            "block when repo survey has no candidate files",
            "block when explicit path hints are missing or unsafe",
        ],
        "handoff_criteria": ["candidate file decision is grounded in repo_survey.json"],
    }
    minimal_patch_package = {
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
        "blocking_policy": [
            "block when requested edit is outside allowed scope",
            "block when patch preflight fails",
            "block when the smallest coherent patch cannot satisfy the task contract",
        ],
        "handoff_criteria": ["worker_report.json lists changed files, blockers, and execution result"],
    }
    verification_package = {
        "id": "verification_evidence_package",
        "owner": "CodeBrigade",
        "purpose": "Prove each planned impact surface or return concrete blockers.",
        "impact_surfaces": [surface.get("surface", "") for surface in surfaces if isinstance(surface, dict) and surface.get("surface")],
        "read_scope": ["surface_verification_matrix", "required_verification", "acceptance_contract"],
        "edit_scope": [],
        "verification_scope": targeted_commands + negative_tests,
        "risk_controls": ["do not treat syntax-only checks as behavior proof"],
        "blocking_policy": [
            "block when planned verification cannot run and no explicit blocker is recorded",
            "block when high-risk surfaces have only partial executed evidence",
            "block when verification_contract_trace marks acceptance requirements as syntax_only, skipped, blocked, failed, planned_only, or missing",
        ],
        "handoff_criteria": [
            "verification_report.json names executed, skipped, failed, or blocked checks",
            "verification_contract_trace links acceptance requirements to behavior evidence or explicit blockers",
        ],
    }
    packages: list[dict[str, Any]] = [evidence_package]
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
                "blocking_policy": [
                    "block when old/new compatibility evidence is missing",
                    "block when public contract breakage is not explicitly accepted",
                ],
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
                "blocking_policy": [
                    "block when negative boundary evidence is missing",
                    "block when untrusted input flow is not identified",
                ],
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
                "blocking_policy": [
                    "block when runtime default or environment override behavior is unknown",
                    "block when missing/invalid config behavior is not proven or explicitly accepted",
                ],
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
                "blocking_policy": [
                    "block when shared-state boundary is unknown",
                    "block when remaining race risk is not named",
                ],
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
                "blocking_policy": [
                    "block when dependency impact is not mapped",
                    "block when behavior preservation cannot be verified",
                ],
                "handoff_criteria": ["refactor evidence shows behavior preservation and dependency impact"],
            }
        )
    packages.extend([minimal_patch_package, verification_package])
    package_ids = [package["id"] for package in packages]
    special_package_ids = [
        package_id
        for package_id in package_ids
        if package_id not in {"evidence_survey_package", "minimal_patch_package", "verification_evidence_package"}
    ]
    dependency_rows: list[dict[str, Any]] = []
    for package_id in package_ids:
        if package_id == "evidence_survey_package":
            depends_on: list[str] = []
            reason = "root package; establishes repository evidence before mutation"
        elif package_id in special_package_ids:
            depends_on = ["evidence_survey_package"]
            reason = "specialized boundary planning needs repository evidence before it can constrain edits"
        elif package_id == "minimal_patch_package":
            depends_on = ["evidence_survey_package", *special_package_ids]
            reason = "source mutation waits for repository evidence and all specialized boundary constraints"
        else:
            depends_on = [item for item in package_ids if item != "verification_evidence_package"]
            reason = "final verification waits for every evidence, boundary, and mutation package"
        dependency_rows.append(
            {
                "package_id": package_id,
                "depends_on": depends_on,
                "dependency_reason": reason,
            }
        )
    execution_batches = (
        [["evidence_survey_package"], special_package_ids, ["minimal_patch_package"], ["verification_evidence_package"]]
        if special_package_ids
        else [["evidence_survey_package"], ["minimal_patch_package"], ["verification_evidence_package"]]
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
        "review_order": package_ids,
        "package_dependency_graph": {
            "rows": dependency_rows,
            "root_packages": ["evidence_survey_package"],
            "terminal_packages": ["verification_evidence_package"],
            "parallelizable_after_survey": special_package_ids or ["minimal_patch_package"],
            "execution_batches": execution_batches,
            "complete": True,
            "blockers": [],
        },
        "global_handoff_criteria": [
            "each package is passed, blocked, or explicitly deferred",
            "package blockers are reflected in review_gate.json",
            "final report answers the original task rather than only package-local success",
            "package_dependency_graph dependencies are respected before source mutation or final verification",
        ],
        "risk_focus": [item.get("risk", "") for item in risk_items if isinstance(item, dict) and item.get("risk")],
        "definition_of_done": problem.get("definition_of_done", []),
    }


def surface_package_matrix(surface_matrix: dict[str, Any], work_packages: dict[str, Any]) -> dict[str, Any]:
    packages = work_packages.get("packages", []) if isinstance(work_packages.get("packages"), list) else []
    rows: list[dict[str, Any]] = []
    for surface_row in surface_matrix.get("rows", []):
        if not isinstance(surface_row, dict):
            continue
        surface = str(surface_row.get("surface") or "")
        package_ids = [
            str(package.get("id") or "")
            for package in packages
            if isinstance(package, dict)
            and surface
            and surface in [item for item in package.get("impact_surfaces", []) if isinstance(item, str)]
        ]
        blockers = [str(item) for item in surface_row.get("blockers", []) if isinstance(item, str)]
        if not package_ids:
            blockers.append(f"no implementation work package covers {surface}")
        rows.append(
            {
                "surface": surface,
                "risk": surface_row.get("risk", ""),
                "verification_evidence": surface_row.get("covered_by", []) if isinstance(surface_row.get("covered_by"), list) else [],
                "package_ids": package_ids,
                "blockers": blockers,
            }
        )
    blockers = [blocker for row in rows for blocker in row["blockers"]]
    return {
        "role": "RiskScribe",
        "rows": rows,
        "complete": not blockers,
        "blockers": blockers,
    }


def trace_surfaces_for_requirement(requirement: str, surfaces: list[str]) -> list[str]:
    lowered = requirement.lower()
    matched: list[str] = []
    rules = [
        ("security_boundary", ["security", "boundary", "bypass", "auth", "token", "input", "path"]),
        ("public_api_contract", ["api", "schema", "caller", "public", "contract", "response", "request"]),
        ("data_compatibility", ["compatibility", "legacy", "old", "new", "mixed", "data", "record"]),
        ("runtime_configuration", ["config", "runtime", "environment", "startup"]),
        ("concurrency_runtime", ["parallel", "retry", "race", "cache", "state", "concurrency"]),
        ("internal_architecture", ["architecture", "refactor", "dependency"]),
        ("test_surface", ["test", "verification", "pytest", "unittest"]),
    ]
    for surface, needles in rules:
        if surface in surfaces and any(needle in lowered for needle in needles):
            matched.append(surface)
    if "source_behavior" in surfaces and (
        not matched
        or any(needle in lowered for needle in ["behavior", "request", "task", "changed", "user-visible", "original"])
    ):
        matched.insert(0, "source_behavior")
    return list(dict.fromkeys(matched or surfaces[:1]))


def acceptance_trace_matrix(
    problem: dict[str, Any],
    quality: dict[str, Any],
    acceptance: dict[str, Any],
    verification: dict[str, Any],
    surface_matrix: dict[str, Any],
    work_packages: dict[str, Any],
) -> dict[str, Any]:
    surfaces = [
        str(row.get("surface") or "")
        for row in surface_matrix.get("rows", [])
        if isinstance(row, dict) and row.get("surface")
    ]
    package_ids_by_surface: dict[str, list[str]] = {}
    for package in work_packages.get("packages", []):
        if not isinstance(package, dict):
            continue
        package_id = str(package.get("id") or "")
        for surface in package.get("impact_surfaces", []):
            if isinstance(surface, str) and package_id:
                package_ids_by_surface.setdefault(surface, []).append(package_id)
    surface_evidence = {
        str(row.get("surface") or ""): row.get("covered_by", [])
        for row in surface_matrix.get("rows", [])
        if isinstance(row, dict) and row.get("surface")
    }
    requirement_sources: dict[str, list[str]] = {}
    for source, items in [
        ("problem_statement.definition_of_done", problem.get("definition_of_done", [])),
        ("quality_bar.must_have_evidence", quality.get("must_have_evidence", [])),
        ("acceptance_contract.must_prove", acceptance.get("must_prove", [])),
    ]:
        if not isinstance(items, list):
            continue
        for item in items:
            requirement = str(item)
            if requirement:
                requirement_sources.setdefault(requirement, []).append(source)
    definition_of_done_items = [
        str(item)
        for item in problem.get("definition_of_done", [])
        if isinstance(item, str) and item
    ]
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    fallback_packages = ["verification_evidence_package"] if "verification_evidence_package" in {
        str(package.get("id") or "")
        for package in work_packages.get("packages", [])
        if isinstance(package, dict)
    } else []
    for requirement, sources in requirement_sources.items():
        linked_surfaces = trace_surfaces_for_requirement(requirement, surfaces)
        package_ids = sorted(
            {
                package_id
                for surface in linked_surfaces
                for package_id in package_ids_by_surface.get(surface, [])
            }
        ) or fallback_packages
        planned_evidence = sorted(
            {
                str(item)
                for surface in linked_surfaces
                for item in surface_evidence.get(surface, [])
                if str(item)
            }
        )
        if not planned_evidence:
            planned_evidence = [str(command) for command in verification.get("targeted_commands", []) if str(command)]
        if "final report" in requirement.lower() and "final_report.md" not in planned_evidence:
            planned_evidence.append("final_report.md")
        status = "planned" if package_ids and planned_evidence else "blocked"
        if status == "blocked":
            blockers.append(f"acceptance requirement lacks trace evidence: {requirement}")
        rows.append(
            {
                "requirement": requirement,
                "source": sources,
                "linked_surfaces": linked_surfaces,
                "package_ids": package_ids,
                "planned_evidence": planned_evidence,
                "status": status,
            }
        )
    traced_definition_of_done = sorted(
        {
            str(row["requirement"])
            for row in rows
            if isinstance(row.get("source"), list)
            and "problem_statement.definition_of_done" in row["source"]
            and row.get("status") == "planned"
        }
    )
    missing_definition_of_done = sorted(
        item
        for item in definition_of_done_items
        if item not in traced_definition_of_done
    )
    if missing_definition_of_done:
        blockers.extend(
            f"definition_of_done lacks acceptance trace: {item}"
            for item in missing_definition_of_done
        )
    return {
        "role": "RiskScribe",
        "rows": rows,
        "row_count": len(rows),
        "definition_of_done_count": len(definition_of_done_items),
        "traced_definition_of_done_count": len(traced_definition_of_done),
        "definition_of_done_complete": not missing_definition_of_done and bool(definition_of_done_items),
        "missing_definition_of_done": missing_definition_of_done,
        "complete": not blockers and bool(rows),
        "blockers": blockers,
    }


def constraint_trace_matrix(
    problem: dict[str, Any],
    work_packages: dict[str, Any],
    acceptance_trace: dict[str, Any],
) -> dict[str, Any]:
    package_ids = [
        str(package.get("id") or "")
        for package in work_packages.get("packages", [])
        if isinstance(package, dict) and package.get("id")
    ]
    fallback_packages = [package_id for package_id in ["evidence_survey_package", "verification_evidence_package"] if package_id in package_ids]
    evidence_items = sorted(
        {
            str(item)
            for row in acceptance_trace.get("rows", [])
            if isinstance(row, dict)
            for item in row.get("planned_evidence", [])
            if str(item)
        }
    )
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    for constraint in problem.get("known_constraints", []):
        text = str(constraint)
        if not text:
            continue
        lowered = text.lower()
        linked_packages = [
            package_id
            for package_id in package_ids
            if (
                "verification" in package_id
                or ("source" in lowered and package_id == "minimal_patch_package")
                or ("public" in lowered and package_id == "compatibility_package")
                or ("test" in lowered and package_id == "verification_evidence_package")
                or ("repo" in lowered and package_id == "evidence_survey_package")
                or ("behavior" in lowered and package_id in {"minimal_patch_package", "verification_evidence_package"})
            )
        ] or fallback_packages or package_ids[:1]
        planned_evidence = evidence_items[:5] or ["verification_report.json", "final_report.md"]
        status = "planned" if linked_packages and planned_evidence else "blocked"
        if status == "blocked":
            blockers.append(f"constraint lacks trace evidence: {text}")
        rows.append(
            {
                "constraint": text,
                "source": "problem_statement.known_constraints",
                "package_ids": linked_packages,
                "planned_evidence": planned_evidence,
                "status": status,
            }
        )
    return {
        "role": "RiskScribe",
        "rows": rows,
        "row_count": len(rows),
        "complete": not blockers and bool(rows),
        "blockers": blockers,
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
    expert_plan: dict[str, Any] | None = None,
    change_control: dict[str, Any] | None = None,
    work_packages: dict[str, Any] | None = None,
    package_matrix: dict[str, Any] | None = None,
    acceptance_trace: dict[str, Any] | None = None,
    constraint_trace: dict[str, Any] | None = None,
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
    if triage["risk_level"] == "high":
        if not expert_plan or expert_plan.get("level") != "expert":
            blockers.append("high-risk task lacks expert quality plan")
        else:
            for key in ("tradeoff_register", "rollback_strategy", "observability_plan", "review_checklist", "escalation_policy"):
                if not expert_plan.get(key):
                    blockers.append(f"expert quality plan missing {key}")
    if change_control is None:
        blockers.append("change control plan is missing")
    else:
        for key in ("allowed_change_intents", "protected_invariants", "diff_review_questions", "rollback_triggers", "post_change_proofs"):
            if len(change_control.get(key, []) if isinstance(change_control.get(key), list) else []) < 3:
                blockers.append(f"change control plan missing {key}")
        if len(change_control.get("mutation_requires", []) if isinstance(change_control.get("mutation_requires"), list) else []) < 4:
            blockers.append("change control plan missing mutation_requires")
    if surface_matrix.get("complete") is False:
        blockers.extend(str(item) for item in surface_matrix.get("blockers", []))
    if package_matrix is not None and package_matrix.get("complete") is False:
        blockers.extend(str(item) for item in package_matrix.get("blockers", []))
    if acceptance_trace is None:
        blockers.append("acceptance trace matrix is missing")
    elif acceptance_trace.get("complete") is not True:
        blockers.extend(str(item) for item in acceptance_trace.get("blockers", []))
        if not acceptance_trace.get("blockers"):
            blockers.append("acceptance trace matrix is incomplete")
    if constraint_trace is None:
        blockers.append("constraint trace matrix is missing")
    elif constraint_trace.get("complete") is not True:
        blockers.extend(str(item) for item in constraint_trace.get("blockers", []))
        if not constraint_trace.get("blockers"):
            blockers.append("constraint trace matrix is incomplete")
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
            "expert quality planning",
            "change control planning",
            "implementation work package completeness",
            "acceptance evidence alignment",
            "acceptance traceability",
            "constraint traceability",
        ],
    }


def code_brigade_handoff(
    triage: dict[str, Any],
    verification: dict[str, Any],
    quality: dict[str, Any],
    work_packages: dict[str, Any],
    acceptance_trace: dict[str, Any],
    repair_plan: dict[str, Any],
    output_contract: dict[str, Any],
) -> dict[str, Any]:
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
        "package_review_order": work_packages.get("review_order", []),
        "package_dependency_graph": work_packages.get("package_dependency_graph", {}),
        "global_handoff_criteria": work_packages.get("global_handoff_criteria", []),
        "diagnostic_repair_plan": repair_plan,
        "worker_output_contract": output_contract,
        "acceptance_trace_required": acceptance_trace.get("complete") is True,
        "acceptance_trace_row_count": acceptance_trace.get("row_count", 0),
        "definition_of_done_trace_required": acceptance_trace.get("definition_of_done_complete") is True,
        "definition_of_done_count": acceptance_trace.get("definition_of_done_count", 0),
        "traced_definition_of_done_count": acceptance_trace.get("traced_definition_of_done_count", 0),
        "required_quality_evidence": quality["must_have_evidence"],
    }


def worker_output_contract(
    work_packages: dict[str, Any],
    acceptance_trace: dict[str, Any],
    constraint_trace: dict[str, Any],
    repair_plan: dict[str, Any],
) -> dict[str, Any]:
    packages = work_packages.get("packages") if isinstance(work_packages.get("packages"), list) else []
    trace_rows = acceptance_trace.get("rows") if isinstance(acceptance_trace.get("rows"), list) else []
    constraint_rows = constraint_trace.get("rows") if isinstance(constraint_trace.get("rows"), list) else []
    package_rows: list[dict[str, Any]] = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        package_id = str(package.get("id") or "")
        if not package_id:
            continue
        acceptance_evidence = sorted(
            {
                str(item)
                for row in trace_rows
                if isinstance(row, dict) and package_id in (row.get("package_ids") if isinstance(row.get("package_ids"), list) else [])
                for item in (row.get("planned_evidence") if isinstance(row.get("planned_evidence"), list) else [])
                if str(item)
            }
        )
        acceptance_requirements = sorted(
            {
                str(row.get("requirement") or "")
                for row in trace_rows
                if isinstance(row, dict)
                and package_id in (row.get("package_ids") if isinstance(row.get("package_ids"), list) else [])
                and row.get("requirement")
            }
        )
        constraint_evidence = sorted(
            {
                str(item)
                for row in constraint_rows
                if isinstance(row, dict) and package_id in (row.get("package_ids") if isinstance(row.get("package_ids"), list) else [])
                for item in (row.get("planned_evidence") if isinstance(row.get("planned_evidence"), list) else [])
                if str(item)
            }
        )
        if not acceptance_evidence:
            acceptance_evidence = ["worker_report.json", "verification_report.json"]
        if package_id == "verification_evidence_package" and "verification_report.contract_trace" not in acceptance_evidence:
            acceptance_evidence.append("verification_report.contract_trace")
        if not acceptance_requirements:
            acceptance_requirements = ["package must explain why no acceptance requirement maps directly to it"]
        package_rows.append(
            {
                "package_id": package_id,
                "required_status_field": "work_package_statuses[].status",
                "allowed_statuses": ["planned", "implemented", "blocked"],
                "required_evidence_source": "work_package_statuses[].evidence_source",
                "acceptance_requirements": acceptance_requirements,
                "acceptance_evidence": acceptance_evidence,
                "constraint_evidence": constraint_evidence,
                "blocker_contract": [
                    "blocked packages must name a concrete blocker",
                    "blocked packages must preserve dependency context",
                    "blocked verification packages must return command output or execution blocker",
                ],
            }
        )
    return {
        "role": "PlanningBrigade",
        "target": "CodeBrigade",
        "required_reports": [
            "worker_report.json",
            "verification_report.json",
            "verification_contract_trace",
            "review_gate.json",
            "final_report.md",
        ],
        "required_package_statuses": [row["package_id"] for row in package_rows],
        "package_result_contract": package_rows,
        "final_review_inputs": [
            "worker_report.work_package_statuses",
            "worker_report.changed_files",
            "verification_report.commands_executed",
            "verification_report.contract_trace",
            "review_gate.findings",
            "diagnostic_repair_request.json when verification fails",
        ],
        "failure_contract": [
            "return blocked status instead of claiming partial success",
            "name residual blockers in worker_report.notes",
            "queue diagnostic repair when verification output identifies a repo-local failure",
        ],
        "diagnostic_repair_required_when": repair_plan.get("stop_conditions", []) if isinstance(repair_plan.get("stop_conditions"), list) else [],
        "handoff_to": "CodeBrigade",
    }


def build_planning_packet(payload: dict[str, Any]) -> dict[str, Any]:
    task = task_text(payload)
    helpers = {
        "task_triage": task_triage,
        "problem_statement": problem_statement,
        "repo_survey_request": repo_survey_request,
        "assumption_register": assumption_register,
        "investigation_playbook": investigation_playbook,
        "dependency_map": dependency_map,
        "work_breakdown": work_breakdown,
        "impact_analysis": impact_analysis,
        "execution_forecast": execution_forecast,
        "expert_quality_plan": expert_quality_plan,
        "change_control_plan": change_control_plan,
        "design_options": design_options,
        "verification_strategy": verification_strategy,
        "diagnostic_repair_plan": diagnostic_repair_plan,
        "surface_verification_matrix": surface_verification_matrix,
        "risk_register": risk_register,
        "quality_bar": quality_bar,
        "acceptance_contract": acceptance_contract,
        "implementation_brief_blueprint": implementation_brief_blueprint,
        "implementation_work_packages": implementation_work_packages,
        "surface_package_matrix": surface_package_matrix,
        "acceptance_trace_matrix": acceptance_trace_matrix,
        "constraint_trace_matrix": constraint_trace_matrix,
        "worker_output_contract": worker_output_contract,
        "planning_review_gate": planning_review_gate,
        "code_brigade_handoff": code_brigade_handoff,
    }
    context: dict[str, Any] = {"payload": payload}
    role_trace: list[dict[str, Any]] = []
    for module in (task_triage_role, repo_surveyor, design_strategos, verification_architect):
        result = module.run(context if module is not task_triage_role else payload, helpers)
        outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
        context.update(outputs)
        role_trace.append(
            {
                "role": result.get("role"),
                "module": module.__name__,
                "outputs": sorted(outputs),
                "may_mutate_source": False,
            }
        )
    context["change_control_plan"] = design_strategos.finalize_change_control(context, helpers)
    role_trace[2]["outputs"] = sorted([*role_trace[2]["outputs"], "change_control_plan"])
    risk_result = risk_scribe.run(context, helpers)
    risk_outputs = risk_result.get("outputs") if isinstance(risk_result.get("outputs"), dict) else {}
    context.update(risk_outputs)
    role_trace.append(
        {
            "role": risk_result.get("role"),
            "module": risk_scribe.__name__,
            "outputs": sorted(risk_outputs),
            "may_mutate_source": False,
        }
    )
    return {
        "ok": bool(task),
        "contract_version": CONTRACT_VERSION,
        "worker": "PlanningBrigade",
        "kind": "ceraxia_planning_packet",
        "task": task,
        "roles_completed": ROLE_ORDER,
        "role_execution_trace": role_trace,
        "problem_statement": context["problem_statement"],
        "task_triage": context["task_triage"],
        "repo_survey_request": context["repo_survey_request"],
        "assumption_register": context["assumption_register"],
        "investigation_playbook": context["investigation_playbook"],
        "dependency_map": context["dependency_map"],
        "work_breakdown": context["work_breakdown"],
        "impact_analysis": context["impact_analysis"],
        "execution_forecast": context["execution_forecast"],
        "expert_quality_plan": context["expert_quality_plan"],
        "change_control_plan": context["change_control_plan"],
        "design_options": context["design_options"],
        "verification_strategy": context["verification_strategy"],
        "diagnostic_repair_plan": context["diagnostic_repair_plan"],
        "surface_verification_matrix": context["surface_verification_matrix"],
        "surface_package_matrix": context["surface_package_matrix"],
        "risk_register": context["risk_register"],
        "quality_bar": context["quality_bar"],
        "acceptance_contract": context["acceptance_contract"],
        "acceptance_trace_matrix": context["acceptance_trace_matrix"],
        "constraint_trace_matrix": context["constraint_trace_matrix"],
        "implementation_brief_blueprint": context["implementation_brief_blueprint"],
        "implementation_work_packages": context["implementation_work_packages"],
        "worker_output_contract": context["worker_output_contract"],
        "planning_review_gate": context["planning_review_gate"],
        "code_brigade_handoff": context["code_brigade_handoff"],
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
    parser.add_argument("--feedback-request", type=Path, help="Build a PlanningBrigade replan intake from Ceraxia planning_feedback_request.json.")
    parser.add_argument("--feedback-replan-packet", action="store_true", help="With --feedback-request, emit a new planning packet from the feedback replan payload.")
    parser.add_argument("--validate", action="store_true", help="Exit non-zero when the generated planning packet has contract problems.")
    args = parser.parse_args()
    if args.feedback_request:
        loaded = json.loads(args.feedback_request.read_text(encoding="utf-8"))
        intake = build_planning_feedback_intake(loaded if isinstance(loaded, dict) else {})
        if args.feedback_replan_packet:
            if intake["status"] == "blocked_invalid_request":
                print(json.dumps(intake, ensure_ascii=False, indent=2), file=sys.stderr)
                return 2
            packet = build_planning_packet(intake["replan_payload"])
            problems = validate_planning_packet(packet) if args.validate else []
            print(json.dumps(packet, ensure_ascii=False, indent=2))
            if problems:
                print(json.dumps({"ok": False, "validation_problems": problems}, ensure_ascii=False, indent=2), file=sys.stderr)
                return 2
            return 0
        print(json.dumps(intake, ensure_ascii=False, indent=2))
        return 2 if intake["status"] == "blocked_invalid_request" else 0
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
