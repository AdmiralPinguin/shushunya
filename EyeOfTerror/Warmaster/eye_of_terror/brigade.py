"""Registry, worker/governor health, and brigade-readiness aggregation."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .gateway_util import validate_service_host
from .governors import governor_refs
from .native_runs import native_adapter_for_execution
from .registry import worker_refs
from .runtime_state import REPO_ROOT


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
    execution = contract.get("execution") if isinstance(contract.get("execution"), dict) else {}
    native_adapter = native_adapter_for_execution(execution)
    if not steps and native_adapter is not None:
        steps = [
            {
                "step_id": native_adapter.step_id,
                "worker": native_adapter.backend,
                "depends_on": [],
                "expected_artifacts": [],
                "expected_artifact_count": 0,
            }
        ]
    return {
        "kind": str(contract.get("kind") or ""),
        "goal": str(contract.get("goal") or ""),
        "assigned_governor": str(contract.get("assigned_governor") or ""),
        "steps": steps,
        "step_count": len(steps),
        "required_artifacts": len(contract.get("required_artifacts") if isinstance(contract.get("required_artifacts"), list) else []),
        "execution": execution,
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


def registry_summary(items: list[dict[str, Any]], include_health: bool = False) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    reachable = 0
    metadata_available = 0
    names: list[str] = []
    for item in items:
        name = str(item.get("name") or "")
        if name:
            names.append(name)
        status = str(item.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        if item.get("metadata_available"):
            metadata_available += 1
        runtime = item.get("runtime") if isinstance(item.get("runtime"), dict) else {}
        if runtime.get("reachable"):
            reachable += 1
    summary = {
        "total": len(items),
        "active": by_status.get("active", 0),
        "prototype": by_status.get("prototype", 0),
        "planned": by_status.get("planned", 0),
        "by_status": by_status,
        "names": names,
    }
    if metadata_available:
        summary["metadata_available"] = metadata_available
    if include_health:
        summary["reachable"] = reachable
        summary["unreachable"] = len(items) - reachable
    return summary


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
