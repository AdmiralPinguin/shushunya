from __future__ import annotations

import argparse
import json
import re
import shutil
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .contracts import validate_task_contract_payload
from .inner_circle.iskandar import plan_lore_reconstruction
from doctor import run_doctor
from .http_executor import execute_run as execute_http_run, preflight_workers as preflight_http_workers
from .governors import governor_by_name, governor_refs
from .ledger import TaskLedger
from .local_executor import WORKER_COMMANDS, execute_run as execute_local_run, input_artifact_errors, ordered_dispatch_paths
from .pipeline import write_pipeline_run
from .registry import worker_refs
from .routing import route_message


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_RUNS: set[str] = set()
ACTIVE_RUNS_LOCK = threading.Lock()
MAX_LIST_LIMIT = 200
MAX_ARTIFACT_TEXT_BYTES = 500000
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ALLOWED_SERVICE_HOSTS = {"127.0.0.1", "localhost", "::1"}


def parse_limit(raw_value: str, default: int, maximum: int = MAX_LIST_LIMIT) -> int:
    if not raw_value.isdigit():
        return default
    return max(0, min(int(raw_value), maximum))


def parse_nonnegative_int(raw_value: str, default: int) -> int:
    if not raw_value.isdigit():
        return default
    return max(0, int(raw_value))


def requested_step_ids_from_payload(payload: dict[str, Any]) -> list[str]:
    if "step_ids" not in payload:
        return []
    raw_step_ids = payload.get("step_ids")
    if not isinstance(raw_step_ids, list):
        raise ValueError("step_ids must be a list of non-empty strings")
    step_ids: list[str] = []
    for index, item in enumerate(raw_step_ids):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"step_ids[{index}] must be a non-empty string")
        step_id = item.strip()
        if step_id in step_ids:
            raise ValueError(f"step_ids contains duplicate step: {step_id}")
        step_ids.append(step_id)
    return step_ids


def valid_task_id(task_id: str) -> bool:
    return bool(TASK_ID_RE.fullmatch(task_id)) and ".." not in task_id


def resolve_run_child_path(run_dir: Path, requested: str, default_name: str) -> Path:
    root = run_dir.resolve()
    candidate = Path(requested) if requested else root / default_name
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path must stay inside run_dir: {default_name}")
    return resolved


def validate_service_host(host: str) -> str:
    normalized = host.strip().lower()
    if normalized not in ALLOWED_SERVICE_HOSTS:
        raise ValueError("worker service host must be a loopback host")
    return normalized


def response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
    handler.end_headers()
    handler.wfile.write(data)


