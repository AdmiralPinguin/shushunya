"""Ceraxia's native leadership facade for the Skitarii coding warband.

Ceraxia makes one warband-level decision and delegates one mission. Repository
exploration, file selection, detailed planning, implementation, verification,
and repair belong exclusively to Skitarii.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ..native_code_run import (
    NATIVE_EXECUTION,
    build_native_code_contract,
    native_governor_plan,
    validate_native_code_contract,
)


def executable_client_action(task_id: str, action: dict[str, Any]) -> dict[str, Any]:
    """Turn a templated service action into an executable client request."""
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
    return {
        "kind": str(action.get("kind") or ""),
        "method": method,
        "path": path,
        "body": dict(action.get("body") if isinstance(action.get("body"), dict) else {}),
        "reason": str(action.get("reason") or ""),
    }


def classify_code_task(goal: str) -> dict[str, Any]:
    """Return leadership-level risk hints without inventing an implementation plan."""
    text = " ".join(str(goal or "").lower().split())
    signals = {
        "bugfix": ("fix", "bug", "repair", "исправ", "почин", "ошиб"),
        "new_feature": ("add ", "implement", "feature", "create", "добав", "созд"),
        "public_contract": ("api", "endpoint", "schema", "protocol", "contract"),
        "migration": ("migration", "migrate", "compatibility", "backward", "миграц", "совместим"),
        "security_boundary": ("security", "auth", "token", "secret", "permission", "безопас"),
        "multi_surface": ("multi-file", "several files", "несколько файлов", "architecture", "refactor"),
    }
    kinds = [name for name, needles in signals.items() if any(needle in text for needle in needles)]
    if not kinds:
        kinds = ["general_code_change"]
    score = 1
    score += 2 if "multi_surface" in kinds else 0
    score += 2 if "migration" in kinds else 0
    score += 1 if "public_contract" in kinds else 0
    score += 1 if "security_boundary" in kinds else 0
    score += 1 if len(re.findall(r"`[^`]+`", goal)) >= 4 else 0
    score += 1 if len(goal) > 1_500 else 0
    complexity = "high" if score >= 5 else ("medium" if score >= 3 else "low")
    return {
        "kinds": kinds,
        "complexity": complexity,
        "complexity_score": score,
        "workflow_mode": "native_skitarii_mission",
        "leadership_risks": [
            risk
            for risk, enabled in {
                "cross_surface_regression": "multi_surface" in kinds,
                "public_contract_regression": "public_contract" in kinds,
                "migration_compatibility": "migration" in kinds,
                "security_boundary": "security_boundary" in kinds,
            }.items()
            if enabled
        ],
    }


def patch_contract_capabilities() -> dict[str, Any]:
    """Describe the native result boundary, not a Ceraxia-authored patch plan."""
    return {
        "execution_backend": "SkitariiWarband",
        "result_kind": "skitarii_bridge_result",
        "planning_owner": "SkitariiWarband",
        "implementation_owner": "SkitariiWarband",
        "verification_owner": "SkitariiWarband",
        "repair_owner": "SkitariiWarband",
        "required_result_fields": [
            "ok",
            "phase",
            "status",
            "summary",
            "artifacts",
            "patch_stage",
            "ready_to_apply",
        ],
        "completion_gates": [
            "executable acceptance passed",
            (
                "private held-out verification passed, or an explicitly degraded "
                "independent public behavioural replay passed with actionable diagnostics"
            ),
            "repository mutations are staged or applied through the controlled patch gate",
        ],
        "safety_gates": [
            "Ceraxia directive is validated before repository access",
            "Skitarii owns file and command selection inside its sandbox",
            "unrelated user changes are preserved",
            (
                "incomplete candidate evidence creates an internal revision; only an "
                "unproven safety/cleanup boundary pauses execution"
            ),
        ],
    }


def _contract_payload(contract: Any) -> dict[str, Any]:
    if isinstance(contract, dict):
        return dict(contract)
    converter = getattr(contract, "to_dict", None)
    if callable(converter):
        payload = converter()
        if isinstance(payload, dict):
            return payload
    raise ValueError("native Ceraxia contract must be an object")


def oversight_plan(contract: Any) -> dict[str, Any]:
    """Return leadership boundaries for one mission; never a worker/file plan."""
    payload = validate_native_code_contract(_contract_payload(contract))
    return {
        "governor": "Ceraxia",
        "kind": "native_code_leadership_oversight",
        "execution": dict(NATIVE_EXECUTION),
        "leadership_scope": {
            "owns": [
                "mission intent",
                "priorities",
                "hard constraints",
                "success boundaries",
                "tradeoffs",
                "escalation conditions",
            ],
            "delegates": [
                "repository exploration",
                "file selection",
                "detailed planning",
                "implementation",
                "verification",
                "internal repair",
            ],
            "delegated_to": "SkitariiWarband",
        },
        "completion_criteria": list(payload["completion_criteria"]),
        "quality_gates": list(payload["quality_gates"]),
        "non_goals": list(payload["non_goals"]),
        "revision_policy": {
            "decision_owner": "Ceraxia",
            "execution_backend": "SkitariiWarband",
            "execution_step": "skitarii",
            "detailed_revision_plan_owner": "SkitariiWarband",
            "preserve_task_and_mission_identity": True,
            "ordinary_check_failure_is_terminal": False,
            "actionable_findings_required": True,
            "automatic_worker_revision_required": True,
            "blocked_only_for_external_impasse": True,
        },
        "reporting_policy": {
            "requires_executable_evidence": True,
            "requires_patch_gate_evidence": True,
            "requires_actionable_revision_findings": True,
            "requires_resume_condition_for_external_impasse": True,
        },
    }


def plan_actions(
    contract: dict[str, Any],
    ok: bool,
    errors: list[str],
    missing_workers: list[str],
    unavailable_workers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Preserve the governor action API while exposing the native backend gate."""
    task_id = str(contract.get("task_id") or "")
    blocked = list(errors or [])
    if missing_workers or unavailable_workers:
        blocked.append("SkitariiWarband backend is unavailable")
    ready = bool(ok) and not blocked
    if ready:
        next_action = {
            "kind": "prepare_run",
            "method": "POST",
            "endpoint": "POST /prepare_run",
            "body": {
                "task_id": task_id,
                "commander_order": "<same commander_order used for /plan>",
            },
            "reason": "Ceraxia leadership decision can be persisted as one native Skitarii mission",
        }
    else:
        next_action = {
            "kind": "inspect_capabilities",
            "method": "GET",
            "endpoint": "GET /capabilities",
            "body": {},
            "reason": blocked[0] if blocked else "native code plan failed validation",
        }
    return {
        "can_prepare_run": ready,
        "can_inspect_capabilities": True,
        "next_action": next_action,
    }


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
            "phase": "plan_ready" if ok else "plan_needs_input",
            "decision": {
                "can_prepare_run": bool(actions.get("can_prepare_run")),
                "can_inspect_capabilities": bool(actions.get("can_inspect_capabilities")),
                "delegated_to": "SkitariiWarband" if ok else "",
                "recommended_kind": str(next_action.get("kind") or ""),
                "recommended_endpoint": str(next_action.get("endpoint") or ""),
            },
            "display": {
                "headline": "Ceraxia leadership decision is ready" if ok else "Ceraxia decision needs attention",
                "detail": str(next_action.get("reason") or "Ceraxia can prepare one Skitarii mission"),
                "severity": "info" if ok else "warning",
                "task_id": task_id,
                "step_count": int(pipeline.get("step_count") or 0),
            },
            "next_action": next_action,
            "client_action": executable_client_action(task_id, next_action),
        },
    )
    return enriched


