"""Native run-package contract for Ceraxia's single Skitarii delegation.

This module deliberately contains no repository, file, command, dependency, or
implementation plan.  Ceraxia owns the leadership boundary; Skitarii owns every
detailed planning and execution decision behind the one native mission step.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from EyeOfTerror.common_protocol import governor_plan, validate_protocol_payload
from EyeOfTerror.common_protocol.ceraxia_directive import validate_ceraxia_directive
from EyeOfTerror.common_protocol.protocol import utc_now


NATIVE_CONTRACT_VERSION = 2
NATIVE_RUN_KIND = "native_skitarii_code"
NATIVE_EXECUTION = {
    "kind": "skitarii_mission",
    "step_id": "skitarii",
    "backend": "SkitariiWarband",
}
NATIVE_CONTRACT_FIELDS = {
    "version",
    "task_id",
    "mission_id",
    "kind",
    "goal",
    "assigned_governor",
    "non_goals",
    "completion_criteria",
    "quality_gates",
    "execution",
}
NATIVE_EXECUTION_FIELDS = {"kind", "step_id", "backend"}
GOVERNOR_PLAN_FIELDS = {
    "type",
    "protocol_version",
    "mission_id",
    "created_at",
    "governor",
    "understanding",
    "work_plan",
    "quality_gates",
    "expected_deliverables",
}
DELEGATION_FIELDS = {
    "step_id",
    "worker",
    "goal",
    "depends_on",
    "expected_artifacts",
}
STATUS_FIELDS = {
    "ok",
    "version",
    "run_kind",
    "task_id",
    "mission_id",
    "governor",
    "step_count",
    "steps",
    "execution",
    "run_dir",
    "contract_path",
    "directive_path",
    "governor_plan_path",
    "receipt_path",
}
STATUS_STEP_FIELDS = {
    "step_id",
    "worker",
    "backend",
    "purpose",
    "depends_on",
    "input_artifacts",
    "expected_artifacts",
}
RECEIPT_FIELDS = {
    "kind",
    "version",
    "task_id",
    "mission_id",
    "created_at",
    "prepare_request_sha256",
    "contract_sha256",
    "directive_sha256",
    "governor_plan_sha256",
}
PACKAGE_FILES = {
    "contract": "contract.json",
    "leadership_directive": "ceraxia_directive.json",
    "governor_plan": "governor_plan.json",
    "status": "status.json",
    "receipt": "native_run_receipt.json",
}
_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_MISSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _text(value: Any, field: str, *, limit: int = 100_000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not result:
        raise ValueError(f"{field} must not be empty")
    if len(result) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    if any(ord(char) < 32 and char not in "\r\n\t" for char in result):
        raise ValueError(f"{field} contains control characters")
    return result


def _task_id(value: Any, field: str = "task_id") -> str:
    result = _text(value, field, limit=128)
    if not _TASK_ID_RE.fullmatch(result) or ".." in result:
        raise ValueError(
            f"{field} must match [A-Za-z0-9][A-Za-z0-9_.-]{{0,127}} and exclude '..'",
        )
    return result


def _mission_id(value: Any, field: str = "mission_id") -> str:
    result = _text(value, field, limit=256)
    if not _MISSION_ID_RE.fullmatch(result) or ".." in result:
        raise ValueError(f"{field} must be a non-path identifier")
    return result


def _strings(value: Any, field: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    result = [_text(item, f"{field}[{index}]", limit=4_000) for index, item in enumerate(value)]
    if required and not result:
        raise ValueError(f"{field} must not be empty")
    if len(result) > 32:
        raise ValueError(f"{field} has more than 32 items")
    if len(set(result)) != len(result):
        raise ValueError(f"{field} contains duplicate items")
    return result


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _read_json_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{path.name} is missing or is not a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"refusing to replace symlink: {path.name}")
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _generated_task_id(goal: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", goal.lower())[:6]
    slug = "-".join(words)[:80] or "task"
    digest = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:16]
    return f"ceraxia-{slug}-{digest}-code-task"


def build_native_code_contract(
    goal: str,
    task_id: str | None,
    mission_id: str = "",
) -> dict[str, Any]:
    """Build the exact v2 contract for one Ceraxia-to-Skitarii mission."""
    clean_goal = _text(goal, "goal")
    clean_task_id = _task_id(task_id or _generated_task_id(clean_goal))
    clean_mission_id = _mission_id(mission_id or f"mission-{clean_task_id}")
    return validate_native_code_contract(
        {
            "version": NATIVE_CONTRACT_VERSION,
            "task_id": clean_task_id,
            "mission_id": clean_mission_id,
            "kind": "code",
            "goal": clean_goal,
            "assigned_governor": "Ceraxia",
            "non_goals": [
                "Do not perform unrelated refactors.",
                "Do not bypass Ceraxia's leadership directive.",
                "Do not claim completion without executable verification evidence.",
            ],
            "completion_criteria": [
                "The requested behavior is implemented and verified by Skitarii.",
                "Repository mutations are represented by a controlled, auditable patch result.",
                "Blockers and required user decisions are reported honestly.",
            ],
            "quality_gates": [
                "ceraxia_leadership_directive_valid",
                "skitarii_executable_acceptance",
                "controlled_repository_apply",
            ],
            "execution": dict(NATIVE_EXECUTION),
        },
    )


def validate_native_code_contract(
    payload: dict[str, Any],
    expected_task_id: str = "",
    expected_mission_id: str = "",
) -> dict[str, Any]:
    """Validate and normalize an exact native contract; raise ``ValueError`` on drift."""
    if not isinstance(payload, dict):
        raise ValueError("native code contract must be an object")
    unknown = sorted(set(payload) - NATIVE_CONTRACT_FIELDS)
    missing = sorted(NATIVE_CONTRACT_FIELDS - set(payload))
    if unknown:
        raise ValueError(f"native code contract has unknown fields: {unknown}")
    if missing:
        raise ValueError(f"native code contract is missing fields: {missing}")
    if payload.get("version") != NATIVE_CONTRACT_VERSION:
        raise ValueError(f"version must be {NATIVE_CONTRACT_VERSION}")
    task_id = _task_id(payload.get("task_id"))
    mission_id = _mission_id(payload.get("mission_id"))
    if expected_task_id and task_id != expected_task_id:
        raise ValueError("native code contract task_id does not match the run")
    if expected_mission_id and mission_id != expected_mission_id:
        raise ValueError("native code contract mission_id does not match the mission")
    if payload.get("kind") != "code":
        raise ValueError("kind must be code")
    if payload.get("assigned_governor") != "Ceraxia":
        raise ValueError("assigned_governor must be Ceraxia")
    execution = payload.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("execution must be an object")
    if set(execution) != NATIVE_EXECUTION_FIELDS or execution != NATIVE_EXECUTION:
        raise ValueError(f"execution must be exactly {NATIVE_EXECUTION}")
    return {
        "version": NATIVE_CONTRACT_VERSION,
        "task_id": task_id,
        "mission_id": mission_id,
        "kind": "code",
        "goal": _text(payload.get("goal"), "goal"),
        "assigned_governor": "Ceraxia",
        "non_goals": _strings(payload.get("non_goals"), "non_goals"),
        "completion_criteria": _strings(
            payload.get("completion_criteria"),
            "completion_criteria",
            required=True,
        ),
        "quality_gates": _strings(payload.get("quality_gates"), "quality_gates", required=True),
        "execution": dict(NATIVE_EXECUTION),
    }


def _validate_native_governor_plan(
    payload: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("native governor_plan must be an object")
    unknown = sorted(set(payload) - GOVERNOR_PLAN_FIELDS)
    missing = sorted(GOVERNOR_PLAN_FIELDS - set(payload))
    if unknown:
        raise ValueError(f"native governor_plan has unknown fields: {unknown}")
    if missing:
        raise ValueError(f"native governor_plan is missing fields: {missing}")
    validate_protocol_payload(payload, expected_type="governor_plan")
    if payload.get("mission_id") != contract["mission_id"]:
        raise ValueError("governor_plan mission_id does not match the native contract")
    if payload.get("governor") != "Ceraxia":
        raise ValueError("governor_plan governor must be Ceraxia")
    work_plan = payload.get("work_plan")
    if not isinstance(work_plan, list) or len(work_plan) != 1 or not isinstance(work_plan[0], dict):
        raise ValueError("native governor_plan must contain exactly one Skitarii delegation")
    delegation = work_plan[0]
    if set(delegation) != DELEGATION_FIELDS:
        raise ValueError("native governor_plan delegation has non-leadership fields")
    expected_delegation = {
        "step_id": "skitarii",
        "worker": "SkitariiWarband",
        "goal": "Own detailed planning, implementation, verification, and internal repair.",
        "depends_on": [],
        "expected_artifacts": [],
    }
    if delegation != expected_delegation:
        raise ValueError("native governor_plan delegation must be the exact Skitarii mission boundary")
    quality_gates = _strings(payload.get("quality_gates"), "governor_plan.quality_gates", required=True)
    if quality_gates != contract["quality_gates"]:
        raise ValueError("governor_plan quality_gates do not match the native contract")
    expected_deliverables = _strings(
        payload.get("expected_deliverables"),
        "governor_plan.expected_deliverables",
        required=True,
    )
    return {
        key: payload[key]
        for key in (
            "type",
            "protocol_version",
            "mission_id",
            "created_at",
            "governor",
            "understanding",
            "work_plan",
            "quality_gates",
            "expected_deliverables",
        )
    } | {
        "understanding": _text(payload.get("understanding"), "governor_plan.understanding"),
        "quality_gates": quality_gates,
        "expected_deliverables": expected_deliverables,
    }


def native_governor_plan(
    contract: dict[str, Any],
    commander_order: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a protocol governor plan containing one leadership delegation only."""
    normalized = validate_native_code_contract(contract)
    command = commander_order if isinstance(commander_order, dict) else {}
    if command:
        validate_protocol_payload(command, expected_type="commander_order")
        if str(command.get("mission_id") or "") != normalized["mission_id"]:
            raise ValueError("commander_order mission_id does not match the native contract")
        if str(command.get("to") or "") != "Ceraxia":
            raise ValueError("commander_order must delegate the code mission to Ceraxia")
    understanding = str(
        command.get("primary_goal")
        or command.get("commander_intent")
        or normalized["goal"]
    ).strip()
    payload = governor_plan(
        normalized["mission_id"],
        governor="Ceraxia",
        understanding=understanding,
        work_plan=[
            {
                "step_id": "skitarii",
                "worker": "SkitariiWarband",
                "goal": "Own detailed planning, implementation, verification, and internal repair.",
                "depends_on": [],
                "expected_artifacts": [],
            },
        ],
        quality_gates=normalized["quality_gates"],
        expected_deliverables=["verified Skitarii result and controlled patch package"],
    )
    return _validate_native_governor_plan(payload, normalized)


