from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..contracts import TaskContract, build_code_task_contract, validate_task_contract_payload
from EyeOfTerror.common_protocol import governor_plan_from_contract, validate_protocol_payload
from ..pipeline import build_dispatch_packets, pipeline_status, write_pipeline_run
from ..registry import worker_by_name


REPO_ROOT = Path(__file__).resolve().parents[4]


def executable_client_action(task_id: str, action: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(action, dict) or not action:
        return {}
    method = str(action.get("method") or "").upper()
    endpoint = str(action.get("endpoint") or "")
    endpoint_method = ""
    path = endpoint
    if " " in endpoint:
        endpoint_method, path = endpoint.split(" ", 1)
        endpoint_method = endpoint_method.upper()
    method = method or endpoint_method
    if "{task_id}" in path:
        path = path.replace("{task_id}", quote(task_id, safe=""))
    body = action.get("body") if isinstance(action.get("body"), dict) else {}
    return {
        "kind": str(action.get("kind") or ""),
        "method": method,
        "path": path,
        "body": body,
        "reason": str(action.get("reason") or ""),
    }


def worker_metadata(path: str) -> dict[str, Any]:
    metadata_path = REPO_ROOT / path / "worker.json"
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def downstream_step_ids(contract: TaskContract, step_id: str) -> list[str]:
    downstream: list[str] = []
    seen: set[str] = set()
    changed = True
    while changed:
        changed = False
        for step in contract.worker_plan:
            if step.step_id in seen:
                continue
            if step_id in step.depends_on or any(dependency in seen for dependency in step.depends_on):
                seen.add(step.step_id)
                downstream.append(step.step_id)
                changed = True
    return downstream


def step_quality_checks(step_id: str) -> list[str]:
    checks_by_step = {
        "repository_survey": [
            "repository survey records dominant languages, likely entry points, and test surfaces",
            "survey excludes local runtime, model, cache, and generated artifact directories",
        ],
        "change_planning": [
            "change plan is scoped to the task goal and repository evidence",
            "plan records assumptions, risks, and candidate files before implementation",
        ],
        "implementation": [
            "patch manifest is auditable and separates intended edits from unresolved blockers",
            "implementation package must not silently mutate unrelated files",
        ],
        "verification": [
            "verification report names commands to run or explains why they are blocked",
            "test gaps are explicit and cannot be treated as success",
        ],
        "code_review": [
            "code review checks scope control, test evidence, and unsafe assumptions",
            "review returns ready, needs_revision, or blocked with focused revision steps",
        ],
        "finalize": [
            "final manifest lists deliverables, review status, blockers, and next safe action",
            "final package is ready only when review passes or blockers are explicit",
        ],
    }
    return checks_by_step.get(step_id, ["expected artifacts exist and satisfy the step purpose"])


def step_role_policy(step_id: str) -> dict[str, Any]:
    policies = {
        "repository_survey": {
            "role": "repository_mapper",
            "authority": "read_only_repository_mapping",
            "may_mutate_source": False,
            "required_evidence": ["repo_root", "dominant_extensions", "test_files", "python_symbols"],
            "forbidden_actions": ["source_mutation", "verification_claim_without_artifacts"],
        },
        "change_planning": {
            "role": "change_strategist",
            "authority": "scoped_plan_from_repository_evidence",
            "may_mutate_source": False,
            "required_evidence": ["candidate_files", "test_surface", "implementation_policy"],
            "forbidden_actions": ["source_mutation", "claiming_tests_passed_without_execution"],
        },
        "implementation": {
            "role": "patchwright",
            "authority": "scoped_source_mutation_from_patch_contract_or_safe_inference",
            "may_mutate_source": True,
            "required_evidence": ["patch_source", "operation_count", "changed_files", "rollback"],
            "forbidden_actions": ["shell_execution", "unsafe_overwrite", "duplicate_function_append"],
        },
        "verification": {
            "role": "verifier",
            "authority": "allowlisted_verification_and_narrow_repairs",
            "may_mutate_source": True,
            "required_evidence": ["verification_executed", "verification_blockers", "verification_repairs"],
            "forbidden_actions": ["shell_execution", "unallowlisted_command", "broad_unscoped_rewrite"],
        },
        "code_review": {
            "role": "critic",
            "authority": "read_only_package_review_and_revision_ordering",
            "may_mutate_source": False,
            "required_evidence": ["review_status", "findings", "revision_plan"],
            "forbidden_actions": ["source_mutation", "approving_without_verification_evidence"],
        },
        "finalize": {
            "role": "final_packager",
            "authority": "read_only_final_manifest_packaging",
            "may_mutate_source": False,
            "required_evidence": ["deliverables", "verification_summary", "next_safe_action"],
            "forbidden_actions": ["source_mutation", "hiding_blockers"],
        },
    }
    return policies.get(
        step_id,
        {
            "role": "generic_worker",
            "authority": "artifact_generation_only",
            "may_mutate_source": False,
            "required_evidence": ["expected_artifacts"],
            "forbidden_actions": ["unscoped_source_mutation"],
        },
    )


def classify_code_task(goal: str) -> dict[str, Any]:
    lowered = goal.lower()
    markers = {
        "explicit_patch": ["ceraxia_patch", "ceraxia_replace_in_file", "ceraxia_create_file", "ceraxia_files"],
        "test_repair": ["почини тест", "fix test", "pytest", "unittest", "assert", "traceback", "ошибка тест"],
        "api_contract": ["api", "endpoint", "contract", "schema", "http", "request", "response"],
        "multi_file": ["multi-file", "несколько файлов", "ceraxia_files", "files", "package", "module"],
        "repo_grade": ["repo-grade", "real repo", "8-15", "architecture", "architect", "migration", "refactor", "compatibility", "backward"],
        "architecture_change": ["architecture", "architect", "adr", "design decision", "refactor", "migration"],
        "new_feature": ["добавь", "add ", "implement", "feature", "создай", "create"],
        "bugfix": ["почини", "fix", "bug", "ошибка", "исправь", "repair"],
    }
    detected = [
        kind
        for kind, needles in markers.items()
        if any(needle in lowered for needle in needles)
    ]
    if not detected:
        detected = ["general_code_change"]
    complexity_score = 1
    if "multi_file" in detected:
        complexity_score += 2
    if "test_repair" in detected:
        complexity_score += 1
    if "api_contract" in detected:
        complexity_score += 1
    if "repo_grade" in detected:
        complexity_score += 2
    if "architecture_change" in detected:
        complexity_score += 2
    if len(re.findall(r"`[^`]+`", goal)) >= 4:
        complexity_score += 1
    if len(goal) > 1500:
        complexity_score += 1
    complexity = "high" if complexity_score >= 5 else ("medium" if complexity_score >= 3 else "low")
    workflow_mode = "repo_grade" if complexity == "high" or "repo_grade" in detected or "architecture_change" in detected else "focused_fix"
    required_governor_checks = [
        "repository survey must identify candidate source and test files before mutation",
        "implementation must expose patch_source, operation_count, changed_files, and rollback evidence",
        "verification must run allowlisted commands or record explicit blockers",
        "review must approve or produce a focused revision_plan",
        "final manifest must preserve execution evidence, blockers, and next_safe_action",
    ]
    if "test_repair" in detected:
        required_governor_checks.append("test repair tasks must preserve failing-test diagnostics and rerun the relevant command after repair")
    if "multi_file" in detected:
        required_governor_checks.append("multi-file tasks must keep changed-file scope evidence for every mutated file")
    if "api_contract" in detected:
        required_governor_checks.append("API/contract tasks must preserve public response shape and schema compatibility evidence")
    repo_grade_required_evidence: list[str] = []
    if workflow_mode == "repo_grade":
        repo_grade_required_evidence = [
            "architecture decision record with alternatives and tradeoffs",
            "impact matrix covering source, tests, docs, config, and compatibility surfaces where relevant",
            "focused verification for the changed behavior",
            "broad verification for repo-level regressions or an explicit blocker",
            "self-review decision record before final packaging",
            "PR-style final summary with changed files, verification, risks, and rollback notes",
        ]
        required_governor_checks.extend(repo_grade_required_evidence)
    return {
        "kinds": detected,
        "complexity": complexity,
        "complexity_score": complexity_score,
        "workflow_mode": workflow_mode,
        "repo_grade_required_evidence": repo_grade_required_evidence,
        "risk_flags": [
            flag
            for flag, enabled in {
                "multi_file_scope_drift": "multi_file" in detected,
                "test_diagnostic_required": "test_repair" in detected,
                "public_contract_regression": "api_contract" in detected,
                "architecture_drift": "architecture_change" in detected,
                "repo_grade_evidence_required": workflow_mode == "repo_grade",
                "natural_language_patch_inference": "explicit_patch" not in detected,
            }.items()
            if enabled
        ],
        "required_governor_checks": required_governor_checks,
    }


def worker_specialization_briefs(contract: TaskContract, task_profile: dict[str, Any]) -> list[dict[str, Any]]:
    by_step = {
        "repository_survey": {
            "brief": "Map the repository before anyone edits it.",
            "must_produce": ["repo_map", "candidate_files", "test_files", "recommended_read_order"],
            "handoff_question": "Which files should the strategist inspect first, and why?",
        },
        "change_planning": {
            "brief": "Convert repo evidence into a scoped implementation plan and architecture decision.",
            "must_produce": ["candidate file rationale", "test surface", "verification suggestions", "risk notes", "architecture decision record", "repo-grade workflow"],
            "handoff_question": "What is the narrowest safe patch path, and which architecture tradeoffs were rejected?",
        },
        "implementation": {
            "brief": "Apply only explicit or safely inferred scoped patch operations.",
            "must_produce": ["patch_source", "operation_count", "changed_files", "rollback"],
            "handoff_question": "What exactly changed, and what should verification run?",
        },
        "verification": {
            "brief": "Run allowlisted checks and perform only narrow repair loops.",
            "must_produce": ["executed commands", "focused verification", "broad verification or blocker", "repair_loop_state", "failed_commands", "candidate_source_paths"],
            "handoff_question": "Did checks pass after repair, or what blocks review?",
        },
        "code_review": {
            "brief": "Judge scope, architecture evidence, verification, and revision needs.",
            "must_produce": ["approved flag", "findings", "architecture review", "patch_scope_review", "revision_plan"],
            "handoff_question": "Can the final packager mark this ready?",
        },
        "finalize": {
            "brief": "Package final evidence for Warmaster and chat clients.",
            "must_produce": ["deliverables", "execution_report", "patch_package", "pr_summary", "next_safe_action", "blockers"],
            "handoff_question": "What should the operator or Warmaster do next?",
        },
    }
    briefs: list[dict[str, Any]] = []
    for step in contract.worker_plan:
        defaults = by_step.get(step.step_id, {})
        briefs.append(
            {
                "step_id": step.step_id,
                "worker": step.worker,
                "purpose": step.purpose,
                "brief": defaults.get("brief", step.purpose),
                "task_profile": task_profile,
                "must_produce": defaults.get("must_produce", step.expected_artifacts),
                "handoff_question": defaults.get("handoff_question", "What evidence should the next step consume?"),
                "authority_boundary": step_role_policy(step.step_id).get("authority", ""),
            }
        )
    return briefs


def step_quality_matrix(contract: TaskContract) -> list[dict[str, Any]]:
    final_steps = ["code_review", "finalize"]
    matrix: list[dict[str, Any]] = []
    for step in contract.worker_plan:
        downstream = downstream_step_ids(contract, step.step_id)
        rerun_targets = [step.step_id] + [item for item in downstream if item not in final_steps] + final_steps
        deduped_targets: list[str] = []
        for target in rerun_targets:
            if target not in deduped_targets:
                deduped_targets.append(target)
        matrix.append(
            {
                "step_id": step.step_id,
                "worker": step.worker,
                "required_inputs": [
                    artifact
                    for dependency in step.depends_on
                    for artifact in next((candidate.expected_artifacts for candidate in contract.worker_plan if candidate.step_id == dependency), [])
                ],
                "expected_artifacts": step.expected_artifacts,
                "checks": step_quality_checks(step.step_id),
                "role_policy": step_role_policy(step.step_id),
                "blockers": [
                    "missing expected artifact",
                    "artifact contradicts the task contract",
                    "worker hides test gaps or unsafe assumptions",
                ],
                "revision_targets": deduped_targets,
            }
        )
    return matrix


def patch_contract_capabilities() -> dict[str, Any]:
    return {
        "input_markers": [
            "CERAXIA_TARGET_REPO",
            "CERAXIA_PATCH",
            "CERAXIA_FILES",
            "CERAXIA_CREATE_FILE",
            "CERAXIA_FILE_CONTENT",
            "CERAXIA_REPLACE_IN_FILE",
            "CERAXIA_OLD",
            "CERAXIA_NEW",
            "CERAXIA_VERIFY",
        ],
        "operation_types": ["replace", "write_file", "append"],
        "synthesis_modes": [
            "explicit_json_patch",
            "single_file_create_marker",
            "single_file_replace_marker",
            "multi_file_json_marker",
            "natural_language_simple_replace",
            "natural_language_add_function",
            "test_inferred_return_mismatch",
            "test_inferred_missing_function",
            "test_inferred_arithmetic_return",
        ],
        "verification_allowlist": [
            "pytest",
            "python -m pytest",
            "python -m unittest",
            "python -m py_compile",
        ],
        "safety_gates": [
            "target paths must be relative and stay inside the target repository",
            "excluded runtime, cache, model, build, and VCS directories cannot be patched",
            "write_file requires overwrite=true when existing content differs",
            "write_file is idempotent when existing content already matches",
            "operation batches are atomic and roll back earlier mutations on failure",
            "verification commands run without a shell and must match the allowlist",
            "natural language replace inference requires explicit backtick-delimited path, old text, and new text",
            "natural language add-function inference requires explicit backtick-delimited path, function name, and safe return literal",
            "natural language add-function inference blocks duplicate Python function definitions",
            "test-inferred return mismatch mode requires exactly one import/assertEqual literal candidate and one simple source return literal",
            "test-inferred missing function mode requires exactly one import/assertEqual literal candidate",
            "test-inferred arithmetic return mode requires exactly one two-argument assertEqual arithmetic candidate",
        ],
        "repair_loops": [
            "expected_colon_py_compile",
            "assertion_return_mismatch_literal",
            "name_error_return_literal",
            "import_error_missing_function_literal",
        ],
        "repository_intelligence": [
            "python_symbol_extraction",
            "test_surface_mapping",
            "suggested_verification_commands",
        ],
    }


def oversight_plan(contract: TaskContract) -> dict[str, Any]:
    planned_step_ids = [step.step_id for step in contract.worker_plan]
    task_profile = classify_code_task(contract.goal)
    specialization_briefs = worker_specialization_briefs(contract, task_profile)
    artifacts_by_role = {
        "survey": [artifact for artifact in contract.required_artifacts if artifact.endswith("/repo_survey.json")],
        "plan": [artifact for artifact in contract.required_artifacts if artifact.endswith("/change_plan.md")],
        "patch": [artifact for artifact in contract.required_artifacts if artifact.endswith("/patch_manifest.json")],
        "verification": [artifact for artifact in contract.required_artifacts if artifact.endswith("/verification_report.json")],
        "review": [artifact for artifact in contract.required_artifacts if artifact.endswith("/code_review.json")],
        "final": [artifact for artifact in contract.required_artifacts if artifact.endswith("/final_manifest.json")],
    }
    handoffs = [
        {
            "from_step": step.step_id,
            "to_steps": [candidate.step_id for candidate in contract.worker_plan if step.step_id in candidate.depends_on],
            "artifacts": step.expected_artifacts,
        }
        for step in contract.worker_plan
    ]
    return {
        "governor": contract.assigned_governor,
        "kind": "code_task_oversight",
        "quality_gates": contract.quality_gates,
        "completion_criteria": contract.completion_criteria,
        "non_goals": contract.non_goals,
        "artifact_roles": artifacts_by_role,
        "handoffs": handoffs,
        "task_profile": task_profile,
        "worker_specialization_briefs": specialization_briefs,
        "step_quality_matrix": step_quality_matrix(contract),
        "final_review": {
            "critic_step": "code_review",
            "final_step": "finalize",
            "final_artifact": artifacts_by_role["final"][0] if artifacts_by_role["final"] else "",
            "deliverable_role": "final",
            "requires_critic_approval_or_blockers": True,
            "requires_gap_disclosure": True,
            "requires_evidence_trace": True,
        },
        "revision_policy": {
            "source_step": "code_review",
            "final_steps": ["code_review", "finalize"],
            "allowed_steps": planned_step_ids,
            "requires_downstream_rerun": True,
            "requires_focused_context": True,
            "requires_gap_disclosure": True,
        },
        "iteration_policy": {
            "controller": "WarmasterGateway",
            "recommended_endpoint": "POST /runs/{task_id}/start_research_loop_http",
            "max_revision_cycles": 3,
            "poll_endpoint": "GET /runs/{task_id}/orchestration?events_after=0",
            "auto_revision_triggers": [
                "code_review status is needs_revision or blocked",
                "verification_report has missing commands or failed checks",
                "patch_manifest is not auditable",
            ],
            "stop_conditions": [
                "final_manifest status is ready",
                "revision_plan is invalid",
                "revision plan fingerprint repeats without progress",
                "max_revision_cycles is reached",
                "external implementation authority or human input is required",
            ],
            "final_readiness_checks": [
                "review approval or explicit blockers are present",
                "verification commands or blockers are disclosed",
                "patch intent is traceable to task scope",
                "final package files exist and are readable",
            ],
        },
        "reporting_policy": {
            "requires_worker_briefs": True,
            "requires_execution_report": True,
            "requires_changed_file_scope_evidence": True,
            "requires_command_evidence": True,
            "requires_blocker_visibility": True,
        },
        "patch_contract": patch_contract_capabilities(),
    }


def plan_actions(contract: dict[str, Any], ok: bool, errors: list[str], missing_workers: list[str], unavailable_workers: list[dict[str, Any]]) -> dict[str, Any]:
    actions = {
        "can_prepare_run": ok,
        "can_inspect_capabilities": True,
    }
    if ok:
        next_action = {
            "kind": "prepare_run",
            "method": "POST",
            "endpoint": "POST /prepare_run",
            "body": {
                "task": str(contract.get("goal") or ""),
                "task_id": str(contract.get("task_id") or ""),
            },
            "reason": "governor plan is valid and required workers are available",
        }
    else:
        reason = "governor plan failed validation"
        if missing_workers or unavailable_workers:
            reason = "required Mechanicum workers are missing or unavailable"
        elif errors:
            reason = "task contract failed validation"
        next_action = {
            "kind": "inspect_capabilities",
            "method": "GET",
            "endpoint": "GET /capabilities",
            "body": {},
            "reason": reason,
        }
    actions["next_action"] = next_action
    return actions


def payload_with_plan_view(payload: dict[str, Any]) -> dict[str, Any]:
    actions = payload.get("actions") if isinstance(payload.get("actions"), dict) else {}
    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
    contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
    pipeline = payload.get("pipeline") if isinstance(payload.get("pipeline"), dict) else {}
    task_id = str(contract.get("task_id") or "")
    ok = bool(payload.get("ok"))
    enriched = dict(payload)
    enriched.update(
        {
            "phase": "plan_ready" if ok else "plan_blocked",
            "decision": {
                "can_prepare_run": bool(actions.get("can_prepare_run")),
                "can_inspect_capabilities": bool(actions.get("can_inspect_capabilities")),
                "recommended_kind": str(next_action.get("kind") or ""),
                "recommended_endpoint": str(next_action.get("endpoint") or ""),
            },
            "display": {
                "headline": "Code plan is ready" if ok else "Code plan needs attention",
                "detail": str(next_action.get("reason") or "Ceraxia can prepare the run"),
                "severity": "info" if ok else "warning",
                "task_id": task_id,
                "step_count": int(pipeline.get("step_count") or 0),
            },
            "next_action": next_action,
            "client_action": executable_client_action(task_id, next_action),
        }
    )
    return enriched


@dataclass
class CeraxiaPlan:
    contract: TaskContract

    def to_dict(self) -> dict[str, Any]:
        contract = self.contract.to_dict()
        task_profile = classify_code_task(self.contract.goal)
        specialization_briefs = worker_specialization_briefs(self.contract, task_profile)
        validation_errors = validate_task_contract_payload(contract)
        missing_workers: list[str] = []
        unavailable_workers: list[dict[str, Any]] = []
        resolved_workers: dict[str, Any] = {}
        for step in self.contract.worker_plan:
            worker = worker_by_name(step.worker)
            if worker is None:
                missing_workers.append(step.worker)
                continue
            worker_payload = worker.to_dict()
            metadata = worker_metadata(worker.path)
            if metadata:
                worker_payload["status"] = metadata.get("status", "")
                worker_payload["capabilities"] = metadata.get("capabilities", [])
                if isinstance(metadata.get("role_contract"), dict):
                    worker_payload["role_contract"] = metadata["role_contract"]
            resolved_workers[step.worker] = worker_payload
            if metadata.get("status") == "planned" and step.worker not in {item.get("name") for item in unavailable_workers}:
                unavailable_workers.append(
                    {
                        "name": step.worker,
                        "status": "planned",
                        "port": worker.port,
                        "role": worker.role,
                        "path": worker.path,
                    }
                )
        ok = not missing_workers and not unavailable_workers and not validation_errors
        protocol_plan = governor_plan_from_contract(f"mission-{self.contract.task_id}", contract)
        validate_protocol_payload(protocol_plan, expected_type="governor_plan")
        return {
            "ok": ok,
            "governor": "Ceraxia",
            "contract": contract,
            "governor_plan": protocol_plan,
            "validation": {"ok": not validation_errors, "errors": validation_errors},
            "pipeline": pipeline_status(self.contract, build_dispatch_packets(self.contract)),
            "task_profile": task_profile,
            "worker_specialization_briefs": specialization_briefs,
            "patch_contract": patch_contract_capabilities(),
            "resolved_workers": resolved_workers,
            "missing_workers": missing_workers,
            "unavailable_workers": unavailable_workers,
            "oversight": oversight_plan(self.contract),
            "actions": plan_actions(contract, ok, validation_errors, missing_workers, unavailable_workers),
        }


def plan_code_task(user_task: str, task_id: str | None = None) -> CeraxiaPlan:
    return CeraxiaPlan(contract=build_code_task_contract(user_task, task_id=task_id))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build a Ceraxia code task plan.")
    parser.add_argument("task", help="User task text")
    parser.add_argument("--task-id", default="", help="Stable task id")
    parser.add_argument("--run-dir", default="", help="Write contract and dispatch packets to this directory")
    args = parser.parse_args()
    plan = plan_code_task(args.task, task_id=args.task_id or None)
    if args.run_dir:
        status = write_pipeline_run(plan.contract, Path(args.run_dir), oversight=oversight_plan(plan.contract))
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
