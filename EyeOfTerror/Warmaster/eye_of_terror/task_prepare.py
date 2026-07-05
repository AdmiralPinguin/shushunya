"""Task preparation: route a user message to a governor, build and validate
the run package (contract, oversight, dispatch), and run task preflight."""
from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .actions import created_task_actions, task_preflight_actions, task_preflight_body
from .brigade import (
    compact_brigade_readiness,
    contract_required_workers,
    contract_summary,
    fetch_service_capabilities,
    missing_contract_workers,
    required_workers_from_capabilities,
    worker_availability,
)
from .contracts import validate_task_contract_payload
from .gateway_util import post_json, valid_task_id, validate_service_host
from .governors import governor_by_name
from .inner_circle.ceraxia import plan_code_task
from .inner_circle.iskandar import plan_research_writing as plan_lore_reconstruction
from .ledger import TaskLedger
from .oversight_guard import compact_oversight_summary
from .pipeline import write_pipeline_run
from .routing import route_message
from .run_validation import plan_oversight_errors, verify_prepared_run_package
from .views import payload_with_task_view


def prepare_task_via_governor_service(message: str, task_id: str | None, run_root: Path, governor: Any, host: str = "127.0.0.1", port: int | None = None) -> dict[str, Any]:
    host = validate_service_host(host)
    service_port = int(port or governor.port)
    base = f"http://{host}:{service_port}"
    try:
        plan = post_json(base + "/plan", {"task": message, "task_id": task_id or ""})
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": f"governor service unavailable: {exc}",
            "error_code": "governor_service_unavailable",
            "governor": governor.name,
            "actions": task_preflight_actions(False, "governor_service_unavailable", task_id or "", governor_transport="http", governor_host=host, message=message),
        }
    capabilities = fetch_service_capabilities(host, service_port, timeout_sec=2.0)
    required_workers = []
    if capabilities.get("ok"):
        payload = capabilities.get("capabilities") if isinstance(capabilities.get("capabilities"), dict) else {}
        required_workers = required_workers_from_capabilities(payload)
    if required_workers:
        availability = worker_availability(required_workers)
        if not availability["ok"]:
            return {
                "ok": False,
                "gateway": "WarmasterGateway",
                "error": "governor required workers are missing or unavailable in Mechanicum registry",
                "error_code": "governor_workers_unavailable" if availability["unavailable_workers"] and not availability["missing_workers"] else "governor_workers_missing",
                "governor": governor.name,
                "required_workers": required_workers,
                "missing_workers": availability["missing_workers"],
                "unavailable_workers": availability["unavailable_workers"],
                "worker_availability": availability,
                "actions": task_preflight_actions(
                    False,
                    "governor_workers_unavailable" if availability["unavailable_workers"] and not availability["missing_workers"] else "governor_workers_missing",
                    task_id or "",
                    governor_transport="http",
                    governor_host=host,
                    message=message,
                ),
            }
    if not plan.get("ok"):
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor service returned an invalid plan",
            "error_code": "governor_plan_failed",
            "governor": governor.name,
            "plan": plan,
            "actions": task_preflight_actions(False, "governor_plan_failed", task_id or "", governor_transport="http", governor_host=host, message=message),
        }
    contract = plan.get("contract") if isinstance(plan.get("contract"), dict) else {}
    service_task_id = str(contract.get("task_id") or "").strip()
    if not service_task_id or not valid_task_id(service_task_id):
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor service returned an invalid task_id",
            "error_code": "invalid_governor_task_id",
            "governor": governor.name,
            "task_id": service_task_id,
            "actions": task_preflight_actions(False, "invalid_governor_task_id", service_task_id, governor_transport="http", governor_host=host, message=message),
        }
    validation_errors = validate_task_contract_payload(contract)
    if validation_errors:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor service produced invalid task contract",
            "error_code": "invalid_task_contract",
            "task_id": service_task_id,
            "validation": {"ok": False, "errors": validation_errors},
            "actions": task_preflight_actions(False, "invalid_task_contract", service_task_id, governor_transport="http", governor_host=host, message=message),
        }
    availability = worker_availability(contract_required_workers(contract))
    if not availability["ok"]:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor task contract references missing or unavailable Mechanicum workers",
            "error_code": "contract_workers_unavailable" if availability["unavailable_workers"] and not availability["missing_workers"] else "contract_workers_missing",
            "task_id": service_task_id,
            "governor": governor.name,
            "missing_workers": availability["missing_workers"],
            "unavailable_workers": availability["unavailable_workers"],
            "worker_availability": availability,
            "actions": task_preflight_actions(
                False,
                "contract_workers_unavailable" if availability["unavailable_workers"] and not availability["missing_workers"] else "contract_workers_missing",
                service_task_id,
                governor_transport="http",
                governor_host=host,
                message=message,
            ),
        }
    oversight_errors = plan_oversight_errors(contract, plan)
    if oversight_errors:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor service produced invalid oversight",
            "error_code": "invalid_oversight",
            "task_id": service_task_id,
            "oversight_validation": {"ok": False, "errors": oversight_errors},
            "actions": task_preflight_actions(False, "invalid_oversight", service_task_id, governor_transport="http", governor_host=host, message=message),
        }
    run_dir = run_root / service_task_id
    if run_dir.exists():
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "task_id already exists",
            "error_code": "task_exists",
            "task_id": service_task_id,
            "run_dir": str(run_dir),
            "actions": task_preflight_actions(False, "task_exists", service_task_id, governor_transport="http", governor_host=host, message=message),
        }
    try:
        prepared = post_json(base + "/prepare_run", {"task": message, "task_id": service_task_id, "run_dir": str(run_dir)})
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": f"governor service unavailable: {exc}",
            "error_code": "governor_service_unavailable",
            "governor": governor.name,
            "task_id": service_task_id,
            "actions": task_preflight_actions(False, "governor_service_unavailable", service_task_id, governor_transport="http", governor_host=host, message=message),
        }
    if not prepared.get("ok"):
        cleanup = cleanup_unregistered_run_dir(run_root, run_dir)
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor service failed to prepare run",
            "error_code": "governor_prepare_failed",
            "governor": governor.name,
            "task_id": service_task_id,
            "response": prepared,
            "cleanup": cleanup,
            "actions": task_preflight_actions(False, "governor_prepare_failed", service_task_id, governor_transport="http", governor_host=host, message=message),
        }
    planned_oversight = plan.get("oversight") if isinstance(plan.get("oversight"), dict) else {}
    package_errors = verify_prepared_run_package(run_dir, contract, planned_oversight)
    if package_errors:
        cleanup = cleanup_unregistered_run_dir(run_root, run_dir)
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor service prepared an invalid run package",
            "error_code": "governor_prepare_invalid_run",
            "governor": governor.name,
            "task_id": service_task_id,
            "validation": {"ok": False, "errors": package_errors},
            "cleanup": cleanup,
            "actions": task_preflight_actions(False, "governor_prepare_invalid_run", service_task_id, governor_transport="http", governor_host=host, message=message),
        }
    TaskLedger.create(run_dir / "task_ledger.json", service_task_id, str(contract.get("goal") or message), governor.name)
    return {
        "ok": True,
        "gateway": "WarmasterGateway",
        "governor": governor.name,
        "governor_transport": "http",
        "task_id": service_task_id,
        "run_dir": str(run_dir),
        "status": prepared.get("status", {}),
        "actions": created_task_actions(service_task_id),
    }


