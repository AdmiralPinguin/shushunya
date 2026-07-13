"""Route tasks and validate either native-warband or worker-planned packages."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
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
from .command_text import task_text_from_commander_order
from .contracts import validate_task_contract_payload
from .gateway_util import post_json, valid_task_id, validate_service_host
from .governors import governor_by_name
from .ledger import TaskLedger
from .native_code_run import (
    load_native_code_run,
    validate_native_code_contract,
    validate_native_code_run_package,
)
from .native_research_run import (
    load_native_research_run,
    native_research_prepare_request_sha256,
    validate_native_research_contract,
    validate_native_research_run_package,
)
from .oversight_guard import compact_oversight_summary
from .pipeline import write_pipeline_run
from .routing import route_message
from .run_validation import plan_oversight_errors, verify_prepared_run_package
from .views import payload_with_task_view
from EyeOfTerror.common_protocol import governor_plan_from_contract, validate_protocol_payload
from EyeOfTerror.common_protocol.ceraxia_directive import (
    CeraxiaDirectiveError,
    validate_directive_for_commander as validate_ceraxia_directive_for_commander,
)
from EyeOfTerror.common_protocol.iskandar_directive import (
    IskandarDirectiveError,
    validate_directive_for_commander as validate_iskandar_directive_for_commander,
)


def _post_governor_json(
    url: str,
    payload: dict[str, Any],
    governor_name: str,
) -> dict[str, Any]:
    """Attach only the selected governor's dedicated loopback credential."""
    headers: dict[str, str] = {}
    token_env = {
        "Ceraxia": "CERAXIA_BEARER_TOKEN",
        "IskandarKhayon": "RESEARCH_WARBAND_BEARER_TOKEN",
    }.get(str(governor_name), "")
    if token_env:
        token = os.environ.get(token_env, "")
        if any(char in token for char in "\r\n"):
            raise ValueError(f"invalid {governor_name} bearer token")
        if governor_name == "IskandarKhayon" and (
            len(token) < 32 or token.startswith("REPLACE_") or len(set(token)) < 8
        ):
            raise ValueError(f"{governor_name} bearer token is missing or unsafe")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return post_json(url, payload, headers=headers)


def _bounded_http_error_payload(error: urllib.error.HTTPError) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    try:
        raw = error.read(1_000_001)
        if len(raw) > 1_000_000:
            return {}
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=invalid_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def plan_image_task(task_text, task_id=None):
    # Lazy import: moriana_governor pulls the whole forge stack (Pillow, pydantic, ...),
    # which lives in the forge venv. Warmaster task planning must import without it.
    from EyeOfTerror.Pictorium.Moriana.moriana_governor import plan_image_task as moriana_plan_image_task

    return moriana_plan_image_task(task_text, task_id=task_id)


def commander_order_required_payload(
    task_id: str | None,
    governor_transport: str,
    governor_host: str,
    message: str,
    include_brigade_health: bool = False,
) -> dict[str, Any]:
    return {
        "ok": False,
        "gateway": "WarmasterGateway",
        "error": "commander_order is required for strict mission protocol",
        "error_code": "commander_order_required",
        "task_id": task_id or "",
        "protocol_mode": "strict_commander_order",
        "actions": task_preflight_actions(
            False,
            "commander_order_required",
            task_id or "",
            include_brigade_health,
            governor_transport,
            governor_host,
            message,
        ),
    }


def mission_id_from_commander(task_id: str | None, commander_order: dict[str, Any] | None = None) -> str:
    if isinstance(commander_order, dict) and str(commander_order.get("mission_id") or "").strip():
        return str(commander_order.get("mission_id") or "").strip()
    return f"mission-{task_id or 'unassigned'}"