def _native_status(run_dir: Path, contract: dict[str, Any]) -> dict[str, Any]:
    resolved = run_dir.resolve()
    return {
        "ok": True,
        "version": NATIVE_CONTRACT_VERSION,
        "run_kind": NATIVE_RUN_KIND,
        "task_id": contract["task_id"],
        "mission_id": contract["mission_id"],
        "governor": "Ceraxia",
        "step_count": 1,
        "steps": [
            {
                "step_id": "skitarii",
                "worker": "SkitariiWarband",
                "backend": "SkitariiWarband",
                "purpose": "Own detailed planning, implementation, verification, and internal repair.",
                "depends_on": [],
                "input_artifacts": [],
                "expected_artifacts": [],
            },
        ],
        "execution": dict(NATIVE_EXECUTION),
        "run_dir": str(resolved),
        "contract_path": str(resolved / PACKAGE_FILES["contract"]),
        "directive_path": str(resolved / PACKAGE_FILES["leadership_directive"]),
        "governor_plan_path": str(resolved / PACKAGE_FILES["governor_plan"]),
        "receipt_path": str(resolved / PACKAGE_FILES["receipt"]),
    }


def _validate_status(payload: dict[str, Any], contract: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != STATUS_FIELDS:
        raise ValueError("native status has missing or unknown fields")
    if payload.get("ok") is not True or payload.get("version") != NATIVE_CONTRACT_VERSION:
        raise ValueError("native status identity is invalid")
    if payload.get("run_kind") != NATIVE_RUN_KIND:
        raise ValueError(f"run_kind must be {NATIVE_RUN_KIND}")
    for field in ("task_id", "mission_id"):
        if payload.get(field) != contract[field]:
            raise ValueError(f"native status {field} does not match the contract")
    if payload.get("governor") != "Ceraxia" or payload.get("execution") != NATIVE_EXECUTION:
        raise ValueError("native status leadership or execution identity is invalid")
    steps = payload.get("steps")
    if payload.get("step_count") != 1 or not isinstance(steps, list) or len(steps) != 1:
        raise ValueError("native status must expose exactly one UI step")
    if not isinstance(steps[0], dict) or set(steps[0]) != STATUS_STEP_FIELDS:
        raise ValueError("native status Skitarii step has missing or unknown fields")
    expected_step = _native_status(run_dir, contract)["steps"][0]
    if steps[0] != expected_step:
        raise ValueError("native status step is not the exact Skitarii mission boundary")
    expected_paths = _native_status(run_dir, contract)
    for field in ("run_dir", "contract_path", "directive_path", "governor_plan_path", "receipt_path"):
        if payload.get(field) != expected_paths[field]:
            raise ValueError(f"native status {field} does not match the run directory")
    return dict(payload)


def _validate_receipt(payload: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != RECEIPT_FIELDS:
        raise ValueError("native run receipt has missing or unknown fields")
    if payload.get("kind") != "native_code_run_receipt" or payload.get("version") != 1:
        raise ValueError("native run receipt identity is invalid")
    if payload.get("task_id") != contract["task_id"] or payload.get("mission_id") != contract["mission_id"]:
        raise ValueError("native run receipt is not bound to the contract")
    _text(payload.get("created_at"), "receipt.created_at", limit=128)
    request_hash = payload.get("prepare_request_sha256")
    if request_hash != "" and (not isinstance(request_hash, str) or not _SHA256_RE.fullmatch(request_hash)):
        raise ValueError("prepare_request_sha256 must be empty or a lowercase SHA-256")
    for field in ("contract_sha256", "directive_sha256", "governor_plan_sha256"):
        if not isinstance(payload.get(field), str) or not _SHA256_RE.fullmatch(str(payload.get(field))):
            raise ValueError(f"{field} must be a lowercase SHA-256")
    return dict(payload)


def write_native_code_run(
    run_dir: Path,
    contract: dict[str, Any],
    directive: dict[str, Any],
    governor_plan: dict[str, Any],
    prepare_request_sha256: str = "",
) -> dict[str, Any]:
    """Atomically publish a native run package, writing ``status.json`` last."""
    target = Path(run_dir)
    if target.is_symlink():
        raise ValueError("native run directory must not be a symlink")
    target.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        raise ValueError("native run path must be a directory")
    dispatch = target / "dispatch"
    if dispatch.exists() or dispatch.is_symlink():
        raise ValueError("native Skitarii runs must not contain a dispatch directory")
    normalized_contract = validate_native_code_contract(contract)
    normalized_directive = validate_ceraxia_directive(
        directive,
        expected_task_id=normalized_contract["task_id"],
        expected_mission_id=normalized_contract["mission_id"],
        require_delegation=True,
    )
    normalized_plan = _validate_native_governor_plan(governor_plan, normalized_contract)
    if prepare_request_sha256 and not _SHA256_RE.fullmatch(prepare_request_sha256):
        raise ValueError("prepare_request_sha256 must be a lowercase SHA-256")
    receipt = {
        "kind": "native_code_run_receipt",
        "version": 1,
        "task_id": normalized_contract["task_id"],
        "mission_id": normalized_contract["mission_id"],
        "created_at": utc_now(),
        "prepare_request_sha256": prepare_request_sha256,
        "contract_sha256": _sha256(normalized_contract),
        "directive_sha256": _sha256(normalized_directive),
        "governor_plan_sha256": _sha256(normalized_plan),
    }
    status = _native_status(target, normalized_contract)
    for key, payload in (
        ("contract", normalized_contract),
        ("leadership_directive", normalized_directive),
        ("governor_plan", normalized_plan),
        ("receipt", receipt),
        ("status", status),
    ):
        _write_json_atomic(target / PACKAGE_FILES[key], payload)
    errors = validate_native_code_run_package(target)
    if errors:
        raise ValueError(f"native run package failed post-write validation: {errors}")
    return status


def load_native_code_run(run_dir: Path) -> dict[str, Any]:
    target = Path(run_dir)
    loaded: dict[str, Any] = {"run_dir": str(target.resolve())}
    errors: list[str] = []
    for key, filename in PACKAGE_FILES.items():
        try:
            loaded[key] = _read_json_object(target / filename)
        except ValueError as exc:
            loaded[key] = {}
            errors.append(str(exc))
    loaded["errors"] = errors
    loaded["ok"] = not errors
    return loaded


def validate_native_code_run_package(run_dir: Path) -> list[str]:
    target = Path(run_dir)
    if target.is_symlink():
        return ["native run directory must not be a symlink"]
    if not target.is_dir():
        return ["native run directory does not exist"]
    if (target / "dispatch").exists() or (target / "dispatch").is_symlink():
        return ["native Skitarii runs must not contain a dispatch directory"]
    loaded = load_native_code_run(target)
    errors = list(loaded.get("errors") if isinstance(loaded.get("errors"), list) else [])
    if errors:
        return errors
    try:
        contract = validate_native_code_contract(
            loaded["contract"],
            expected_task_id=target.name,
        )
    except ValueError as exc:
        return [str(exc)]
    try:
        directive = validate_ceraxia_directive(
            loaded["leadership_directive"],
            expected_task_id=contract["task_id"],
            expected_mission_id=contract["mission_id"],
            require_delegation=True,
        )
    except ValueError as exc:
        errors.append(str(exc))
        directive = {}
    try:
        plan = _validate_native_governor_plan(loaded["governor_plan"], contract)
    except ValueError as exc:
        errors.append(str(exc))
        plan = {}
    try:
        _validate_status(loaded["status"], contract, target)
    except ValueError as exc:
        errors.append(str(exc))
    try:
        receipt = _validate_receipt(loaded["receipt"], contract)
    except ValueError as exc:
        errors.append(str(exc))
        receipt = {}
    if receipt:
        expected_hashes = {
            "contract_sha256": _sha256(contract),
            "directive_sha256": _sha256(directive) if directive else "",
            "governor_plan_sha256": _sha256(plan) if plan else "",
        }
        for field, expected in expected_hashes.items():
            if receipt.get(field) != expected:
                errors.append(f"native run receipt {field} does not match package content")
    leftovers = sorted(path.name for path in target.glob(".*.tmp"))
    if leftovers:
        errors.append(f"native run package has unfinished atomic writes: {leftovers}")
    return errors


def is_native_code_run(run_dir: Path) -> bool:
    try:
        payload = _read_json_object(Path(run_dir) / PACKAGE_FILES["contract"])
    except ValueError:
        return False
    return (
        payload.get("version") == NATIVE_CONTRACT_VERSION
        and payload.get("kind") == "code"
        and payload.get("assigned_governor") == "Ceraxia"
        and payload.get("execution") == NATIVE_EXECUTION
        and "worker_plan" not in payload
    )


__all__ = [
    "NATIVE_CONTRACT_VERSION",
    "NATIVE_EXECUTION",
    "NATIVE_RUN_KIND",
    "build_native_code_contract",
    "is_native_code_run",
    "load_native_code_run",
    "native_governor_plan",
    "validate_native_code_contract",
    "validate_native_code_run_package",
    "write_native_code_run",
]