def route_failure_payload(route: Any) -> dict[str, Any]:
    error_code = "governor_inactive" if route.governor else "no_supported_governor"
    required_governor: dict[str, Any] = {}
    if route.governor:
        governor_ref = governor_by_name(str(route.governor))
        if governor_ref is not None:
            required_governor = {
                "name": governor_ref.name,
                "status": governor_ref.status,
                "port": governor_ref.port,
                "service": governor_ref.service,
                "task_kinds": list(governor_ref.task_kinds),
                "route_terms": list(governor_ref.route_terms),
            }
    return {
        "ok": False,
        "gateway": "WarmasterGateway",
        "error": route.reason,
        "error_code": error_code,
        "kind": route.kind,
        "governor": route.governor,
        "route": {"kind": route.kind, "governor": route.governor, "ok": route.ok, "reason": route.reason},
        "required_governor": required_governor,
        "actions": {
            "can_create_task": False,
            "can_check_brigade_readiness": True,
            "next_action": {
                "kind": "inspect_capabilities",
                "method": "GET",
                "endpoint": "GET /capabilities",
                "body": {},
                "reason": "no active governor can accept this task",
            },
        },
    }


def cleanup_unregistered_run_dir(run_root: Path, run_dir: Path) -> dict[str, Any]:
    root = run_root.resolve()
    target = run_dir.resolve()
    if target != root and root not in target.parents:
        return {"attempted": False, "removed": False, "reason": "run_dir is outside run_root"}
    if not target.exists():
        return {"attempted": False, "removed": False, "reason": "run_dir does not exist"}
    if (target / "task_ledger.json").exists():
        return {"attempted": False, "removed": False, "reason": "ledger exists"}
    try:
        shutil.rmtree(target)
    except OSError as exc:
        return {"attempted": True, "removed": False, "error": str(exc)}
    return {"attempted": True, "removed": True}