def _ceraxia_prepare_request_sha256(
    task: str,
    task_id: str,
    commander_order: dict[str, Any],
) -> str:
    """Recompute the native receipt identity at the Warmaster trust boundary."""
    canonical = json.dumps(
        {
            "task": task,
            "task_id": task_id,
            "mission_id": str(
                commander_order.get("mission_id") or f"mission-{task_id}"
            ),
            "commander_order": commander_order,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def governor_payload_for(message: str, task_id: str | None, commander_order: dict[str, Any] | None = None) -> dict[str, Any]:
    if commander_order:
        validate_protocol_payload(commander_order, expected_type="commander_order")
        payload = {"task": task_text_from_commander_order(commander_order), "task_id": task_id or ""}
        payload["commander_order"] = commander_order
        return payload
    payload = {"task": message, "task_id": task_id or ""}
    return payload


def governor_task_text(message: str, commander_order: dict[str, Any] | None = None) -> str:
    if commander_order:
        validate_protocol_payload(commander_order, expected_type="commander_order")
        return task_text_from_commander_order(commander_order)
    return message


def attach_governor_plan_payload(
    payload: dict[str, Any],
    mission_id: str,
    commander_order: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
    if not contract:
        return payload
    if mission_id == "mission-unassigned" and str(contract.get("task_id") or "").strip():
        mission_id = f"mission-{str(contract.get('task_id') or '').strip()}"
    plan = payload.get("governor_plan") if isinstance(payload.get("governor_plan"), dict) else None
    if plan is None:
        plan = governor_plan_from_contract(mission_id, contract, commander_order)
    else:
        plan = dict(plan)
        plan["mission_id"] = mission_id
        if str(plan.get("understanding") or "").strip().startswith("ПРИКАЗ ВАРМАСТЕРА"):
            plan["understanding"] = str(
                (commander_order or {}).get("primary_goal")
                or (commander_order or {}).get("commander_intent")
                or plan.get("understanding")
                or ""
            ).strip()
    validate_protocol_payload(plan, expected_type="governor_plan")
    payload["governor_plan"] = plan
    return payload


def prepare_native_ceraxia_via_service(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor: Any,
    host: str = "127.0.0.1",
    port: int | None = None,
    commander_order: dict[str, Any] | None = None,
    require_commander_order: bool = False,
) -> dict[str, Any]:
    """Prepare one native Ceraxia -> Skitarii mission.

    The structural ``/plan`` endpoint is used only to allocate a missing task id.
    The leader model is called exactly once by ``/prepare_run``.  A persisted
    prepare receipt makes the latter safe to replay after a lost HTTP response.
    """
    if require_commander_order and not commander_order:
        return commander_order_required_payload(task_id, "http", host, message)
    if not isinstance(commander_order, dict) or not commander_order:
        return commander_order_required_payload(task_id, "http", host, message)
    validate_protocol_payload(commander_order, expected_type="commander_order")
    host = validate_service_host(host)
    service_port = int(port or governor.port)
    base = f"http://{host}:{service_port}"
    resolved_task_id = str(task_id or "").strip()
    if not resolved_task_id:
        try:
            preview = _post_governor_json(
                base + "/plan",
                governor_payload_for(message, None, commander_order=commander_order),
                governor.name,
            )
            preview_contract = preview.get("contract") if isinstance(preview.get("contract"), dict) else {}
            resolved_task_id = str(preview_contract.get("task_id") or "").strip()
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            return {
                "ok": False,
                "gateway": "WarmasterGateway",
                "governor": "Ceraxia",
                "error": f"Ceraxia structural preview unavailable: {exc}",
                "error_code": "governor_service_unavailable",
                "actions": task_preflight_actions(
                    False, "governor_service_unavailable", "",
                    governor_transport="http", governor_host=host, message=message,
                ),
            }
    if not valid_task_id(resolved_task_id):
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": "Ceraxia",
            "task_id": resolved_task_id,
            "error": "Ceraxia returned an invalid task_id",
            "error_code": "invalid_governor_task_id",
            "actions": task_preflight_actions(
                False, "invalid_governor_task_id", resolved_task_id,
                governor_transport="http", governor_host=host, message=message,
            ),
        }
    root = run_root.resolve()
    run_dir = (root / resolved_task_id).resolve()
    if run_dir == root or root not in run_dir.parents:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": "Ceraxia",
            "task_id": resolved_task_id,
            "error": "native code run escaped run_root",
            "error_code": "invalid_run_dir",
            "actions": task_preflight_actions(
                False, "invalid_run_dir", resolved_task_id,
                governor_transport="http", governor_host=host, message=message,
            ),
        }
    request_payload = {
        **governor_payload_for(
            message,
            resolved_task_id,
            commander_order=commander_order,
        ),
        "run_dir": str(run_dir),
    }
    try:
        prepared = _post_governor_json(
            base + "/prepare_run",
            request_payload,
            governor.name,
        )
    except urllib.error.HTTPError as exc:
        error_payload = _bounded_http_error_payload(exc)
        error_code = str(error_payload.get("error_code") or "")
        if error_code == "delegation_not_authorized":
            public_code = "ceraxia_delegation_not_authorized"
        elif error_code == "prepare_identity_conflict":
            public_code = "ceraxia_prepare_identity_conflict"
        else:
            public_code = "governor_service_unavailable"
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": "Ceraxia",
            "task_id": resolved_task_id,
            "error": str(error_payload.get("error") or f"Ceraxia returned HTTP {exc.code}"),
            "error_code": public_code,
            "leadership_directive": error_payload.get("leadership_directive", {}),
            "response": error_payload,
            "actions": task_preflight_actions(
                False, public_code, resolved_task_id,
                governor_transport="http", governor_host=host, message=message,
            ),
        }
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": "Ceraxia",
            "task_id": resolved_task_id,
            "error": f"Ceraxia prepare unavailable: {exc}",
            "error_code": "governor_service_unavailable",
            "actions": task_preflight_actions(
                False, "governor_service_unavailable", resolved_task_id,
                governor_transport="http", governor_host=host, message=message,
            ),
        }
    if prepared.get("ok") is not True:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": "Ceraxia",
            "task_id": resolved_task_id,
            "error": str(prepared.get("error") or "Ceraxia did not prepare the native run"),
            "error_code": str(prepared.get("error_code") or "governor_prepare_failed"),
            "response": prepared,
            "actions": task_preflight_actions(
                False, str(prepared.get("error_code") or "governor_prepare_failed"),
                resolved_task_id, governor_transport="http", governor_host=host,
                message=message,
            ),
        }
    try:
        contract_payload = prepared.get("contract")
        if not isinstance(contract_payload, dict):
            raise ValueError("prepare response omitted the native contract")
        contract_payload = validate_native_code_contract(
            contract_payload,
            expected_task_id=resolved_task_id,
            expected_mission_id=mission_id_from_commander(resolved_task_id, commander_order),
        )
        persisted_package = load_native_code_run(run_dir)
        if persisted_package.get("ok") is not True:
            load_errors = persisted_package.get("errors")
            detail = "; ".join(load_errors) if isinstance(load_errors, list) else ""
            raise ValueError(
                "persisted native package could not be loaded"
                + (f": {detail}" if detail else "")
            )
        persisted_contract = (
            persisted_package.get("contract")
            if isinstance(persisted_package.get("contract"), dict)
            else {}
        )
        if persisted_contract != contract_payload:
            raise ValueError("prepare response and persisted native contract differ")
        package_errors = validate_native_code_run_package(run_dir)
        if package_errors:
            raise ValueError("; ".join(package_errors))
        persisted_receipt = (
            persisted_package.get("receipt")
            if isinstance(persisted_package.get("receipt"), dict)
            else {}
        )
        expected_request_sha256 = _ceraxia_prepare_request_sha256(
            str(contract_payload.get("goal") or ""),
            resolved_task_id,
            commander_order,
        )
        if persisted_receipt.get("prepare_request_sha256") != expected_request_sha256:
            raise ValueError("persisted native receipt belongs to a different prepare request")
        directive = validate_ceraxia_directive_for_commander(
            prepared.get("leadership_directive"),
            commander_order,
            expected_task_id=resolved_task_id,
            expected_mission_id=mission_id_from_commander(resolved_task_id, commander_order),
            require_delegation=True,
        )
        persisted_directive = validate_ceraxia_directive_for_commander(
            persisted_package.get("leadership_directive"),
            commander_order,
            expected_task_id=resolved_task_id,
            expected_mission_id=mission_id_from_commander(resolved_task_id, commander_order),
            require_delegation=True,
        )
        if directive != persisted_directive:
            raise ValueError("prepare response and persisted Ceraxia directive differ")
        persisted_governor_plan = (
            persisted_package.get("governor_plan")
            if isinstance(persisted_package.get("governor_plan"), dict)
            else {}
        )
        persisted_status = (
            persisted_package.get("status")
            if isinstance(persisted_package.get("status"), dict)
            else {}
        )
        if prepared.get("governor_plan") != persisted_governor_plan:
            raise ValueError("prepare response and persisted governor plan differ")
        if prepared.get("status") != persisted_status:
            raise ValueError("prepare response and persisted native status differ")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, CeraxiaDirectiveError) as exc:
        cleanup = cleanup_unregistered_run_dir(run_root, run_dir)
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": "Ceraxia",
            "task_id": resolved_task_id,
            "error": f"Ceraxia prepared an invalid native code run: {exc}",
            "error_code": "governor_prepare_invalid_run",
            "cleanup": cleanup,
            "actions": task_preflight_actions(
                False, "governor_prepare_invalid_run", resolved_task_id,
                governor_transport="http", governor_host=host, message=message,
            ),
        }
    ledger_path = run_dir / "task_ledger.json"
    if ledger_path.exists():
        ledger = TaskLedger.load(ledger_path)
        ledger_data = ledger.to_dict()
        if (
            str(ledger_data.get("task_id") or "") != resolved_task_id
            or str(ledger_data.get("governor") or "") != "Ceraxia"
        ):
            return {
                "ok": False,
                "gateway": "WarmasterGateway",
                "governor": "Ceraxia",
                "task_id": resolved_task_id,
                "error": "existing ledger does not belong to this Ceraxia prepare request",
                "error_code": "ceraxia_prepare_identity_conflict",
                "actions": task_preflight_actions(
                    False, "ceraxia_prepare_identity_conflict", resolved_task_id,
                    governor_transport="http", governor_host=host, message=message,
                ),
            }
    else:
        TaskLedger.create(
            ledger_path,
            resolved_task_id,
            str(contract_payload.get("goal") or message),
            "Ceraxia",
        )
    return {
        "ok": True,
        "gateway": "WarmasterGateway",
        "governor": "Ceraxia",
        "governor_transport": "http",
        "protocol_mode": "commander_order",
        "task_id": resolved_task_id,
        "run_dir": str(run_dir),
        "status": persisted_status,
        "contract": contract_payload,
        "governor_plan": persisted_governor_plan,
        "leadership_directive": directive,
        "prepare_replayed": bool(prepared.get("prepare_replayed")),
        "actions": created_task_actions(resolved_task_id),
    }


