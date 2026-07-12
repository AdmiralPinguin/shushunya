"""Native run-package contract for Iskandar's ResearchWarband delegation.

The package contains one leadership decision and one opaque warband mission.
It intentionally contains no search queries, URLs, selected sources,
subquestions, hypotheses, claims, citations, artifact paths, or detailed work
plan.  Those decisions belong to ResearchWarband behind the native boundary.
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
from EyeOfTerror.common_protocol.iskandar_directive import (
    validate_directive_for_commander,
)
from EyeOfTerror.common_protocol.protocol import utc_now


NATIVE_RESEARCH_CONTRACT_VERSION = 1
NATIVE_RESEARCH_RUN_KIND = "native_research_warband"
NATIVE_RESEARCH_EXECUTION = {
    "kind": "research_warband_mission",
    "step_id": "research_warband",
    "backend": "ResearchWarband",
}
NATIVE_RESEARCH_CONTRACT_FIELDS = {
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
NATIVE_RESEARCH_EXECUTION_FIELDS = {"kind", "step_id", "backend"}
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
    "commander_order_path",
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
    "commander_order_sha256",
    "directive_sha256",
    "governor_plan_sha256",
}
PACKAGE_FILES = {
    "contract": "contract.json",
    "commander_order": "commander_order.json",
    "leadership_directive": "iskandar_directive.json",
    "governor_plan": "governor_plan.json",
    "status": "status.json",
    "receipt": "native_run_receipt.json",
}
_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_MISSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMANDER_ORDER_FIELDS = {
    "type",
    "protocol_version",
    "mission_id",
    "created_at",
    "from",
    "to",
    "supporting_governors",
    "user_request",
    "commander_intent",
    "primary_goal",
    "success_conditions",
    "constraints",
    "escalate_to_user_if",
    "reporting_policy",
}
REPORTING_POLICY_FIELDS = {
    "progress_events_required",
    "final_report_required",
    "revision_is_internal",
}
MAX_COMMANDER_ORDER_BYTES = 131_072

_DELEGATION_PURPOSE = (
    "Own detailed planning, search, reading, evidence construction, analysis, "
    "writing, verification, and internal repair."
)


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


def _exact_text(value: Any, field: str, *, limit: int = 100_000) -> str:
    result = _text(value, field, limit=limit)
    if result != value:
        raise ValueError(f"{field} must not contain leading or trailing whitespace")
    return result


def _exact_strings(
    value: Any,
    field: str,
    *,
    required: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    result = [
        _exact_text(item, f"{field}[{index}]", limit=4_000)
        for index, item in enumerate(value)
    ]
    if required and not result:
        raise ValueError(f"{field} must not be empty")
    if len(result) > 32:
        raise ValueError(f"{field} has more than 32 items")
    if len(set(result)) != len(result):
        raise ValueError(f"{field} contains duplicate items")
    return result


def validate_native_research_commander_order(
    payload: dict[str, Any],
    *,
    expected_mission_id: str = "",
) -> dict[str, Any]:
    """Validate a bounded, exact Warmaster -> Iskandar commander order."""
    if not isinstance(payload, dict):
        raise ValueError("commander_order must be an object")
    unknown = sorted(set(payload) - COMMANDER_ORDER_FIELDS)
    missing = sorted(COMMANDER_ORDER_FIELDS - set(payload))
    if unknown:
        raise ValueError(f"commander_order has unknown fields: {unknown}")
    if missing:
        raise ValueError(f"commander_order is missing fields: {missing}")
    validate_protocol_payload(payload, expected_type="commander_order")
    mission_id = _mission_id(payload.get("mission_id"), "commander_order.mission_id")
    if expected_mission_id and mission_id != expected_mission_id:
        raise ValueError("commander_order mission_id does not match the native research contract")
    if payload.get("from") != "Warmaster" or payload.get("to") != "IskandarKhayon":
        raise ValueError("commander_order authority must be Warmaster -> IskandarKhayon")
    reporting_policy = payload.get("reporting_policy")
    if not isinstance(reporting_policy, dict) or set(reporting_policy) != REPORTING_POLICY_FIELDS:
        raise ValueError("commander_order.reporting_policy has missing or unknown fields")
    if any(reporting_policy.get(field) is not True for field in REPORTING_POLICY_FIELDS):
        raise ValueError("commander_order.reporting_policy flags must all be true")
    normalized = {
        "type": "commander_order",
        "protocol_version": 1,
        "mission_id": mission_id,
        "created_at": _exact_text(
            payload.get("created_at"), "commander_order.created_at", limit=128,
        ),
        "from": "Warmaster",
        "to": "IskandarKhayon",
        "supporting_governors": _exact_strings(
            payload.get("supporting_governors"), "commander_order.supporting_governors",
        ),
        "user_request": _exact_text(
            payload.get("user_request"), "commander_order.user_request", limit=100_000,
        ),
        "commander_intent": _exact_text(
            payload.get("commander_intent"), "commander_order.commander_intent", limit=10_000,
        ),
        "primary_goal": _exact_text(
            payload.get("primary_goal"), "commander_order.primary_goal", limit=10_000,
        ),
        "success_conditions": _exact_strings(
            payload.get("success_conditions"),
            "commander_order.success_conditions",
            required=True,
        ),
        "constraints": _exact_strings(
            payload.get("constraints"), "commander_order.constraints",
        ),
        "escalate_to_user_if": _exact_strings(
            payload.get("escalate_to_user_if"), "commander_order.escalate_to_user_if",
        ),
        "reporting_policy": {
            "progress_events_required": True,
            "final_report_required": True,
            "revision_is_internal": True,
        },
    }
    if len(_canonical_bytes(normalized)) > MAX_COMMANDER_ORDER_BYTES:
        raise ValueError(
            f"commander_order exceeds {MAX_COMMANDER_ORDER_BYTES} canonical bytes",
        )
    return normalized


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
    slug = "-".join(words)[:80] or "research"
    digest = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:16]
    return f"iskandar-{slug}-{digest}-research-task"


def build_native_research_contract(
    goal: str,
    task_id: str | None,
    mission_id: str = "",
) -> dict[str, Any]:
    """Build the exact contract for one Iskandar-to-ResearchWarband mission."""
    clean_goal = _text(goal, "goal")
    clean_task_id = _task_id(task_id or _generated_task_id(clean_goal))
    clean_mission_id = _mission_id(mission_id or f"mission-{clean_task_id}")
    return validate_native_research_contract(
        {
            "version": NATIVE_RESEARCH_CONTRACT_VERSION,
            "task_id": clean_task_id,
            "mission_id": clean_mission_id,
            "kind": "research",
            "goal": clean_goal,
            "assigned_governor": "IskandarKhayon",
            "non_goals": [
                "Do not pre-plan search queries, URLs, hypotheses, claims, or artifact paths.",
                "Do not bypass Iskandar's validated research directive.",
                "Do not claim support from a URL without archived evidence and provenance.",
                "Do not claim completion while a major claim is unsupported or contested silently.",
            ],
            "completion_criteria": [
                "ResearchWarband returns an accepted result or an honest blocked outcome.",
                "Every major factual claim is linked to mechanically validated source evidence.",
                "Semantic support, conflicts, uncertainty, and uncovered question branches are explicit.",
                "Final artifacts pass bounded independent result validation.",
            ],
            "quality_gates": [
                "iskandar_research_directive_valid",
                "research_warband_provenance_valid",
                "unsupported_major_claim_rate_zero",
                "semantic_entailment_review_complete",
                "question_coverage_or_explicit_gap",
            ],
            "execution": dict(NATIVE_RESEARCH_EXECUTION),
        },
    )


def validate_native_research_contract(
    payload: dict[str, Any],
    expected_task_id: str = "",
    expected_mission_id: str = "",
) -> dict[str, Any]:
    """Validate and normalize an exact native research contract."""
    if not isinstance(payload, dict):
        raise ValueError("native research contract must be an object")
    unknown = sorted(set(payload) - NATIVE_RESEARCH_CONTRACT_FIELDS)
    missing = sorted(NATIVE_RESEARCH_CONTRACT_FIELDS - set(payload))
    if unknown:
        raise ValueError(f"native research contract has unknown fields: {unknown}")
    if missing:
        raise ValueError(f"native research contract is missing fields: {missing}")
    if payload.get("version") != NATIVE_RESEARCH_CONTRACT_VERSION:
        raise ValueError(f"version must be {NATIVE_RESEARCH_CONTRACT_VERSION}")
    task_id = _task_id(payload.get("task_id"))
    mission_id = _mission_id(payload.get("mission_id"))
    if expected_task_id and task_id != expected_task_id:
        raise ValueError("native research contract task_id does not match the run")
    if expected_mission_id and mission_id != expected_mission_id:
        raise ValueError("native research contract mission_id does not match the mission")
    if payload.get("kind") != "research":
        raise ValueError("kind must be research")
    if payload.get("assigned_governor") != "IskandarKhayon":
        raise ValueError("assigned_governor must be IskandarKhayon")
    execution = payload.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("execution must be an object")
    if (
        set(execution) != NATIVE_RESEARCH_EXECUTION_FIELDS
        or execution != NATIVE_RESEARCH_EXECUTION
    ):
        raise ValueError(f"execution must be exactly {NATIVE_RESEARCH_EXECUTION}")
    return {
        "version": NATIVE_RESEARCH_CONTRACT_VERSION,
        "task_id": task_id,
        "mission_id": mission_id,
        "kind": "research",
        "goal": _text(payload.get("goal"), "goal"),
        "assigned_governor": "IskandarKhayon",
        "non_goals": _strings(payload.get("non_goals"), "non_goals"),
        "completion_criteria": _strings(
            payload.get("completion_criteria"), "completion_criteria", required=True,
        ),
        "quality_gates": _strings(
            payload.get("quality_gates"), "quality_gates", required=True,
        ),
        "execution": dict(NATIVE_RESEARCH_EXECUTION),
    }


def native_research_prepare_request_sha256(
    contract: dict[str, Any],
    commander_order: dict[str, Any],
) -> str:
    """Hash the exact prepare identity, including the canonical commander order."""
    normalized_contract = validate_native_research_contract(contract)
    normalized_order = validate_native_research_commander_order(
        commander_order,
        expected_mission_id=normalized_contract["mission_id"],
    )
    return _sha256(
        {
            "task": normalized_contract["goal"],
            "task_id": normalized_contract["task_id"],
            "mission_id": normalized_contract["mission_id"],
            "commander_order": normalized_order,
        },
    )


def _expected_delegation() -> dict[str, Any]:
    return {
        "step_id": "research_warband",
        "worker": "ResearchWarband",
        "goal": _DELEGATION_PURPOSE,
        "depends_on": [],
        "expected_artifacts": [],
    }


def _validate_native_research_governor_plan(
    payload: dict[str, Any],
    contract: dict[str, Any],
    commander_order: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("native research governor_plan must be an object")
    unknown = sorted(set(payload) - GOVERNOR_PLAN_FIELDS)
    missing = sorted(GOVERNOR_PLAN_FIELDS - set(payload))
    if unknown:
        raise ValueError(f"native research governor_plan has unknown fields: {unknown}")
    if missing:
        raise ValueError(f"native research governor_plan is missing fields: {missing}")
    validate_protocol_payload(payload, expected_type="governor_plan")
    if payload.get("mission_id") != contract["mission_id"]:
        raise ValueError("governor_plan mission_id does not match the native research contract")
    if payload.get("governor") != "IskandarKhayon":
        raise ValueError("governor_plan governor must be IskandarKhayon")
    if commander_order:
        normalized_order = validate_native_research_commander_order(
            commander_order,
            expected_mission_id=contract["mission_id"],
        )
        expected_understanding = str(
            normalized_order.get("primary_goal")
            or normalized_order.get("commander_intent")
            or contract["goal"]
        )
        if payload.get("understanding") != expected_understanding:
            raise ValueError("governor_plan understanding does not match commander_order")
    work_plan = payload.get("work_plan")
    if not isinstance(work_plan, list) or len(work_plan) != 1 or not isinstance(work_plan[0], dict):
        raise ValueError(
            "native research governor_plan must contain exactly one ResearchWarband delegation",
        )
    delegation = work_plan[0]
    if set(delegation) != DELEGATION_FIELDS:
        raise ValueError("native research governor_plan delegation has non-leadership fields")
    if delegation != _expected_delegation():
        raise ValueError(
            "native research governor_plan delegation must be the exact ResearchWarband boundary",
        )
    quality_gates = _strings(
        payload.get("quality_gates"), "governor_plan.quality_gates", required=True,
    )
    if quality_gates != contract["quality_gates"]:
        raise ValueError("governor_plan quality_gates do not match the native research contract")
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


def native_research_governor_plan(
    contract: dict[str, Any],
    commander_order: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a protocol plan containing one leadership delegation only."""
    normalized = validate_native_research_contract(contract)
    command = commander_order if isinstance(commander_order, dict) else {}
    if command:
        command = validate_native_research_commander_order(
            command,
            expected_mission_id=normalized["mission_id"],
        )
    understanding = str(
        command.get("primary_goal")
        or command.get("commander_intent")
        or normalized["goal"]
    ).strip()
    payload = governor_plan(
        normalized["mission_id"],
        governor="IskandarKhayon",
        understanding=understanding,
        work_plan=[_expected_delegation()],
        quality_gates=normalized["quality_gates"],
        expected_deliverables=[
            "verified research result, evidence ledger, source manifest, and final report",
        ],
    )
    return _validate_native_research_governor_plan(payload, normalized, command or None)