@dataclass(frozen=True)
class NativeContractView:
    """Attribute-compatible view used by existing service/gateway call sites."""

    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)

    @property
    def task_id(self) -> str:
        return str(self.payload["task_id"])

    @property
    def mission_id(self) -> str:
        return str(self.payload["mission_id"])

    @property
    def goal(self) -> str:
        return str(self.payload["goal"])

    @property
    def assigned_governor(self) -> str:
        return "Ceraxia"

    @property
    def non_goals(self) -> list[str]:
        return list(self.payload["non_goals"])

    @property
    def completion_criteria(self) -> list[str]:
        return list(self.payload["completion_criteria"])

    @property
    def quality_gates(self) -> list[str]:
        return list(self.payload["quality_gates"])

    @property
    def required_artifacts(self) -> list[str]:
        return []

@dataclass
class CeraxiaPlan:
    contract: NativeContractView
    commander_order: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        contract = validate_native_code_contract(self.contract.to_dict())
        validation_errors: list[str] = []
        try:
            protocol_plan = native_governor_plan(contract, self.commander_order)
        except ValueError as exc:
            validation_errors.append(str(exc))
            protocol_plan = {}
        ok = not validation_errors
        pipeline = {
            "kind": "code_task",
            "mode": "native_skitarii_mission",
            "authoritative": True,
            "step_count": 1,
            "required_workers": [],
            "active_execution_backend": "SkitariiWarband",
            "steps": [
                {
                    "step_id": "skitarii",
                    "worker": "SkitariiWarband",
                    "depends_on": [],
                    "expected_artifacts": [],
                },
            ],
        }
        task_profile = classify_code_task(contract["goal"])
        warband_briefs = [
            {
                "step_id": "skitarii",
                "worker": "SkitariiWarband",
                "brief": "Own detailed planning, implementation, verification, and internal repair.",
                "authority_boundary": "subordinate_warband_execution",
                "task_profile": task_profile,
            },
        ]
        return {
            "ok": ok,
            "governor": "Ceraxia",
            "contract": contract,
            "governor_plan": protocol_plan,
            "validation": {"ok": ok, "errors": validation_errors},
            "pipeline": pipeline,
            "task_profile": task_profile,
            "worker_specialization_briefs": warband_briefs,
            "patch_contract": patch_contract_capabilities(),
            "resolved_workers": {
                "SkitariiWarband": {
                    "name": "SkitariiWarband",
                    "port": 7200,
                    "role": "native coding warband",
                    "execution_backend": "SkitariiWarband",
                    "native": True,
                },
            },
            "missing_workers": [],
            "unavailable_workers": [],
            "oversight": oversight_plan(contract),
            "actions": plan_actions(contract, ok, validation_errors, [], []),
        }


def plan_code_task(
    user_task: str,
    task_id: str | None = None,
    *,
    mission_id: str = "",
    commander_order: dict[str, Any] | None = None,
) -> CeraxiaPlan:
    if commander_order and not mission_id:
        mission_id = str(commander_order.get("mission_id") or "")
    contract = build_native_code_contract(user_task, task_id, mission_id=mission_id)
    return CeraxiaPlan(NativeContractView(contract), commander_order=commander_order)


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Build a native Ceraxia-to-Skitarii leadership plan.")
    parser.add_argument("task", help="User task text")
    parser.add_argument("--task-id", default="", help="Stable task id")
    parser.add_argument("--mission-id", default="", help="Stable commander mission id")
    parser.add_argument("--run-dir", default="", help="Reserved for the authenticated Ceraxia service")
    args = parser.parse_args()
    if args.run_dir:
        parser.error("native run preparation requires a validated leadership directive from Ceraxia service")
    plan = plan_code_task(args.task, task_id=args.task_id or None, mission_id=args.mission_id)
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