def prepare_native_iskandar_via_service(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor: Any,
    host: str = "127.0.0.1",
    port: int | None = None,
    commander_order: dict[str, Any] | None = None,
    require_commander_order: bool = False,
) -> dict[str, Any]:
    """Prepare one commander-bound Iskandar -> ResearchWarband native run."""
    if require_commander_order and not commander_order:
        return commander_order_required_payload(task_id, "http", host, message)
    if not isinstance(commander_order, dict) or not commander_order:
        return commander_order_required_payload(task_id, "http", host, message)
    validate_protocol_payload(commander_order, expected_type="commander_order")
    host = validate_service_host(host)
    service_port = int(port or governor.port)
    base = f"http://{host}:{service_port}"
    resolved_task_id = str(task_id or "").strip()
    if not resolved_task_id:
        try:
            preview = _post_governor_json(
                base + "/plan",
                governor_payload_for(message, None, commander_order=commander_order),
                governor.name,
            )
            preview_contract = (
                preview.get("contract")
                if isinstance(preview.get("contract"), dict)
                else {}
            )
            resolved_task_id = str(preview_contract.get("task_id") or "").strip()
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            return {
                "ok": False,
                "gateway": "WarmasterGateway",
                "governor": "IskandarKhayon",
                "error": f"Iskandar structural preview unavailable: {exc}",
                "error_code": "governor_service_unavailable",
                "actions": task_preflight_actions(
                    False, "governor_service_unavailable", "",
                    governor_transport="http", governor_host=host, message=message,
                ),
            }
    if not valid_task_id(resolved_task_id):
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": "IskandarKhayon",
            "task_id": resolved_task_id,
            "error": "Iskandar returned an invalid task_id",
            "error_code": "invalid_governor_task_id",
            "actions": task_preflight_actions(
                False, "invalid_governor_task_id", resolved_task_id,
                governor_transport="http", governor_host=host, message=message,
            ),
        }
    root = run_root.resolve()
    run_dir = (root / resolved_task_id).resolve()
    if run_dir == root or root not in run_dir.parents:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": "IskandarKhayon",
            "task_id": resolved_task_id,
            "error": "native research run escaped run_root",
            "error_code": "invalid_run_dir",
            "actions": task_preflight_actions(
                False, "invalid_run_dir", resolved_task_id,
                governor_transport="http", governor_host=host, message=message,
            ),
        }
    request_payload = {
        **governor_payload_for(
            message,
            resolved_task_id,
            commander_order=commander_order,
        ),
        "run_dir": str(run_dir),
    }
    try:
        prepared = _post_governor_json(
            base + "/prepare_run",
            request_payload,
            governor.name,
        )
    except urllib.error.HTTPError as exc:
        error_payload = _bounded_http_error_payload(exc)
        error_code = str(error_payload.get("error_code") or "")
        public_code = {
            "delegation_not_authorized": "iskandar_delegation_not_authorized",
            "prepare_identity_conflict": "iskandar_prepare_identity_conflict",
            "research_warband_backend_unavailable": "research_warband_backend_unavailable",
        }.get(error_code, "governor_service_unavailable")
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": "IskandarKhayon",
            "task_id": resolved_task_id,
            "error": str(error_payload.get("error") or f"Iskandar returned HTTP {exc.code}"),
            "error_code": public_code,
            "leadership_directive": error_payload.get("leadership_directive", {}),
            "response": error_payload,
            "actions": task_preflight_actions(
                False, public_code, resolved_task_id,
                governor_transport="http", governor_host=host, message=message,
            ),
        }
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": "IskandarKhayon",
            "task_id": resolved_task_id,
            "error": f"Iskandar prepare unavailable: {exc}",
            "error_code": "governor_service_unavailable",
            "actions": task_preflight_actions(
                False, "governor_service_unavailable", resolved_task_id,
                governor_transport="http", governor_host=host, message=message,
            ),
        }
    if prepared.get("ok") is not True:
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": "IskandarKhayon",
            "task_id": resolved_task_id,
            "error": str(prepared.get("error") or "Iskandar did not prepare the native run"),
            "error_code": str(prepared.get("error_code") or "governor_prepare_failed"),
            "response": prepared,
            "actions": task_preflight_actions(
                False, str(prepared.get("error_code") or "governor_prepare_failed"),
                resolved_task_id, governor_transport="http", governor_host=host,
                message=message,
            ),
        }
    try:
        contract_payload = prepared.get("contract")
        if not isinstance(contract_payload, dict):
            raise ValueError("prepare response omitted the native research contract")
        contract_payload = validate_native_research_contract(
            contract_payload,
            expected_task_id=resolved_task_id,
            expected_mission_id=mission_id_from_commander(resolved_task_id, commander_order),
        )
        persisted = load_native_research_run(run_dir)
        if persisted.get("ok") is not True:
            errors = persisted.get("errors") if isinstance(persisted.get("errors"), list) else []
            raise ValueError("persisted native research package could not be loaded: " + "; ".join(errors))
        if persisted.get("contract") != contract_payload:
            raise ValueError("prepare response and persisted native research contract differ")
        package_errors = validate_native_research_run_package(run_dir)
        if package_errors:
            raise ValueError("; ".join(package_errors))
        receipt = persisted.get("receipt") if isinstance(persisted.get("receipt"), dict) else {}
        expected_request_sha256 = native_research_prepare_request_sha256(
            contract_payload,
            commander_order,
        )
        if receipt.get("kind") != "native_research_run_receipt" or receipt.get("version") != 1:
            raise ValueError("persisted native research receipt identity is invalid")
        if receipt.get("prepare_request_sha256") != expected_request_sha256:
            raise ValueError("persisted native research receipt belongs to a different prepare request")
        directive = validate_iskandar_directive_for_commander(
            prepared.get("leadership_directive"),
            commander_order,
            expected_task_id=resolved_task_id,
            expected_mission_id=mission_id_from_commander(resolved_task_id, commander_order),
            require_delegation=True,
        )
        persisted_directive = validate_iskandar_directive_for_commander(
            persisted.get("leadership_directive"),
            commander_order,
            expected_task_id=resolved_task_id,
            expected_mission_id=mission_id_from_commander(resolved_task_id, commander_order),
            require_delegation=True,
        )
        if directive != persisted_directive:
            raise ValueError("prepare response and persisted Iskandar directive differ")
        persisted_plan = persisted.get("governor_plan") if isinstance(persisted.get("governor_plan"), dict) else {}
        persisted_status = persisted.get("status") if isinstance(persisted.get("status"), dict) else {}
        if prepared.get("governor_plan") != persisted_plan:
            raise ValueError("prepare response and persisted native research governor plan differ")
        if prepared.get("status") != persisted_status:
            raise ValueError("prepare response and persisted native research status differ")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, IskandarDirectiveError) as exc:
        cleanup = cleanup_unregistered_run_dir(run_root, run_dir)
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": "IskandarKhayon",
            "task_id": resolved_task_id,
            "error": f"Iskandar prepared an invalid native research run: {exc}",
            "error_code": "governor_prepare_invalid_run",
            "cleanup": cleanup,
            "actions": task_preflight_actions(
                False, "governor_prepare_invalid_run", resolved_task_id,
                governor_transport="http", governor_host=host, message=message,
            ),
        }
    ledger_path = run_dir / "task_ledger.json"
    if ledger_path.exists():
        ledger_data = TaskLedger.load(ledger_path).to_dict()
        if (
            str(ledger_data.get("task_id") or "") != resolved_task_id
            or str(ledger_data.get("governor") or "") != "IskandarKhayon"
        ):
            return {
                "ok": False,
                "gateway": "WarmasterGateway",
                "governor": "IskandarKhayon",
                "task_id": resolved_task_id,
                "error": "existing ledger does not belong to this Iskandar prepare request",
                "error_code": "iskandar_prepare_identity_conflict",
                "actions": task_preflight_actions(
                    False, "iskandar_prepare_identity_conflict", resolved_task_id,
                    governor_transport="http", governor_host=host, message=message,
                ),
            }
    else:
        TaskLedger.create(
            ledger_path,
            resolved_task_id,
            str(contract_payload.get("goal") or message),
            "IskandarKhayon",
        )
    return {
        "ok": True,
        "gateway": "WarmasterGateway",
        "governor": "IskandarKhayon",
        "governor_transport": "http",
        "protocol_mode": "commander_order",
        "task_id": resolved_task_id,
        "run_dir": str(run_dir),
        "status": persisted_status,
        "contract": contract_payload,
        "governor_plan": persisted_plan,
        "leadership_directive": directive,
        "prepare_replayed": bool(prepared.get("prepare_replayed")),
        "actions": created_task_actions(resolved_task_id),
    }