def _native_status(run_dir: Path, contract: dict[str, Any]) -> dict[str, Any]:
    resolved = run_dir.resolve()
    return {
        "ok": True,
        "version": NATIVE_RESEARCH_CONTRACT_VERSION,
        "run_kind": NATIVE_RESEARCH_RUN_KIND,
        "task_id": contract["task_id"],
        "mission_id": contract["mission_id"],
        "governor": "IskandarKhayon",
        "step_count": 1,
        "steps": [
            {
                "step_id": "research_warband",
                "worker": "ResearchWarband",
                "backend": "ResearchWarband",
                "purpose": _DELEGATION_PURPOSE,
                "depends_on": [],
                "input_artifacts": [],
                "expected_artifacts": [],
            },
        ],
        "execution": dict(NATIVE_RESEARCH_EXECUTION),
        "run_dir": str(resolved),
        "contract_path": str(resolved / PACKAGE_FILES["contract"]),
        "commander_order_path": str(resolved / PACKAGE_FILES["commander_order"]),
        "directive_path": str(resolved / PACKAGE_FILES["leadership_directive"]),
        "governor_plan_path": str(resolved / PACKAGE_FILES["governor_plan"]),
        "receipt_path": str(resolved / PACKAGE_FILES["receipt"]),
    }


def _validate_status(
    payload: dict[str, Any],
    contract: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != STATUS_FIELDS:
        raise ValueError("native research status has missing or unknown fields")
    if payload.get("ok") is not True or payload.get("version") != NATIVE_RESEARCH_CONTRACT_VERSION:
        raise ValueError("native research status identity is invalid")
    if payload.get("run_kind") != NATIVE_RESEARCH_RUN_KIND:
        raise ValueError(f"run_kind must be {NATIVE_RESEARCH_RUN_KIND}")
    for field in ("task_id", "mission_id"):
        if payload.get(field) != contract[field]:
            raise ValueError(f"native research status {field} does not match the contract")
    if (
        payload.get("governor") != "IskandarKhayon"
        or payload.get("execution") != NATIVE_RESEARCH_EXECUTION
    ):
        raise ValueError("native research status leadership or execution identity is invalid")
    steps = payload.get("steps")
    if payload.get("step_count") != 1 or not isinstance(steps, list) or len(steps) != 1:
        raise ValueError("native research status must expose exactly one UI step")
    if not isinstance(steps[0], dict) or set(steps[0]) != STATUS_STEP_FIELDS:
        raise ValueError("native research status step has missing or unknown fields")
    expected = _native_status(run_dir, contract)
    if steps[0] != expected["steps"][0]:
        raise ValueError("native research status step is not the exact ResearchWarband boundary")
    for field in (
        "run_dir",
        "contract_path",
        "commander_order_path",
        "directive_path",
        "governor_plan_path",
        "receipt_path",
    ):
        if payload.get(field) != expected[field]:
            raise ValueError(f"native research status {field} does not match the run directory")
    return dict(payload)


def _validate_receipt(
    payload: dict[str, Any],
    contract: dict[str, Any],
    commander_order: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != RECEIPT_FIELDS:
        raise ValueError("native research run receipt has missing or unknown fields")
    if payload.get("kind") != "native_research_run_receipt" or payload.get("version") != 1:
        raise ValueError("native research run receipt identity is invalid")
    if payload.get("task_id") != contract["task_id"] or payload.get("mission_id") != contract["mission_id"]:
        raise ValueError("native research run receipt is not bound to the contract")
    _text(payload.get("created_at"), "receipt.created_at", limit=128)
    request_hash = payload.get("prepare_request_sha256")
    if not isinstance(request_hash, str) or not _SHA256_RE.fullmatch(request_hash):
        raise ValueError("prepare_request_sha256 must be a lowercase SHA-256")
    expected_request_hash = native_research_prepare_request_sha256(contract, commander_order)
    if request_hash != expected_request_hash:
        raise ValueError("prepare_request_sha256 does not match commander-bound prepare identity")
    for field in (
        "contract_sha256",
        "commander_order_sha256",
        "directive_sha256",
        "governor_plan_sha256",
    ):
        if not isinstance(payload.get(field), str) or not _SHA256_RE.fullmatch(str(payload.get(field))):
            raise ValueError(f"{field} must be a lowercase SHA-256")
    if payload.get("commander_order_sha256") != _sha256(commander_order):
        raise ValueError("commander_order_sha256 does not match commander_order.json")
    return dict(payload)


def write_native_research_run(
    run_dir: Path,
    contract: dict[str, Any],
    directive: dict[str, Any],
    governor_plan_payload: dict[str, Any],
    commander_order: dict[str, Any],
    prepare_request_sha256: str = "",
) -> dict[str, Any]:
    """Atomically publish a native package, writing ``status.json`` last."""
    target = Path(run_dir)
    if target.is_symlink():
        raise ValueError("native research run directory must not be a symlink")
    target.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        raise ValueError("native research run path must be a directory")
    dispatch = target / "dispatch"
    if dispatch.exists() or dispatch.is_symlink():
        raise ValueError("native ResearchWarband runs must not contain a dispatch directory")
    normalized_contract = validate_native_research_contract(contract)
    normalized_order = validate_native_research_commander_order(
        commander_order,
        expected_mission_id=normalized_contract["mission_id"],
    )
    normalized_directive = validate_directive_for_commander(
        directive,
        normalized_order,
        expected_task_id=normalized_contract["task_id"],
        expected_mission_id=normalized_contract["mission_id"],
        require_delegation=True,
    )
    normalized_plan = _validate_native_research_governor_plan(
        governor_plan_payload, normalized_contract, normalized_order,
    )
    expected_prepare_hash = native_research_prepare_request_sha256(
        normalized_contract, normalized_order,
    )
    if prepare_request_sha256 and prepare_request_sha256 != expected_prepare_hash:
        raise ValueError(
            "prepare_request_sha256 does not match commander-bound prepare identity",
        )
    prepare_request_sha256 = expected_prepare_hash
    receipt = {
        "kind": "native_research_run_receipt",
        "version": 1,
        "task_id": normalized_contract["task_id"],
        "mission_id": normalized_contract["mission_id"],
        "created_at": utc_now(),
        "prepare_request_sha256": prepare_request_sha256,
        "contract_sha256": _sha256(normalized_contract),
        "commander_order_sha256": _sha256(normalized_order),
        "directive_sha256": _sha256(normalized_directive),
        "governor_plan_sha256": _sha256(normalized_plan),
    }
    status = _native_status(target, normalized_contract)
    for key, item in (
        ("contract", normalized_contract),
        ("commander_order", normalized_order),
        ("leadership_directive", normalized_directive),
        ("governor_plan", normalized_plan),
        ("receipt", receipt),
        ("status", status),
    ):
        _write_json_atomic(target / PACKAGE_FILES[key], item)
    errors = validate_native_research_run_package(target)
    if errors:
        raise ValueError(f"native research run package failed post-write validation: {errors}")
    return status


def load_native_research_run(run_dir: Path) -> dict[str, Any]:
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


def validate_native_research_run_package(run_dir: Path) -> list[str]:
    target = Path(run_dir)
    if target.is_symlink():
        return ["native research run directory must not be a symlink"]
    if not target.is_dir():
        return ["native research run directory does not exist"]
    if (target / "dispatch").exists() or (target / "dispatch").is_symlink():
        return ["native ResearchWarband runs must not contain a dispatch directory"]
    loaded = load_native_research_run(target)
    errors = list(loaded.get("errors") if isinstance(loaded.get("errors"), list) else [])
    if errors:
        return errors
    try:
        contract = validate_native_research_contract(
            loaded["contract"], expected_task_id=target.name,
        )
    except ValueError as exc:
        return [str(exc)]
    try:
        commander_order = validate_native_research_commander_order(
            loaded["commander_order"],
            expected_mission_id=contract["mission_id"],
        )
    except ValueError as exc:
        errors.append(str(exc))
        commander_order = {}
    try:
        directive = validate_directive_for_commander(
            loaded["leadership_directive"],
            commander_order,
            expected_task_id=contract["task_id"],
            expected_mission_id=contract["mission_id"],
            require_delegation=True,
        )
    except ValueError as exc:
        errors.append(str(exc))
        directive = {}
    try:
        plan = _validate_native_research_governor_plan(
            loaded["governor_plan"], contract, commander_order or None,
        )
    except ValueError as exc:
        errors.append(str(exc))
        plan = {}
    try:
        _validate_status(loaded["status"], contract, target)
    except ValueError as exc:
        errors.append(str(exc))
    try:
        receipt = _validate_receipt(loaded["receipt"], contract, commander_order)
    except ValueError as exc:
        errors.append(str(exc))
        receipt = {}
    if receipt:
        expected_hashes = {
            "contract_sha256": _sha256(contract),
            "commander_order_sha256": _sha256(commander_order) if commander_order else "",
            "directive_sha256": _sha256(directive) if directive else "",
            "governor_plan_sha256": _sha256(plan) if plan else "",
        }
        for field, expected in expected_hashes.items():
            if receipt.get(field) != expected:
                errors.append(f"native research run receipt {field} does not match package content")
    leftovers = sorted(path.name for path in target.glob(".*.tmp"))
    if leftovers:
        errors.append(f"native research run package has unfinished atomic writes: {leftovers}")
    return errors


def is_native_research_run(run_dir: Path) -> bool:
    try:
        payload = _read_json_object(Path(run_dir) / PACKAGE_FILES["contract"])
    except ValueError:
        return False
    return (
        payload.get("version") == NATIVE_RESEARCH_CONTRACT_VERSION
        and payload.get("kind") == "research"
        and payload.get("assigned_governor") == "IskandarKhayon"
        and payload.get("execution") == NATIVE_RESEARCH_EXECUTION
        and "worker_plan" not in payload
    )


__all__ = [
    "COMMANDER_ORDER_FIELDS",
    "MAX_COMMANDER_ORDER_BYTES",
    "NATIVE_RESEARCH_CONTRACT_VERSION",
    "NATIVE_RESEARCH_EXECUTION",
    "NATIVE_RESEARCH_RUN_KIND",
    "build_native_research_contract",
    "is_native_research_run",
    "load_native_research_run",
    "native_research_governor_plan",
    "native_research_prepare_request_sha256",
    "validate_native_research_commander_order",
    "validate_native_research_contract",
    "validate_native_research_run_package",
    "write_native_research_run",
]
