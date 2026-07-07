"""Warmaster campaign orchestration above ordinary single-governor runs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .artifacts import artifact_status
from .command_text import task_text_from_commander_order
from .gateway_util import valid_task_id, validate_service_host
from .ledger import TaskLedger, now_iso
from .mission_control import link_run_to_mission, mission_protocol_audit, open_mission
from .orchestrator import cancel_http_worker_tasks, research_loop_run
from .routing import route_message
from .run_package import load_ledger_dict
from .run_state import run_summary
from .task_prepare import prepare_task

CAMPAIGN_DIR = "_campaigns"
PLAN_FILE = "campaign_plan.json"
STATE_FILE = "campaign_state.json"
FINAL_REPORT_FILE = "campaign_final_report.json"
TERMINAL_CAMPAIGN_STATUSES = {"completed", "blocked", "cancelled"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "blocked", "corrupt"}


def generated_campaign_id(message: str) -> str:
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:12]
    return f"campaign-{digest}"


def resolve_campaign_id(message: str, campaign_id: str | None = None) -> str:
    resolved = (campaign_id or "").strip() or generated_campaign_id(message)
    if not valid_task_id(resolved):
        raise ValueError("invalid campaign_id")
    return resolved


def campaigns_root(run_root: Path) -> Path:
    return run_root / CAMPAIGN_DIR


def campaign_dir(run_root: Path, campaign_id: str) -> Path:
    if not valid_task_id(campaign_id):
        raise ValueError("invalid campaign_id")
    return campaigns_root(run_root) / campaign_id


def read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def campaign_client_action(campaign_id: str, method: str, endpoint: str, body: dict[str, Any] | None = None, reason: str = "") -> dict[str, Any]:
    path = endpoint
    for prefix in ("GET ", "POST "):
        if path.startswith(prefix):
            path = path[len(prefix):]
    path = path.replace("{campaign_id}", campaign_id)
    return {
        "method": method,
        "path": path,
        "body": body or {},
        "reason": reason,
    }


def campaign_action(campaign_id: str, kind: str, method: str, endpoint: str, body: dict[str, Any] | None = None, reason: str = "") -> dict[str, Any]:
    action = {
        "kind": kind,
        "method": method,
        "endpoint": endpoint,
        "body": body or {},
        "reason": reason,
    }
    action["client_action"] = campaign_client_action(campaign_id, method, endpoint, body, reason)
    return action


def campaign_task_text(original_task: str, subrun_id: str, handoff_path: str = "") -> str:
    if subrun_id == "research":
        return (
            "Исследуй источники, требования и фактическую основу для большой смешанной задачи. "
            "Собери evidence-grounded brief, карту источников, ограничения, риски и критерии приемки "
            "для следующей кодовой/реализационной бригады. Исходная задача: "
            f"{original_task}"
        )
    if subrun_id == "implementation":
        handoff_clause = (
            f" Перед началом прочитай handoff Вармастера: {handoff_path}. "
            if handoff_path
            else " Перед началом дождись handoff Вармастера от исследовательской подзадачи. "
        )
        return (
            "Реализуй кодовую/техническую часть большой смешанной задачи как senior engineer."
            f"{handoff_clause}"
            "Не игнорируй ограничения, критерии приемки и артефакты из handoff; в финальном отчете явно "
            "укажи, какие handoff-входы использованы. Исходная задача: "
            f"{original_task}"
        )
    raise ValueError(f"unknown campaign subrun: {subrun_id}")


def decompose_task(message: str, campaign_id: str | None = None, route: dict[str, Any] | None = None) -> dict[str, Any]:
    goal = message.strip()
    if not goal:
        raise ValueError("message is required")
    resolved_campaign_id = resolve_campaign_id(goal, campaign_id)
    route_payload = route if isinstance(route, dict) else route_message(goal).to_dict()
    research_task_id = f"{resolved_campaign_id}-research"
    implementation_task_id = f"{resolved_campaign_id}-code"
    plan = {
        "schema_version": 1,
        "campaign_id": resolved_campaign_id,
        "original_task": goal,
        "status": "planned",
        "decomposition_strategy": "sequential_research_then_implementation",
        "route": route_payload,
        "global_acceptance_criteria": [
            "research subrun completes with source-backed brief and final manifest",
            "implementation subrun is created only after research handoff is available",
            "implementation task references the campaign handoff input",
            "all required subruns complete without unresolved blockers",
            "Warmaster final review verifies original task coverage and handoff usage",
        ],
        "subruns": [
            {
                "id": "research",
                "task_id": research_task_id,
                "governor": "IskandarKhayon",
                "kind": "research",
                "depends_on": [],
                "task": campaign_task_text(goal, "research"),
                "expected_artifacts": [
                    "/work/research/research_corpus.json",
                    "/work/research/source_map.json",
                    "/work/research/synthesis_plan.json",
                    "/work/research/reconstruction_ru.md",
                    "/work/research/final_manifest.json",
                ],
                "produces_handoffs": ["research_to_implementation"],
            },
            {
                "id": "implementation",
                "task_id": implementation_task_id,
                "governor": "Ceraxia",
                "kind": "code",
                "depends_on": ["research"],
                "task": campaign_task_text(goal, "implementation", "{handoff_path}"),
                "expected_artifacts": [
                    "/work/ceraxia/repo_survey.json",
                    "/work/ceraxia/change_plan.md",
                    "/work/ceraxia/patch_manifest.json",
                    "/work/ceraxia/verification_report.json",
                    "/work/ceraxia/code_review.json",
                    "/work/ceraxia/final_manifest.json",
                ],
                "requires_handoffs": ["research_to_implementation"],
            },
        ],
        "handoffs": [
            {
                "id": "research_to_implementation",
                "from_subrun": "research",
                "to_subrun": "implementation",
                "required_artifacts": [
                    "final_manifest.json",
                    "research_corpus.json",
                    "source_map.json",
                    "synthesis_plan.json",
                    "reconstruction_ru.md",
                ],
                "summary_required": True,
                "acceptance_criteria": [
                    "handoff records source run status and final manifest summary",
                    "handoff lists available and missing expected artifacts",
                    "implementation subrun task receives handoff path before creation",
                ],
            }
        ],
    }
    return plan


def validate_campaign_plan(plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if plan.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    campaign_id = str(plan.get("campaign_id") or "")
    if not valid_task_id(campaign_id):
        errors.append("campaign_id is invalid")
    if not str(plan.get("original_task") or "").strip():
        errors.append("original_task is required")
    subruns = plan.get("subruns")
    if not isinstance(subruns, list) or not subruns:
        errors.append("subruns must be a non-empty list")
        subruns = []
    seen_ids: set[str] = set()
    for index, subrun in enumerate(subruns):
        if not isinstance(subrun, dict):
            errors.append(f"subruns[{index}] must be an object")
            continue
        subrun_id = str(subrun.get("id") or "")
        if not subrun_id:
            errors.append(f"subruns[{index}].id is required")
        elif subrun_id in seen_ids:
            errors.append(f"duplicate subrun id: {subrun_id}")
        seen_ids.add(subrun_id)
        task_id = str(subrun.get("task_id") or "")
        if not valid_task_id(task_id):
            errors.append(f"subrun {subrun_id or index} task_id is invalid")
        if not str(subrun.get("governor") or ""):
            errors.append(f"subrun {subrun_id or index} governor is required")
        if not str(subrun.get("task") or "").strip():
            errors.append(f"subrun {subrun_id or index} task is required")
        depends_on = subrun.get("depends_on")
        if not isinstance(depends_on, list):
            errors.append(f"subrun {subrun_id or index} depends_on must be a list")
    for subrun in subruns:
        if not isinstance(subrun, dict):
            continue
        for dependency in subrun.get("depends_on", []) if isinstance(subrun.get("depends_on"), list) else []:
            if str(dependency) not in seen_ids:
                errors.append(f"subrun {subrun.get('id')} has unknown dependency: {dependency}")
    handoffs = plan.get("handoffs")
    if not isinstance(handoffs, list):
        errors.append("handoffs must be a list")
        handoffs = []
    for index, handoff in enumerate(handoffs):
        if not isinstance(handoff, dict):
            errors.append(f"handoffs[{index}] must be an object")
            continue
        handoff_id = str(handoff.get("id") or "")
        if not handoff_id:
            errors.append(f"handoffs[{index}].id is required")
        if str(handoff.get("from_subrun") or "") not in seen_ids:
            errors.append(f"handoff {handoff_id or index} from_subrun is unknown")
        if str(handoff.get("to_subrun") or "") not in seen_ids:
            errors.append(f"handoff {handoff_id or index} to_subrun is unknown")
        required = handoff.get("required_artifacts")
        if not isinstance(required, list) or not required:
            errors.append(f"handoff {handoff_id or index} required_artifacts must be non-empty")
    return errors


def initial_campaign_state(plan: dict[str, Any]) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "schema_version": 1,
        "campaign_id": plan["campaign_id"],
        "status": "planned",
        "phase": "prepared",
        "original_task": plan["original_task"],
        "created_at": timestamp,
        "updated_at": timestamp,
        "subruns": {
            str(subrun["id"]): {
                "id": str(subrun["id"]),
                "task_id": str(subrun["task_id"]),
                "governor": str(subrun["governor"]),
                "kind": str(subrun.get("kind") or ""),
                "depends_on": subrun.get("depends_on", []),
                "status": "planned",
                "created": False,
                "run_dir": "",
                "last_run_status": "",
            }
            for subrun in plan.get("subruns", [])
            if isinstance(subrun, dict) and subrun.get("id")
        },
        "handoffs": {
            str(handoff["id"]): {
                "id": str(handoff["id"]),
                "from_subrun": str(handoff["from_subrun"]),
                "to_subrun": str(handoff["to_subrun"]),
                "status": "pending",
                "path": "",
                "checks": [],
            }
            for handoff in plan.get("handoffs", [])
            if isinstance(handoff, dict) and handoff.get("id")
        },
        "events": [{"at": timestamp, "type": "campaign_prepared", "payload": {"campaign_id": plan["campaign_id"]}}],
    }


def load_campaign_plan(run_root: Path, campaign_id: str) -> dict[str, Any]:
    path = campaign_dir(run_root, campaign_id) / PLAN_FILE
    if not path.exists():
        raise FileNotFoundError(f"campaign not found: {campaign_id}")
    return read_json_object(path)


def load_campaign_state(run_root: Path, campaign_id: str) -> dict[str, Any]:
    path = campaign_dir(run_root, campaign_id) / STATE_FILE
    if not path.exists():
        raise FileNotFoundError(f"campaign state not found: {campaign_id}")
    return read_json_object(path)


def save_campaign_state(run_root: Path, campaign_id: str, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    write_json(campaign_dir(run_root, campaign_id) / STATE_FILE, state)


def record_campaign_event(state: dict[str, Any], event_type: str, payload: dict[str, Any] | None = None) -> None:
    state.setdefault("events", []).append({"at": now_iso(), "type": event_type, "payload": payload or {}})


def campaign_preflight(message: str, campaign_id: str | None = None) -> dict[str, Any]:
    route = route_message(message).to_dict()
    plan = decompose_task(message, campaign_id=campaign_id, route=route)
    errors = validate_campaign_plan(plan)
    ok = not errors and bool(route.get("ok")) and bool(route.get("requires_decomposition"))
    resolved_id = str(plan.get("campaign_id") or "")
    next_action = (
        campaign_action(
            resolved_id,
            "create_campaign",
            "POST",
            "POST /campaign",
            {"message": message, "campaign_id": resolved_id},
            "campaign decomposition preflight passed",
        )
        if ok
        else campaign_action(
            resolved_id,
            "inspect_task_route",
            "POST",
            "POST /task_preflight",
            {"message": message, "task_id": resolved_id},
            "task does not currently require campaign decomposition",
        )
    )
    return {
        "ok": ok,
        "phase": "campaign_preflight",
        "campaign_id": resolved_id,
        "route": route,
        "plan": plan,
        "validation_errors": errors,
        "error_code": "" if ok else ("invalid_campaign_plan" if errors else "campaign_not_required"),
        "next_action": next_action,
        "client_action": next_action.get("client_action", {}),
    }


def prepare_campaign(run_root: Path, message: str, campaign_id: str | None = None, force: bool = False) -> dict[str, Any]:
    preflight = campaign_preflight(message, campaign_id)
    if not preflight.get("ok"):
        return preflight
    resolved_id = str(preflight["campaign_id"])
    target_dir = campaign_dir(run_root, resolved_id)
    if target_dir.exists() and not force:
        next_action = campaign_action(
            resolved_id,
            "inspect_campaign",
            "GET",
            "GET /campaigns/{campaign_id}",
            {},
            "campaign_id already exists",
        )
        return {
            "ok": False,
            "phase": "campaign_exists",
            "campaign_id": resolved_id,
            "error_code": "campaign_exists",
            "error": "campaign already exists",
            "next_action": next_action,
            "client_action": next_action.get("client_action", {}),
        }
    warmaster_root = Path(__file__).resolve().parents[1]
    mission = open_mission(warmaster_root, message, resolved_id, source_channel="campaign")
    if not mission.get("ok"):
        return {
            "ok": False,
            "phase": "campaign_commander_intake_failed",
            "campaign_id": resolved_id,
            "mission": mission,
            "error_code": str(mission.get("error_code") or "commander_intake_failed"),
            "error": str(mission.get("error") or "Warmaster commander intake failed"),
        }
    target_dir.mkdir(parents=True, exist_ok=True)
    plan = preflight["plan"]
    plan["mission_id"] = str(mission.get("mission_id") or "")
    plan["commander_order"] = mission.get("commander_order") if isinstance(mission.get("commander_order"), dict) else {}
    state = initial_campaign_state(plan)
    state["mission_id"] = str(mission.get("mission_id") or "")
    state["mission_dir"] = str(mission.get("mission_dir") or "")
    write_json(
        target_dir / "mission_ref.json",
        {
            "mission_id": str(mission.get("mission_id") or ""),
            "mission_dir": str(mission.get("mission_dir") or ""),
            "assigned_governor": str((mission.get("commander_order") or {}).get("to") or ""),
        },
    )
    write_json(target_dir / PLAN_FILE, plan)
    write_json(target_dir / STATE_FILE, state)
    next_action = campaign_action(
        resolved_id,
        "start_campaign",
        "POST",
        "POST /campaigns/{campaign_id}/start",
        {},
        "campaign is prepared and ready to start",
    )
    return {
        "ok": True,
        "phase": "campaign_prepared",
        "campaign_id": resolved_id,
        "campaign_dir": str(target_dir),
        "mission": {
            "mission_id": str(mission.get("mission_id") or ""),
            "assigned_governor": str((mission.get("commander_order") or {}).get("to") or ""),
            "mission_dir": str(mission.get("mission_dir") or ""),
        },
        "plan": plan,
        "state": state,
        "next_action": next_action,
        "client_action": next_action.get("client_action", {}),
    }


def subrun_plan(plan: dict[str, Any], subrun_id: str) -> dict[str, Any]:
    for item in plan.get("subruns", []) if isinstance(plan.get("subruns"), list) else []:
        if isinstance(item, dict) and str(item.get("id") or "") == subrun_id:
            return item
    raise ValueError(f"unknown subrun: {subrun_id}")


def dependencies_completed(state: dict[str, Any], subrun: dict[str, Any]) -> bool:
    subruns = state.get("subruns") if isinstance(state.get("subruns"), dict) else {}
    for dependency in subrun.get("depends_on", []) if isinstance(subrun.get("depends_on"), list) else []:
        dependency_state = subruns.get(str(dependency)) if isinstance(subruns, dict) else {}
        protocol_completion = dependency_state.get("protocol_completion") if isinstance(dependency_state, dict) else {}
        if (
            not isinstance(dependency_state, dict)
            or dependency_state.get("status") != "completed"
            or not isinstance(protocol_completion, dict)
            or protocol_completion.get("ok") is not True
        ):
            return False
    return True


def required_handoffs_ready(state: dict[str, Any], subrun: dict[str, Any]) -> bool:
    handoffs = state.get("handoffs") if isinstance(state.get("handoffs"), dict) else {}
    for handoff_id in subrun.get("requires_handoffs", []) if isinstance(subrun.get("requires_handoffs"), list) else []:
        handoff_state = handoffs.get(str(handoff_id)) if isinstance(handoffs, dict) else {}
        if not isinstance(handoff_state, dict) or handoff_state.get("status") != "ready":
            return False
    return True


def run_artifact_names(summary: dict[str, Any]) -> set[str]:
    result = summary.get("result") if isinstance(summary.get("result"), dict) else {}
    result_artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
    names = {Path(str(path)).name for path in result_artifacts}
    ledger_path = Path(str(summary.get("run_dir") or "")) / "task_ledger.json"
    ledger, _ = load_ledger_dict(ledger_path)
    for item in artifact_status(ledger).get("artifacts", []):
        if isinstance(item, dict) and item.get("path"):
            names.add(Path(str(item["path"])).name)
    return names


def subrun_protocol_completion(run_root: Path, task_id: str) -> dict[str, Any]:
    run_dir = run_root / task_id
    ref = read_json_object(run_dir / "mission_ref.json") if (run_dir / "mission_ref.json").exists() else {}
    mission_id = str(ref.get("mission_id") or "")
    mission_dir_text = str(ref.get("mission_dir") or "")
    if not mission_id or not mission_dir_text:
        return {
            "ok": False,
            "mission_id": mission_id,
            "mission_dir": mission_dir_text,
            "errors": ["mission_ref.json must include mission_id and mission_dir"],
        }
    mission_dir = Path(mission_dir_text)
    if not mission_dir.exists():
        return {
            "ok": False,
            "mission_id": mission_id,
            "mission_dir": mission_dir_text,
            "errors": ["mission_dir does not exist"],
        }
    audit = mission_protocol_audit(mission_dir)
    mission = read_json_object(mission_dir / "mission.json") if (mission_dir / "mission.json").exists() else {}
    acceptance = read_json_object(mission_dir / "acceptance_review.json") if (mission_dir / "acceptance_review.json").exists() else {}
    final = read_json_object(mission_dir / "final_response.json") if (mission_dir / "final_response.json").exists() else {}
    errors = list(audit.get("errors") if isinstance(audit.get("errors"), list) else [])
    if mission.get("status") != "completed":
        errors.append(f"mission status must be completed, got {mission.get('status') or 'missing'}")
    if acceptance.get("type") != "acceptance_review" or acceptance.get("accepted") is not True:
        errors.append("accepted acceptance_review.json is required")
    if acceptance and acceptance.get("reviewer") != "Warmaster":
        errors.append("acceptance_review reviewer must be Warmaster")
    if final.get("type") != "final_response" or not str(final.get("answer") or "").strip():
        errors.append("final_response.json with answer is required")
    return {
        "ok": not errors and bool(audit.get("ok")),
        "mission_id": mission_id,
        "mission_dir": mission_dir_text,
        "mission_status": str(mission.get("status") or ""),
        "accepted": acceptance.get("accepted") is True,
        "reviewer": str(acceptance.get("reviewer") or ""),
        "has_final_response": bool(final),
        "audit_ok": bool(audit.get("ok")),
        "audit_error_count": len(audit.get("errors") if isinstance(audit.get("errors"), list) else []),
        "errors": errors,
    }


def create_handoff(run_root: Path, campaign_id: str, plan: dict[str, Any], state: dict[str, Any], handoff_id: str) -> dict[str, Any]:
    handoff_plan = next(
        (item for item in plan.get("handoffs", []) if isinstance(item, dict) and item.get("id") == handoff_id),
        None,
    )
    if not isinstance(handoff_plan, dict):
        raise ValueError(f"unknown handoff: {handoff_id}")
    source_id = str(handoff_plan["from_subrun"])
    target_id = str(handoff_plan["to_subrun"])
    source_state = state.get("subruns", {}).get(source_id, {}) if isinstance(state.get("subruns"), dict) else {}
    source_task_id = str(source_state.get("task_id") or "")
    source_summary = run_summary(run_root / source_task_id)
    source_protocol = subrun_protocol_completion(run_root, source_task_id) if source_task_id else {"ok": False, "errors": ["source task_id is missing"]}
    available_names = run_artifact_names(source_summary)
    required_names = [str(item) for item in handoff_plan.get("required_artifacts", []) if isinstance(item, str)]
    missing = [name for name in required_names if name not in available_names]
    payload = {
        "schema_version": 1,
        "handoff_id": handoff_id,
        "campaign_id": campaign_id,
        "from_subrun": source_id,
        "to_subrun": target_id,
        "source_task_id": source_task_id,
        "source_status": source_summary.get("status"),
        "source_protocol_completion": source_protocol,
        "source_goal": source_summary.get("goal"),
        "source_final_manifest_summary": source_summary.get("final_manifest_summary", {}),
        "source_result": source_summary.get("result", {}),
        "required_artifacts": required_names,
        "available_artifact_names": sorted(available_names),
        "missing_required_artifacts": missing,
        "constraints": [
            "target subrun must read this handoff before implementation",
            "target final report must reference used handoff inputs",
            "target must preserve original task acceptance criteria unless explicitly blocked",
        ],
        "acceptance_criteria": handoff_plan.get("acceptance_criteria", []),
        "status": "ready" if not missing and source_summary.get("status") == "completed" and source_protocol.get("ok") is True else "incomplete",
        "created_at": now_iso(),
    }
    handoff_path = campaign_dir(run_root, campaign_id) / "handoffs" / f"{handoff_id}.json"
    write_json(handoff_path, payload)
    handoff_state = state.setdefault("handoffs", {}).setdefault(handoff_id, {})
    handoff_state.update(
        {
            "id": handoff_id,
            "from_subrun": source_id,
            "to_subrun": target_id,
            "status": payload["status"],
            "path": str(handoff_path),
            "checks": [
                {"name": "source_completed", "ok": source_summary.get("status") == "completed"},
                {"name": "source_protocol_completed", "ok": source_protocol.get("ok") is True, "errors": source_protocol.get("errors", [])},
                {"name": "required_artifacts_available", "ok": not missing, "missing": missing},
            ],
        }
    )
    record_campaign_event(state, "handoff_created", {"handoff_id": handoff_id, "status": payload["status"], "path": str(handoff_path)})
    return payload


def final_review(run_root: Path, campaign_id: str, plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    subrun_states = state.get("subruns") if isinstance(state.get("subruns"), dict) else {}
    handoff_states = state.get("handoffs") if isinstance(state.get("handoffs"), dict) else {}
    for subrun in plan.get("subruns", []) if isinstance(plan.get("subruns"), list) else []:
        subrun_id = str(subrun.get("id") or "")
        item_state = subrun_states.get(subrun_id, {}) if isinstance(subrun_states, dict) else {}
        task_id = str(item_state.get("task_id") or subrun.get("task_id") or "")
        protocol_completion = subrun_protocol_completion(run_root, task_id) if task_id else {"ok": False, "errors": ["task_id is missing"]}
        checks.append({"name": f"subrun_completed:{subrun_id}", "ok": item_state.get("status") == "completed", "status": item_state.get("status", "")})
        checks.append(
            {
                "name": f"subrun_protocol_completed:{subrun_id}",
                "ok": protocol_completion.get("ok") is True,
                "mission_id": protocol_completion.get("mission_id", ""),
                "errors": protocol_completion.get("errors", []),
            }
        )
        for handoff_id in subrun.get("requires_handoffs", []) if isinstance(subrun.get("requires_handoffs"), list) else []:
            task_text = str(subrun.get("task") or "")
            handoff_state = handoff_states.get(str(handoff_id), {}) if isinstance(handoff_states, dict) else {}
            handoff_path = str(handoff_state.get("path") or "")
            checks.append(
                {
                    "name": f"handoff_used_by_task:{subrun_id}:{handoff_id}",
                    "ok": bool(handoff_path) and (handoff_path in task_text or "{handoff_path}" in task_text),
                    "handoff_path": handoff_path,
                }
            )
    for handoff_id, handoff_state in handoff_states.items() if isinstance(handoff_states, dict) else []:
        checks.append({"name": f"handoff_ready:{handoff_id}", "ok": handoff_state.get("status") == "ready", "status": handoff_state.get("status", "")})
    ok = all(bool(check.get("ok")) for check in checks)
    report = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "status": "completed" if ok else "blocked",
        "original_task": plan.get("original_task", ""),
        "global_acceptance_criteria": plan.get("global_acceptance_criteria", []),
        "checks": checks,
        "subruns": subrun_states,
        "handoffs": handoff_states,
        "blockers": [check for check in checks if not check.get("ok")],
        "deliverables": {
            subrun_id: (subrun_state.get("run_summary", {}).get("result", {}) if isinstance(subrun_state.get("run_summary"), dict) else {})
            for subrun_id, subrun_state in subrun_states.items()
            if isinstance(subrun_state, dict)
        },
        "created_at": now_iso(),
    }
    write_json(campaign_dir(run_root, campaign_id) / FINAL_REPORT_FILE, report)
    return report


def refresh_campaign_state(run_root: Path, campaign_id: str) -> dict[str, Any]:
    plan = load_campaign_plan(run_root, campaign_id)
    state = load_campaign_state(run_root, campaign_id)
    if state.get("status") in TERMINAL_CAMPAIGN_STATUSES:
        return state
    blocked = False
    any_created = False
    all_completed = True
    for item in plan.get("subruns", []) if isinstance(plan.get("subruns"), list) else []:
        if not isinstance(item, dict):
            continue
        subrun_id = str(item.get("id") or "")
        task_id = str(item.get("task_id") or "")
        sub_state = state.setdefault("subruns", {}).setdefault(subrun_id, {})
        run_dir = run_root / task_id
        if run_dir.exists():
            any_created = True
            summary = run_summary(run_dir)
            status = str(summary.get("status") or "unknown")
            protocol_completion = subrun_protocol_completion(run_root, task_id) if status == "completed" else {}
            sub_state.update(
                {
                    "id": subrun_id,
                    "task_id": task_id,
                    "governor": item.get("governor", ""),
                    "kind": item.get("kind", ""),
                    "depends_on": item.get("depends_on", []),
                    "status": status,
                    "created": True,
                    "run_dir": str(run_dir),
                    "last_run_status": status,
                    "run_summary": summary,
                    "protocol_completion": protocol_completion,
                }
            )
            if status == "completed":
                if protocol_completion.get("ok") is not True:
                    all_completed = False
                    continue
                for handoff_id in item.get("produces_handoffs", []) if isinstance(item.get("produces_handoffs"), list) else []:
                    handoff_state = state.get("handoffs", {}).get(str(handoff_id), {}) if isinstance(state.get("handoffs"), dict) else {}
                    if handoff_state.get("status") != "ready":
                        create_handoff(run_root, campaign_id, plan, state, str(handoff_id))
            elif status in TERMINAL_RUN_STATUSES:
                blocked = True
                all_completed = False
            else:
                all_completed = False
        else:
            sub_state.setdefault("status", "planned")
            all_completed = False
    if blocked:
        state["status"] = "blocked"
        state["phase"] = "blocked"
    elif all_completed:
        report = final_review(run_root, campaign_id, plan, state)
        state["final_report"] = report
        state["status"] = str(report.get("status") or "blocked")
        state["phase"] = "final_review"
        record_campaign_event(state, "campaign_final_review", {"status": state["status"]})
    elif any_created:
        state["status"] = "running"
        state["phase"] = "running"
    else:
        state["status"] = "planned"
        state["phase"] = "prepared"
    save_campaign_state(run_root, campaign_id, state)
    return state


def campaign_state(run_root: Path, campaign_id: str) -> dict[str, Any]:
    state = refresh_campaign_state(run_root, campaign_id)
    next_action = campaign_next_action(campaign_id, state)
    final_report_path = campaign_dir(run_root, campaign_id) / FINAL_REPORT_FILE
    return {
        "ok": True,
        "phase": "campaign_state",
        "campaign_id": campaign_id,
        "campaign_dir": str(campaign_dir(run_root, campaign_id)),
        "state": state,
        "plan": load_campaign_plan(run_root, campaign_id),
        "final_report": read_json_object(final_report_path) if final_report_path.exists() else {},
        "next_action": next_action,
        "client_action": next_action.get("client_action", {}),
    }


def campaign_next_action(campaign_id: str, state: dict[str, Any]) -> dict[str, Any]:
    status = str(state.get("status") or "")
    if status == "completed":
        return campaign_action(campaign_id, "inspect_final_report", "GET", "GET /campaigns/{campaign_id}", {}, "campaign is completed")
    if status == "cancelled":
        return campaign_action(campaign_id, "inspect_campaign", "GET", "GET /campaigns/{campaign_id}", {}, "campaign is cancelled")
    if status == "blocked":
        return campaign_action(campaign_id, "inspect_blockers", "GET", "GET /campaigns/{campaign_id}", {}, "campaign is blocked")
    return campaign_action(campaign_id, "resume_campaign", "POST", "POST /campaigns/{campaign_id}/resume", {}, "execute next ready campaign subrun")


def list_campaigns(run_root: Path) -> list[dict[str, Any]]:
    root = campaigns_root(run_root)
    if not root.exists():
        return []
    campaigns: list[dict[str, Any]] = []
    for path in root.iterdir():
        if not path.is_dir() or not valid_task_id(path.name):
            continue
        try:
            state = refresh_campaign_state(run_root, path.name)
        except Exception as exc:  # noqa: BLE001 - corrupt campaigns must not hide healthy ones.
            state = {"campaign_id": path.name, "status": "corrupt", "error": str(exc)}
        campaigns.append(
            {
                "campaign_id": path.name,
                "campaign_dir": str(path),
                "mission_id": str(state.get("mission_id") or ""),
                "status": state.get("status", "unknown"),
                "phase": state.get("phase", ""),
                "original_task": state.get("original_task", ""),
                "updated_at": state.get("updated_at", ""),
                "next_action": campaign_next_action(path.name, state),
            }
        )
    return sorted(campaigns, key=lambda item: str(item.get("updated_at") or ""), reverse=True)


def create_subrun(run_root: Path, campaign_id: str, subrun_id: str, governor_transport: str = "local", governor_host: str = "127.0.0.1") -> dict[str, Any]:
    plan = load_campaign_plan(run_root, campaign_id)
    state = refresh_campaign_state(run_root, campaign_id)
    subrun = dict(subrun_plan(plan, subrun_id))
    if not dependencies_completed(state, subrun):
        return {"ok": False, "phase": "dependency_wait", "campaign_id": campaign_id, "subrun_id": subrun_id, "error_code": "dependencies_not_completed"}
    if not required_handoffs_ready(state, subrun):
        return {"ok": False, "phase": "handoff_wait", "campaign_id": campaign_id, "subrun_id": subrun_id, "error_code": "handoff_not_ready"}
    for handoff_id in subrun.get("requires_handoffs", []) if isinstance(subrun.get("requires_handoffs"), list) else []:
        handoff_state = state.get("handoffs", {}).get(str(handoff_id), {}) if isinstance(state.get("handoffs"), dict) else {}
        subrun["task"] = str(subrun["task"]).replace("{handoff_path}", str(handoff_state.get("path") or ""))
    warmaster_root = Path(__file__).resolve().parents[1]
    mission = open_mission(warmaster_root, str(subrun["task"]), str(subrun["task_id"]), source_channel=f"campaign:{campaign_id}:{subrun_id}")
    if not mission.get("ok"):
        return {
            "ok": False,
            "phase": "subrun_commander_intake_failed",
            "campaign_id": campaign_id,
            "subrun_id": subrun_id,
            "task_id": str(subrun["task_id"]),
            "mission": mission,
            "error_code": str(mission.get("error_code") or "commander_intake_failed"),
        }
    command = mission.get("commander_order") if isinstance(mission.get("commander_order"), dict) else {}
    handoff_constraints: list[str] = []
    for handoff_id in subrun.get("requires_handoffs", []) if isinstance(subrun.get("requires_handoffs"), list) else []:
        handoff_state = state.get("handoffs", {}).get(str(handoff_id), {}) if isinstance(state.get("handoffs"), dict) else {}
        handoff_path = str(handoff_state.get("path") or "").strip()
        if handoff_path:
            handoff_constraints.append(f"Use campaign handoff {handoff_id}: {handoff_path}")
    if handoff_constraints:
        constraints = command.get("constraints") if isinstance(command.get("constraints"), list) else []
        known = {item for item in constraints if isinstance(item, str)}
        for item in handoff_constraints:
            if item not in known:
                constraints.append(item)
                known.add(item)
        command["constraints"] = constraints
        mission["commander_order"] = command
        mission["governor_task"] = task_text_from_commander_order(command)
        mission_dir_text = str(mission.get("mission_dir") or "").strip()
        if mission_dir_text:
            mission_dir = Path(mission_dir_text)
            write_json(mission_dir / "commander_order.json", command)
    expected_governor = str(subrun.get("governor") or "").strip()
    assigned_governor = str(command.get("to") or "").strip()
    if expected_governor and assigned_governor != expected_governor:
        return {
            "ok": False,
            "phase": "subrun_commander_governor_mismatch",
            "campaign_id": campaign_id,
            "subrun_id": subrun_id,
            "task_id": str(subrun["task_id"]),
            "expected_governor": expected_governor,
            "assigned_governor": assigned_governor,
            "mission": mission,
            "error_code": "campaign_subrun_governor_mismatch",
        }
    prepared = prepare_task(
        str(mission.get("governor_task") or subrun["task"]),
        str(subrun["task_id"]),
        run_root,
        governor_transport=governor_transport,
        governor_host=governor_host,
        forced_governor=assigned_governor or expected_governor,
        commander_order=command,
        require_commander_order=True,
    )
    if prepared.get("ok"):
        link_run_to_mission(run_root / str(subrun["task_id"]), mission)
        plan_subrun = subrun_plan(plan, subrun_id)
        plan_subrun["task"] = subrun["task"]
        plan_subrun["mission_id"] = str(mission.get("mission_id") or "")
        write_json(campaign_dir(run_root, campaign_id) / PLAN_FILE, plan)
        sub_state = state.setdefault("subruns", {}).setdefault(subrun_id, {})
        sub_state.update({"status": "created", "created": True, "run_dir": str(run_root / str(subrun["task_id"])), "mission_id": str(mission.get("mission_id") or "")})
        record_campaign_event(state, "subrun_created", {"subrun_id": subrun_id, "task_id": subrun["task_id"], "mission_id": str(mission.get("mission_id") or "")})
        save_campaign_state(run_root, campaign_id, state)
    return {
        "ok": bool(prepared.get("ok")),
        "phase": "subrun_created" if prepared.get("ok") else "subrun_create_failed",
        "campaign_id": campaign_id,
        "subrun_id": subrun_id,
        "mission": {
            "mission_id": str(mission.get("mission_id") or ""),
            "assigned_governor": assigned_governor,
            "mission_dir": str(mission.get("mission_dir") or ""),
        },
        "task": prepared,
    }


def next_ready_subrun(plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    for subrun in plan.get("subruns", []) if isinstance(plan.get("subruns"), list) else []:
        if not isinstance(subrun, dict):
            continue
        subrun_id = str(subrun.get("id") or "")
        sub_state = state.get("subruns", {}).get(subrun_id, {}) if isinstance(state.get("subruns"), dict) else {}
        protocol_completion = sub_state.get("protocol_completion") if isinstance(sub_state, dict) else {}
        if sub_state.get("status") == "completed" and isinstance(protocol_completion, dict) and protocol_completion.get("ok") is not True:
            return subrun
        if sub_state.get("status") == "completed":
            continue
        if dependencies_completed(state, subrun) and required_handoffs_ready(state, subrun):
            return subrun
    return None


def execute_next_ready_subrun(
    run_root: Path,
    campaign_id: str,
    run_mode: str = "http",
    host: str = "127.0.0.1",
    timeout_sec: int = 1800,
    max_revision_cycles: int = 3,
    allow_resume: bool = True,
    governor_transport: str = "local",
    governor_host: str = "127.0.0.1",
) -> dict[str, Any]:
    if run_mode not in {"local", "http"}:
        raise ValueError("run_mode must be local or http")
    host = validate_service_host(host)
    plan = load_campaign_plan(run_root, campaign_id)
    state = refresh_campaign_state(run_root, campaign_id)
    if state.get("status") in TERMINAL_CAMPAIGN_STATUSES:
        return {"ok": state.get("status") == "completed", "phase": "terminal", "campaign_id": campaign_id, "state": state}
    subrun = next_ready_subrun(plan, state)
    if not subrun:
        state = refresh_campaign_state(run_root, campaign_id)
        return {"ok": state.get("status") == "completed", "phase": "no_ready_subrun", "campaign_id": campaign_id, "state": state}
    subrun_id = str(subrun["id"])
    task_id = str(subrun["task_id"])
    if not (run_root / task_id).exists():
        created = create_subrun(run_root, campaign_id, subrun_id, governor_transport=governor_transport, governor_host=governor_host)
        if not created.get("ok"):
            return created
    record_campaign_event(state, "subrun_execution_started", {"subrun_id": subrun_id, "task_id": task_id, "run_mode": run_mode})
    save_campaign_state(run_root, campaign_id, state)
    execution = research_loop_run(
        run_root,
        task_id,
        run_mode=run_mode,
        host=host,
        timeout_sec=timeout_sec,
        max_revision_cycles=max_revision_cycles,
        allow_resume=allow_resume,
    )
    state = refresh_campaign_state(run_root, campaign_id)
    record_campaign_event(state, "subrun_execution_finished", {"subrun_id": subrun_id, "task_id": task_id, "ok": bool(execution.get("ok")), "phase": execution.get("phase", "")})
    save_campaign_state(run_root, campaign_id, state)
    return {
        "ok": bool(execution.get("ok")) and state.get("status") != "blocked",
        "phase": "subrun_executed",
        "campaign_id": campaign_id,
        "subrun_id": subrun_id,
        "task_id": task_id,
        "execution": execution,
        "state": state,
        "next_action": campaign_next_action(campaign_id, state),
    }


def resume_campaign(
    run_root: Path,
    campaign_id: str,
    run_mode: str = "http",
    host: str = "127.0.0.1",
    timeout_sec: int = 1800,
    max_revision_cycles: int = 3,
    allow_resume: bool = True,
    governor_transport: str = "local",
    governor_host: str = "127.0.0.1",
    max_subruns: int = 8,
) -> dict[str, Any]:
    cycles: list[dict[str, Any]] = []
    for _ in range(max(1, min(int(max_subruns), 32))):
        state = refresh_campaign_state(run_root, campaign_id)
        if state.get("status") in TERMINAL_CAMPAIGN_STATUSES:
            break
        result = execute_next_ready_subrun(
            run_root,
            campaign_id,
            run_mode=run_mode,
            host=host,
            timeout_sec=timeout_sec,
            max_revision_cycles=max_revision_cycles,
            allow_resume=allow_resume,
            governor_transport=governor_transport,
            governor_host=governor_host,
        )
        cycles.append(result)
        state = refresh_campaign_state(run_root, campaign_id)
        if state.get("status") in TERMINAL_CAMPAIGN_STATUSES or result.get("phase") == "no_ready_subrun" or not result.get("ok"):
            break
    state = refresh_campaign_state(run_root, campaign_id)
    return {
        "ok": state.get("status") == "completed",
        "phase": "campaign_resumed",
        "campaign_id": campaign_id,
        "cycles": cycles,
        "state": state,
        "next_action": campaign_next_action(campaign_id, state),
    }


def cancel_campaign(run_root: Path, campaign_id: str, reason: str = "", host: str = "127.0.0.1") -> dict[str, Any]:
    host = validate_service_host(host)
    state = load_campaign_state(run_root, campaign_id)
    if state.get("status") in TERMINAL_CAMPAIGN_STATUSES:
        return {"ok": False, "phase": "terminal", "campaign_id": campaign_id, "state": state, "error": "campaign is already terminal"}
    cancellations: list[dict[str, Any]] = []
    for sub_state in state.get("subruns", {}).values() if isinstance(state.get("subruns"), dict) else []:
        if not isinstance(sub_state, dict):
            continue
        task_id = str(sub_state.get("task_id") or "")
        ledger_path = run_root / task_id / "task_ledger.json"
        if not ledger_path.exists():
            continue
        ledger = TaskLedger.load(ledger_path)
        ledger_data = ledger.to_dict()
        if ledger_data.get("status") not in TERMINAL_RUN_STATUSES:
            accepted = ledger.request_cancel(reason or "campaign cancelled")
            cancellations.append({"task_id": task_id, "accepted": accepted, "worker_cancellations": cancel_http_worker_tasks(run_root / task_id, host=host)})
    state["status"] = "cancelled"
    state["phase"] = "cancelled"
    record_campaign_event(state, "campaign_cancelled", {"reason": reason, "cancellations": cancellations})
    save_campaign_state(run_root, campaign_id, state)
    return {"ok": True, "phase": "cancelled", "campaign_id": campaign_id, "state": state, "cancellations": cancellations}