def prepare_task(message: str, task_id: str | None, run_root: Path, governor_transport: str = "local", governor_host: str = "127.0.0.1") -> dict[str, Any]:
    if task_id is not None and not valid_task_id(task_id):
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "task_id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127} and must not contain '..'",
            "error_code": "invalid_task_id",
            "task_id": task_id,
            "actions": task_preflight_actions(False, "invalid_task_id", task_id or "", governor_transport=governor_transport, governor_host=governor_host, message=message),
        }
    route = route_message(message)
    if not route.ok:
        return route_failure_payload(route)
    governor = route.governor
    governor_ref = governor_by_name(governor)
    if governor_ref is None or not governor_ref.active():
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": f"governor is not active: {governor}",
            "error_code": "governor_inactive",
            "kind": route.kind,
            "actions": task_preflight_actions(False, "governor_inactive", task_id or "", governor_transport=governor_transport, governor_host=governor_host, message=message),
        }
    if governor_transport == "http":
        return prepare_task_via_governor_service(message, task_id, run_root, governor_ref, host=governor_host)
    if governor_transport != "local":
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor_transport must be local or http",
            "error_code": "invalid_governor_transport",
            "actions": task_preflight_actions(False, "invalid_governor_transport", task_id or "", governor_transport=governor_transport, governor_host=governor_host, message=message),
        }
    if governor == "Ceraxia":
        plan = plan_code_task(message, task_id=task_id)
    else:
        plan = plan_lore_reconstruction(message, task_id=task_id)
    run_dir = run_root / plan.contract.task_id
    if run_dir.exists():
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "task_id already exists",
            "error_code": "task_exists",
            "task_id": plan.contract.task_id,
            "run_dir": str(run_dir),
            "actions": task_preflight_actions(False, "task_exists", plan.contract.task_id, governor_transport=governor_transport, governor_host=governor_host, message=message),
        }
    validation_errors = validate_task_contract_payload(plan.contract.to_dict())
    if validation_errors:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor produced invalid task contract",
            "error_code": "invalid_task_contract",
            "task_id": plan.contract.task_id,
            "validation": {"ok": False, "errors": validation_errors},
            "actions": task_preflight_actions(False, "invalid_task_contract", plan.contract.task_id, governor_transport=governor_transport, governor_host=governor_host, message=message),
        }
    contract_payload = plan.contract.to_dict()
    availability = worker_availability(contract_required_workers(contract_payload))
    if not availability["ok"]:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor task contract references missing or unavailable Mechanicum workers",
            "error_code": "contract_workers_unavailable" if availability["unavailable_workers"] and not availability["missing_workers"] else "contract_workers_missing",
            "task_id": plan.contract.task_id,
            "governor": governor,
            "missing_workers": availability["missing_workers"],
            "unavailable_workers": availability["unavailable_workers"],
            "worker_availability": availability,
            "actions": task_preflight_actions(
                False,
                "contract_workers_unavailable" if availability["unavailable_workers"] and not availability["missing_workers"] else "contract_workers_missing",
                plan.contract.task_id,
                governor_transport=governor_transport,
                governor_host=governor_host,
                message=message,
            ),
        }
    plan_payload = plan.to_dict()
    oversight_errors = plan_oversight_errors(contract_payload, plan_payload)
    if oversight_errors:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor produced invalid oversight",
            "error_code": "invalid_oversight",
            "task_id": plan.contract.task_id,
            "oversight_validation": {"ok": False, "errors": oversight_errors},
            "actions": task_preflight_actions(False, "invalid_oversight", plan.contract.task_id, governor_transport=governor_transport, governor_host=governor_host, message=message),
        }
    oversight = plan_payload.get("oversight") if isinstance(plan_payload.get("oversight"), dict) else None
    status = write_pipeline_run(plan.contract, run_dir, oversight=oversight)
    TaskLedger.create(run_dir / "task_ledger.json", plan.contract.task_id, plan.contract.goal, governor)
    return {
        "ok": status["ok"],
        "gateway": "WarmasterGateway",
        "governor": governor,
        "task_id": plan.contract.task_id,
        "run_dir": str(run_dir),
        "status": status,
        "actions": created_task_actions(plan.contract.task_id),
    }