def prepare_task_via_governor_service(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor: Any,
    host: str = "127.0.0.1",
    port: int | None = None,
    commander_order: dict[str, Any] | None = None,
    require_commander_order: bool = False,
) -> dict[str, Any]:
    if governor.name == "Ceraxia":
        return prepare_native_ceraxia_via_service(
            message,
            task_id,
            run_root,
            governor,
            host=host,
            port=port,
            commander_order=commander_order,
            require_commander_order=require_commander_order,
        )
    if governor.name == "IskandarKhayon":
        return prepare_native_iskandar_via_service(
            message,
            task_id,
            run_root,
            governor,
            host=host,
            port=port,
            commander_order=commander_order,
            require_commander_order=require_commander_order,
        )
    if require_commander_order and not commander_order:
        return commander_order_required_payload(task_id, "http", host, message)
    host = validate_service_host(host)
    service_port = int(port or governor.port)
    base = f"http://{host}:{service_port}"
    governor_payload = governor_payload_for(message, task_id, commander_order=commander_order)
    try:
        plan = _post_governor_json(base + "/plan", governor_payload, governor.name)
        plan = attach_governor_plan_payload(plan, mission_id_from_commander(task_id, commander_order), commander_order)
    except urllib.error.HTTPError as exc:
        error_payload = _bounded_http_error_payload(exc)
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": f"governor service returned HTTP {exc.code}",
            "error_code": "governor_service_unavailable",
            "governor": governor.name,
            "task_id": task_id or "",
            "response": error_payload,
            "actions": task_preflight_actions(
                False,
                "governor_service_unavailable",
                task_id or "",
                governor_transport="http",
                governor_host=host,
                message=message,
            ),
        }
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
    if (
        not service_task_id
        or not valid_task_id(service_task_id)
        or (task_id is not None and service_task_id != task_id)
    ):
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
    run_dir = (run_root / service_task_id).resolve()
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
        prepare_payload = {
            **governor_payload_for(
                message,
                service_task_id,
                commander_order=commander_order,
            ),
            "run_dir": str(run_dir),
        }
        prepared = _post_governor_json(
            base + "/prepare_run",
            prepare_payload,
            governor.name,
        )
    except urllib.error.HTTPError as exc:
        error_payload = _bounded_http_error_payload(exc)
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": f"governor service returned HTTP {exc.code}",
            "error_code": "governor_service_unavailable",
            "governor": governor.name,
            "task_id": service_task_id,
            "response": error_payload,
            "actions": task_preflight_actions(
                False,
                "governor_service_unavailable",
                service_task_id,
                governor_transport="http",
                governor_host=host,
                message=message,
            ),
        }
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
    planned_governor_plan = plan.get("governor_plan") if isinstance(plan.get("governor_plan"), dict) else {}
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
    if planned_governor_plan:
        (run_dir / "governor_plan.json").write_text(json.dumps(planned_governor_plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "gateway": "WarmasterGateway",
        "governor": governor.name,
        "governor_transport": "http",
        "protocol_mode": "commander_order" if commander_order else "commander_order_missing",
        "task_id": service_task_id,
        "run_dir": str(run_dir),
        "status": prepared.get("status", {}),
        "governor_plan": planned_governor_plan,
        "actions": created_task_actions(service_task_id),
    }


def route_failure_payload(route: Any) -> dict[str, Any]:
    error_code = str(getattr(route, "error_code", "") or ("governor_inactive" if route.governor else "no_supported_governor"))
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
        "route": route.to_dict() if hasattr(route, "to_dict") else {"kind": route.kind, "governor": route.governor, "ok": route.ok, "reason": route.reason},
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
    try:
        entry = run_dir.lstat()
    except FileNotFoundError:
        return {"attempted": False, "removed": False, "reason": "run_dir does not exist"}
    except OSError as exc:
        return {"attempted": False, "removed": False, "error": str(exc)}
    if stat.S_ISLNK(entry.st_mode):
        return {"attempted": False, "removed": False, "reason": "run_dir is a symlink"}
    if not stat.S_ISDIR(entry.st_mode):
        return {"attempted": False, "removed": False, "reason": "run_dir is not a directory"}
    target = run_dir.resolve()
    if target == root:
        return {"attempted": False, "removed": False, "reason": "run_dir is run_root"}
    if root not in target.parents:
        return {"attempted": False, "removed": False, "reason": "run_dir is outside run_root"}
    if (target / "task_ledger.json").exists():
        return {"attempted": False, "removed": False, "reason": "ledger exists"}
    try:
        shutil.rmtree(target)
    except OSError as exc:
        return {"attempted": True, "removed": False, "error": str(exc)}
    return {"attempted": True, "removed": True}


def resolve_governor_for_prepare(message: str, forced_governor: str | None = None) -> tuple[str, Any, dict[str, Any]]:
    if forced_governor:
        governor_name = forced_governor.strip()
        governor_ref = governor_by_name(governor_name)
        if governor_ref is None:
            return governor_name, None, {
                "ok": False,
                "error": f"unknown forced governor: {governor_name}",
                "error_code": "unknown_forced_governor",
                "kind": "commanded",
                "governor": governor_name,
                "route": {
                    "ok": True,
                    "governor": governor_name,
                    "kind": "commanded",
                    "reason": "selected by Warmaster commander_order",
                    "source": "forced_governor",
                    "model_brain": {"status": "skipped", "reason": "governor already selected by commander_order"},
                },
            }
        return governor_name, governor_ref, {
            "ok": True,
            "kind": "commanded",
            "governor": governor_name,
            "route": {
                "ok": True,
                "governor": governor_name,
                "kind": "commanded",
                "reason": "selected by Warmaster commander_order",
                "source": "forced_governor",
                "model_brain": {"status": "skipped", "reason": "governor already selected by commander_order"},
            },
        }
    route = route_message(message)
    if not route.ok:
        return str(route.governor or ""), None, route_failure_payload(route)
    if route.requires_decomposition:
        return str(route.governor or ""), None, {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "task requires multi-governor decomposition before a single run can be prepared",
            "error_code": "multi_governor_decomposition_required",
            "kind": route.kind,
            "governor": route.governor,
            "route": route.to_dict(),
        }
    governor_name = str(route.governor or "")
    return governor_name, governor_by_name(governor_name), {"ok": True, "kind": route.kind, "governor": governor_name, "route": route.to_dict()}


def prepare_task(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor_transport: str = "local",
    governor_host: str = "127.0.0.1",
    forced_governor: str | None = None,
    commander_order: dict[str, Any] | None = None,
    require_commander_order: bool = False,
) -> dict[str, Any]:
    if require_commander_order and not commander_order:
        return commander_order_required_payload(task_id, governor_transport, governor_host, message)
    if task_id is not None and not valid_task_id(task_id):
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "task_id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127} and must not contain '..'",
            "error_code": "invalid_task_id",
            "task_id": task_id,
            "actions": task_preflight_actions(False, "invalid_task_id", task_id or "", governor_transport=governor_transport, governor_host=governor_host, message=message),
        }
    governor, governor_ref, route_payload = resolve_governor_for_prepare(message, forced_governor=forced_governor)
    if not route_payload.get("ok"):
        if isinstance(route_payload.get("actions"), dict):
            return route_payload
        payload = {
            "gateway": "WarmasterGateway",
            **route_payload,
            "actions": task_preflight_actions(
                False,
                str(route_payload.get("error_code") or "governor_inactive"),
                task_id or "",
                governor_transport=governor_transport,
                governor_host=governor_host,
                message=message,
            ),
        }
        return payload
    if governor_ref is None or not governor_ref.active():
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": f"governor is not active: {governor}",
            "error_code": "governor_inactive",
            "kind": str(route_payload.get("kind") or "commanded"),
            "route": route_payload.get("route") if isinstance(route_payload.get("route"), dict) else {},
            "actions": task_preflight_actions(False, "governor_inactive", task_id or "", governor_transport=governor_transport, governor_host=governor_host, message=message),
        }
    if governor in {"Ceraxia", "IskandarKhayon"} and governor_transport == "local":
        is_research = governor == "IskandarKhayon"
        error_code = (
            "iskandar_leader_service_required"
            if is_research
            else "ceraxia_leader_service_required"
        )
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": governor,
            "kind": str(route_payload.get("kind") or "commanded"),
            "route": (
                route_payload.get("route")
                if isinstance(route_payload.get("route"), dict)
                else {}
            ),
            "protocol_mode": (
                "commander_order" if commander_order else "commander_order_missing"
            ),
            "error": (
                "Iskandar research missions require governor_transport=http so the live leader "
                "can validate and persist iskandar_directive.json"
                if is_research
                else "Ceraxia code missions require governor_transport=http so the live leader "
                "can validate and persist ceraxia_directive.json"
            ),
            "error_code": error_code,
            "task_id": task_id or "",
            "actions": task_preflight_actions(
                False,
                error_code,
                task_id or "",
                governor_transport="http",
                governor_host=governor_host,
                message=message,
            ),
        }
    if governor_transport == "http":
        return prepare_task_via_governor_service(
            message,
            task_id,
            run_root,
            governor_ref,
            host=governor_host,
            commander_order=commander_order,
            require_commander_order=require_commander_order,
        )
    if governor_transport != "local":
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "governor_transport must be local or http",
            "error_code": "invalid_governor_transport",
            "actions": task_preflight_actions(False, "invalid_governor_transport", task_id or "", governor_transport=governor_transport, governor_host=governor_host, message=message),
        }
    if governor != "Moriana":
        raise RuntimeError("native governors cannot enter the legacy local worker-plan path")
    task_text = governor_task_text(message, commander_order)
    plan = plan_image_task(task_text, task_id=task_id)
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
    plan_payload = attach_governor_plan_payload(plan_payload, mission_id_from_commander(plan.contract.task_id, commander_order), commander_order)
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
    status = write_pipeline_run(
        plan.contract,
        run_dir,
        oversight=oversight,
        mission_id=mission_id_from_commander(plan.contract.task_id, commander_order),
    )
    TaskLedger.create(run_dir / "task_ledger.json", plan.contract.task_id, plan.contract.goal, governor)
    (run_dir / "governor_plan.json").write_text(json.dumps(plan_payload["governor_plan"], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "ok": status["ok"],
        "gateway": "WarmasterGateway",
        "governor": governor,
        "protocol_mode": "commander_order" if commander_order else "commander_order_missing",
        "task_id": plan.contract.task_id,
        "run_dir": str(run_dir),
        "status": status,
        "governor_plan": plan_payload["governor_plan"],
        "actions": created_task_actions(plan.contract.task_id),
    }


def preflight_task(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor_transport: str = "local",
    governor_host: str = "127.0.0.1",
    include_brigade_health: bool = False,
    forced_governor: str | None = None,
    commander_order: dict[str, Any] | None = None,
    require_commander_order: bool = False,
) -> dict[str, Any]:
    if require_commander_order and not commander_order:
        return commander_order_required_payload(task_id, governor_transport, governor_host, message, include_brigade_health)
    if task_id is not None and not valid_task_id(task_id):
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "invalid task_id",
            "error_code": "invalid_task_id",
            "task_id": task_id,
            "actions": task_preflight_actions(False, "invalid_task_id", task_id or "", include_brigade_health, governor_transport, governor_host, message),
        }
    governor_name, governor_ref, route_payload = resolve_governor_for_prepare(message, forced_governor=forced_governor)
    if not route_payload.get("ok"):
        if isinstance(route_payload.get("actions"), dict):
            return route_payload
        return {
            "gateway": "WarmasterGateway",
            **route_payload,
            "actions": task_preflight_actions(
                False,
                str(route_payload.get("error_code") or "governor_inactive"),
                task_id or "",
                include_brigade_health,
                governor_transport,
                governor_host,
                message,
            ),
        }
    if governor_ref is None or not governor_ref.active():
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": f"governor is not active: {governor_name}",
            "error_code": "governor_inactive",
            "kind": str(route_payload.get("kind") or "commanded"),
            "route": route_payload.get("route") if isinstance(route_payload.get("route"), dict) else {},
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
    if governor_ref.name in {"Ceraxia", "IskandarKhayon"} and governor_transport == "local":
        is_research = governor_ref.name == "IskandarKhayon"
        error_code = (
            "iskandar_leader_service_required"
            if is_research
            else "ceraxia_leader_service_required"
        )
        return {
            "ok": False,
            "gateway": "WarmasterGateway",
            "governor": governor_ref.name,
            "kind": str(route_payload.get("kind") or "commanded"),
            "route": (
                route_payload.get("route")
                if isinstance(route_payload.get("route"), dict)
                else {}
            ),
            "protocol_mode": (
                "commander_order" if commander_order else "commander_order_missing"
            ),
            "error": (
                "Iskandar research preflight requires governor_transport=http so the live leader "
                "can produce a validated leadership directive"
                if is_research
                else "Ceraxia code preflight requires governor_transport=http so the live leader "
                "can produce a validated leadership directive"
            ),
            "error_code": error_code,
            "task_id": task_id or "",
            "actions": task_preflight_actions(
                False,
                error_code,
                task_id or "",
                include_brigade_health,
                "http",
                governor_host,
                message,
            ),
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
            plan_payload = _post_governor_json(
                base + "/plan",
                governor_payload_for(message, task_id, commander_order=commander_order),
                governor_ref.name,
            )
            if governor_ref.name not in {"Ceraxia", "IskandarKhayon"}:
                plan_payload = attach_governor_plan_payload(
                    plan_payload,
                    mission_id_from_commander(task_id, commander_order),
                    commander_order,
                )
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
        if governor_ref.name == "Ceraxia":
            resolved_task_id = str(contract.get("task_id") or "").strip()
            errors: list[str] = []
            try:
                contract = validate_native_code_contract(
                    contract,
                    expected_task_id=str(task_id or ""),
                    expected_mission_id=mission_id_from_commander(
                        resolved_task_id or task_id,
                        commander_order,
                    ),
                )
            except ValueError as exc:
                errors.append(str(exc))
            run_dir = run_root / resolved_task_id if resolved_task_id else run_root / "_invalid"
            backend = (
                plan_payload.get("active_execution_backend")
                if isinstance(plan_payload.get("active_execution_backend"), dict)
                else {}
            )
            backend_ready = backend.get("healthy") is True
            if not backend_ready:
                errors.append("SkitariiWarband backend is not ready")
            if resolved_task_id and run_dir.exists():
                errors.append("task_id already exists")
            ok = not errors
            if resolved_task_id and run_dir.exists():
                error_code = "task_exists"
            elif not backend_ready:
                error_code = "skitarii_backend_unavailable"
            elif errors:
                error_code = "invalid_native_code_contract"
            else:
                error_code = ""
            payload = {
                "ok": ok,
                "gateway": "WarmasterGateway",
                "governor": "Ceraxia",
                "governor_transport": "http",
                "protocol_mode": "commander_order" if commander_order else "commander_order_missing",
                "task_id": resolved_task_id,
                "route": route_payload.get("route") if isinstance(route_payload.get("route"), dict) else {},
                "contract_summary": contract_summary(contract),
                "governor_plan": plan_payload.get("governor_plan") if isinstance(plan_payload.get("governor_plan"), dict) else {},
                "leadership_authorization": "pending_prepare",
                "active_execution_backend": backend,
                "validation": {"ok": not errors, "errors": errors},
                "missing_workers": [],
                "unavailable_workers": [],
                "worker_availability": {
                    "ok": backend_ready,
                    "required_workers": [],
                    "available_workers": [],
                    "missing_workers": [],
                    "unavailable_workers": [],
                },
                "would_create_run_dir": str(run_dir) if resolved_task_id else "",
                "error_code": error_code,
                "actions": task_preflight_actions(
                    ok,
                    error_code,
                    resolved_task_id,
                    include_brigade_health,
                    "http",
                    governor_host,
                    message,
                ),
            }
            if include_brigade_health:
                payload["brigade_readiness"] = compact_brigade_readiness(host=governor_host)
            return payload
        if governor_ref.name == "IskandarKhayon":
            resolved_task_id = str(contract.get("task_id") or "").strip()
            errors: list[str] = []
            try:
                contract = validate_native_research_contract(
                    contract,
                    expected_task_id=str(task_id or ""),
                    expected_mission_id=mission_id_from_commander(
                        resolved_task_id or task_id,
                        commander_order,
                    ),
                )
            except ValueError as exc:
                errors.append(str(exc))
            run_dir = run_root / resolved_task_id if resolved_task_id else run_root / "_invalid"
            backend = (
                plan_payload.get("active_execution_backend")
                if isinstance(plan_payload.get("active_execution_backend"), dict)
                else {}
            )
            backend_ready = backend.get("healthy") is True
            if not backend_ready:
                errors.append("ResearchWarband backend is not ready")
            if resolved_task_id and run_dir.exists():
                errors.append("task_id already exists")
            ok = not errors
            if resolved_task_id and run_dir.exists():
                error_code = "task_exists"
            elif not backend_ready:
                error_code = "research_warband_backend_unavailable"
            elif errors:
                error_code = "invalid_native_research_contract"
            else:
                error_code = ""
            payload = {
                "ok": ok,
                "gateway": "WarmasterGateway",
                "governor": "IskandarKhayon",
                "governor_transport": "http",
                "protocol_mode": "commander_order" if commander_order else "commander_order_missing",
                "task_id": resolved_task_id,
                "route": route_payload.get("route") if isinstance(route_payload.get("route"), dict) else {},
                "contract_summary": contract_summary(contract),
                "governor_plan": plan_payload.get("governor_plan") if isinstance(plan_payload.get("governor_plan"), dict) else {},
                "leadership_authorization": "pending_prepare",
                "active_execution_backend": backend,
                "validation": {"ok": not errors, "errors": errors},
                "missing_workers": [],
                "unavailable_workers": [],
                "worker_availability": {
                    "ok": backend_ready,
                    "required_workers": [],
                    "available_workers": [],
                    "missing_workers": [],
                    "unavailable_workers": [],
                },
                "would_create_run_dir": str(run_dir) if resolved_task_id else "",
                "error_code": error_code,
                "actions": task_preflight_actions(
                    ok,
                    error_code,
                    resolved_task_id,
                    include_brigade_health,
                    "http",
                    governor_host,
                    message,
                ),
            }
            if include_brigade_health:
                payload["brigade_readiness"] = compact_brigade_readiness(host=governor_host)
            return payload
    else:
        if governor_ref.name != "Moriana":
            raise RuntimeError("native governors cannot enter the legacy local preflight path")
        task_text = governor_task_text(message, commander_order)
        plan = plan_image_task(task_text, task_id=task_id)
        plan_payload = plan.to_dict()
        plan_payload = attach_governor_plan_payload(plan_payload, mission_id_from_commander(str(plan.contract.task_id), commander_order), commander_order)
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
        "protocol_mode": "commander_order" if commander_order else "commander_order_missing",
        "task_id": resolved_task_id,
        "route": route_payload.get("route") if isinstance(route_payload.get("route"), dict) else {},
        "contract_summary": contract_summary(contract),
        "governor_plan": plan_payload.get("governor_plan") if isinstance(plan_payload.get("governor_plan"), dict) else {},
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