def read_payload(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def post_json(url: str, payload: dict[str, Any], timeout_sec: float = 10.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("service response is not a JSON object")
    return result


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
    return {
        "ok": False,
        "gateway": "WarmasterGateway",
        "error": route.reason,
        "error_code": error_code,
        "kind": route.kind,
        "governor": route.governor,
        "route": {"kind": route.kind, "governor": route.governor, "ok": route.ok, "reason": route.reason},
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


def plan_oversight_errors(contract: dict[str, Any], plan_payload: dict[str, Any]) -> list[str]:
    oversight = plan_payload.get("oversight") if isinstance(plan_payload.get("oversight"), dict) else {}
    if not oversight:
        return ["governor plan did not include oversight"]
    return validate_oversight_payload(contract, oversight, {"steps": contract_summary(contract).get("steps", [])})


def verify_prepared_run_package(run_dir: Path, planned_contract: dict[str, Any], planned_oversight: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    contract_payload = run_contract(run_dir)
    if not contract_payload.get("ok"):
        errors.append(str(contract_payload.get("error") or "contract unavailable"))
    elif contract_payload.get("contract") != planned_contract:
        errors.append("prepared contract does not match governor plan")
    status, status_error = load_json_object(run_dir / "status.json", "status")
    if status_error:
        errors.append(status_error)
    oversight_payload = run_oversight(run_dir)
    if not oversight_payload.get("ok"):
        errors.append(str(oversight_payload.get("error") or "oversight unavailable"))
    else:
        prepared_oversight = oversight_payload.get("oversight") if isinstance(oversight_payload.get("oversight"), dict) else {}
        if prepared_oversight != planned_oversight:
            errors.append("prepared oversight does not match governor plan")
        if not status_error:
            errors.extend(validate_oversight_against_run(run_dir, prepared_oversight, status))
    if not status_error:
        errors.extend(run_dispatch_package_errors(run_dir, status))
    return errors


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


def compact_brigade_readiness(host: str = "127.0.0.1") -> dict[str, Any]:
    try:
        health = brigade_health_snapshot(host=host)
    except Exception as exc:  # noqa: BLE001 - task preflight should not crash on optional readiness diagnostics.
        return {
            "ready": False,
            "blocker_count": 1,
            "warning_count": 0,
            "blockers": [f"Brigade readiness unavailable: {exc}"],
            "warnings": [],
            "error": str(exc),
        }
    summary = health.get("summary") if isinstance(health.get("summary"), dict) else {}
    return {
        "ready": bool(summary.get("ready")),
        "blocker_count": int(summary.get("blocker_count") or 0),
        "warning_count": int(summary.get("warning_count") or 0),
        "blockers": summary.get("blockers") if isinstance(summary.get("blockers"), list) else [],
        "warnings": summary.get("warnings") if isinstance(summary.get("warnings"), list) else [],
    }


def created_task_actions(task_id: str) -> dict[str, Any]:
    return {
        "can_preflight_run": True,
        "can_start_run": False,
        "next_action": {
            "kind": "preflight_run",
            "method": "POST",
            "endpoint": "POST /runs/{task_id}/preflight_http",
            "body": {},
            "reason": "run package was created and should be preflighted before execution",
        },
        "run_summary": {
            "method": "GET",
            "endpoint": "GET /runs/{task_id}/summary",
            "body": {},
        },
    }


def task_preflight_body(
    task_id: str,
    include_brigade_health: bool = False,
    governor_transport: str = "",
    governor_host: str = "",
    message: str = "<same message>",
) -> dict[str, Any]:
    body = {"message": message, "task_id": task_id} if task_id else {"message": message}
    if governor_transport:
        body["governor_transport"] = governor_transport
    if governor_host:
        body["governor_host"] = governor_host
    if include_brigade_health:
        body["include_brigade_health"] = True
    return body


def task_preflight_actions(
    ok: bool,
    error_code: str,
    task_id: str,
    include_brigade_health: bool = False,
    governor_transport: str = "",
    governor_host: str = "",
    message: str = "<same message>",
) -> dict[str, Any]:
    actions = {
        "can_create_task": ok,
        "can_check_brigade_readiness": True,
    }
    retry_body = task_preflight_body(task_id, include_brigade_health, governor_transport, governor_host, message)
    create_body = task_preflight_body(task_id, False, governor_transport, governor_host, message)
    if ok:
        next_action = {
            "kind": "create_task",
            "method": "POST",
            "endpoint": "POST /task",
            "body": create_body,
            "reason": "task preflight passed",
        }
    elif error_code == "task_exists":
        next_action = {
            "kind": "inspect_existing_run",
            "method": "GET",
            "endpoint": "GET /runs/{task_id}/summary",
            "body": {},
            "reason": "task_id already exists",
        }
    elif error_code in {"contract_workers_missing", "contract_workers_unavailable", "governor_workers_missing", "governor_workers_unavailable"}:
        next_action = {
            "kind": "inspect_brigade",
            "method": "GET",
            "endpoint": "GET /brigade_health",
            "body": {},
            "reason": "required workers are missing or unavailable",
        }
    elif error_code in {"governor_service_unavailable", "governor_plan_failed", "governor_prepare_failed", "governor_prepare_invalid_run", "invalid_governor_task_id", "invalid_task_contract"}:
        next_action = {
            "kind": "inspect_governor",
            "method": "GET",
            "endpoint": "GET /governors?health=1",
            "body": {},
            "reason": error_code or "governor diagnostics are required",
        }
    elif error_code == "invalid_oversight":
        next_action = {
            "kind": "inspect_governor",
            "method": "GET",
            "endpoint": "GET /governors?health=1",
            "body": {},
            "reason": "governor oversight is invalid",
        }
    elif error_code in {"governor_inactive", "no_supported_governor"}:
        next_action = {
            "kind": "inspect_capabilities",
            "method": "GET",
            "endpoint": "GET /capabilities",
            "body": {},
            "reason": "no active governor can accept this task",
        }
    elif include_brigade_health:
        next_action = {
            "kind": "inspect_preflight",
            "method": "POST",
            "endpoint": "POST /task_preflight",
            "body": retry_body,
            "reason": error_code or "task preflight failed",
        }
    else:
        next_action = {
            "kind": "inspect_preflight",
            "method": "POST",
            "endpoint": "POST /task_preflight",
            "body": retry_body,
            "reason": error_code or "task preflight failed",
        }
    actions["next_action"] = next_action
    return actions


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


def load_ledger_dict(ledger_path: Path) -> tuple[dict[str, Any], str]:
    if not ledger_path.exists():
        return {}, "ledger not found"
    try:
        return TaskLedger.load(ledger_path).to_dict(), ""
    except Exception as exc:  # noqa: BLE001 - gateway must report corrupt run state instead of crashing.
        return {}, str(exc)


def load_json_object(path: Path, label: str) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, f"{label} not found"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"{label} is corrupt: {exc}"
    if not isinstance(payload, dict):
        return {}, f"{label} is not a JSON object"
    return payload, ""


def load_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return payload


def sandbox_artifact_file_status(workspace_root: str, sandbox_path: str) -> dict[str, Any]:
    item: dict[str, Any] = {"path": sandbox_path}
    if workspace_root and sandbox_path.startswith("/work/"):
        host_path = Path(workspace_root) / sandbox_path.removeprefix("/work/")
        item["host_path"] = str(host_path)
        item["exists"] = host_path.exists()
        item["bytes"] = host_path.stat().st_size if host_path.exists() else 0
    else:
        item["exists"] = False
        item["bytes"] = 0
    return item


def run_progress(status: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    planned_steps = status.get("steps", [])
    ledger_steps = ledger.get("steps", [])
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    workspace_root = str(result.get("workspace_root") or "")
    if not isinstance(planned_steps, list):
        planned_steps = []
    if not isinstance(ledger_steps, list):
        ledger_steps = []
    by_status: dict[str, int] = {}
    ledger_by_step: dict[str, dict[str, Any]] = {}
    for step in ledger_steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("step_id") or "")
        if step_id:
            ledger_by_step[step_id] = step
        step_status = str(step.get("status") or "unknown")
        by_status[step_status] = by_status.get(step_status, 0) + 1
    completed = by_status.get("completed", 0) + by_status.get("ready", 0)
    failed = by_status.get("failed", 0)
    planned_step_ids = [
        str(step.get("step_id") or "")
        for step in planned_steps
        if isinstance(step, dict) and step.get("step_id")
    ]
    completed_statuses = {"completed", "ready", "passed_with_warnings"}
    completed_step_ids = [
        step_id
        for step_id in planned_step_ids
        if str(ledger_by_step.get(step_id, {}).get("status") or "") in completed_statuses
    ]
    failed_step_ids = [
        step_id
        for step_id in planned_step_ids
        if str(ledger_by_step.get(step_id, {}).get("status") or "") in {"failed", "blocked", "needs_revision", "preflight_failed"}
    ]
    pending_step_ids = [
        step_id
        for step_id in planned_step_ids
        if step_id not in completed_step_ids and step_id not in failed_step_ids
    ]
    completed_set = set(completed_step_ids)
    failed_set = set(failed_step_ids)
    ready_step_ids: list[str] = []
    blocked_step_ids: list[str] = []
    waiting_step_ids: list[str] = []
    step_states: list[dict[str, Any]] = []
    for planned in planned_steps:
        if not isinstance(planned, dict):
            continue
        step_id = str(planned.get("step_id") or "")
        if not step_id:
            continue
        recorded = ledger_by_step.get(step_id, {})
        recorded_status = str(recorded.get("status") or "")
        input_artifacts = planned.get("input_artifacts") if isinstance(planned.get("input_artifacts"), list) else []
        expected_artifacts = planned.get("expected_artifacts") if isinstance(planned.get("expected_artifacts"), list) else []
        artifacts = recorded.get("artifacts") if isinstance(recorded.get("artifacts"), list) else []
        depends_on = planned.get("depends_on") if isinstance(planned.get("depends_on"), list) else []
        dependency_status = [
            {
                "step_id": str(dependency),
                "completed": str(dependency) in completed_set,
                "failed": str(dependency) in failed_set,
            }
            for dependency in depends_on
        ]
        dependency_blocked = any(item["failed"] for item in dependency_status)
        dependency_ready = all(item["completed"] for item in dependency_status)
        if step_id in pending_step_ids:
            if dependency_ready:
                ready_step_ids.append(step_id)
            elif dependency_blocked:
                blocked_step_ids.append(step_id)
            else:
                waiting_step_ids.append(step_id)
        step_states.append(
            {
                "step_id": step_id,
                "worker": str(planned.get("worker") or recorded.get("worker") or ""),
                "status": recorded_status or "pending",
                "depends_on": depends_on,
                "dependency_status": dependency_status,
                "dependencies_ready": dependency_ready,
                "dependencies_blocked": dependency_blocked,
                "input_artifacts": input_artifacts,
                "input_artifact_status": [sandbox_artifact_file_status(workspace_root, str(path)) for path in input_artifacts],
                "expected_artifacts": expected_artifacts,
                "expected_artifact_status": [sandbox_artifact_file_status(workspace_root, str(path)) for path in expected_artifacts],
                "artifacts": artifacts,
                "artifact_status": [sandbox_artifact_file_status(workspace_root, str(path)) for path in artifacts],
                "summary": str(recorded.get("summary") or ""),
                "recorded": bool(recorded),
            }
        )
    return {
        "planned_steps": len(planned_steps),
        "recorded_steps": len(ledger_steps),
        "completed_steps": completed,
        "failed_steps": failed,
        "pending_steps": len(pending_step_ids),
        "ready_steps": len(ready_step_ids),
        "blocked_steps": len(blocked_step_ids),
        "waiting_steps": len(waiting_step_ids),
        "by_status": by_status,
        "planned_step_ids": planned_step_ids,
        "completed_step_ids": completed_step_ids,
        "failed_step_ids": failed_step_ids,
        "pending_step_ids": pending_step_ids,
        "ready_step_ids": ready_step_ids,
        "blocked_step_ids": blocked_step_ids,
        "waiting_step_ids": waiting_step_ids,
        "next_step_id": pending_step_ids[0] if pending_step_ids else "",
        "next_ready_step_id": ready_step_ids[0] if ready_step_ids else "",
        "step_states": step_states,
    }


def run_summary(run_dir: Path) -> dict[str, Any]:
    status_path = run_dir / "status.json"
    ledger_path = run_dir / "task_ledger.json"
    status, status_error = load_json_object(status_path, "status") if status_path.exists() else ({}, "")
    ledger, ledger_error = load_ledger_dict(ledger_path)
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    revision_plan = result.get("revision_plan") if isinstance(result.get("revision_plan"), dict) else {"required": False, "steps": []}
    revision_plan_errors = validate_revision_plan(run_dir, revision_plan)
    package_errors = run_package_action_errors(run_dir)
    oversight_errors = run_oversight_validation_errors(run_dir, status)
    summary = {
        "task_id": ledger.get("task_id") or status.get("task_id") or run_dir.name,
        "run_dir": str(run_dir),
        "status": "corrupt" if (ledger_error and ledger_path.exists()) or status_error else ledger.get("status") or status.get("status") or "unknown",
        "goal": ledger.get("goal") or "",
        "governor": ledger.get("governor") or status.get("governor") or "",
        "created_at": ledger.get("created_at") or "",
        "updated_at": ledger.get("updated_at") or "",
        "result": result,
        "revision_plan": revision_plan,
        "revision_plan_errors": revision_plan_errors,
        "revision_plan_summary": revision_plan_summary(revision_plan, revision_plan_errors),
        "package_errors": package_errors,
        "oversight_errors": oversight_errors,
        "oversight_summary": run_oversight_summary(run_dir),
        "final_manifest_summary": final_manifest_summary(result),
        "progress": run_progress(status, ledger),
        "last_preflight": last_run_preflight(ledger),
    }
    summary["actions"] = run_actions(
        str(summary["status"]),
        revision_plan,
        revision_plan_errors=revision_plan_errors,
        package_errors=package_errors,
        oversight_errors=oversight_errors,
    )
    if status_error:
        summary["status_error"] = status_error
    if ledger_error and ledger_path.exists():
        summary["ledger_error"] = ledger_error
    return summary


def list_runs(run_root: Path) -> list[dict[str, Any]]:
    if not run_root.exists():
        return []
    runs = [run_summary(path) for path in run_root.iterdir() if path.is_dir()]
    return sorted(runs, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)


def run_status_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for run in runs:
        status = str(run.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    active = sum(by_status.get(status, 0) for status in ("running", "cancelling", "queued"))
    return {"total": len(runs), "active": active, "by_status": by_status}


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


def orchestration_view_fields(
    summary: dict[str, Any],
    active: bool = False,
    event_cursor_next: int = 0,
    final_payload: dict[str, Any] | None = None,
    final_max_bytes: int | None = None,
    task_id: str = "",
) -> dict[str, Any]:
    actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
    status = str(summary.get("status") or "")
    phase = "inspect"
    final_payload = final_payload if isinstance(final_payload, dict) else {}
    if active or status in {"running", "queued", "cancelling"}:
        phase = "running"
        next_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/orchestration", "body": {"events_after": event_cursor_next}, "reason": "run is active"}
    elif actions.get("can_start_revision"):
        phase = "revision_required"
    elif status == "completed":
        phase = "completed"
        if not final_payload:
            final_payload = {
                "summary": summary.get("final_manifest_summary") if isinstance(summary.get("final_manifest_summary"), dict) else {},
                "deliverable": "",
            }
        if final_payload.get("ok") or final_payload.get("summary"):
            body = {"max_bytes": final_max_bytes} if final_max_bytes is not None else {}
            next_action = {"kind": "inspect_final", "method": "GET", "endpoint": "GET /runs/{task_id}/final", "body": body, "reason": "run completed and final package is available"}
    elif actions.get("can_resume"):
        phase = "resume_required"
    elif actions.get("can_start"):
        phase = "ready_to_start"
    elif status in {"failed", "cancelled", "interrupted", "preflight_failed"}:
        phase = "needs_attention"
    elif status == "created":
        phase = "ready_to_preflight"
    decision = {
        "can_poll": phase == "running",
        "can_start": phase == "ready_to_start",
        "can_resume": phase == "resume_required",
        "can_execute_revision": phase == "revision_required",
        "can_inspect_final": phase == "completed" and bool(final_payload.get("ok") or final_payload.get("summary")),
        "can_inspect_diagnostics": phase in {"needs_attention", "inspect", "ready_to_preflight"},
        "recommended_kind": str(next_action.get("kind") or ""),
        "recommended_endpoint": str(next_action.get("endpoint") or ""),
    }
    snapshot = {"summary": summary, "active": active}
    resolved_task_id = task_id or str(summary.get("task_id") or "")
    return {
        "status": status,
        "phase": phase,
        "active": active,
        "decision": decision,
        "display": orchestration_display(phase, status, snapshot, next_action, final_payload),
        "next_action": next_action,
        "client_action": executable_client_action(resolved_task_id, next_action),
    }


def run_orchestration_card(run: dict[str, Any], active: bool = False) -> dict[str, Any]:
    task_id = str(run.get("task_id") or "")
    view = orchestration_view_fields(run, active=active, event_cursor_next=0, task_id=task_id)
    return {
        "task_id": task_id,
        "status": view["status"],
        "phase": view["phase"],
        "active": view["active"],
        "goal": str(run.get("goal") or ""),
        "governor": str(run.get("governor") or ""),
        "created_at": str(run.get("created_at") or ""),
        "updated_at": str(run.get("updated_at") or ""),
        "decision": view["decision"],
        "display": view["display"],
        "next_action": view["next_action"],
        "client_action": view["client_action"],
    }


def run_orchestration_cards(runs: list[dict[str, Any]], active_task_ids: list[str] | set[str] | None = None) -> list[dict[str, Any]]:
    active_set = set(active_task_ids or [])
    return [run_orchestration_card(run, active=str(run.get("task_id") or "") in active_set) for run in runs]


def recovery_candidate_display(run: dict[str, Any], resume_ready: bool, resume_errors: list[str], pending_step_ids: list[Any]) -> dict[str, Any]:
    progress = run.get("progress") if isinstance(run.get("progress"), dict) else {}
    planned_steps = int(progress.get("planned_steps") or 0)
    completed_steps = int(progress.get("completed_steps") or 0)
    failed_steps = int(progress.get("failed_steps") or 0)
    pending_steps = len(pending_step_ids) if pending_step_ids else int(progress.get("pending_steps") or 0)
    if resume_ready:
        headline = "Recovery is ready"
        detail = f"{pending_steps} pending steps can resume"
        severity = "info"
    else:
        headline = "Recovery needs inspection"
        detail = resume_errors[0] if resume_errors else "Run package cannot be resumed automatically"
        severity = "warning"
    return {
        "headline": headline,
        "detail": detail,
        "severity": severity,
        "progress": {
            "planned_steps": planned_steps,
            "completed_steps": completed_steps,
            "failed_steps": failed_steps,
            "pending_steps": pending_steps,
        },
    }


def recovery_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for run in runs:
        actions = run.get("actions") if isinstance(run.get("actions"), dict) else {}
        if run.get("status") != "interrupted" or not actions.get("can_resume"):
            continue
        next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
        resume_ready = False
        resume_errors: list[str] = []
        pending_step_ids = run.get("progress", {}).get("pending_step_ids", []) if isinstance(run.get("progress"), dict) else []
        try:
            pending_step_ids = resume_step_ids_from_run(Path(str(run.get("run_dir") or "")))
            resume_ready = True
        except Exception as exc:  # noqa: BLE001 - recovery listing should diagnose malformed run packages.
            resume_errors.append(str(exc))
        task_id = str(run.get("task_id") or "")
        candidates.append(
            {
                "task_id": task_id,
                "status": str(run.get("status") or ""),
                "updated_at": str(run.get("updated_at") or ""),
                "pending_step_ids": pending_step_ids,
                "resume_ready": resume_ready,
                "resume_errors": resume_errors,
                "next_action": next_action,
                "client_action": executable_client_action(task_id, next_action),
                "display": recovery_candidate_display(run, resume_ready, resume_errors, pending_step_ids),
            }
        )
    startable = [candidate for candidate in candidates if candidate.get("resume_ready")]
    blocked = [candidate for candidate in candidates if not candidate.get("resume_ready")]
    return {
        "recoverable": len(candidates),
        "startable": len(startable),
        "blocked": len(blocked),
        "task_ids": [candidate["task_id"] for candidate in candidates if candidate.get("task_id")],
        "candidates": candidates,
    }


def last_run_preflight(ledger: dict[str, Any]) -> dict[str, Any]:
    events = ledger.get("events") if isinstance(ledger.get("events"), list) else []
    for event in reversed(events):
        if not isinstance(event, dict) or event.get("type") != "run_preflight_recorded":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        return {"at": str(event.get("at") or ""), **payload}
    return {}


def dispatch_workers_by_step(run_dir: Path) -> dict[str, str]:
    workers: dict[str, str] = {}
    for dispatch_path in ordered_dispatch_paths(run_dir):
        packet = load_json_file(dispatch_path)
        step_id = str(packet.get("step_id") or dispatch_path.stem)
        worker = str(packet.get("worker") or "")
        if step_id:
            workers[step_id] = worker
    return workers


def validate_revision_plan(run_dir: Path, revision_plan: dict[str, Any]) -> list[str]:
    if not revision_plan.get("required"):
        return []
    raw_steps = revision_plan.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return ["revision_plan.steps must be a non-empty list when required"]
    try:
        workers_by_step = dispatch_workers_by_step(run_dir)
    except Exception as exc:  # noqa: BLE001 - summaries should report invalid run packages instead of crashing.
        return [f"revision dispatch unavailable: {exc}"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_steps):
        if not isinstance(item, dict):
            errors.append(f"revision_plan.steps[{index}] must be an object")
            continue
        step_id = str(item.get("step_id") or "").strip()
        worker = str(item.get("worker") or "").strip()
        if not step_id:
            errors.append(f"revision_plan.steps[{index}].step_id must be a non-empty string")
            continue
        if step_id in seen:
            errors.append(f"revision_plan references duplicate step: {step_id}")
        seen.add(step_id)
        expected_worker = workers_by_step.get(step_id)
        if expected_worker is None:
            errors.append(f"revision_plan references unknown dispatch step: {step_id}")
        if not worker:
            errors.append(f"revision_plan.steps[{index}].worker must be a non-empty string")
        elif expected_worker is not None and worker != expected_worker:
            errors.append(f"revision_plan worker mismatch for {step_id}: expected {expected_worker}, got {worker}")
        for field_name in ("reason", "source", "priority"):
            if field_name in item and not isinstance(item.get(field_name), str):
                errors.append(f"revision_plan.steps[{index}].{field_name} must be a string")
    return errors


def revision_plan_summary(revision_plan: dict[str, Any], revision_plan_errors: list[str] | None = None) -> dict[str, Any]:
    raw_steps = revision_plan.get("steps") if isinstance(revision_plan.get("steps"), list) else []
    steps = [item for item in raw_steps if isinstance(item, dict)]
    step_ids: list[str] = []
    workers: list[str] = []
    reasons: list[str] = []
    for item in steps:
        step_id = str(item.get("step_id") or "").strip()
        worker = str(item.get("worker") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if step_id and step_id not in step_ids:
            step_ids.append(step_id)
        if worker and worker not in workers:
            workers.append(worker)
        if reason and reason not in reasons:
            reasons.append(reason)
    errors = revision_plan_errors or []
    return {
        "required": bool(revision_plan.get("required")),
        "valid": not errors,
        "step_count": len(step_ids),
        "worker_count": len(workers),
        "step_ids": step_ids,
        "workers": workers,
        "reasons": reasons[:5],
        "errors": errors,
    }


def run_actions(
    status: str,
    revision_plan: dict[str, Any],
    revision_plan_errors: list[str] | None = None,
    package_errors: list[str] | None = None,
    oversight_errors: list[str] | None = None,
) -> dict[str, Any]:
    terminal_locked = status in {"completed", "running", "cancelling", "queued", "corrupt"}
    preflightable = status != "corrupt"
    revision_required = bool(revision_plan.get("required"))
    revision_valid = not (revision_plan_errors or [])
    package_valid = not (package_errors or [])
    oversight_valid = not (oversight_errors or [])
    resume_required = status == "interrupted"
    runnable = not terminal_locked and not revision_required and not resume_required and package_valid and oversight_valid
    revision_runnable = revision_required and revision_valid and package_valid and oversight_valid and status not in {"running", "cancelling", "queued", "corrupt"}
    actions = {
        "can_preflight_local": preflightable,
        "can_preflight_http": preflightable,
        "can_execute": runnable,
        "can_start": runnable,
        "can_cancel": status in {"running", "cancelling", "queued"},
        "can_resume": status == "interrupted" and not revision_required and package_valid and oversight_valid,
        "can_execute_revision": revision_runnable,
        "can_start_revision": revision_runnable,
        "force_required_for_rerun": status == "completed" and not revision_required,
    }
    if status == "corrupt":
        next_action = {"kind": "inspect", "method": "GET", "endpoint": "GET /runs/{task_id}", "body": {}, "reason": "run state is corrupt"}
    elif status in {"running", "queued"}:
        next_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {}, "reason": "run is already active"}
    elif status == "cancelling":
        next_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {}, "reason": "cancellation is in progress"}
    elif not package_valid:
        next_action = {"kind": "inspect_package", "method": "GET", "endpoint": "GET /runs/{task_id}/package", "body": {}, "reason": "run package is incomplete or inconsistent"}
    elif not oversight_valid:
        next_action = {"kind": "inspect_oversight", "method": "GET", "endpoint": "GET /runs/{task_id}/oversight", "body": {}, "reason": "governor oversight is missing or inconsistent"}
    elif revision_required and not revision_valid:
        next_action = {"kind": "inspect_revision", "method": "GET", "endpoint": "GET /runs/{task_id}/summary", "body": {}, "reason": "revision_plan is invalid"}
    elif revision_runnable:
        next_action = {"kind": "execute_revision", "method": "POST", "endpoint": "POST /runs/{task_id}/start_revision_http", "body": {}, "reason": "revision_plan requires selected steps to rerun"}
    elif resume_required:
        next_action = {"kind": "resume", "method": "POST", "endpoint": "POST /runs/{task_id}/start_resume_http", "body": {}, "reason": "run is interrupted and has pending steps"}
    elif actions["force_required_for_rerun"]:
        next_action = {"kind": "rerun_requires_force", "method": "POST", "endpoint": "POST /runs/{task_id}/start_http", "body": {"force": True}, "reason": "run already completed"}
    elif runnable:
        next_action = {"kind": "start", "method": "POST", "endpoint": "POST /runs/{task_id}/start_http", "body": {}, "reason": "run is ready to execute"}
    else:
        next_action = {"kind": "inspect", "method": "GET", "endpoint": "GET /runs/{task_id}/summary", "body": {}, "reason": f"no automatic action for status {status or 'unknown'}"}
    actions["next_action"] = next_action
    return actions


def action_for_mode(action: dict[str, Any], mode: str) -> dict[str, Any]:
    result = dict(action)
    endpoint = str(result.get("endpoint") or "")
    if mode == "local":
        endpoint = endpoint.replace("_http", "_local")
    elif mode == "http":
        endpoint = endpoint.replace("_local", "_http")
    result["endpoint"] = endpoint
    return result


def run_preflight_actions(preflight: dict[str, Any], run_action_hints: dict[str, Any] | None = None) -> dict[str, Any]:
    mode = str(preflight.get("mode") or "http")
    ok = bool(preflight.get("ok"))
    step_ids = preflight.get("step_ids") if isinstance(preflight.get("step_ids"), list) else []
    run_action_hints = run_action_hints or {}
    summary_next_action = run_action_hints.get("next_action") if isinstance(run_action_hints.get("next_action"), dict) else {}
    body: dict[str, Any] = {}
    if step_ids:
        body["step_ids"] = step_ids
    actions = {
        "can_start_run": ok and bool(run_action_hints.get("can_start", True)),
        "can_inspect_package": True,
        "can_inspect_oversight": True,
        "can_check_brigade_readiness": True,
    }
    if ok and actions["can_start_run"]:
        next_action = {
            "kind": "start_run",
            "method": "POST",
            "endpoint": "POST /runs/{task_id}/start_http" if mode == "http" else "POST /runs/{task_id}/start_local",
            "body": body,
            "reason": "run preflight passed",
        }
    elif ok and summary_next_action:
        next_action = action_for_mode(summary_next_action, mode)
        if body and next_action.get("kind") in {"start", "start_run", "resume", "execute_revision"}:
            next_body = next_action.get("body") if isinstance(next_action.get("body"), dict) else {}
            next_action["body"] = {**next_body, **body}
    elif preflight.get("oversight_errors"):
        next_action = {
            "kind": "inspect_oversight",
            "method": "GET",
            "endpoint": "GET /runs/{task_id}/oversight",
            "body": {},
            "reason": "run oversight failed preflight",
        }
    elif preflight.get("dispatch_errors") or preflight.get("input_failures") or preflight.get("missing_local_commands"):
        next_action = {
            "kind": "inspect_package",
            "method": "GET",
            "endpoint": "GET /runs/{task_id}/package",
            "body": {},
            "reason": "run package failed preflight",
        }
    elif preflight.get("worker_preflight_failures"):
        next_action = {
            "kind": "inspect_brigade",
            "method": "GET",
            "endpoint": "GET /brigade_health",
            "body": {},
            "reason": "worker service preflight failed",
        }
    else:
        next_action = {
            "kind": "inspect_preflight",
            "method": "POST",
            "endpoint": "POST /runs/{task_id}/preflight_http" if mode == "http" else "POST /runs/{task_id}/preflight_local",
            "body": body,
            "reason": "run preflight failed",
        }
    actions["next_action"] = next_action
    return actions


def run_contract(run_dir: Path) -> dict[str, Any]:
    contract_path = run_dir / "contract.json"
    if not contract_path.exists():
        return {"ok": False, "error": "contract not found", "error_code": "contract_not_found"}
    payload, error = load_json_object(contract_path, "contract")
    if error:
        return {"ok": False, "error": error, "error_code": "corrupt_contract"}
    return {"ok": True, "contract": payload}


def run_oversight(run_dir: Path) -> dict[str, Any]:
    oversight_path = run_dir / "oversight.json"
    if not oversight_path.exists():
        return {"ok": False, "error": "oversight not found", "error_code": "oversight_not_found"}
    payload, error = load_json_object(oversight_path, "oversight")
    if error:
        return {"ok": False, "error": error, "error_code": "corrupt_oversight"}
    return {"ok": True, "oversight": payload}


def run_package_action_errors(run_dir: Path) -> list[str]:
    if not (run_dir / "status.json").exists() or not (run_dir / "contract.json").exists():
        return []
    errors: list[str] = []
    status, status_error = load_json_object(run_dir / "status.json", "status")
    if status_error:
        errors.append(status_error)
    contract_payload = run_contract(run_dir)
    if not contract_payload.get("ok"):
        errors.append(str(contract_payload.get("error") or "contract unavailable"))
    if not status_error:
        errors.extend(run_dispatch_package_errors(run_dir, status))
    return errors


def run_oversight_diagnostics(run_dir: Path) -> dict[str, Any]:
    payload = run_oversight(run_dir)
    if not payload.get("ok"):
        return payload
    oversight = payload.get("oversight") if isinstance(payload.get("oversight"), dict) else {}
    status, status_error = load_json_object(run_dir / "status.json", "status")
    validation_errors = [status_error] if status_error else validate_oversight_against_run(run_dir, oversight, status)
    return {
        **payload,
        "summary": compact_oversight_summary(oversight),
        "validation": {"ok": not validation_errors, "errors": validation_errors},
    }


def run_package_diagnostics(run_dir: Path) -> dict[str, Any]:
    status, status_error = load_json_object(run_dir / "status.json", "status")
    contract_payload = run_contract(run_dir)
    oversight_payload = run_oversight_diagnostics(run_dir)
    dispatch_payload = run_dispatch_packets(run_dir)
    errors: list[str] = []
    if status_error:
        errors.append(status_error)
    if not contract_payload.get("ok"):
        errors.append(str(contract_payload.get("error") or "contract unavailable"))
    if not oversight_payload.get("ok"):
        errors.append(str(oversight_payload.get("error") or "oversight unavailable"))
    else:
        errors.extend(oversight_payload.get("validation", {}).get("errors", []) if isinstance(oversight_payload.get("validation"), dict) else [])
    if not status_error:
        errors.extend(run_dispatch_package_errors(run_dir, status))
    dispatch_items = dispatch_payload.get("dispatch") if isinstance(dispatch_payload.get("dispatch"), list) else []
    contract = contract_payload.get("contract") if isinstance(contract_payload.get("contract"), dict) else {}
    return {
        "ok": not errors,
        "task_id": run_dir.name,
        "run_dir": str(run_dir),
        "validation": {"ok": not errors, "errors": errors},
        "files": {
            "contract": (run_dir / "contract.json").exists(),
            "oversight": (run_dir / "oversight.json").exists(),
            "status": (run_dir / "status.json").exists(),
            "dispatch_dir": (run_dir / "dispatch").exists(),
        },
        "contract_summary": contract_summary(contract) if contract else {},
        "oversight_summary": oversight_payload.get("summary", {}) if isinstance(oversight_payload.get("summary"), dict) else {},
        "dispatch_count": len(dispatch_items),
    }


def compact_oversight_summary(oversight: dict[str, Any]) -> dict[str, Any]:
    artifact_roles = oversight.get("artifact_roles") if isinstance(oversight.get("artifact_roles"), dict) else {}
    final_review = oversight.get("final_review") if isinstance(oversight.get("final_review"), dict) else {}
    revision_policy = oversight.get("revision_policy") if isinstance(oversight.get("revision_policy"), dict) else {}
    quality_gates = oversight.get("quality_gates") if isinstance(oversight.get("quality_gates"), list) else []
    completion_criteria = oversight.get("completion_criteria") if isinstance(oversight.get("completion_criteria"), list) else []
    handoffs = oversight.get("handoffs") if isinstance(oversight.get("handoffs"), list) else []
    return {
        "kind": str(oversight.get("kind") or ""),
        "governor": str(oversight.get("governor") or ""),
        "quality_gate_count": len(quality_gates),
        "completion_criteria_count": len(completion_criteria),
        "handoff_count": len(handoffs),
        "artifact_roles": {
            "draft": artifact_roles.get("draft", []),
            "critic": artifact_roles.get("critic", []),
            "final": artifact_roles.get("final", []),
        },
        "final_review": {
            "critic_step": str(final_review.get("critic_step") or ""),
            "final_step": str(final_review.get("final_step") or ""),
            "final_artifact": str(final_review.get("final_artifact") or ""),
            "requires_critic_approval_or_blockers": bool(final_review.get("requires_critic_approval_or_blockers")),
            "requires_gap_disclosure": bool(final_review.get("requires_gap_disclosure")),
            "requires_evidence_trace": bool(final_review.get("requires_evidence_trace")),
        },
        "revision_policy": {
            "source_step": str(revision_policy.get("source_step") or ""),
            "final_steps": revision_policy.get("final_steps", []) if isinstance(revision_policy.get("final_steps"), list) else [],
            "requires_downstream_rerun": bool(revision_policy.get("requires_downstream_rerun")),
            "requires_focused_context": bool(revision_policy.get("requires_focused_context")),
            "requires_gap_disclosure": bool(revision_policy.get("requires_gap_disclosure")),
        },
    }


def run_oversight_summary(run_dir: Path) -> dict[str, Any]:
    payload = run_oversight(run_dir)
    if not payload.get("ok"):
        return {}
    oversight = payload.get("oversight") if isinstance(payload.get("oversight"), dict) else {}
    return compact_oversight_summary(oversight) if oversight else {}


def run_oversight_validation_errors(run_dir: Path, status: dict[str, Any]) -> list[str]:
    if not (run_dir / "status.json").exists() or not (run_dir / "contract.json").exists():
        return []
    payload = run_oversight(run_dir)
    if not payload.get("ok"):
        return [str(payload.get("error") or "oversight unavailable")]
    oversight = payload.get("oversight") if isinstance(payload.get("oversight"), dict) else {}
    return validate_oversight_against_run(run_dir, oversight, status)


def validate_oversight_payload(contract: dict[str, Any], oversight: dict[str, Any], status: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    governor = str(oversight.get("governor") or "")
    if governor != str(contract.get("assigned_governor") or ""):
        errors.append("oversight governor does not match contract assigned_governor")
    required_artifacts = set(contract.get("required_artifacts") if isinstance(contract.get("required_artifacts"), list) else [])
    steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    steps_by_id = {str(step.get("step_id") or ""): step for step in steps if isinstance(step, dict) and step.get("step_id")}
    final_review = oversight.get("final_review") if isinstance(oversight.get("final_review"), dict) else {}
    for field_name in ("critic_step", "final_step"):
        step_id = str(final_review.get(field_name) or "")
        if not step_id:
            errors.append(f"oversight final_review.{field_name} is required")
        elif step_id not in steps_by_id:
            errors.append(f"oversight final_review.{field_name} references unknown step: {step_id}")
    final_artifact = str(final_review.get("final_artifact") or "")
    final_step = str(final_review.get("final_step") or "")
    final_expected = steps_by_id.get(final_step, {}).get("expected_artifacts", []) if final_step in steps_by_id else []
    if not final_artifact:
        errors.append("oversight final_review.final_artifact is required")
    elif final_artifact not in required_artifacts:
        errors.append(f"oversight final artifact is not required by contract: {final_artifact}")
    elif final_artifact not in final_expected:
        errors.append(f"oversight final artifact is not produced by final step: {final_artifact}")
    revision_policy = oversight.get("revision_policy") if isinstance(oversight.get("revision_policy"), dict) else {}
    if not revision_policy:
        errors.append("oversight revision_policy is required")
    else:
        source_step = str(revision_policy.get("source_step") or "")
        if not source_step:
            errors.append("oversight revision_policy.source_step is required")
        elif source_step not in steps_by_id:
            errors.append(f"oversight revision_policy.source_step references unknown step: {source_step}")
        final_steps = revision_policy.get("final_steps")
        if not isinstance(final_steps, list) or not final_steps:
            errors.append("oversight revision_policy.final_steps must be a non-empty list")
        else:
            normalized_final_steps: list[str] = []
            for index, step_id in enumerate(final_steps):
                if not isinstance(step_id, str) or not step_id:
                    errors.append(f"oversight revision_policy.final_steps[{index}] must be a non-empty string")
                    continue
                normalized_final_steps.append(step_id)
                if step_id not in steps_by_id:
                    errors.append(f"oversight revision_policy.final_steps[{index}] references unknown step: {step_id}")
            for required_step in (str(final_review.get("critic_step") or ""), str(final_review.get("final_step") or "")):
                if required_step and required_step not in normalized_final_steps:
                    errors.append(f"oversight revision_policy.final_steps must include final_review step: {required_step}")
        for field_name in ("requires_downstream_rerun", "requires_focused_context", "requires_gap_disclosure"):
            if not isinstance(revision_policy.get(field_name), bool):
                errors.append(f"oversight revision_policy.{field_name} must be a boolean")
    handoffs = oversight.get("handoffs") if isinstance(oversight.get("handoffs"), list) else []
    for index, handoff in enumerate(handoffs):
        if not isinstance(handoff, dict):
            errors.append(f"oversight handoffs[{index}] must be an object")
            continue
        from_step = str(handoff.get("from_step") or "")
        if from_step not in steps_by_id:
            errors.append(f"oversight handoffs[{index}].from_step references unknown step: {from_step}")
        to_steps = handoff.get("to_steps") if isinstance(handoff.get("to_steps"), list) else []
        for to_step in to_steps:
            if str(to_step) not in steps_by_id:
                errors.append(f"oversight handoffs[{index}].to_steps references unknown step: {to_step}")
    return errors


def validate_oversight_against_run(run_dir: Path, oversight: dict[str, Any], status: dict[str, Any]) -> list[str]:
    contract_payload = run_contract(run_dir)
    contract = contract_payload.get("contract") if isinstance(contract_payload.get("contract"), dict) else {}
    if not contract_payload.get("ok"):
        return [str(contract_payload.get("error") or "contract unavailable")]
    return validate_oversight_payload(contract, oversight, status)


def run_dispatch_packets(run_dir: Path) -> dict[str, Any]:
    dispatch_dir = run_dir / "dispatch"
    if not dispatch_dir.exists():
        return {"ok": False, "error": "dispatch directory not found"}
    packets: list[dict[str, Any]] = []
    dispatch_paths = ordered_dispatch_paths(run_dir) if (run_dir / "status.json").exists() else sorted(dispatch_dir.glob("*.json"))
    for dispatch_path in dispatch_paths:
        try:
            packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            packets.append({"path": str(dispatch_path), "ok": False, "error": str(exc)})
            continue
        packets.append({"path": str(dispatch_path), "ok": isinstance(packet, dict), "packet": packet})
    return {"ok": True, "dispatch": packets}


def run_dispatch_package_errors(run_dir: Path, status: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    dispatch_dir = run_dir / "dispatch"
    if not dispatch_dir.exists():
        return ["dispatch directory not found"]
    steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    task_id = str(status.get("task_id") or run_dir.name)
    step_by_id = {
        str(step.get("step_id") or ""): step
        for step in steps
        if isinstance(step, dict) and str(step.get("step_id") or "")
    }
    expected_names = {
        f"{str(step.get('step_id') or '')}.json"
        for step in steps
        if isinstance(step, dict) and str(step.get("step_id") or "")
    }
    actual_names = {path.name for path in dispatch_dir.glob("*.json")}
    for name in sorted(expected_names - actual_names):
        errors.append(f"dispatch packet missing: {name}")
    for name in sorted(actual_names - expected_names):
        errors.append(f"unexpected dispatch packet: {name}")
    dispatch_payload = run_dispatch_packets(run_dir)
    if not dispatch_payload.get("ok"):
        errors.append(str(dispatch_payload.get("error") or "dispatch unavailable"))
        return errors
    dispatch_items = dispatch_payload.get("dispatch") if isinstance(dispatch_payload.get("dispatch"), list) else []
    for item in dispatch_items:
        if not isinstance(item, dict):
            continue
        if not item.get("ok"):
            path = str(item.get("path") or "")
            detail = str(item.get("error") or "dispatch packet is not valid")
            errors.append(f"{path}: {detail}" if path else detail)
            continue
        path = Path(str(item.get("path") or ""))
        packet = item.get("packet") if isinstance(item.get("packet"), dict) else {}
        packet_step_id = str(packet.get("step_id") or "")
        expected_step_id = path.stem
        if packet_step_id != expected_step_id:
            errors.append(f"dispatch step_id mismatch for {path.name}: expected {expected_step_id}, got {packet_step_id or 'missing'}")
        expected_worker = str(step_by_id.get(expected_step_id, {}).get("worker") or "")
        packet_worker = str(packet.get("worker") or "")
        if expected_worker and packet_worker != expected_worker:
            errors.append(f"dispatch worker mismatch for {expected_step_id}: expected {expected_worker}, got {packet_worker or 'missing'}")
        packet_task_id = str(packet.get("task_id") or "")
        if packet_task_id != task_id:
            errors.append(f"dispatch task_id mismatch for {expected_step_id}: expected {task_id}, got {packet_task_id or 'missing'}")
        request = packet.get("request") if isinstance(packet.get("request"), dict) else {}
        request_task_id = str(request.get("task_id") or "")
        if not request_task_id:
            errors.append(f"dispatch request.task_id missing for {expected_step_id}")
    return errors


def run_worker_tasks(run_dir: Path, include_health: bool = False, host: str = "127.0.0.1") -> dict[str, Any]:
    host = validate_service_host(host)
    dispatch_payload = run_dispatch_packets(run_dir)
    if not dispatch_payload.get("ok"):
        return dispatch_payload
    tasks: list[dict[str, Any]] = []
    for item in dispatch_payload.get("dispatch", []):
        packet = item.get("packet") if isinstance(item, dict) else {}
        if not isinstance(packet, dict):
            continue
        request_payload = packet.get("request") if isinstance(packet.get("request"), dict) else {}
        task_id = str(request_payload.get("task_id") or packet.get("task_id") or "")
        worker = str(packet.get("worker") or "")
        port = int(packet.get("port") or 0)
        task: dict[str, Any] = {
            "step_id": str(packet.get("step_id") or ""),
            "worker": worker,
            "port": port,
            "task_id": task_id,
        }
        if include_health and task_id and port:
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/tasks/{quote(task_id, safe='')}", timeout=1.0) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                task["runtime"] = payload if isinstance(payload, dict) else {"ok": False, "error": "task response is not a JSON object"}
            except Exception as exc:  # noqa: BLE001 - worker task lookup is best-effort.
                task["runtime"] = {"ok": False, "error": str(exc)}
        tasks.append(task)
    return {"ok": True, "worker_tasks": tasks}


def event_display(event: dict[str, Any], task_id: str = "") -> dict[str, Any]:
    event_type = str(event.get("type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    severity = "info"
    headline = event_type.replace("_", " ").strip().capitalize() or "Run event"
    detail = ""
    if event_type == "task_created":
        headline = "Task created"
        detail = f"Governor: {payload.get('governor') or 'unknown'}"
    elif event_type == "run_preflight_recorded":
        ok = bool(payload.get("ok"))
        headline = "Preflight passed" if ok else "Preflight needs attention"
        severity = "info" if ok else "warning"
        step_ids = payload.get("step_ids") if isinstance(payload.get("step_ids"), list) else []
        detail = f"{len(step_ids)} steps checked"
    elif event_type == "background_start_requested":
        headline = "Execution queued"
        operation = str(payload.get("operation") or "")
        mode = str(payload.get("mode") or "")
        detail = " ".join(part for part in [operation, mode] if part)
    elif event_type == "status_changed":
        status = str(payload.get("status") or "")
        headline = f"Status: {status}" if status else "Status changed"
        if status in {"failed", "corrupt"}:
            severity = "error"
        elif status in {"cancelled", "interrupted", "preflight_failed"}:
            severity = "warning"
    elif event_type == "step_recorded":
        step_id = str(payload.get("step_id") or "")
        worker = str(payload.get("worker") or "")
        status = str(payload.get("status") or "")
        headline = f"Step {status or 'recorded'}"
        detail = " / ".join(part for part in [step_id, worker] if part)
        if status in {"failed", "blocked", "needs_revision", "preflight_failed"}:
            severity = "warning"
    elif event_type == "result_recorded":
        ok = bool(payload.get("ok"))
        headline = "Result recorded" if ok else "Result failed"
        severity = "info" if ok else "error"
        detail = str(payload.get("summary") or payload.get("status") or "")
    elif event_type in {"cancel_requested", "resume_execution_requested"}:
        headline = event_type.replace("_", " ").capitalize()
        detail = str(payload.get("reason") or payload.get("mode") or "")
    return {
        "task_id": task_id,
        "at": str(event.get("at") or ""),
        "type": event_type,
        "headline": headline,
        "detail": detail,
        "severity": severity,
    }


def display_events_for(task_id: str, events: list[Any]) -> list[dict[str, Any]]:
    return [event_display(event, task_id=task_id) for event in events if isinstance(event, dict)]


def run_events(run_dir: Path, limit: int | None = None, after: int | None = None) -> dict[str, Any]:
    ledger_path = run_dir / "task_ledger.json"
    ledger, ledger_error = load_ledger_dict(ledger_path)
    if ledger_error:
        return {"ok": False, "error": ledger_error}
    events = ledger.get("events", [])
    if not isinstance(events, list):
        events = []
    total = len(events)
    start = None
    if after is not None:
        start = max(0, min(after, total))
        events = events[start:]
        if limit is not None and limit >= 0:
            events = events[:limit]
    elif limit is not None and limit >= 0:
        start = max(0, total - limit)
        events = events[-limit:]
    else:
        start = 0
    next_cursor = start + len(events)
    task_id = str(ledger.get("task_id") or run_dir.name)
    summary = run_summary(run_dir)
    actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
    return {
        "ok": True,
        "task_id": task_id,
        "events": events,
        "display_events": display_events_for(task_id, events),
        "run_client_action": executable_client_action(task_id, next_action),
        "cursor": {"after": start, "next": next_cursor, "total": total},
    }


def all_run_events(run_root: Path, limit: int | None = None, after: int | None = None) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if not run_root.exists():
        return {"ok": True, "events": [], "cursor": {"after": 0, "next": 0, "total": 0}, "errors": []}
    for run_dir in run_root.iterdir():
        if not run_dir.is_dir():
            continue
        ledger, ledger_error = load_ledger_dict(run_dir / "task_ledger.json")
        if ledger_error:
            errors.append({"task_id": run_dir.name, "error": ledger_error})
            continue
        task_id = str(ledger.get("task_id") or run_dir.name)
        run_status = str(ledger.get("status") or "")
        governor = str(ledger.get("governor") or "")
        run_updated_at = str(ledger.get("updated_at") or "")
        summary = run_summary(run_dir)
        actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
        next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
        manifest_summary = summary.get("final_manifest_summary") if isinstance(summary.get("final_manifest_summary"), dict) else {}
        raw_events = ledger.get("events") if isinstance(ledger.get("events"), list) else []
        for index, event in enumerate(raw_events):
            if not isinstance(event, dict):
                continue
            events.append(
                {
                    "task_id": task_id,
                    "run_status": run_status,
                    "governor": governor,
                    "run_updated_at": run_updated_at,
                    "event_index": index,
                    "at": str(event.get("at") or ""),
                    "type": str(event.get("type") or ""),
                    "run_next_action": next_action,
                    "run_client_action": executable_client_action(task_id, next_action),
                    "run_final_manifest_summary": manifest_summary,
                    "display": event_display(event, task_id=task_id),
                    "payload": event.get("payload") if isinstance(event.get("payload"), dict) else {},
                }
            )
    events.sort(key=lambda item: (str(item.get("at") or ""), str(item.get("task_id") or ""), int(item.get("event_index") or 0)))
    for index, event in enumerate(events):
        event["global_index"] = index
    total = len(events)
    if after is not None:
        start = max(0, min(after, total))
        selected = events[start:]
        if limit is not None and limit >= 0:
            selected = selected[:limit]
    elif limit is not None and limit >= 0:
        start = max(0, total - limit)
        selected = events[-limit:]
    else:
        start = 0
        selected = events
    return {
        "ok": True,
        "events": selected,
        "display_events": [item.get("display") for item in selected if isinstance(item.get("display"), dict)],
        "cursor": {"after": start, "next": start + len(selected), "total": total},
        "errors": errors,
    }


def run_snapshot(run_dir: Path, event_limit: int | None = None, events_after: int | None = None) -> dict[str, Any]:
    task_id = run_dir.name
    with ACTIVE_RUNS_LOCK:
        active = task_id in ACTIVE_RUNS
    payload: dict[str, Any] = {
        "ok": True,
        "task_id": task_id,
        "summary": run_summary(run_dir),
        "active": active,
    }
    events_payload = run_events(run_dir, limit=event_limit, after=events_after)
    payload["events"] = events_payload.get("events", [])
    payload["display_events"] = events_payload.get("display_events", [])
    payload["run_client_action"] = events_payload.get("run_client_action", {})
    payload["event_cursor"] = events_payload.get("cursor", {"after": 0, "next": 0, "total": 0})
    payload["revision_plan"] = payload["summary"].get("revision_plan", {"required": False, "steps": []})
    payload["revision_plan_summary"] = payload["summary"].get("revision_plan_summary", {})
    if not events_payload.get("ok"):
        payload["events_error"] = events_payload.get("error", "events unavailable")
    ledger_path = run_dir / "task_ledger.json"
    ledger, ledger_error = load_ledger_dict(ledger_path)
    if ledger_error:
        payload["artifacts_error"] = ledger_error
        payload["artifacts"] = []
    else:
        payload.update(artifact_status(ledger))
    return payload


def orchestration_display(
    phase: str,
    status: str,
    snapshot: dict[str, Any],
    next_action: dict[str, Any],
    final_payload: dict[str, Any],
) -> dict[str, Any]:
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    progress = summary.get("progress") if isinstance(summary.get("progress"), dict) else {}
    planned_steps = int(progress.get("planned_steps") or 0)
    completed_steps = int(progress.get("completed_steps") or 0)
    failed_steps = int(progress.get("failed_steps") or 0)
    pending_steps = int(progress.get("pending_steps") or 0)
    next_step_id = str(progress.get("next_ready_step_id") or progress.get("next_step_id") or "")
    next_worker = ""
    step_states = progress.get("step_states") if isinstance(progress.get("step_states"), list) else []
    for step in step_states:
        if isinstance(step, dict) and step.get("step_id") == next_step_id:
            next_worker = str(step.get("worker") or "")
            break
    final_summary = final_payload.get("summary") if isinstance(final_payload.get("summary"), dict) else {}
    final_deliverable = str(final_payload.get("deliverable") or "")
    revision_summary = summary.get("revision_plan_summary") if isinstance(summary.get("revision_plan_summary"), dict) else {}
    headlines = {
        "running": "Run is active",
        "completed": "Run completed",
        "ready_to_start": "Run is ready to start",
        "resume_required": "Run can be resumed",
        "revision_required": "Revision is required",
        "needs_attention": "Run needs attention",
        "ready_to_preflight": "Run needs preflight",
        "inspect": "Inspect run state",
    }
    headline = headlines.get(phase, "Inspect run state")
    if phase == "running" and planned_steps:
        detail = f"{completed_steps}/{planned_steps} steps complete"
    elif phase == "completed" and final_summary:
        detail = f"Final package status: {final_summary.get('status') or 'unknown'}"
    elif phase == "revision_required":
        detail = f"{int(revision_summary.get('step_count') or 0)} revision steps ready"
    elif phase == "resume_required":
        detail = f"{pending_steps} pending steps can resume"
    elif phase == "ready_to_start":
        detail = "Preflight passed; execution can start"
    elif phase == "needs_attention":
        detail = str(next_action.get("reason") or "Diagnostics are required")
    else:
        detail = str(next_action.get("reason") or status or phase)
    severity = "info"
    if phase in {"needs_attention", "revision_required"} or failed_steps:
        severity = "warning"
    if status in {"failed", "corrupt"}:
        severity = "error"
    return {
        "headline": headline,
        "detail": detail,
        "severity": severity,
        "progress": {
            "planned_steps": planned_steps,
            "completed_steps": completed_steps,
            "failed_steps": failed_steps,
            "pending_steps": pending_steps,
        },
        "next_step": {
            "step_id": next_step_id,
            "worker": next_worker,
        },
        "final_deliverable": final_deliverable,
    }


def orchestration_state(run_dir: Path, event_limit: int | None = 20, events_after: int | None = 0, max_bytes: int = 2000) -> dict[str, Any]:
    snapshot = run_snapshot(run_dir, event_limit=event_limit, events_after=events_after)
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    status = str(summary.get("status") or "")
    final_payload: dict[str, Any] = {}
    if status == "completed":
        ledger_path = run_dir / "task_ledger.json"
        ledger, ledger_error = load_ledger_dict(ledger_path)
        if ledger_error:
            final_payload = {"ok": False, "error": ledger_error}
        else:
            final_payload = final_package(ledger, max_bytes=max_bytes)
    view = orchestration_view_fields(
        summary,
        active=bool(snapshot.get("active")),
        event_cursor_next=int(snapshot.get("event_cursor", {}).get("next", 0)),
        final_payload=final_payload,
        final_max_bytes=max_bytes,
        task_id=run_dir.name,
    )
    return {
        "ok": True,
        "task_id": run_dir.name,
        "phase": view["phase"],
        "status": view["status"],
        "active": view["active"],
        "decision": view["decision"],
        "display": view["display"],
        "display_events": snapshot.get("display_events", []),
        "snapshot": snapshot,
        "final": final_payload,
        "next_action": view["next_action"],
        "client_action": view["client_action"],
    }


def run_step_state(run_dir: Path, step_id: str) -> dict[str, Any]:
    summary = run_summary(run_dir)
    for step in summary.get("progress", {}).get("step_states", []):
        if isinstance(step, dict) and step.get("step_id") == step_id:
            return {"ok": True, "task_id": run_dir.name, "step": step, "summary": summary}
    return {"ok": False, "task_id": run_dir.name, "error": "step not found", "step_id": step_id}


def run_step_artifacts(run_dir: Path, step_id: str) -> dict[str, Any]:
    state = run_step_state(run_dir, step_id)
    if not state.get("ok"):
        return state
    step = state.get("step") if isinstance(state.get("step"), dict) else {}
    return {
        "ok": True,
        "task_id": run_dir.name,
        "step_id": step_id,
        "worker": step.get("worker", ""),
        "status": step.get("status", ""),
        "input_artifacts": step.get("input_artifacts", []),
        "input_artifact_status": step.get("input_artifact_status", []),
        "expected_artifacts": step.get("expected_artifacts", []),
        "expected_artifact_status": step.get("expected_artifact_status", []),
        "artifacts": step.get("artifacts", []),
        "artifact_status": step.get("artifact_status", []),
    }


def run_execution_preflight(
    run_dir: Path,
    mode: str,
    workspace_root: Path | None = None,
    host: str = "127.0.0.1",
    timeout_sec: int = 10,
    step_ids: list[str] | None = None,
) -> dict[str, Any]:
    if mode not in {"local", "http"}:
        raise ValueError("mode must be local or http")
    host = validate_service_host(host)
    status = load_json_file(run_dir / "status.json")
    planned_steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    selected = set(step_ids or [])
    order = [
        str(step.get("step_id") or "")
        for step in planned_steps
        if isinstance(step, dict) and step.get("step_id") and (not selected or str(step.get("step_id") or "") in selected)
    ]
    selected_order = {step_id: index for index, step_id in enumerate(order)}
    producer_by_artifact: dict[str, str] = {}
    for step in planned_steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("step_id") or "")
        expected = step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []
        for artifact in expected:
            if isinstance(artifact, str):
                producer_by_artifact[artifact] = step_id

    dispatch_errors: list[dict[str, Any]] = []
    input_failures: list[dict[str, Any]] = []
    step_checks: list[dict[str, Any]] = []
    missing_local_commands: list[dict[str, Any]] = []
    oversight_errors: list[str] = []
    oversight_payload = run_oversight(run_dir)
    if not oversight_payload.get("ok"):
        oversight_errors.append(str(oversight_payload.get("error") or "oversight unavailable"))
    else:
        oversight = oversight_payload.get("oversight") if isinstance(oversight_payload.get("oversight"), dict) else {}
        oversight_errors.extend(validate_oversight_against_run(run_dir, oversight, status))
    for dispatch_path in ordered_dispatch_paths(run_dir, step_ids=step_ids):
        try:
            packet = load_json_file(dispatch_path)
        except Exception as exc:  # noqa: BLE001 - preflight reports all unreadable dispatch packets.
            dispatch_errors.append({"dispatch": str(dispatch_path), "error": str(exc)})
            continue
        step_id = str(packet.get("step_id") or dispatch_path.stem)
        worker = str(packet.get("worker") or "")
        request = packet.get("request") if isinstance(packet.get("request"), dict) else packet
        input_artifacts = request.get("input_artifacts") if isinstance(request.get("input_artifacts"), list) else []
        input_status: list[dict[str, Any]] = []
        for artifact in input_artifacts:
            artifact_text = str(artifact)
            producer = producer_by_artifact.get(artifact_text, "")
            produced_by_selected = producer in selected_order and selected_order[producer] < selected_order.get(step_id, -1)
            status_item: dict[str, Any] = {
                "path": artifact_text,
                "producer_step_id": producer,
                "produced_by_selected_step": produced_by_selected,
            }
            if produced_by_selected:
                status_item["exists"] = None
                status_item["source"] = "selected_dependency"
            elif workspace_root is not None:
                errors = input_artifact_errors({"input_artifacts": [artifact]}, workspace_root)
                status_item.update(sandbox_artifact_file_status(str(workspace_root), artifact_text))
                if errors:
                    status_item["errors"] = errors
                    input_failures.append({"step_id": step_id, "worker": worker, "path": artifact_text, "errors": errors})
            else:
                status_item["exists"] = None
                status_item["source"] = "workspace_unknown"
            input_status.append(status_item)
        if mode == "local" and worker not in WORKER_COMMANDS:
            missing_local_commands.append({"step_id": step_id, "worker": worker, "error": "no local command registered"})
        step_checks.append(
            {
                "step_id": step_id,
                "worker": worker,
                "dispatch": str(dispatch_path),
                "input_artifacts": input_artifacts,
                "input_artifact_status": input_status,
            }
        )
    worker_failures = preflight_http_workers(run_dir, host, timeout_sec, step_ids=step_ids) if mode == "http" else []
    preflight = {
        "ok": not dispatch_errors and not input_failures and not missing_local_commands and not worker_failures and not oversight_errors,
        "task_id": run_dir.name,
        "mode": mode,
        "run_dir": str(run_dir),
        "host": host if mode == "http" else "",
        "workspace_root": str(workspace_root) if workspace_root is not None else "",
        "step_ids": order,
        "steps": step_checks,
        "dispatch_errors": dispatch_errors,
        "oversight_errors": oversight_errors,
        "oversight_summary": run_oversight_summary(run_dir) if not oversight_errors else {},
        "input_failures": input_failures,
        "missing_local_commands": missing_local_commands,
        "worker_preflight_failures": worker_failures,
    }
    summary = run_summary(run_dir)
    preflight["run_status"] = str(summary.get("status") or "")
    preflight["run_next_action"] = summary.get("actions", {}).get("next_action", {}) if isinstance(summary.get("actions"), dict) else {}
    preflight["actions"] = run_preflight_actions(
        preflight,
        summary.get("actions") if isinstance(summary.get("actions"), dict) else {},
    )
    return preflight


def planned_step_ids_from_run(run_dir: Path) -> list[str]:
    status = load_json_file(run_dir / "status.json")
    steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    return [
        str(step.get("step_id") or "")
        for step in steps
        if isinstance(step, dict) and str(step.get("step_id") or "")
    ]


def validate_requested_step_ids(run_dir: Path, requested: list[str], allowed: list[str] | None = None) -> None:
    available = planned_step_ids_from_run(run_dir)
    unknown = [step_id for step_id in requested if step_id not in available]
    if unknown:
        raise ValueError(f"step_ids reference unknown run steps: {unknown}")
    if allowed is not None:
        blocked = [step_id for step_id in requested if step_id not in allowed]
        if blocked:
            raise ValueError(f"step_ids are not valid for this execution mode: {blocked}")


def record_run_preflight_event(run_dir: Path, preflight: dict[str, Any]) -> None:
    ledger_path = run_dir / "task_ledger.json"
    if not ledger_path.exists():
        return
    payload = {
        "mode": str(preflight.get("mode") or ""),
        "ok": bool(preflight.get("ok")),
        "step_ids": preflight.get("step_ids") if isinstance(preflight.get("step_ids"), list) else [],
        "dispatch_errors": len(preflight.get("dispatch_errors") if isinstance(preflight.get("dispatch_errors"), list) else []),
        "oversight_errors": len(preflight.get("oversight_errors") if isinstance(preflight.get("oversight_errors"), list) else []),
        "input_failures": len(preflight.get("input_failures") if isinstance(preflight.get("input_failures"), list) else []),
        "missing_local_commands": len(preflight.get("missing_local_commands") if isinstance(preflight.get("missing_local_commands"), list) else []),
        "worker_preflight_failures": len(preflight.get("worker_preflight_failures") if isinstance(preflight.get("worker_preflight_failures"), list) else []),
    }
    TaskLedger.load(ledger_path).record_event("run_preflight_recorded", payload)


def orchestrate_prepare_task(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor_transport: str = "local",
    governor_host: str = "127.0.0.1",
    run_mode: str = "http",
    host: str = "127.0.0.1",
    timeout_sec: int = 30,
    include_brigade_health: bool = False,
) -> dict[str, Any]:
    if run_mode not in {"local", "http"}:
        raise ValueError("run_mode must be local or http")
    trace: list[dict[str, Any]] = []
    task_preflight = preflight_task(
        message,
        task_id,
        run_root,
        governor_transport=governor_transport,
        governor_host=governor_host,
        include_brigade_health=include_brigade_health,
    )
    task_preflight_actions = task_preflight.get("actions") if isinstance(task_preflight.get("actions"), dict) else {}
    trace.append({"stage": "task_preflight", "ok": bool(task_preflight.get("ok")), "next_action": task_preflight_actions.get("next_action", {})})
    if not task_preflight.get("ok"):
        next_action = task_preflight_actions.get("next_action", {}) if isinstance(task_preflight_actions.get("next_action"), dict) else {}
        return {
            "ok": False,
            "phase": "task_preflight",
            "trace": trace,
            "task_preflight": task_preflight,
            "actions": task_preflight_actions,
            "next_action": next_action,
            "client_action": executable_client_action(str(task_preflight.get("task_id") or task_id or ""), next_action),
        }
    task = prepare_task(message, task_id, run_root, governor_transport=governor_transport, governor_host=governor_host)
    task_actions = task.get("actions") if isinstance(task.get("actions"), dict) else {}
    trace.append({"stage": "task", "ok": bool(task.get("ok")), "task_id": str(task.get("task_id") or ""), "next_action": task_actions.get("next_action", {})})
    if not task.get("ok"):
        next_action = task_actions.get("next_action", {}) if isinstance(task_actions.get("next_action"), dict) else {}
        return {
            "ok": False,
            "phase": "task",
            "trace": trace,
            "task_preflight": task_preflight,
            "task": task,
            "actions": task_actions,
            "next_action": next_action,
            "client_action": executable_client_action(str(task.get("task_id") or task_id or ""), next_action),
        }
    run_dir = Path(str(task.get("run_dir") or ""))
    if not run_dir.exists():
        next_action = {"kind": "inspect_existing_run", "method": "GET", "endpoint": "GET /runs/{task_id}/summary", "body": {}, "reason": "run directory is missing after task creation"}
        return {
            "ok": False,
            "phase": "task",
            "trace": trace,
            "task_preflight": task_preflight,
            "task": task,
            "error": "task did not create a run directory",
            "next_action": next_action,
            "client_action": executable_client_action(str(task.get("task_id") or task_id or ""), next_action),
        }
    preflight_workspace = resolve_run_child_path(run_dir, "", "work") if run_mode == "local" else None
    run_preflight = run_execution_preflight(
        run_dir,
        mode=run_mode,
        workspace_root=preflight_workspace,
        host=host,
        timeout_sec=timeout_sec,
    )
    record_run_preflight_event(run_dir, run_preflight)
    run_preflight_actions = run_preflight.get("actions") if isinstance(run_preflight.get("actions"), dict) else {}
    trace.append(
        {
            "stage": "run_preflight",
            "ok": bool(run_preflight.get("ok")),
            "task_id": str(run_preflight.get("task_id") or task.get("task_id") or ""),
            "next_action": run_preflight_actions.get("next_action", {}),
        }
    )
    next_action = run_preflight_actions.get("next_action", {}) if isinstance(run_preflight_actions.get("next_action"), dict) else {}
    prepared_task_id = str(task.get("task_id") or run_preflight.get("task_id") or "")
    return {
        "ok": bool(run_preflight.get("ok")),
        "phase": "ready_to_start" if run_preflight.get("ok") else "run_preflight",
        "task_id": prepared_task_id,
        "run_dir": str(run_dir),
        "run_mode": run_mode,
        "trace": trace,
        "task_preflight": task_preflight,
        "task": task,
        "run_preflight": run_preflight,
        "actions": run_preflight_actions,
        "next_action": next_action,
        "client_action": executable_client_action(prepared_task_id, next_action),
    }


def orchestrate_run_task(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor_transport: str = "local",
    governor_host: str = "127.0.0.1",
    run_mode: str = "http",
    host: str = "127.0.0.1",
    timeout_sec: int = 1800,
    include_brigade_health: bool = False,
    auto_start: bool = True,
    force: bool = False,
    reuse_existing: bool = True,
) -> dict[str, Any]:
    prepare_timeout_sec = max(1, min(int(timeout_sec), 7200))
    prepared = orchestrate_prepare_task(
        message,
        task_id,
        run_root,
        governor_transport=governor_transport,
        governor_host=governor_host,
        run_mode=run_mode,
        host=host,
        timeout_sec=min(prepare_timeout_sec, 300),
        include_brigade_health=include_brigade_health,
    )
    trace = list(prepared.get("trace") if isinstance(prepared.get("trace"), list) else [])
    run_task_id = str(prepared.get("task_id") or task_id or "")
    if not prepared.get("ok"):
        task_preflight = prepared.get("task_preflight") if isinstance(prepared.get("task_preflight"), dict) else {}
        if reuse_existing and task_preflight.get("error_code") == "task_exists" and task_id:
            run_dir = run_root / task_id
            state = orchestration_state(run_dir, event_limit=5, events_after=0) if run_dir.exists() else {}
            decision = state.get("decision") if isinstance(state.get("decision"), dict) else {}
            should_start = auto_start and (
                bool(decision.get("can_start"))
                or bool(decision.get("can_resume"))
                or bool(decision.get("can_execute_revision"))
                or force
            )
            if should_start:
                started = orchestrate_start_run(
                    run_root,
                    task_id,
                    run_mode=run_mode,
                    host=host,
                    timeout_sec=prepare_timeout_sec,
                    force=force,
                )
                trace.append(
                    {
                        "stage": "orchestrate_start",
                        "ok": bool(started.get("ok")),
                        "task_id": task_id,
                        "next_action": started.get("next_action") if isinstance(started.get("next_action"), dict) else {},
                    }
                )
                state = orchestration_state(run_dir, event_limit=5, events_after=0) if run_dir.exists() else {}
                return {
                    "ok": bool(started.get("ok")),
                    "phase": "started" if started.get("ok") else "existing_run",
                    "task_id": task_id,
                    "run_mode": run_mode,
                    "reused_existing": True,
                    "trace": trace,
                    "prepare": prepared,
                    "start": started,
                    "orchestration": state,
                    "decision": state.get("decision", {}) if isinstance(state, dict) else {},
                    "display": state.get("display", {}) if isinstance(state, dict) else {},
                    "display_events": state.get("display_events", []) if isinstance(state, dict) else [],
                    "next_action": started.get("next_action") if isinstance(started.get("next_action"), dict) else state.get("next_action", {}),
                    "client_action": state.get("client_action", {}) if isinstance(state, dict) else {},
                }
            return {
                "ok": True,
                "phase": "existing_run",
                "task_id": task_id,
                "run_mode": run_mode,
                "reused_existing": True,
                "trace": trace,
                "prepare": prepared,
                "orchestration": state,
                "decision": state.get("decision", {}) if isinstance(state, dict) else {},
                "display": state.get("display", {}) if isinstance(state, dict) else {},
                "display_events": state.get("display_events", []) if isinstance(state, dict) else [],
                "next_action": state.get("next_action", {}) if isinstance(state, dict) else {},
                "client_action": state.get("client_action", {}) if isinstance(state, dict) else {},
            }
        return {
            "ok": False,
            "phase": str(prepared.get("phase") or "prepare_failed"),
            "task_id": run_task_id,
            "trace": trace,
            "prepare": prepared,
            "next_action": prepared.get("next_action") if isinstance(prepared.get("next_action"), dict) else {},
            "client_action": prepared.get("client_action") if isinstance(prepared.get("client_action"), dict) else {},
        }
    if not auto_start:
        state = orchestration_state(run_root / run_task_id, event_limit=5, events_after=0)
        return {
            "ok": True,
            "phase": "ready_to_start",
            "task_id": run_task_id,
            "trace": trace,
            "prepare": prepared,
            "next_action": prepared.get("next_action") if isinstance(prepared.get("next_action"), dict) else {},
            "orchestration": state,
            "decision": state.get("decision", {}),
            "display": state.get("display", {}),
            "display_events": state.get("display_events", []),
            "client_action": state.get("client_action", {}),
        }
    started = orchestrate_start_run(
        run_root,
        run_task_id,
        run_mode=run_mode,
        host=host,
        timeout_sec=prepare_timeout_sec,
        force=force,
    )
    trace.append(
        {
            "stage": "orchestrate_start",
            "ok": bool(started.get("ok")),
            "task_id": run_task_id,
            "next_action": started.get("next_action") if isinstance(started.get("next_action"), dict) else {},
        }
    )
    run_dir = run_root / run_task_id
    state = orchestration_state(run_dir, event_limit=5, events_after=0) if run_dir.exists() else {}
    return {
        "ok": bool(started.get("ok")),
        "phase": "started" if started.get("ok") else "start_failed",
        "task_id": run_task_id,
        "run_mode": run_mode,
        "trace": trace,
        "prepare": prepared,
        "start": started,
        "orchestration": state,
        "decision": state.get("decision", {}) if isinstance(state, dict) else {},
        "display": state.get("display", {}) if isinstance(state, dict) else {},
        "display_events": state.get("display_events", []) if isinstance(state, dict) else [],
        "next_action": started.get("next_action") if isinstance(started.get("next_action"), dict) else {},
        "client_action": state.get("client_action", {}) if isinstance(state, dict) else {},
    }


def revision_step_ids_from_run(run_dir: Path) -> list[str]:
    ledger_path = run_dir / "task_ledger.json"
    ledger, ledger_error = load_ledger_dict(ledger_path)
    if ledger_error:
        raise ValueError(f"ledger unavailable for revision execution: {ledger_error}")
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    revision_plan = result.get("revision_plan") if isinstance(result.get("revision_plan"), dict) else {}
    if not revision_plan.get("required"):
        raise ValueError("run does not have a required revision_plan")
    revision_errors = validate_revision_plan(run_dir, revision_plan)
    if revision_errors:
        raise ValueError(f"revision_plan is invalid: {revision_errors}")
    raw_steps = revision_plan.get("steps", [])
    if not isinstance(raw_steps, list):
        raise ValueError("revision_plan.steps must be a list")
    requested: list[str] = []
    for item in raw_steps:
        if isinstance(item, dict):
            step_id = str(item.get("step_id") or "").strip()
            if step_id and step_id not in requested:
                requested.append(step_id)
    oversight_payload = run_oversight(run_dir)
    oversight = oversight_payload.get("oversight") if isinstance(oversight_payload.get("oversight"), dict) else {}
    revision_policy = oversight.get("revision_policy") if isinstance(oversight.get("revision_policy"), dict) else {}
    policy_final_steps = revision_policy.get("final_steps") if isinstance(revision_policy.get("final_steps"), list) else []
    final_steps = [str(step_id) for step_id in policy_final_steps if isinstance(step_id, str) and step_id] or ["critic_review", "finalize"]
    for final_step in final_steps:
        if final_step not in requested:
            requested.append(final_step)
    available = {
        path.stem
        for path in ordered_dispatch_paths(run_dir)
    }
    missing = [step_id for step_id in requested if step_id not in available]
    if missing:
        raise ValueError(f"revision_plan references unknown dispatch steps: {missing}")
    return requested


def resume_step_ids_from_run(run_dir: Path) -> list[str]:
    status, status_error = load_json_object(run_dir / "status.json", "status")
    if status_error:
        raise ValueError(f"status unavailable for resume execution: {status_error}")
    ledger, ledger_error = load_ledger_dict(run_dir / "task_ledger.json")
    if ledger_error:
        raise ValueError(f"ledger unavailable for resume execution: {ledger_error}")
    progress = run_progress(status, ledger)
    pending = [str(step_id) for step_id in progress.get("pending_step_ids", []) if step_id]
    if not pending:
        raise ValueError("run has no pending steps to resume")
    return pending


def recover_stale_runs(run_root: Path) -> list[dict[str, Any]]:
    recovered: list[dict[str, Any]] = []
    if not run_root.exists():
        return recovered
    with ACTIVE_RUNS_LOCK:
        active = set(ACTIVE_RUNS)
    for run_dir in run_root.iterdir():
        if not run_dir.is_dir() or run_dir.name in active:
            continue
        ledger_path = run_dir / "task_ledger.json"
        if not ledger_path.exists():
            continue
        try:
            ledger = TaskLedger.load(ledger_path)
        except Exception:  # noqa: BLE001 - corrupt runs are reported by run_summary and must not block recovery.
            recovered.append(run_summary(run_dir))
            continue
        if ledger.data.get("status") in {"running", "cancelling"}:
            ledger.set_status("interrupted")
            ledger.record_event("recovered_stale_run", {"reason": "gateway process has no active worker thread"})
            recovered.append(run_summary(run_dir))
    return recovered


def prepare_run_root(run_root: Path, recover_stale_on_start: bool = True) -> list[dict[str, Any]]:
    run_root.mkdir(parents=True, exist_ok=True)
    if not recover_stale_on_start:
        return []
    return recover_stale_runs(run_root)


def artifact_status(ledger: dict[str, Any]) -> dict[str, Any]:
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    workspace_root = str(result.get("workspace_root") or "")
    artifacts = result.get("artifacts", [])
    if not isinstance(artifacts, list):
        artifacts = []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append_artifact(sandbox_path: str, source: str, extra: dict[str, Any] | None = None) -> None:
        if sandbox_path in seen:
            return
        seen.add(sandbox_path)
        item = sandbox_artifact_file_status(workspace_root, sandbox_path)
        item["source"] = source
        if extra:
            item.update(extra)
        items.append(item)

    for artifact in artifacts:
        sandbox_path = str(artifact)
        append_artifact(sandbox_path, "result")
        if workspace_root and sandbox_path.endswith("/final_manifest.json") and sandbox_path.startswith("/work/"):
            manifest_path = Path(workspace_root) / sandbox_path.removeprefix("/work/")
            if manifest_path.exists():
                manifest_error = ""
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    manifest = {}
                    manifest_error = str(exc)
                if isinstance(manifest, dict):
                    for item in items:
                        if item.get("path") == sandbox_path:
                            if manifest_error:
                                item["manifest_error"] = manifest_error
                            else:
                                item["manifest_summary"] = compact_manifest_summary(manifest)
                            break
                files = manifest.get("files") if isinstance(manifest, dict) else []
                for file_item in files if isinstance(files, list) else []:
                    if isinstance(file_item, dict):
                        package_path = str(file_item.get("path") or "")
                        if package_path:
                            append_artifact(package_path, "final_manifest")
    return {"workspace_root": workspace_root, "artifacts": items}


def compact_manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": manifest.get("status", ""),
        "approved": bool(manifest.get("approved")),
        "critic_status": manifest.get("critic_status", ""),
        "critic_metrics": manifest.get("critic_metrics", {}),
        "revision_focus": manifest.get("revision_focus", {}),
        "warnings": manifest.get("warnings", []),
        "blockers": manifest.get("blockers", []),
    }


def final_manifest_summary(result: dict[str, Any]) -> dict[str, Any]:
    workspace_root = str(result.get("workspace_root") or "")
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
    manifest_artifact = next((str(path) for path in artifacts if str(path).endswith("/final_manifest.json")), "")
    if not workspace_root or not manifest_artifact.startswith("/work/"):
        return {}
    manifest_path = Path(workspace_root) / manifest_artifact.removeprefix("/work/")
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return compact_manifest_summary(manifest) if isinstance(manifest, dict) else {}


def resolve_artifact(ledger: dict[str, Any], artifact_path: str) -> Path:
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    workspace_root = str(result.get("workspace_root") or "")
    if not workspace_root:
        raise ValueError("workspace_root is not recorded for this run")
    if not artifact_path.startswith("/work/"):
        raise ValueError("artifact path must start with /work/")
    root = Path(workspace_root).resolve()
    host_path = (root / artifact_path.removeprefix("/work/")).resolve()
    if root not in host_path.parents and host_path != root:
        raise ValueError("artifact path escapes workspace_root")
    return host_path


def artifact_text(ledger: dict[str, Any], artifact_path: str, max_bytes: int = 500000) -> dict[str, Any]:
    host_path = resolve_artifact(ledger, artifact_path)
    if not host_path.exists():
        return {"ok": False, "error": "artifact not found", "path": artifact_path}
    data = host_path.read_bytes()[: max_bytes + 1]
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    return {
        "ok": True,
        "path": artifact_path,
        "host_path": str(host_path),
        "bytes": host_path.stat().st_size,
        "truncated": truncated,
        "text": data.decode("utf-8", errors="replace"),
    }


def final_package(ledger: dict[str, Any], max_bytes: int = 20000) -> dict[str, Any]:
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
    manifest_artifact = next((str(path) for path in artifacts if str(path).endswith("/final_manifest.json")), "")
    if not manifest_artifact:
        return {"ok": False, "error": "final manifest is not recorded"}
    manifest_path = resolve_artifact(ledger, manifest_artifact)
    if not manifest_path.exists():
        return {"ok": False, "error": "final manifest not found", "path": manifest_artifact}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"final manifest is corrupt: {exc}", "path": manifest_artifact}
    if not isinstance(manifest, dict):
        return {"ok": False, "error": "final manifest is not a JSON object", "path": manifest_artifact}
    files: list[dict[str, Any]] = []
    raw_files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            continue
        sandbox_path = str(raw_file.get("path") or "")
        if not sandbox_path:
            continue
        item = {**raw_file, **sandbox_artifact_file_status(str(result.get("workspace_root") or ""), sandbox_path)}
        if item.get("exists"):
            preview = artifact_text(ledger, sandbox_path, max_bytes=max_bytes)
            if preview.get("ok"):
                item["preview"] = {
                    "bytes": preview.get("bytes", 0),
                    "truncated": bool(preview.get("truncated")),
                    "text": preview.get("text", ""),
                }
        files.append(item)
    return {
        "ok": True,
        "manifest_path": manifest_artifact,
        "host_path": str(manifest_path),
        "summary": compact_manifest_summary(manifest),
        "deliverable": str(manifest.get("deliverable") or ""),
        "manifest": manifest,
        "files": files,
    }


def fetch_json_endpoint(url: str, timeout_sec: float = 1.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout_sec) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("response is not a JSON object")
    return payload


def fetch_worker_health(host: str, port: int, timeout_sec: float = 1.0) -> dict[str, Any]:
    try:
        payload = fetch_json_endpoint(f"http://{host}:{port}/health", timeout_sec=timeout_sec)
        return {"reachable": bool(payload.get("ok")), "health": payload}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return {"reachable": False, "error": str(exc)}


def fetch_service_capabilities(host: str, port: int, timeout_sec: float = 1.0) -> dict[str, Any]:
    try:
        payload = fetch_json_endpoint(f"http://{host}:{port}/capabilities", timeout_sec=timeout_sec)
        return {"ok": bool(payload.get("ok")), "capabilities": payload}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def required_workers_from_capabilities(payload: dict[str, Any]) -> list[str]:
    raw_required = payload.get("required_workers") if isinstance(payload.get("required_workers"), list) else []
    return [str(worker) for worker in raw_required if str(worker)]


def worker_availability(required_workers: list[str]) -> dict[str, Any]:
    enriched_workers = {str(worker.get("name") or ""): worker for worker in worker_registry_snapshot(include_health=False)}
    missing: list[str] = []
    unavailable: list[dict[str, Any]] = []
    available: list[str] = []
    for worker in required_workers:
        entry = enriched_workers.get(worker)
        if entry is None:
            missing.append(worker)
            continue
        status = str(entry.get("status") or "")
        if status == "planned":
            unavailable.append(
                {
                    "name": worker,
                    "status": status,
                    "port": entry.get("port"),
                    "role": entry.get("role", ""),
                    "path": entry.get("path", ""),
                }
            )
            continue
        available.append(worker)
    return {
        "ok": not missing and not unavailable,
        "required_workers": required_workers,
        "available_workers": available,
        "missing_workers": missing,
        "unavailable_workers": unavailable,
    }


def contract_required_workers(contract: dict[str, Any]) -> list[str]:
    worker_plan = contract.get("worker_plan") if isinstance(contract.get("worker_plan"), list) else []
    required = []
    for step in worker_plan:
        if isinstance(step, dict):
            worker = str(step.get("worker") or "")
            if worker and worker not in required:
                required.append(worker)
    return required


def missing_contract_workers(contract: dict[str, Any]) -> list[str]:
    return list(worker_availability(contract_required_workers(contract))["missing_workers"])


def contract_summary(contract: dict[str, Any]) -> dict[str, Any]:
    worker_plan = contract.get("worker_plan") if isinstance(contract.get("worker_plan"), list) else []
    steps = [
        {
            "step_id": str(step.get("step_id") or ""),
            "worker": str(step.get("worker") or ""),
            "depends_on": step.get("depends_on") if isinstance(step.get("depends_on"), list) else [],
            "expected_artifacts": step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else [],
            "expected_artifact_count": len(step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []),
        }
        for step in worker_plan
        if isinstance(step, dict)
    ]
    return {
        "kind": str(contract.get("kind") or ""),
        "goal": str(contract.get("goal") or ""),
        "assigned_governor": str(contract.get("assigned_governor") or ""),
        "steps": steps,
        "step_count": len(steps),
        "required_artifacts": len(contract.get("required_artifacts") if isinstance(contract.get("required_artifacts"), list) else []),
    }


def enrich_worker_metadata(worker: dict[str, Any]) -> dict[str, Any]:
    worker_path = REPO_ROOT / str(worker.get("path") or "") / "worker.json"
    if not worker_path.exists():
        return {**worker, "metadata_available": False}
    try:
        metadata = json.loads(worker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**worker, "metadata_available": False, "metadata_error": str(exc)}
    if not isinstance(metadata, dict):
        return {**worker, "metadata_available": False, "metadata_error": "worker metadata is not an object"}
    return {
        **worker,
        "metadata_available": True,
        "status": metadata.get("status", ""),
        "capabilities": metadata.get("capabilities", []),
        "api_contract": metadata.get("api_contract", ""),
        "metadata": metadata,
    }


def worker_registry_snapshot(include_health: bool = False, host: str = "127.0.0.1") -> list[dict[str, Any]]:
    workers = [enrich_worker_metadata(worker.to_dict()) for worker in worker_refs()]
    if not include_health:
        return workers
    for worker in workers:
        worker["runtime"] = fetch_worker_health(host, int(worker["port"]))
    return workers


def governor_registry_snapshot(include_health: bool = False, host: str = "127.0.0.1") -> list[dict[str, Any]]:
    governors = [governor.to_dict() for governor in governor_refs()]
    if not include_health:
        return governors
    for governor in governors:
        port = int(governor.get("port") or 0)
        governor["runtime"] = fetch_worker_health(host, port) if port else {"reachable": False, "error": "missing port"}
        if port and governor["runtime"].get("reachable"):
            governor["runtime"]["capabilities"] = fetch_service_capabilities(host, port)
    return governors


def governor_worker_requirements(governors: list[dict[str, Any]], workers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for governor in governors:
        runtime = governor.get("runtime") if isinstance(governor.get("runtime"), dict) else {}
        capabilities_result = runtime.get("capabilities") if isinstance(runtime.get("capabilities"), dict) else {}
        capabilities = capabilities_result.get("capabilities") if isinstance(capabilities_result.get("capabilities"), dict) else {}
        required_workers = required_workers_from_capabilities(capabilities)
        if not required_workers:
            continue
        availability = worker_availability(required_workers)
        requirements.append(
            {
                "governor": str(governor.get("name") or ""),
                "required_workers": required_workers,
                "missing_workers": availability["missing_workers"],
                "unavailable_workers": availability["unavailable_workers"],
                "worker_availability": availability,
                "satisfied": bool(availability["ok"]),
            }
        )
    return requirements


def governor_pipeline_summaries(governors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pipelines: list[dict[str, Any]] = []
    for governor in governors:
        runtime = governor.get("runtime") if isinstance(governor.get("runtime"), dict) else {}
        capabilities_result = runtime.get("capabilities") if isinstance(runtime.get("capabilities"), dict) else {}
        capabilities = capabilities_result.get("capabilities") if isinstance(capabilities_result.get("capabilities"), dict) else {}
        pipeline = capabilities.get("pipeline") if isinstance(capabilities.get("pipeline"), dict) else {}
        if not pipeline:
            continue
        pipelines.append(
            {
                "governor": str(governor.get("name") or ""),
                "pipeline": pipeline,
            }
        )
    return pipelines


def brigade_readiness_summary(
    governors: list[dict[str, Any]],
    workers: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    for governor in governors:
        name = str(governor.get("name") or "")
        status = str(governor.get("status") or "")
        reachable = bool(governor.get("runtime", {}).get("reachable")) if isinstance(governor.get("runtime"), dict) else False
        if status == "planned":
            warnings.append(f"Governor is planned and not runnable: {name}")
        elif not reachable:
            blockers.append(f"Governor is not reachable: {name}")
    for worker in workers:
        name = str(worker.get("name") or "")
        status = str(worker.get("status") or "")
        reachable = bool(worker.get("runtime", {}).get("reachable")) if isinstance(worker.get("runtime"), dict) else False
        if status == "planned":
            warnings.append(f"Worker is planned and not runnable: {name}")
        elif not reachable:
            blockers.append(f"Worker is not reachable: {name}")
    for requirement in requirements:
        governor_name = str(requirement.get("governor") or "")
        if requirement.get("satisfied"):
            continue
        missing = requirement.get("missing_workers") if isinstance(requirement.get("missing_workers"), list) else []
        unavailable = requirement.get("unavailable_workers") if isinstance(requirement.get("unavailable_workers"), list) else []
        if missing:
            blockers.append(f"Governor {governor_name} requires missing workers: {', '.join(str(item) for item in missing)}")
        if unavailable:
            names = [str(item.get("name") or item) for item in unavailable]
            blockers.append(f"Governor {governor_name} requires unavailable workers: {', '.join(names)}")
    return {
        "ready": not blockers,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "blockers": blockers,
        "warnings": warnings,
    }


def brigade_plan_snapshot(host: str = "127.0.0.1") -> dict[str, Any]:
    host = validate_service_host(host)
    from start_brigade import brigade_plan  # Imported lazily to keep gateway boot independent from launcher tooling.

    return brigade_plan(
        repo_root=REPO_ROOT,
        host=host,
        workspace_root=Path("runtime/mechanicum-work"),
        warmaster_run_root=Path("runtime/warmaster-runs"),
        iskandar_run_root=Path("runtime/iskandar-runs"),
    )


def brigade_health_snapshot(host: str = "127.0.0.1") -> dict[str, Any]:
    host = validate_service_host(host)
    plan = brigade_plan_snapshot(host=host)
    governors = governor_registry_snapshot(include_health=True, host=host)
    workers = worker_registry_snapshot(include_health=True, host=host)
    requirements = governor_worker_requirements(governors, workers)
    pipelines = governor_pipeline_summaries(governors)
    reachable_governors = sum(1 for item in governors if item.get("runtime", {}).get("reachable"))
    reachable_workers = sum(1 for item in workers if item.get("runtime", {}).get("reachable"))
    readiness = brigade_readiness_summary(governors, workers, requirements)
    return {
        "ok": True,
        "gateway": "WarmasterGateway",
        "host": host,
        "plan": plan,
        "services": {
            "warmaster_gateway": {"name": "WarmasterGateway", "host": host, "port": 7000, "reachable": True},
            "governors": governors,
            "workers": workers,
        },
        "requirements": {"governor_workers": requirements, "governor_pipelines": pipelines},
        "summary": {
            "ready": readiness["ready"],
            "governors_total": len(governors),
            "governors_reachable": reachable_governors,
            "workers_total": len(workers),
            "workers_reachable": reachable_workers,
            "governor_requirements_satisfied": all(item.get("satisfied") for item in requirements) if requirements else None,
            "governor_pipelines_available": len(pipelines),
            "blocker_count": readiness["blocker_count"],
            "warning_count": readiness["warning_count"],
            "blockers": readiness["blockers"],
            "warnings": readiness["warnings"],
        },
    }


def gateway_capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "gateway": "WarmasterGateway",
        "api_version": 1,
        "actions": gateway_actions(),
        "capabilities": [
            "task_routing",
            "task_preflight",
            "task_prepare_orchestration",
            "task_submit_orchestration",
            "run_start_orchestration",
            "run_orchestration_state",
            "run_preparation",
            "run_listing",
            "run_status_summary",
            "run_orchestration_cards",
            "ledger_read",
            "artifact_listing",
            "artifact_text_read",
            "final_package_read",
            "http_governor_planning",
            "run_contract_read",
            "run_package_diagnostics",
            "run_oversight_read",
            "run_dispatch_read",
            "run_worker_task_read",
            "run_events_read",
            "global_run_events_read",
            "local_execution",
            "http_worker_execution",
            "background_execution",
            "cooperative_cancellation",
            "worker_cancel_fanout",
            "stale_run_recovery",
            "startup_stale_run_recovery",
            "interrupted_run_resume",
            "governor_registry",
            "governor_health_snapshot",
            "worker_registry",
            "worker_health_snapshot",
            "brigade_plan_snapshot",
            "brigade_health_snapshot",
            "brigade_readiness_summary",
            "state_snapshot",
            "process_active_run_snapshot",
            "run_action_hints",
            "run_step_artifact_read",
            "run_execution_preflight",
            "restricted_step_execution",
            "recoverable_run_listing",
            "doctor",
        ],
        "endpoints": [
            "GET /health",
            "GET /capabilities",
            "GET /state",
            "GET /state?health=1",
            "GET /recovery",
            "GET /doctor",
            "GET /brigade_plan",
            "GET /brigade_plan?host=127.0.0.1",
            "GET /brigade_health",
            "GET /brigade_health?host=127.0.0.1",
            "GET /governors",
            "GET /governors?health=1",
            "GET /workers",
            "GET /workers?health=1",
            "GET /events",
            "GET /events?limit=20",
            "GET /events?after=0",
            "POST /task_preflight",
            "POST /orchestrate",
            "POST /orchestrate_start",
            "POST /orchestrate_run",
            "POST /task",
            "GET /runs",
            "GET /runs?limit=20",
            "GET /runs/{task_id}",
            "GET /runs/{task_id}/summary",
            "GET /runs/{task_id}/snapshot",
            "GET /runs/{task_id}/orchestration",
            "GET /runs/{task_id}/active",
            "GET /runs/{task_id}/steps/{step_id}",
            "GET /runs/{task_id}/steps/{step_id}/artifacts",
            "GET /runs/{task_id}/ledger",
            "GET /runs/{task_id}/package",
            "GET /runs/{task_id}/contract",
            "GET /runs/{task_id}/oversight",
            "GET /runs/{task_id}/dispatch",
            "GET /runs/{task_id}/worker_tasks",
            "GET /runs/{task_id}/worker_tasks?live=1",
            "GET /runs/{task_id}/events",
            "GET /runs/{task_id}/events?limit=20",
            "GET /runs/{task_id}/events?after=0",
            "GET /runs/{task_id}/artifacts",
            "GET /runs/{task_id}/final",
            "GET /runs/{task_id}/final?max_bytes=1000",
            "GET /runs/{task_id}/artifact_text?path=/work/...",
            "GET /runs/{task_id}/artifact_text?path=/work/...&max_bytes=1000",
            "POST /runs/{task_id}/preflight_local",
            "POST /runs/{task_id}/preflight_http",
            "POST /runs/{task_id}/execute_local",
            "POST /runs/{task_id}/execute_http",
            "POST /runs/{task_id}/execute_revision_local",
            "POST /runs/{task_id}/execute_revision_http",
            "POST /runs/{task_id}/resume_local",
            "POST /runs/{task_id}/resume_http",
            "POST /runs/{task_id}/start_local",
            "POST /runs/{task_id}/start_http",
            "POST /runs/{task_id}/start_revision_local",
            "POST /runs/{task_id}/start_revision_http",
            "POST /runs/{task_id}/start_resume_local",
            "POST /runs/{task_id}/start_resume_http",
            "POST /recovery/start_resume_local",
            "POST /recovery/start_resume_http",
            "POST /runs/{task_id}/cancel",
            "POST /recover_stale",
        ],
    }


def gateway_actions() -> dict[str, Any]:
    return {
        "can_preflight_task": True,
        "can_orchestrate_prepare": True,
        "can_orchestrate_start": True,
        "can_orchestrate_run": True,
        "can_preflight_runs": True,
        "can_create_task": True,
        "can_start_runs": True,
        "can_resume_interrupted_runs": True,
        "can_list_recoverable_runs": True,
        "can_list_orchestration_cards": True,
        "can_bulk_start_recoverable_runs": True,
        "can_poll_global_events": True,
        "can_execute_revisions": True,
        "can_execute_step_subsets": True,
        "can_cancel_runs": True,
        "can_check_brigade_readiness": True,
        "preferred_task_flow": ["POST /task_preflight", "POST /task", "POST /runs/{task_id}/preflight_http", "POST /runs/{task_id}/start_http"],
        "prepare_task_flow": ["POST /orchestrate", "POST /orchestrate_start", "GET /runs/{task_id}/orchestration?events_after=0"],
        "chat_task_flow": ["POST /orchestrate_run", "GET /runs/{task_id}/orchestration?events_after=0"],
        "polling": ["GET /events?after=0", "GET /runs/{task_id}/snapshot?events_after=0"],
        "maintenance": ["GET /recovery", "POST /recovery/start_resume_local", "POST /recovery/start_resume_http", "POST /recover_stale"],
        "run_inspection": [
            "GET /runs/{task_id}/summary",
            "GET /runs/{task_id}/package",
            "GET /runs/{task_id}/oversight",
            "GET /runs/{task_id}/contract",
            "GET /runs/{task_id}/dispatch",
        ],
        "diagnostics": ["GET /state?health=1", "GET /brigade_health", "GET /doctor"],
    }


def gateway_state(run_root: Path, run_limit: int = 20, include_health: bool = False, host: str = "127.0.0.1") -> dict[str, Any]:
    all_runs = list_runs(run_root)
    runs = all_runs[: parse_limit(str(run_limit), default=20)]
    with ACTIVE_RUNS_LOCK:
        process_active_runs = sorted(ACTIVE_RUNS)
    payload = {
        "ok": True,
        "gateway": "WarmasterGateway",
        "capabilities": gateway_capabilities(),
        "actions": gateway_actions(),
        "governors": governor_registry_snapshot(),
        "workers": worker_registry_snapshot(),
        "brigade_plan": brigade_plan_snapshot(),
        "run_summary": run_status_summary(all_runs),
        "recovery": recovery_summary(all_runs),
        "process_active_runs": process_active_runs,
        "runs": runs,
        "orchestration_cards": run_orchestration_cards(runs, process_active_runs),
    }
    if include_health:
        payload["brigade_health"] = brigade_health_snapshot(host=host)
    return payload


def cancel_http_worker_tasks(run_dir: Path, host: str = "127.0.0.1", timeout_sec: float = 1.0) -> list[dict[str, Any]]:
    host = validate_service_host(host)
    dispatch_dir = run_dir / "dispatch"
    if not dispatch_dir.exists():
        return []
    results: list[dict[str, Any]] = []
    for dispatch_path in sorted(dispatch_dir.glob("*.json")):
        try:
            packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            results.append({"dispatch": str(dispatch_path), "ok": False, "error": str(exc)})
            continue
        if not isinstance(packet, dict):
            results.append({"dispatch": str(dispatch_path), "ok": False, "error": "dispatch packet is not an object"})
            continue
        request_payload = packet.get("request") if isinstance(packet.get("request"), dict) else {}
        task_id = str(request_payload.get("task_id") or packet.get("task_id") or "")
        worker = str(packet.get("worker") or "")
        port = int(packet.get("port") or 0)
        if not task_id or not port:
            results.append({"worker": worker, "port": port, "task_id": task_id, "ok": False, "error": "missing task_id or port"})
            continue
        url = f"http://{host}:{port}/tasks/{quote(task_id, safe='')}/cancel"
        try:
            data = json.dumps({}).encode("utf-8")
            request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                payload = json.loads(response.read().decode("utf-8"))
            results.append({"worker": worker, "port": port, "task_id": task_id, "ok": bool(isinstance(payload, dict) and payload.get("ok")), "response": payload})
        except Exception as exc:  # noqa: BLE001 - cancellation fan-out is best-effort.
            results.append({"worker": worker, "port": port, "task_id": task_id, "ok": False, "error": str(exc)})
    return results


def start_background(task_id: str, target: Any) -> bool:
    with ACTIVE_RUNS_LOCK:
        if task_id in ACTIVE_RUNS:
            return False
        ACTIVE_RUNS.add(task_id)

    def wrapped() -> None:
        try:
            target()
        finally:
            with ACTIVE_RUNS_LOCK:
                ACTIVE_RUNS.discard(task_id)

    threading.Thread(target=wrapped, daemon=True).start()
    return True


def orchestrate_start_run(
    run_root: Path,
    task_id: str,
    run_mode: str = "http",
    host: str = "127.0.0.1",
    timeout_sec: int = 1800,
    force: bool = False,
) -> dict[str, Any]:
    if run_mode not in {"local", "http"}:
        raise ValueError("run_mode must be local or http")
    host = validate_service_host(host)
    timeout_sec = max(1, min(int(timeout_sec), 7200))
    run_dir = run_root / task_id
    if not run_dir.exists():
        return {"ok": False, "phase": "missing_run", "task_id": task_id, "error": "run not found"}
    summary = run_summary(run_dir)
    actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
    execution_mode = "full"
    step_ids: list[str] | None = None
    if actions.get("can_start"):
        operation = "start"
    elif actions.get("can_resume"):
        operation = "resume"
        execution_mode = "resume"
        step_ids = resume_step_ids_from_run(run_dir)
    elif actions.get("can_start_revision"):
        operation = "revision"
        execution_mode = "revision"
        step_ids = revision_step_ids_from_run(run_dir)
    elif force and actions.get("force_required_for_rerun"):
        operation = "force_rerun"
    else:
        return {
            "ok": False,
            "phase": "not_startable",
            "task_id": task_id,
            "summary": summary,
            "next_action": next_action,
            "client_action": executable_client_action(task_id, next_action),
            "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
        }

    workspace_root = resolve_run_child_path(run_dir, "", "work")
    if run_mode == "local":
        executor = lambda: execute_local_run(
            REPO_ROOT,
            run_dir,
            workspace_root,
            timeout_sec=timeout_sec,
            step_ids=step_ids,
            execution_mode=execution_mode,
        )
    else:
        executor = lambda: execute_http_run(
            run_dir,
            host=host,
            timeout_sec=timeout_sec,
            workspace_root=None,
            step_ids=step_ids,
            execution_mode=execution_mode,
        )
    ledger_path = run_dir / "task_ledger.json"
    if ledger_path.exists():
        ledger = TaskLedger.load(ledger_path)
        if operation == "resume":
            ledger.record_event("resume_execution_requested", {"mode": f"orchestrate_start_{run_mode}", "step_ids": step_ids or []})
        event_payload: dict[str, Any] = {"mode": f"orchestrate_start_{run_mode}", "operation": operation}
        if step_ids:
            event_payload["step_ids"] = step_ids
        if force:
            event_payload["force"] = True
        ledger.record_event("background_start_requested", event_payload)
    if not start_background(task_id, executor):
        return {
            "ok": False,
            "phase": "already_active",
            "task_id": task_id,
            "error": "run already active",
            "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
        }
    poll_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "run started in background"}
    return {
        "ok": True,
        "phase": "started",
        "task_id": task_id,
        "run_mode": run_mode,
        "operation": operation,
        "step_ids": step_ids or [],
        "next_action": poll_action,
        "client_action": executable_client_action(task_id, poll_action),
        "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
    }


def start_recoverable_runs(run_root: Path, mode: str, host: str = "127.0.0.1", timeout_sec: int = 1800) -> dict[str, Any]:
    if mode not in {"local", "http"}:
        raise ValueError("mode must be local or http")
    host = validate_service_host(host)
    timeout_sec = max(1, min(int(timeout_sec), 7200))
    all_runs = list_runs(run_root)
    candidates = recovery_summary(all_runs).get("candidates", [])
    results: list[dict[str, Any]] = []
    started_count = 0
    for candidate in candidates:
        task_id = str(candidate.get("task_id") or "")
        if not task_id:
            continue
        run_dir = run_root / task_id
        ledger_path = run_dir / "task_ledger.json"
        try:
            step_ids = resume_step_ids_from_run(run_dir)
            if ledger_path.exists():
                ledger = TaskLedger.load(ledger_path)
                ledger.record_event("resume_execution_requested", {"mode": f"bulk_start_resume_{mode}"})
                ledger.record_event("background_start_requested", {"mode": f"bulk_start_resume_{mode}", "step_ids": step_ids})
            workspace_root = resolve_run_child_path(run_dir, "", "work")
            if mode == "local":
                executor = lambda run_dir=run_dir, workspace_root=workspace_root, step_ids=step_ids: execute_local_run(
                    REPO_ROOT,
                    run_dir,
                    workspace_root,
                    timeout_sec=timeout_sec,
                    step_ids=step_ids,
                    execution_mode="resume",
                )
            else:
                executor = lambda run_dir=run_dir, step_ids=step_ids: execute_http_run(
                    run_dir,
                    host=host,
                    timeout_sec=timeout_sec,
                    workspace_root=None,
                    step_ids=step_ids,
                    execution_mode="resume",
                )
            if not start_background(task_id, executor):
                results.append({"task_id": task_id, "ok": False, "status": "already_active"})
                continue
            started_count += 1
            results.append({"task_id": task_id, "ok": True, "status": "started", "step_ids": step_ids})
        except Exception as exc:  # noqa: BLE001 - one malformed recoverable run must not block the queue.
            results.append({"task_id": task_id, "ok": False, "status": "skipped", "error": str(exc)})
    return {
        "ok": True,
        "mode": mode,
        "started": started_count,
        "total_candidates": len(candidates),
        "results": results,
    }


def make_handler(run_root: Path, default_governor_transport: str = "local", default_governor_host: str = "127.0.0.1") -> type[BaseHTTPRequestHandler]:
    class WarmasterHandler(BaseHTTPRequestHandler):
        server_version = "WarmasterGateway/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                response(self, 200, {"ok": True, "gateway": "WarmasterGateway"})
                return
            if parsed.path == "/capabilities":
                response(self, 200, gateway_capabilities())
                return
            if parsed.path == "/state":
                query = parse_qs(parsed.query)
                raw_limit = query.get("run_limit", ["20"])[0]
                run_limit = parse_limit(raw_limit, default=20)
                include_health = query.get("health", ["0"])[0] in {"1", "true", "yes"}
                host = query.get("host", ["127.0.0.1"])[0]
                try:
                    payload = gateway_state(run_root, run_limit=run_limit, include_health=include_health, host=host)
                except ValueError as exc:
                    response(self, 400, {"ok": False, "error": str(exc)})
                    return
                response(self, 200, payload)
                return
            if parsed.path == "/doctor":
                payload = run_doctor()
                response(self, 200 if payload.get("ok") else 500, payload)
                return
            if parsed.path == "/recovery":
                all_runs = list_runs(run_root)
                response(self, 200, {"ok": True, "recovery": recovery_summary(all_runs)})
                return
            if parsed.path == "/brigade_plan":
                query = parse_qs(parsed.query)
                host = query.get("host", ["127.0.0.1"])[0]
                try:
                    payload = brigade_plan_snapshot(host=host)
                except ValueError as exc:
                    response(self, 400, {"ok": False, "error": str(exc)})
                    return
                response(self, 200, payload)
                return
            if parsed.path == "/brigade_health":
                query = parse_qs(parsed.query)
                host = query.get("host", ["127.0.0.1"])[0]
                try:
                    payload = brigade_health_snapshot(host=host)
                except ValueError as exc:
                    response(self, 400, {"ok": False, "error": str(exc)})
                    return
                response(self, 200, payload)
                return
            if parsed.path == "/governors":
                query = parse_qs(parsed.query)
                include_health = query.get("health", ["0"])[0] in {"1", "true", "yes"}
                response(
                    self,
                    200,
                    {
                        "ok": True,
                        "health_checked": include_health,
                        "governors": governor_registry_snapshot(include_health=include_health),
                    },
                )
                return
            if parsed.path == "/workers":
                query = parse_qs(parsed.query)
                include_health = query.get("health", ["0"])[0] in {"1", "true", "yes"}
                response(
                    self,
                    200,
                    {
                        "ok": True,
                        "health_checked": include_health,
                        "workers": worker_registry_snapshot(include_health=include_health),
                    },
                )
                return
            if parsed.path == "/events":
                query = parse_qs(parsed.query)
                raw_limit = query.get("limit", [""])[0]
                limit = parse_limit(raw_limit, default=MAX_LIST_LIMIT) if raw_limit else None
                raw_after = query.get("after", [""])[0]
                after = parse_nonnegative_int(raw_after, default=0) if raw_after else None
                response(self, 200, all_run_events(run_root, limit=limit, after=after))
                return
            parts = [part for part in parsed.path.split("/") if part]
            if parts == ["runs"]:
                query = parse_qs(parsed.query)
                raw_limit = query.get("limit", [""])[0]
                all_runs = list_runs(run_root)
                runs = all_runs[: parse_limit(raw_limit, default=MAX_LIST_LIMIT)] if raw_limit else all_runs
                with ACTIVE_RUNS_LOCK:
                    process_active_runs = sorted(ACTIVE_RUNS)
                response(
                    self,
                    200,
                    {
                        "ok": True,
                        "run_summary": run_status_summary(all_runs),
                        "recovery": recovery_summary(all_runs),
                        "runs": runs,
                        "orchestration_cards": run_orchestration_cards(runs, process_active_runs),
                    },
                )
                return
            if len(parts) == 4 and parts[0] == "runs" and parts[2] == "steps":
                task_id = parts[1]
                run_dir = run_root / task_id
                if not run_dir.exists():
                    response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                    return
                payload = run_step_state(run_dir, parts[3])
                response(self, 200 if payload.get("ok") else 404, payload)
                return
            if len(parts) == 5 and parts[0] == "runs" and parts[2] == "steps" and parts[4] == "artifacts":
                task_id = parts[1]
                run_dir = run_root / task_id
                if not run_dir.exists():
                    response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                    return
                payload = run_step_artifacts(run_dir, parts[3])
                response(self, 200 if payload.get("ok") else 404, payload)
                return
            if len(parts) in {2, 3} and parts[0] == "runs":
                task_id = parts[1]
                run_dir = run_root / task_id
                status_path = run_dir / "status.json"
                ledger_path = run_dir / "task_ledger.json"
                if not run_dir.exists():
                    response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                    return
                if len(parts) == 3 and parts[2] == "summary":
                    response(self, 200, {"ok": True, "summary": run_summary(run_dir)})
                    return
                if len(parts) == 3 and parts[2] == "snapshot":
                    query = parse_qs(parsed.query)
                    raw_event_limit = query.get("event_limit", [""])[0]
                    event_limit = parse_limit(raw_event_limit, default=MAX_LIST_LIMIT) if raw_event_limit else None
                    raw_events_after = query.get("events_after", [""])[0]
                    events_after = parse_nonnegative_int(raw_events_after, default=0) if raw_events_after else None
                    response(self, 200, run_snapshot(run_dir, event_limit=event_limit, events_after=events_after))
                    return
                if len(parts) == 3 and parts[2] == "orchestration":
                    query = parse_qs(parsed.query)
                    raw_event_limit = query.get("event_limit", [""])[0]
                    event_limit = parse_limit(raw_event_limit, default=20) if raw_event_limit else 20
                    raw_events_after = query.get("events_after", [""])[0]
                    events_after = parse_nonnegative_int(raw_events_after, default=0) if raw_events_after else 0
                    raw_max_bytes = query.get("max_bytes", [""])[0]
                    max_bytes = parse_limit(raw_max_bytes, default=2000, maximum=MAX_ARTIFACT_TEXT_BYTES) if raw_max_bytes else 2000
                    response(self, 200, orchestration_state(run_dir, event_limit=event_limit, events_after=events_after, max_bytes=max_bytes))
                    return
                if len(parts) == 3 and parts[2] == "active":
                    with ACTIVE_RUNS_LOCK:
                        active = task_id in ACTIVE_RUNS
                    response(self, 200, {"ok": True, "task_id": task_id, "active": active})
                    return
                if len(parts) == 3 and parts[2] == "ledger":
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    ledger, ledger_error = load_ledger_dict(ledger_path)
                    if ledger_error:
                        response(self, 500, {"ok": False, "error": ledger_error, "task_id": task_id})
                        return
                    response(self, 200, {"ok": True, "ledger": ledger})
                    return
                if len(parts) == 3 and parts[2] == "package":
                    payload = run_package_diagnostics(run_dir)
                    response(self, 200 if payload.get("ok") else 409, payload)
                    return
                if len(parts) == 3 and parts[2] == "contract":
                    payload = run_contract(run_dir)
                    status_code = 500 if payload.get("error_code") == "corrupt_contract" else 404
                    response(self, 200 if payload.get("ok") else status_code, payload)
                    return
                if len(parts) == 3 and parts[2] == "oversight":
                    payload = run_oversight_diagnostics(run_dir)
                    status_code = 500 if payload.get("error_code") == "corrupt_oversight" else 404
                    response(self, 200 if payload.get("ok") else status_code, payload)
                    return
                if len(parts) == 3 and parts[2] == "dispatch":
                    payload = run_dispatch_packets(run_dir)
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                if len(parts) == 3 and parts[2] == "worker_tasks":
                    query = parse_qs(parsed.query)
                    include_live = query.get("live", ["0"])[0] in {"1", "true", "yes"}
                    host = query.get("host", ["127.0.0.1"])[0]
                    try:
                        payload = run_worker_tasks(run_dir, include_health=include_live, host=host)
                    except ValueError as exc:
                        response(self, 400, {"ok": False, "error": str(exc)})
                        return
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                if len(parts) == 3 and parts[2] == "events":
                    query = parse_qs(parsed.query)
                    raw_limit = query.get("limit", [""])[0]
                    limit = parse_limit(raw_limit, default=MAX_LIST_LIMIT) if raw_limit else None
                    raw_after = query.get("after", [""])[0]
                    after = parse_nonnegative_int(raw_after, default=0) if raw_after else None
                    payload = run_events(run_dir, limit=limit, after=after)
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                if len(parts) == 3 and parts[2] == "artifacts":
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    ledger, ledger_error = load_ledger_dict(ledger_path)
                    if ledger_error:
                        response(self, 500, {"ok": False, "error": ledger_error, "task_id": task_id})
                        return
                    response(self, 200, {"ok": True, "task_id": task_id, **artifact_status(ledger)})
                    return
                if len(parts) == 3 and parts[2] == "final":
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    query = parse_qs(parsed.query)
                    raw_max_bytes = query.get("max_bytes", [""])[0]
                    max_bytes = parse_limit(raw_max_bytes, default=20000, maximum=MAX_ARTIFACT_TEXT_BYTES) if raw_max_bytes else 20000
                    ledger, ledger_error = load_ledger_dict(ledger_path)
                    if ledger_error:
                        response(self, 500, {"ok": False, "error": ledger_error, "task_id": task_id})
                        return
                    try:
                        payload = final_package(ledger, max_bytes=max_bytes)
                    except ValueError as exc:
                        response(self, 400, {"ok": False, "error": str(exc), "task_id": task_id})
                        return
                    response(self, 200 if payload.get("ok") else 404, {"task_id": task_id, **payload})
                    return
                if len(parts) == 3 and parts[2] == "artifact_text":
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    query = parse_qs(parsed.query)
                    artifact_path = query.get("path", [""])[0]
                    raw_max_bytes = query.get("max_bytes", [""])[0]
                    max_bytes = parse_limit(raw_max_bytes, default=MAX_ARTIFACT_TEXT_BYTES, maximum=MAX_ARTIFACT_TEXT_BYTES) if raw_max_bytes else MAX_ARTIFACT_TEXT_BYTES
                    ledger, ledger_error = load_ledger_dict(ledger_path)
                    if ledger_error:
                        response(self, 500, {"ok": False, "error": ledger_error, "task_id": task_id})
                        return
                    try:
                        payload = artifact_text(ledger, artifact_path, max_bytes=max_bytes)
                    except ValueError as exc:
                        response(self, 400, {"ok": False, "error": str(exc)})
                        return
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                status, status_error = load_json_object(status_path, "status") if status_path.exists() else ({}, "")
                ledger, ledger_error = load_ledger_dict(ledger_path)
                status_payload = {"ok": True, "task_id": task_id, "run_dir": str(run_dir), "status": status, "ledger": ledger}
                if status_error:
                    status_payload["status_error"] = status_error
                if ledger_error and ledger_path.exists():
                    status_payload["ledger"] = {}
                    status_payload["ledger_error"] = ledger_error
                    response(self, 200, status_payload)
                    return
                response(self, 200, status_payload)
                return
            response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            try:
                payload = read_payload(self)
                if self.path == "/orchestrate":
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    task_id = str(payload.get("task_id") or "").strip() or None
                    governor_transport = str(payload.get("governor_transport") or default_governor_transport).strip() or default_governor_transport
                    governor_host = str(payload.get("governor_host") or default_governor_host).strip() or default_governor_host
                    run_mode = str(payload.get("run_mode") or "http").strip() or "http"
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    timeout_sec = max(1, min(int(payload.get("timeout_sec") or 30), 7200))
                    include_brigade_health = bool(payload.get("include_brigade_health"))
                    prepared = orchestrate_prepare_task(
                        message,
                        task_id,
                        run_root,
                        governor_transport=governor_transport,
                        governor_host=governor_host,
                        run_mode=run_mode,
                        host=host,
                        timeout_sec=timeout_sec,
                        include_brigade_health=include_brigade_health,
                    )
                    response(self, 200 if prepared.get("ok") else 409, prepared)
                    return
                if self.path == "/orchestrate_start":
                    task_id = str(payload.get("task_id") or "").strip()
                    if not task_id:
                        response(self, 400, {"ok": False, "error": "task_id is required"})
                        return
                    if not valid_task_id(task_id):
                        response(self, 400, {"ok": False, "error": "invalid task_id", "task_id": task_id})
                        return
                    run_mode = str(payload.get("run_mode") or "http").strip() or "http"
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    timeout_sec = max(1, min(int(payload.get("timeout_sec") or 1800), 7200))
                    started = orchestrate_start_run(
                        run_root,
                        task_id,
                        run_mode=run_mode,
                        host=host,
                        timeout_sec=timeout_sec,
                        force=bool(payload.get("force")),
                    )
                    response(self, 202 if started.get("ok") else 409, started)
                    return
                if self.path == "/orchestrate_run":
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    task_id = str(payload.get("task_id") or "").strip() or None
                    governor_transport = str(payload.get("governor_transport") or default_governor_transport).strip() or default_governor_transport
                    governor_host = str(payload.get("governor_host") or default_governor_host).strip() or default_governor_host
                    run_mode = str(payload.get("run_mode") or "http").strip() or "http"
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    timeout_sec = max(1, min(int(payload.get("timeout_sec") or 1800), 7200))
                    include_brigade_health = bool(payload.get("include_brigade_health"))
                    auto_start = bool(payload.get("auto_start", True))
                    submitted = orchestrate_run_task(
                        message,
                        task_id,
                        run_root,
                        governor_transport=governor_transport,
                        governor_host=governor_host,
                        run_mode=run_mode,
                        host=host,
                        timeout_sec=timeout_sec,
                        include_brigade_health=include_brigade_health,
                        auto_start=auto_start,
                        force=bool(payload.get("force")),
                        reuse_existing=bool(payload.get("reuse_existing", True)),
                    )
                    if submitted.get("ok") and submitted.get("phase") == "started":
                        response(self, 202, submitted)
                    else:
                        response(self, 200 if submitted.get("ok") else 409, submitted)
                    return
                if self.path == "/task":
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    task_id = str(payload.get("task_id") or "").strip() or None
                    governor_transport = str(payload.get("governor_transport") or default_governor_transport).strip() or default_governor_transport
                    governor_host = str(payload.get("governor_host") or default_governor_host).strip() or default_governor_host
                    prepared = prepare_task(message, task_id, run_root, governor_transport=governor_transport, governor_host=governor_host)
                    response(self, 409 if prepared.get("error_code") == "task_exists" else (200 if prepared.get("ok") else 400), prepared)
                    return
                if self.path == "/task_preflight":
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    task_id = str(payload.get("task_id") or "").strip() or None
                    governor_transport = str(payload.get("governor_transport") or default_governor_transport).strip() or default_governor_transport
                    governor_host = str(payload.get("governor_host") or default_governor_host).strip() or default_governor_host
                    include_brigade_health = bool(payload.get("include_brigade_health"))
                    preflight = preflight_task(
                        message,
                        task_id,
                        run_root,
                        governor_transport=governor_transport,
                        governor_host=governor_host,
                        include_brigade_health=include_brigade_health,
                    )
                    response(self, 409 if preflight.get("error_code") == "task_exists" else (200 if preflight.get("ok") else 400), preflight)
                    return
                if self.path == "/recover_stale":
                    recovered = recover_stale_runs(run_root)
                    response(self, 200, {"ok": True, "recovered": recovered})
                    return
                if self.path in {"/recovery/start_resume_local", "/recovery/start_resume_http"}:
                    try:
                        mode = "local" if self.path.endswith("_local") else "http"
                        host = str(payload.get("host") or "127.0.0.1")
                        timeout_sec = int(payload.get("timeout_sec") or 1800)
                        recovered = start_recoverable_runs(run_root, mode=mode, host=host, timeout_sec=timeout_sec)
                    except ValueError as exc:
                        response(self, 400, {"ok": False, "error": str(exc)})
                        return
                    response(self, 202, recovered)
                    return
                parts = [part for part in self.path.split("?")[0].split("/") if part]
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel":
                    task_id = parts[1]
                    ledger_path = run_root / task_id / "task_ledger.json"
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    reason = str(payload.get("reason") or "").strip()
                    ledger = TaskLedger.load(ledger_path)
                    if ledger.to_dict().get("status") in {"completed", "failed", "cancelled", "corrupt"}:
                        response(
                            self,
                            409,
                            {
                                "ok": False,
                                "task_id": task_id,
                                "error": "run is already terminal",
                                "ledger": ledger.to_dict(),
                            },
                        )
                        return
                    ledger.request_cancel(reason)
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    worker_cancellations = cancel_http_worker_tasks(run_root / task_id, host=host)
                    response(
                        self,
                        200,
                        {
                            "ok": True,
                            "task_id": task_id,
                            "status": "cancelling",
                            "ledger": ledger.to_dict(),
                            "worker_cancellations": worker_cancellations,
                        },
                    )
                    return
                execution_modes = {
                    "preflight_local",
                    "preflight_http",
                    "execute_local",
                    "execute_http",
                    "start_local",
                    "start_http",
                    "execute_revision_local",
                    "execute_revision_http",
                    "resume_local",
                    "resume_http",
                    "start_revision_local",
                    "start_revision_http",
                    "start_resume_local",
                    "start_resume_http",
                }
                if len(parts) == 3 and parts[0] == "runs" and parts[2] in execution_modes:
                    task_id = parts[1]
                    run_dir = run_root / task_id
                    if not run_dir.exists():
                        response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                        return
                    ledger_path = run_dir / "task_ledger.json"
                    force = bool(payload.get("force"))
                    preflight_mode = parts[2] in {"preflight_local", "preflight_http"}
                    revision_mode = "revision" in parts[2]
                    resume_mode = "resume" in parts[2]
                    if ledger_path.exists():
                        ledger = TaskLedger.load(ledger_path)
                        ledger_data = ledger.to_dict()
                        if resume_mode and ledger_data.get("status") != "interrupted":
                            response(
                                self,
                                409,
                                {
                                    "ok": False,
                                    "error": "resume requires an interrupted run",
                                    "ledger": ledger_data,
                                },
                            )
                            return
                        if not preflight_mode and not force and ledger_data.get("status") == "completed" and not revision_mode:
                            response(
                                self,
                                409,
                                {
                                    "ok": False,
                                    "error": "run already completed; pass force=true to rerun",
                                    "ledger": ledger_data,
                                },
                            )
                            return
                        if resume_mode:
                            ledger.record_event("resume_execution_requested", {"mode": parts[2]})
                    requested_step_ids = requested_step_ids_from_payload(payload)
                    mode_step_ids = revision_step_ids_from_run(run_dir) if revision_mode else (resume_step_ids_from_run(run_dir) if resume_mode else None)
                    if requested_step_ids:
                        validate_requested_step_ids(run_dir, requested_step_ids, allowed=mode_step_ids)
                        restricted_step_ids = requested_step_ids
                    else:
                        restricted_step_ids = mode_step_ids
                    workspace_root = resolve_run_child_path(run_dir, str(payload.get("workspace_root") or ""), "work")
                    timeout_sec = max(1, min(int(payload.get("timeout_sec") or 1800), 7200))
                    execution_mode = "revision" if revision_mode else ("resume" if resume_mode else "full")
                    if parts[2] in {"preflight_local", "preflight_http"}:
                        if parts[2] == "preflight_http":
                            host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                            http_workspace_root = workspace_root if "workspace_root" in payload else None
                            preflight = run_execution_preflight(
                                run_dir,
                                mode="http",
                                workspace_root=http_workspace_root,
                                host=host,
                                timeout_sec=timeout_sec,
                                step_ids=restricted_step_ids,
                            )
                        else:
                            preflight = run_execution_preflight(
                                run_dir,
                                mode="local",
                                workspace_root=workspace_root,
                                timeout_sec=timeout_sec,
                                step_ids=restricted_step_ids,
                            )
                        record_run_preflight_event(run_dir, preflight)
                        response(self, 200 if preflight.get("ok") else 409, preflight)
                        return
                    if parts[2] in {"execute_local", "start_local", "execute_revision_local", "start_revision_local", "resume_local", "start_resume_local"}:
                        executor = lambda: execute_local_run(REPO_ROOT, run_dir, workspace_root, timeout_sec=timeout_sec, step_ids=restricted_step_ids, execution_mode=execution_mode)
                    else:
                        host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                        http_workspace_root = workspace_root if "workspace_root" in payload else None
                        executor = lambda: execute_http_run(run_dir, host=host, timeout_sec=timeout_sec, workspace_root=http_workspace_root, step_ids=restricted_step_ids, execution_mode=execution_mode)
                    if parts[2].startswith("start_"):
                        if ledger_path.exists():
                            try:
                                event_payload: dict[str, Any] = {"mode": parts[2]}
                                if restricted_step_ids:
                                    event_payload["step_ids"] = restricted_step_ids
                                TaskLedger.load(ledger_path).record_event("background_start_requested", event_payload)
                            except Exception:
                                pass
                        started = start_background(task_id, executor)
                        if not started:
                            response(self, 409, {"ok": False, "error": "run already active", "task_id": task_id})
                            return
                        response(self, 202, {"ok": True, "task_id": task_id, "status": "started"})
                        return
                    if parts[2] in {"execute_local", "execute_revision_local", "resume_local"}:
                        summary = execute_local_run(REPO_ROOT, run_dir, workspace_root, timeout_sec=timeout_sec, step_ids=restricted_step_ids, execution_mode=execution_mode)
                    else:
                        host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                        http_workspace_root = workspace_root if "workspace_root" in payload else None
                        summary = execute_http_run(run_dir, host=host, timeout_sec=timeout_sec, workspace_root=http_workspace_root, step_ids=restricted_step_ids, execution_mode=execution_mode)
                    response(self, 200 if summary.get("ok") else 500, {"ok": bool(summary.get("ok")), "summary": summary})
                    return
                response(self, 404, {"ok": False, "error": "not found"})
            except ValueError as exc:
                response(self, 400, {"ok": False, "gateway": "WarmasterGateway", "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - gateway boundary records routing failures.
                response(self, 500, {"ok": False, "gateway": "WarmasterGateway", "error": str(exc)})

    return WarmasterHandler


def serve(host: str, port: int, run_root: Path, recover_stale_on_start: bool = True, governor_transport: str = "local", governor_host: str = "127.0.0.1") -> None:
    prepare_run_root(run_root, recover_stale_on_start=recover_stale_on_start)
    server = ThreadingHTTPServer((host, port), make_handler(run_root, default_governor_transport=governor_transport, default_governor_host=governor_host))
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the EyeOfTerror Warmaster Gateway.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7000)
    parser.add_argument("--run-root", default="runtime/warmaster-runs")
    parser.add_argument("--no-recover-stale-on-start", action="store_true")
    parser.add_argument("--governor-transport", choices=["local", "http"], default="local")
    parser.add_argument("--governor-host", default="127.0.0.1")
    args = parser.parse_args()
    serve(
        args.host,
        args.port,
        Path(args.run_root),
        recover_stale_on_start=not args.no_recover_stale_on_start,
        governor_transport=args.governor_transport,
        governor_host=args.governor_host,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