def preflight_task(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor_transport: str = "local",
    governor_host: str = "127.0.0.1",
    include_brigade_health: bool = False,
) -> dict[str, Any]:
    if task_id is not None and not valid_task_id(task_id):
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "invalid task_id",
            "error_code": "invalid_task_id",
            "task_id": task_id,
            "actions": task_preflight_actions(False, "invalid_task_id", task_id or "", include_brigade_health, governor_transport, governor_host, message),
        }
    route = route_message(message)
    if not route.ok:
        return route_failure_payload(route)
    governor_ref = governor_by_name(route.governor)
    if governor_ref is None or not governor_ref.active():
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": f"governor is not active: {route.governor}",
            "error_code": "governor_inactive",
            "kind": route.kind,
            "actions": task_preflight_actions(False, "governor_inactive", task_id or "", include_brigade_health, governor_transport, governor_host, message),
        }
    if governor_transport not in {"local", "http"}:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor_transport must be local or http",
            "error_code": "invalid_governor_transport",
            "actions": task_preflight_actions(False, "invalid_governor_transport", task_id or "", include_brigade_health, governor_transport, governor_host, message),
        }
    if governor_transport == "http":
        host = validate_service_host(governor_host)
        port = int(governor_ref.port)
        base = f"http://{host}:{port}"
        capabilities = fetch_service_capabilities(host, port, timeout_sec=2.0)
        if capabilities.get("ok"):
            payload = capabilities.get("capabilities") if isinstance(capabilities.get("capabilities"), dict) else {}
            required_workers = required_workers_from_capabilities(payload)
            availability = worker_availability(required_workers)
            if not availability["ok"]:
                return {
                    "ok": False,
                    "gateway": "WarmasterGateway",
                    "error": "governor required workers are missing or unavailable in Mechanicum registry",
                    "error_code": "governor_workers_unavailable" if availability["unavailable_workers"] and not availability["missing_workers"] else "governor_workers_missing",
                    "governor": governor_ref.name,
                    "required_workers": required_workers,
                    "missing_workers": availability["missing_workers"],
                    "unavailable_workers": availability["unavailable_workers"],
                    "worker_availability": availability,
                    "actions": task_preflight_actions(
                        False,
                        "governor_workers_unavailable" if availability["unavailable_workers"] and not availability["missing_workers"] else "governor_workers_missing",
                        task_id or "",
                        include_brigade_health,
                        governor_transport,
                        governor_host,
                        message,
                    ),
                }
        try:
            plan_payload = post_json(base + "/plan", {"task": message, "task_id": task_id or ""})
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            return {
                "ok": False,
                "gateway": "WarmasterGateway",
                "error": f"governor service unavailable: {exc}",
                "error_code": "governor_service_unavailable",
                "governor": governor_ref.name,
                "actions": task_preflight_actions(False, "governor_service_unavailable", task_id or "", include_brigade_health, governor_transport, governor_host, message),
            }
        contract = plan_payload.get("contract") if isinstance(plan_payload.get("contract"), dict) else {}
        oversight = plan_payload.get("oversight") if isinstance(plan_payload.get("oversight"), dict) else {}
    else:
        plan = plan_lore_reconstruction(message, task_id=task_id)
        plan_payload = plan.to_dict()
        contract = plan_payload.get("contract") if isinstance(plan_payload.get("contract"), dict) else plan.contract.to_dict()
        oversight = plan_payload.get("oversight") if isinstance(plan_payload.get("oversight"), dict) else {}
    resolved_task_id = str(contract.get("task_id") or "").strip()
    run_dir = run_root / resolved_task_id if resolved_task_id else run_root / "_invalid"
    if resolved_task_id and run_dir.exists():
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "task_id already exists",
            "error_code": "task_exists",
            "task_id": resolved_task_id,
            "run_dir": str(run_dir),
            "actions": task_preflight_actions(False, "task_exists", resolved_task_id, include_brigade_health, governor_transport, governor_host, message),
        }
    validation_errors = validate_task_contract_payload(contract)
    availability = {"ok": True, "missing_workers": [], "unavailable_workers": []}
    if not validation_errors:
        availability = worker_availability(contract_required_workers(contract))
    missing_workers = list(availability["missing_workers"])
    unavailable_workers = list(availability["unavailable_workers"])
    oversight_errors = [] if validation_errors else plan_oversight_errors(contract, {"oversight": oversight})
    ok = not validation_errors and not missing_workers and not unavailable_workers and not oversight_errors
    error_code = ""
    if not ok:
        error_code = (
            "contract_workers_missing"
            if missing_workers
            else ("contract_workers_unavailable" if unavailable_workers else ("invalid_oversight" if oversight_errors else "invalid_task_contract"))
        )
    payload = {
        "ok": ok,
        "gateway": "WarmasterGateway",
        "governor": governor_ref.name,
        "governor_transport": governor_transport,
        "task_id": resolved_task_id,
        "route": {"kind": route.kind, "governor": route.governor},
        "contract_summary": contract_summary(contract),
        "governor_plan_actions": plan_payload.get("actions") if isinstance(plan_payload.get("actions"), dict) else {},
        "oversight_summary": compact_oversight_summary(oversight) if oversight else {},
        "oversight_validation": {"ok": not oversight_errors, "errors": oversight_errors},
        "validation": {"ok": not validation_errors, "errors": validation_errors},
        "missing_workers": missing_workers,
        "unavailable_workers": unavailable_workers,
        "worker_availability": availability,
        "would_create_run_dir": str(run_dir) if resolved_task_id else "",
        "error_code": error_code,
        "actions": task_preflight_actions(ok, error_code, resolved_task_id, include_brigade_health, governor_transport, governor_host, message),
    }
    if include_brigade_health:
        payload["brigade_readiness"] = compact_brigade_readiness(host=governor_host)
    return payload
