"""Crash-safe Abaddon bridge for one native Iskandar ResearchWarband mission."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit


DEFAULT_RESEARCH_WARBAND_URL = "http://127.0.0.1:7201"
MAX_RESPONSE_BYTES = 64_000_000
POLL_INTERVAL_SECONDS = 0.5
ERROR_CLEANUP_TIMEOUT_SECONDS = 60.0
TERMINAL_SERVICE_STATUSES = frozenset({"done", "blocked", "failed", "cancelled"})
ACTIVE_SERVICE_STATUSES = frozenset({"queued", "running", "needs_user", "cancelling"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SERVICE_MISSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep bearer credentials on the one exact loopback origin."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_PRIVATE_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirect(),
)


class ResearchWarbandBridgeError(RuntimeError):
    """The native research service boundary could not be proved safe."""


def _validate_service_mission_id(value: Any) -> str:
    """Enforce the public 7201 mission-store identity contract before I/O."""
    if (
        type(value) is not str
        or not _SERVICE_MISSION_ID_RE.fullmatch(value)
        or value in {".", ".."}
    ):
        raise ResearchWarbandBridgeError(
            "ResearchWarband mission_id is outside the port 7201 service contract"
        )
    return value


def _service_url() -> str:
    value = os.environ.get(
        "RESEARCH_WARBAND_URL", DEFAULT_RESEARCH_WARBAND_URL
    ).strip()
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ResearchWarbandBridgeError("ResearchWarband URL is malformed") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 7201
        or parsed.path not in {"", "/"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ResearchWarbandBridgeError(
            "ResearchWarband URL must be exactly http://127.0.0.1:7201"
        )
    return DEFAULT_RESEARCH_WARBAND_URL


def _bearer_token() -> str:
    token = os.environ.get("RESEARCH_WARBAND_BEARER_TOKEN", "")
    if (
        len(token) < 32
        or len(set(token)) < 8
        or token.startswith("REPLACE_")
        or any(char in token for char in "\r\n")
    ):
        raise ResearchWarbandBridgeError(
            "ResearchWarband production bearer token is missing or unsafe"
        )
    return token


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _request_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _strict_json_object(raw: bytes) -> dict[str, Any]:
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
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=invalid_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ResearchWarbandBridgeError(
            "ResearchWarband returned malformed JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ResearchWarbandBridgeError(
            "ResearchWarband returned a non-object JSON response"
        )
    return value


def _validate_json_response(response: Any, target: str) -> None:
    if response.geturl() != target:
        raise ResearchWarbandBridgeError(
            "ResearchWarband response URL does not match the requested loopback URL"
        )
    media_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0]
    if media_type.strip().lower() != "application/json":
        raise ResearchWarbandBridgeError(
            "ResearchWarband response Content-Type must be application/json"
        )
    length = response.headers.get("Content-Length")
    if length:
        try:
            parsed_length = int(length)
        except ValueError as exc:
            raise ResearchWarbandBridgeError(
                "ResearchWarband returned an invalid Content-Length"
            ) from exc
        if parsed_length < 0 or parsed_length > MAX_RESPONSE_BYTES:
            raise ResearchWarbandBridgeError(
                "ResearchWarband response exceeds the byte limit"
            )


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ResearchWarbandBridgeError(f"{label} is missing or not a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchWarbandBridgeError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise ResearchWarbandBridgeError(f"{label} must contain a JSON object")
    return value


def _json_request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
    allowed_statuses: frozenset[int] = frozenset(),
) -> tuple[int, dict[str, Any]]:
    body = _canonical_bytes(payload) if payload is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {_bearer_token()}",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    target = _service_url() + path
    request = urllib.request.Request(
        target,
        data=body,
        headers=headers,
        method=method,
    )
    status = 200
    try:
        with _PRIVATE_OPENER.open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            _validate_json_response(response, target)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        if 300 <= status < 400:
            raise ResearchWarbandBridgeError(
                "ResearchWarband attempted an HTTP redirect"
            ) from exc
        _validate_json_response(exc, target)
        raw = exc.read(MAX_RESPONSE_BYTES + 1)
        if status not in allowed_statuses:
            detail = raw[:8192].decode("utf-8", errors="replace")
            raise ResearchWarbandBridgeError(
                f"ResearchWarband HTTP {status}: {detail}"
            ) from exc
    except urllib.error.URLError as exc:
        raise ResearchWarbandBridgeError(
            f"ResearchWarband request failed: {exc}"
        ) from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ResearchWarbandBridgeError(
            "ResearchWarband response exceeds the byte limit"
        )
    return status, _strict_json_object(raw)


def research_warband_backend_health(timeout_sec: float = 2.0) -> dict[str, Any]:
    try:
        _status, health = _json_request("GET", "/health", timeout=timeout_sec)
        identity = health.get("identity")
        readiness = identity.get("readiness") if isinstance(identity, dict) else None
        if (
            health.get("ok") is not True
            or health.get("service") != "ResearchWarband"
            or not isinstance(identity, dict)
            or identity.get("standalone_test_mode") is not False
            or identity.get("bearer_auth_required") is not True
            or not isinstance(readiness, dict)
            or readiness.get("ready") is not True
        ):
            raise ResearchWarbandBridgeError(
                "port 7201 is not the ready bearer-protected production backend"
            )
        return {
            "ok": True,
            "healthy": True,
            "status": "ready",
            "service": "ResearchWarband",
            "url": _service_url(),
            "identity": identity,
        }
    except (OSError, ValueError, ResearchWarbandBridgeError) as exc:
        return {
            "ok": False,
            "healthy": False,
            "status": "unavailable",
            "service": "ResearchWarband",
            "url": DEFAULT_RESEARCH_WARBAND_URL,
            "error": str(exc),
        }


def load_research_warband_envelope(
    run_dir: Path, task_id: str
) -> dict[str, Any]:
    from EyeOfTerror.common_protocol.iskandar_directive import (
        validate_directive_for_commander,
    )

    from .native_research_run import (
        load_native_research_run,
        validate_native_research_run_package,
    )

    target = Path(run_dir).resolve()
    errors = validate_native_research_run_package(target)
    if errors:
        raise ResearchWarbandBridgeError(
            "native ResearchWarband package is invalid: " + "; ".join(errors)
        )
    loaded = load_native_research_run(target)
    if loaded.get("ok") is not True:
        raise ResearchWarbandBridgeError("native ResearchWarband package did not load")
    contract = loaded.get("contract")
    order = loaded.get("commander_order")
    directive = loaded.get("leadership_directive")
    if not all(isinstance(item, dict) for item in (contract, order, directive)):
        raise ResearchWarbandBridgeError(
            "native ResearchWarband package is incomplete"
        )
    if contract.get("task_id") != task_id:
        raise ResearchWarbandBridgeError(
            "native ResearchWarband task identity changed"
        )
    mission_id = _validate_service_mission_id(contract.get("mission_id"))
    normalized = validate_directive_for_commander(
        directive,
        order,
        expected_task_id=task_id,
        expected_mission_id=mission_id,
        require_delegation=True,
    )
    return {
        "mission_id": mission_id,
        "task_id": task_id,
        "leadership_directive": normalized,
        "commander_order": order,
    }


def _mission_dir(run_dir: Path, mission_id: str) -> Path:
    mission_id = _validate_service_mission_id(mission_id)
    ref = _read_json(run_dir / "mission_ref.json", "mission_ref.json")
    if ref.get("mission_id") != mission_id:
        raise ResearchWarbandBridgeError(
            "mission_ref.json does not match the research mission"
        )
    raw = ref.get("mission_dir")
    if type(raw) is not str or not raw.strip():
        raise ResearchWarbandBridgeError(
            "mission_ref.json does not bind a mission directory"
        )
    candidate = Path(raw)
    root_candidate = Path(
        os.environ.get(
            "WARMMASTER_MISSIONS_ROOT",
            str(Path(__file__).resolve().parents[1] / "missions"),
        )
    )
    try:
        root = root_candidate.resolve(strict=True)
        path = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ResearchWarbandBridgeError(
            f"linked mission directory is unavailable: {exc}"
        ) from exc
    if (
        not candidate.is_absolute()
        or candidate.is_symlink()
        or not path.is_dir()
        or path.parent != root
        or path.name != mission_id
    ):
        raise ResearchWarbandBridgeError(
            "linked mission directory is outside the configured mission authority root"
        )
    mission = _read_json(path / "mission.json", "linked mission.json")
    if mission.get("mission_id") != mission_id:
        raise ResearchWarbandBridgeError(
            "linked mission.json does not match the research mission"
        )
    return path


def _next_action_for_clarification() -> dict[str, Any]:
    return {
        "kind": "provide_clarification",
        "method": "POST",
        "endpoint": "POST /runs/{task_id}/clarification",
        "body": {"answer": ""},
        "reason": "ResearchWarband is waiting for clarification on this mission",
    }


def _warmaster_result(
    run_dir: Path,
    task_id: str,
    mission_id: str,
    *,
    ok: bool,
    status: str,
    summary: str,
    raw_result: dict[str, Any] | None = None,
    question: str = "",
    error: str = "",
) -> dict[str, Any]:
    needs_user = status == "needs_user"
    result: dict[str, Any] = {
        "ok": ok,
        "task_id": task_id,
        "phase": status,
        "status": status,
        "final_step": "research_warband",
        "summary": summary,
        "artifacts": [],
        "artifact_root": str(run_dir.resolve()),
        "needs_user": needs_user,
        "question": question,
        "next_action": _next_action_for_clarification() if needs_user else {},
        "research_warband_mission_id": mission_id,
        "research_result": raw_result or {},
        "via": "research_warband",
    }
    if error:
        result["error"] = error
    return result


def _external_answer(raw_result: dict[str, Any]) -> str:
    external = raw_result.get("external_evaluator_result")
    if isinstance(external, dict):
        value = external.get("final_text")
        if type(value) is str and value.strip():
            return value.strip()
    value = raw_result.get("reason")
    return value.strip() if type(value) is str else ""


def _verification_passed(raw_result: dict[str, Any]) -> bool:
    audit = raw_result.get("pipeline_audit")
    report = audit.get("verification_report") if isinstance(audit, dict) else None
    return bool(
        isinstance(report, dict)
        and report.get("accepted") is True
        and report.get("integrity_ok") is True
        and not report.get("issues")
    )


def _append_protocol_progress(
    mission_dir: Path,
    mission_id: str,
    *,
    phase: str,
    status: str,
    title: str,
    body: str,
) -> None:
    from . import mission_control as mc

    mc.append_progress_event(
        mission_dir / "progress_events.jsonl",
        mc.progress_event(
            mission_id,
            "IskandarKhayon",
            "governor",
            phase,
            status,
            title,
            body[:400],
        ),
    )


def _finalize_protocol(
    run_dir: Path,
    mission_id: str,
    result: dict[str, Any],
) -> None:
    from . import mission_control as mc

    mission_dir = _mission_dir(run_dir, mission_id)
    status = str(result.get("status") or "failed")
    summary = str(result.get("summary") or "ResearchWarband returned no summary")
    if status == "completed":
        report = mc.governor_report(
            mission_id,
            governor="IskandarKhayon",
            status="ready",
            summary=summary,
            deliverables=[],
            quality_review={
                "passed": True,
                "checks": [
                    {"name": "research_warband_acceptance", "ok": True},
                    {
                        "name": "evidence_and_semantic_verification",
                        "ok": _verification_passed(
                            result.get("research_result")
                            if isinstance(result.get("research_result"), dict)
                            else {}
                        ),
                    },
                ],
                "final_manifest_summary": {},
            },
            revision_plan={"required": False, "steps": []},
            user_facing_answer=summary,
        )
        review = mc.acceptance_review(
            mission_id,
            accepted=True,
            reason=(
                "ResearchWarband returned an accepted evidence ledger and the "
                "deterministic verification boundary reported no integrity issues."
            ),
            required_revision={},
            escalate_to_user=False,
        )
        mc.validate_protocol_payload(report, expected_type="governor_report")
        mc.validate_protocol_payload(review, expected_type="acceptance_review")
        mc._write_json(mission_dir / "governor_report.json", report)
        mc._write_json(
            mc._next_numbered_path(
                mission_dir / "governor_reports", "governor_report"
            ),
            report,
        )
        mc._write_json(mission_dir / "acceptance_review.json", review)
        mc._write_json(
            mc._next_numbered_path(
                mission_dir / "acceptance_reviews", "acceptance_review"
            ),
            review,
        )
        final = mc.final_response(mission_id, "completed", summary, artifacts=[])
        mc._write_json(mission_dir / "final_response.json", final)
        mc.record_mission_state(
            mission_dir, "completed", run_status="completed", phase="completed"
        )
        _append_protocol_progress(
            mission_dir,
            mission_id,
            phase="completed",
            status="done",
            title="Искандар передал проверенный результат",
            body=summary,
        )
        return
    public_status = "cancelled" if status == "cancelled" else "blocked"
    final = mc.final_response(mission_id, public_status, summary, artifacts=[])
    final["phase"] = status
    final["needs_user"] = bool(result.get("needs_user"))
    if result.get("question"):
        final["question"] = result["question"]
    if result.get("next_action"):
        final["next_action"] = result["next_action"]
    mc._write_json(mission_dir / "final_response.json", final)
    mc.record_mission_state(
        mission_dir,
        public_status,
        run_status=public_status,
        phase=status,
    )
    _append_protocol_progress(
        mission_dir,
        mission_id,
        phase="cancelled" if status == "cancelled" else "blocked",
        status="cancelled" if status == "cancelled" else "blocked",
        title=(
            "Исследовательская миссия отменена"
            if status == "cancelled"
            else "ResearchWarband остановила миссию"
        ),
        body=summary,
    )


def _update_remote_meta(
    ledger: Any,
    *,
    mission_id: str,
    request_sha256: str,
    status: str,
    snapshot: dict[str, Any] | None = None,
) -> None:
    old = (
        ledger.data.get("research_warband_mission")
        if isinstance(ledger.data.get("research_warband_mission"), dict)
        else {}
    )
    updated = {
        **old,
        "id": mission_id,
        "request_sha256": request_sha256,
        "status": status,
        "service": _service_url(),
    }
    if isinstance(snapshot, dict):
        for field in ("attempt", "inflight", "cleanup_complete", "updated"):
            if field in snapshot:
                updated[field] = snapshot[field]
    ledger.data["research_warband_mission"] = updated
    ledger.save()


def _record_remote_activity(
    run_dir: Path,
    task_id: str,
    mission_id: str,
    service_status: str,
) -> None:
    """Make durable/UI state follow an adopted active 7201 mission exactly once."""
    from .ledger import TaskLedger

    ledger = TaskLedger.load(Path(run_dir) / "task_ledger.json")
    current_status = str(ledger.data.get("status") or "")
    desired_status = "cancelling" if service_status == "cancelling" else "running"
    if current_status in {"created", "assigned", "interrupted"}:
        ledger.set_status(desired_status)
    meta = (
        ledger.data.get("research_warband_mission")
        if isinstance(ledger.data.get("research_warband_mission"), dict)
        else {}
    )
    if meta.get("bridge_activity_announced") is True:
        return
    ledger.data["research_warband_mission"] = {
        **meta,
        "bridge_activity_announced": True,
    }
    ledger.record_event(
        "research_warband_execution_started",
        {"mission_id": mission_id, "service_status": service_status},
    )
    try:
        from . import mission_control as mc

        mission_dir = _mission_dir(Path(run_dir), mission_id)
        mc.record_mission_state(
            mission_dir,
            "executing",
            run_status=desired_status,
            phase="executing",
            active=True,
        )
        _append_protocol_progress(
            mission_dir,
            mission_id,
            phase="executing",
            status="running",
            title="ResearchWarband начала исследование",
            body="Искандар передал миссию исследовательской варбанде.",
        )
    except (OSError, ValueError, ResearchWarbandBridgeError):
        # The run ledger remains the authoritative recoverable execution state.
        pass


def _validate_snapshot_identity(
    snapshot: dict[str, Any], mission_id: str, request_sha256: str
) -> str:
    if snapshot.get("id") != mission_id:
        raise ResearchWarbandBridgeError(
            "ResearchWarband mission identity changed"
        )
    if snapshot.get("request_sha256") != request_sha256:
        raise ResearchWarbandBridgeError(
            "ResearchWarband mission request hash does not match the native envelope"
        )
    status = snapshot.get("status")
    if type(status) is not str or status not in (
        ACTIVE_SERVICE_STATUSES | TERMINAL_SERVICE_STATUSES
    ):
        raise ResearchWarbandBridgeError(
            f"ResearchWarband returned unknown status {status!r}"
        )
    return status


def inspect_research_warband_mission(
    mission_id: str,
    request_sha256: str,
    timeout_sec: float = 2.0,
) -> dict[str, Any]:
    """Read one exact 7201 mission through the authenticated bridge boundary.

    This is intentionally the only runtime-inspection entry point exposed to
    Warmaster views.  It inherits the bridge's exact loopback-origin,
    no-redirect, bearer-token, response-size, strict-JSON, and identity checks;
    callers must not manufacture an unauthenticated service URL themselves.
    """
    validated_id = _validate_service_mission_id(mission_id)
    if type(request_sha256) is not str or not _SHA256_RE.fullmatch(request_sha256):
        raise ResearchWarbandBridgeError(
            "ResearchWarband runtime inspection requires the bound request hash"
        )
    _status, snapshot = _json_request(
        "GET",
        f"/missions/{quote(validated_id, safe='')}",
        timeout=max(0.1, float(timeout_sec)),
    )
    _validate_snapshot_identity(snapshot, validated_id, request_sha256)
    return snapshot


def _record_cleanup_outcome(ledger: Any, outcome: dict[str, Any]) -> None:
    old = (
        ledger.data.get("research_warband_mission")
        if isinstance(ledger.data.get("research_warband_mission"), dict)
        else {}
    )
    updated = {
        **old,
        "bridge_cleanup_required": bool(outcome.get("required")),
        "bridge_cleanup_requested": bool(outcome.get("requested")),
        "bridge_cleanup_proven": bool(outcome.get("proven")),
        "bridge_cleanup_pending": (
            bool(outcome.get("required")) and outcome.get("proven") is not True
        ),
        "bridge_cleanup_status": str(outcome.get("status") or "unknown"),
    }
    error = str(outcome.get("error") or "").strip()
    if error:
        updated["bridge_cleanup_error"] = error[:1000]
    else:
        updated.pop("bridge_cleanup_error", None)
    ledger.data["research_warband_mission"] = updated
    ledger.save()


def _cancel_remote_and_wait_for_cleanup(
    run_dir: Path,
    mission_id: str,
    request_sha256: str,
    *,
    identity_previously_bound: bool,
    timeout_sec: float,
) -> dict[str, Any]:
    """Cancel only the exact bound service mission and prove terminal cleanup.

    This routine deliberately adopts by request hash before cancellation.  An
    ambiguous failed POST can therefore be cleaned up, while a foreign mission
    reusing the same id is never touched.
    """
    from .ledger import TaskLedger

    mission_id = _validate_service_mission_id(mission_id)
    if not _SHA256_RE.fullmatch(request_sha256):
        return {
            "required": True,
            "requested": False,
            "proven": False,
            "status": "identity_invalid",
            "error": "persisted ResearchWarband request hash is invalid",
        }
    encoded = quote(mission_id, safe="")
    deadline = time.monotonic() + max(0.01, float(timeout_sec))
    requested = False
    last_status = "unknown"
    last_snapshot: dict[str, Any] = {}
    last_error = ""
    while True:
        try:
            get_status, snapshot = _json_request(
                "GET",
                f"/missions/{encoded}",
                timeout=min(10.0, max(0.1, float(timeout_sec))),
                allowed_statuses=frozenset({404}),
            )
            if get_status == 404:
                if identity_previously_bound:
                    last_error = (
                        "previously bound ResearchWarband mission disappeared; "
                        "process cleanup cannot be proven"
                    )
                    last_status = "missing_after_bind"
                else:
                    return {
                        "required": True,
                        "requested": requested,
                        "proven": True,
                        "status": "absent",
                        "inflight": False,
                        "cleanup_complete": True,
                    }
            else:
                service_status = _validate_snapshot_identity(
                    snapshot, mission_id, request_sha256
                )
                identity_previously_bound = True
                last_status = service_status
                last_snapshot = snapshot
                try:
                    ledger = TaskLedger.load(Path(run_dir) / "task_ledger.json")
                    _update_remote_meta(
                        ledger,
                        mission_id=mission_id,
                        request_sha256=request_sha256,
                        status=service_status,
                        snapshot=snapshot,
                    )
                except Exception as ledger_error:  # noqa: BLE001
                    last_error = f"cleanup metadata persistence failed: {ledger_error}"
                if (
                    service_status in TERMINAL_SERVICE_STATUSES
                    and snapshot.get("inflight") is False
                    and snapshot.get("cleanup_complete") is True
                ):
                    return {
                        "required": True,
                        "requested": requested,
                        "proven": True,
                        "status": service_status,
                        "inflight": False,
                        "cleanup_complete": True,
                    }
                if not requested:
                    cancel_status, response = _json_request(
                        "POST",
                        f"/missions/{encoded}/cancel",
                        payload={},
                        timeout=min(10.0, max(0.1, float(timeout_sec))),
                        allowed_statuses=frozenset({404, 409}),
                    )
                    if cancel_status == 404:
                        last_error = (
                            "bound ResearchWarband mission disappeared during cancellation"
                        )
                        last_status = "missing_after_bind"
                    elif cancel_status == 409:
                        # A terminal/cancelling race is safe only after the next
                        # authoritative snapshot proves process cleanup.
                        requested = True
                    else:
                        if set(response) != {"ok", "status"}:
                            raise ResearchWarbandBridgeError(
                                "ResearchWarband cancellation response shape changed"
                            )
                        if response.get("ok") is not True:
                            raise ResearchWarbandBridgeError(
                                "ResearchWarband did not acknowledge cancellation"
                            )
                        requested = True
        except Exception as cleanup_error:  # noqa: BLE001
            last_error = str(cleanup_error)
        if time.monotonic() >= deadline:
            return {
                "required": True,
                "requested": requested,
                "proven": False,
                "status": last_status,
                "inflight": last_snapshot.get("inflight"),
                "cleanup_complete": last_snapshot.get("cleanup_complete"),
                "error": last_error
                or "ResearchWarband cleanup was not proven before timeout",
            }
        time.sleep(POLL_INTERVAL_SECONDS)


def _terminal_result(
    run_dir: Path,
    task_id: str,
    mission_id: str,
    service_status: str,
    raw_result: dict[str, Any],
) -> dict[str, Any]:
    outcome = str(raw_result.get("outcome") or "")
    if service_status == "done":
        if outcome not in {"accepted", "accepted_with_uncertainty"}:
            raise ResearchWarbandBridgeError(
                "ResearchWarband done status has a non-accepted pipeline outcome"
            )
        if not _verification_passed(raw_result):
            raise ResearchWarbandBridgeError(
                "ResearchWarband accepted without a clean verification report"
            )
        answer = _external_answer(raw_result)
        if not answer:
            raise ResearchWarbandBridgeError(
                "ResearchWarband accepted without a user-facing answer"
            )
        return _warmaster_result(
            run_dir,
            task_id,
            mission_id,
            ok=True,
            status="completed",
            summary=answer,
            raw_result=raw_result,
        )
    if service_status == "cancelled":
        return _warmaster_result(
            run_dir,
            task_id,
            mission_id,
            ok=False,
            status="cancelled",
            summary="ResearchWarband mission was cancelled.",
            raw_result=raw_result,
        )
    reason = str(raw_result.get("reason") or "").strip()
    if not reason:
        reason = f"ResearchWarband mission ended with status {service_status}."
    return _warmaster_result(
        run_dir,
        task_id,
        mission_id,
        ok=False,
        status="blocked" if service_status == "blocked" else "failed",
        summary=reason,
        raw_result=raw_result,
        error=reason if service_status == "failed" else "",
    )


def _record_waiting(
    run_dir: Path,
    task_id: str,
    mission_id: str,
    question: str,
) -> dict[str, Any]:
    from .ledger import TaskLedger

    waiting = _warmaster_result(
        run_dir,
        task_id,
        mission_id,
        ok=False,
        status="needs_user",
        summary=question,
        question=question,
    )
    ledger = TaskLedger.load(run_dir / "task_ledger.json")
    current = ledger.data.get("result")
    if not isinstance(current, dict) or (
        current.get("status"), current.get("question")
    ) != ("needs_user", question):
        ledger.set_result(waiting)
        ledger.record_event(
            "research_warband_needs_user",
            {"mission_id": mission_id, "question": question[:500]},
        )
        try:
            mission_dir = _mission_dir(run_dir, mission_id)
            _append_protocol_progress(
                mission_dir,
                mission_id,
                phase="blocked",
                status="blocked",
                title="Искандару требуется уточнение",
                body=question,
            )
        except (OSError, ValueError, ResearchWarbandBridgeError):
            pass
    return waiting


def run_via_research_warband(
    run_dir: Path,
    task_id: str,
    timeout_sec: int = 604_800,
) -> dict[str, Any]:
    """Submit, adopt and poll one durable production research mission."""
    from .ledger import TaskLedger

    target = Path(run_dir).resolve()
    ledger = TaskLedger.load(target / "task_ledger.json")
    mission_id = ""
    request_hash = ""
    remote_identity_bound = False
    remote_operation_ambiguous = False
    try:
        envelope = load_research_warband_envelope(target, task_id)
        mission_id = _validate_service_mission_id(envelope.get("mission_id"))
        request_hash = _request_sha256(envelope)
        old_meta = (
            ledger.data.get("research_warband_mission")
            if isinstance(ledger.data.get("research_warband_mission"), dict)
            else {}
        )
        if old_meta and (
            old_meta.get("id") != mission_id
            or old_meta.get("request_sha256") != request_hash
            or old_meta.get("service") != _service_url()
        ):
            raise ResearchWarbandBridgeError(
                "persisted ResearchWarband mission identity differs from the native run"
            )
        _update_remote_meta(
            ledger,
            mission_id=mission_id,
            request_sha256=request_hash,
            status=str(old_meta.get("status") or "planned"),
        )
        encoded_id = quote(mission_id, safe="")
        remote_operation_ambiguous = True
        status, snapshot = _json_request(
            "GET",
            f"/missions/{encoded_id}",
            allowed_statuses=frozenset({404}),
        )
        if status == 404:
            remote_operation_ambiguous = False
            creation_deadline = time.monotonic() + min(max(timeout_sec, 30), 300)
            while True:
                remote_operation_ambiguous = True
                create_status, created = _json_request(
                    "POST",
                    "/missions",
                    payload=envelope,
                    timeout=min(max(float(timeout_sec), 30.0), 180.0),
                    allowed_statuses=frozenset({409, 429}),
                )
                if create_status == 429:
                    remote_operation_ambiguous = False
                    if time.monotonic() < creation_deadline:
                        time.sleep(POLL_INTERVAL_SECONDS)
                        continue
                    raise ResearchWarbandBridgeError(
                        "ResearchWarband queue remained full before the adoption deadline"
                    )
                if create_status == 409:
                    remote_operation_ambiguous = True
                    _get_status, snapshot = _json_request(
                        "GET", f"/missions/{encoded_id}"
                    )
                else:
                    if set(created) != {
                        "mission_id",
                        "status",
                        "request_sha256",
                        "idempotent",
                    }:
                        raise ResearchWarbandBridgeError(
                            "ResearchWarband creation response shape changed"
                        )
                    if created.get("mission_id") != mission_id:
                        raise ResearchWarbandBridgeError(
                            "ResearchWarband changed the mission identity"
                        )
                    if created.get("request_sha256") != request_hash:
                        raise ResearchWarbandBridgeError(
                            "ResearchWarband request identity differs from the native envelope"
                        )
                    if type(created.get("idempotent")) is not bool:
                        raise ResearchWarbandBridgeError(
                            "ResearchWarband creation response has invalid idempotency proof"
                        )
                    if created.get("status") not in (
                        ACTIVE_SERVICE_STATUSES | TERMINAL_SERVICE_STATUSES
                    ):
                        raise ResearchWarbandBridgeError(
                            "ResearchWarband creation response has an invalid status"
                        )
                    remote_identity_bound = True
                    remote_operation_ambiguous = False
                    remote_operation_ambiguous = True
                    _get_status, snapshot = _json_request(
                        "GET", f"/missions/{encoded_id}"
                    )
                    remote_operation_ambiguous = False
                ledger = TaskLedger.load(target / "task_ledger.json")
                ledger.record_event(
                    "research_warband_mission_started",
                    {"mission_id": mission_id, "request_sha256": request_hash},
                )
                break
        if (
            snapshot.get("id") != mission_id
            or snapshot.get("request_sha256") != request_hash
        ) and type(snapshot.get("id")) is str and type(
            snapshot.get("request_sha256")
        ) is str:
            # A fully identified foreign mission must never be cancelled.
            remote_operation_ambiguous = False
        initial_service_status = _validate_snapshot_identity(
            snapshot, mission_id, request_hash
        )
        remote_identity_bound = True
        remote_operation_ambiguous = False
        ledger = TaskLedger.load(target / "task_ledger.json")
        _update_remote_meta(
            ledger,
            mission_id=mission_id,
            request_sha256=request_hash,
            status=initial_service_status,
            snapshot=snapshot,
        )
        if initial_service_status in ACTIVE_SERVICE_STATUSES:
            _record_remote_activity(
                target, task_id, mission_id, initial_service_status
            )
        deadline = time.monotonic() + max(1, int(timeout_sec))
        previous_status = ""
        cancel_forwarded = False
        while True:
            ledger = TaskLedger.load(target / "task_ledger.json")
            if ledger.cancel_requested() and not cancel_forwarded:
                cancel_status, cancel_response = _json_request(
                    "POST",
                    f"/missions/{encoded_id}/cancel",
                    payload={},
                    allowed_statuses=frozenset({409}),
                )
                if cancel_status != 409 and (
                    set(cancel_response) != {"ok", "status"}
                    or cancel_response.get("ok") is not True
                ):
                    raise ResearchWarbandBridgeError(
                        "ResearchWarband did not acknowledge cancellation"
                    )
                cancel_forwarded = True
                ledger.record_event(
                    "research_warband_cancel_forwarded", {"mission_id": mission_id}
                )
            _status, snapshot = _json_request(
                "GET", f"/missions/{encoded_id}", timeout=30.0
            )
            service_status = _validate_snapshot_identity(
                snapshot, mission_id, request_hash
            )
            _update_remote_meta(
                ledger,
                mission_id=mission_id,
                request_sha256=request_hash,
                status=service_status,
                snapshot=snapshot,
            )
            if service_status in ACTIVE_SERVICE_STATUSES:
                _record_remote_activity(
                    target, task_id, mission_id, service_status
                )
            if service_status != previous_status:
                ledger = TaskLedger.load(target / "task_ledger.json")
                ledger.record_event(
                    "research_warband_status",
                    {"mission_id": mission_id, "status": service_status},
                )
                previous_status = service_status
            if service_status == "needs_user":
                question = str(snapshot.get("question") or "").strip()
                if not question:
                    raise ResearchWarbandBridgeError(
                        "ResearchWarband entered needs_user without a question"
                    )
                _record_waiting(target, task_id, mission_id, question)
            if service_status in TERMINAL_SERVICE_STATUSES:
                raw_result = snapshot.get("result")
                if service_status == "cancelled" and raw_result is None:
                    raw_result = {}
                if not isinstance(raw_result, dict):
                    raise ResearchWarbandBridgeError(
                        "ResearchWarband terminal mission has no object result"
                    )
                if (
                    snapshot.get("inflight") is not False
                    or snapshot.get("cleanup_complete") is not True
                ):
                    if time.monotonic() >= deadline:
                        raise ResearchWarbandBridgeError(
                            "ResearchWarband terminal mission did not prove process cleanup "
                            "before the bridge deadline"
                        )
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue
                result = _terminal_result(
                    target, task_id, mission_id, service_status, raw_result
                )
                ledger = TaskLedger.load(target / "task_ledger.json")
                ledger.record_step(
                    "research_warband",
                    "ResearchWarband",
                    result["status"],
                    summary=result["summary"],
                    details={"mission_id": mission_id, "outcome": raw_result.get("outcome")},
                )
                ledger.set_result(result)
                terminal = {
                    "completed": "completed",
                    "cancelled": "cancelled",
                    "blocked": "blocked",
                    "failed": "failed",
                }[result["status"]]
                ledger.force_status(terminal, reason=result["summary"])
                _finalize_protocol(target, mission_id, result)
                return result
            if time.monotonic() >= deadline:
                raise ResearchWarbandBridgeError(
                    "ResearchWarband bridge timed out; cancellation and cleanup are required"
                )
            time.sleep(POLL_INTERVAL_SECONDS)
    except Exception as exc:  # noqa: BLE001 - bridge errors must become durable outcomes.
        cleanup: dict[str, Any] = {
            "required": False,
            "requested": False,
            "proven": True,
            "status": "not_started",
        }
        if (
            mission_id
            and _SHA256_RE.fullmatch(request_hash)
            and (remote_identity_bound or remote_operation_ambiguous)
        ):
            try:
                cleanup = _cancel_remote_and_wait_for_cleanup(
                    target,
                    mission_id,
                    request_hash,
                    identity_previously_bound=remote_identity_bound,
                    timeout_sec=max(
                        ERROR_CLEANUP_TIMEOUT_SECONDS,
                        min(float(timeout_sec), 604_800.0),
                    ),
                )
            except Exception as cleanup_error:  # noqa: BLE001
                cleanup = {
                    "required": True,
                    "requested": False,
                    "proven": False,
                    "status": "cleanup_error",
                    "error": str(cleanup_error),
                }
        cleanup_pending = bool(cleanup.get("required")) and cleanup.get("proven") is not True
        message = f"ResearchWarband bridge blocked: {exc}"
        if cleanup.get("required"):
            if cleanup.get("proven") is True:
                message += (
                    " Remote process cleanup was proven "
                    f"(service status {cleanup.get('status')})."
                )
            else:
                cleanup_error = str(cleanup.get("error") or "unknown cleanup state")
                message += (
                    " REMOTE PROCESS CLEANUP IS PENDING AND UNPROVEN; "
                    f"the run remains recoverable: {cleanup_error}"
                )
        failure = _warmaster_result(
            target,
            task_id,
            mission_id,
            ok=False,
            status="interrupted" if cleanup_pending else "blocked",
            summary=message,
            error=str(exc),
        )
        failure["research_warband_cleanup"] = cleanup
        ledger = TaskLedger.load(target / "task_ledger.json")
        if mission_id and _SHA256_RE.fullmatch(request_hash):
            try:
                _record_cleanup_outcome(ledger, cleanup)
            except Exception as cleanup_persist_error:  # noqa: BLE001
                cleanup["proven"] = False
                cleanup["error"] = (
                    "cleanup outcome could not be persisted: "
                    f"{cleanup_persist_error}"
                )
                failure["research_warband_cleanup"] = cleanup
                failure["summary"] += (
                    " Cleanup proof could not be durably persisted."
                )
        ledger.set_result(failure)
        ledger.record_event(
            "research_warband_bridge_cleanup",
            {
                "mission_id": mission_id,
                "required": bool(cleanup.get("required")),
                "requested": bool(cleanup.get("requested")),
                "proven": bool(cleanup.get("proven")),
                "status": str(cleanup.get("status") or "unknown"),
                "error": str(cleanup.get("error") or "")[:500],
            },
        )
        ledger.force_status(
            "interrupted" if cleanup_pending else "blocked",
            reason=failure["summary"],
        )
        if mission_id and not cleanup_pending:
            try:
                _finalize_protocol(target, mission_id, failure)
            except Exception as finalize_error:  # noqa: BLE001
                ledger.record_event(
                    "research_warband_finalize_error",
                    {"error": str(finalize_error)[:500]},
                )
        return failure


def answer_research_warband_mission(
    run_dir: Path, task_id: str, answer: str
) -> dict[str, Any]:
    from .ledger import TaskLedger

    text = str(answer).strip()
    if not text:
        return {"ok": False, "status": "invalid", "error": "answer is required"}
    if len(text.encode("utf-8")) > 8_000:
        return {
            "ok": False,
            "status": "invalid",
            "error": "answer exceeds 8000 bytes",
        }
    ledger = TaskLedger.load(Path(run_dir) / "task_ledger.json")
    data = ledger.to_dict()
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    meta = (
        data.get("research_warband_mission")
        if isinstance(data.get("research_warband_mission"), dict)
        else {}
    )
    mission_id = str(meta.get("id") or "")
    request_hash = str(meta.get("request_sha256") or "")
    if (
        result.get("status") != "needs_user"
        or result.get("needs_user") is not True
        or not _SERVICE_MISSION_ID_RE.fullmatch(mission_id)
        or mission_id in {".", ".."}
        or not _SHA256_RE.fullmatch(request_hash)
        or meta.get("service") != _service_url()
    ):
        return {
            "ok": False,
            "status": "conflict",
            "error": "run is not waiting for ResearchWarband clarification",
        }
    encoded = quote(mission_id, safe="")
    _status, snapshot = _json_request("GET", f"/missions/{encoded}")
    try:
        service_status = _validate_snapshot_identity(
            snapshot, mission_id, request_hash
        )
    except ResearchWarbandBridgeError as exc:
        return {"ok": False, "status": "conflict", "error": str(exc)}
    if service_status != "needs_user":
        return {
            "ok": False,
            "status": "conflict",
            "error": "ResearchWarband is not waiting for this answer",
        }
    _status, response = _json_request(
        "POST", f"/missions/{encoded}/answer", payload={"answer": text}
    )
    if set(response) != {"ok", "status"} or response.get("ok") is not True:
        return {
            "ok": False,
            "status": str(response.get("status") or "conflict"),
            "error": "ResearchWarband did not accept the clarification",
        }
    resumed = dict(result)
    resumed.update(
        {
            "phase": "running",
            "status": "running",
            "summary": "Clarification accepted; ResearchWarband resumed the same mission.",
            "question": "",
            "needs_user": False,
            "next_action": {},
        }
    )
    ledger.set_result(resumed)
    ledger.record_event(
        "research_warband_answer_forwarded", {"mission_id": mission_id}
    )
    return {
        "ok": True,
        "status": "running",
        "task_id": task_id,
        "mission_id": mission_id,
    }


def cancel_research_warband_mission_for_run(
    run_dir: Path, task_id: str, timeout_sec: float = 60.0
) -> dict[str, Any]:
    from .ledger import TaskLedger

    target = Path(run_dir)
    ledger = TaskLedger.load(target / "task_ledger.json")
    data = ledger.to_dict()
    meta = (
        data.get("research_warband_mission")
        if isinstance(data.get("research_warband_mission"), dict)
        else {}
    )
    mission_id = str(meta.get("id") or "")
    request_hash = str(meta.get("request_sha256") or "")
    if (
        not _SERVICE_MISSION_ID_RE.fullmatch(mission_id)
        or mission_id in {".", ".."}
        or not _SHA256_RE.fullmatch(request_hash)
        or meta.get("service") != _service_url()
        or str(meta.get("status") or "") not in ACTIVE_SERVICE_STATUSES
    ):
        return {
            "ok": False,
            "status": "not_active",
            "error": "run has no active ResearchWarband mission",
        }
    cleanup = _cancel_remote_and_wait_for_cleanup(
        target,
        mission_id,
        request_hash,
        identity_previously_bound=True,
        timeout_sec=timeout_sec,
    )
    ledger = TaskLedger.load(target / "task_ledger.json")
    _record_cleanup_outcome(ledger, cleanup)
    if cleanup.get("proven") is not True:
        return {
            "ok": False,
            "status": "cancel_cleanup_unproven",
            "mission_id": mission_id,
            "cleanup_complete": False,
            "error": str(
                cleanup.get("error")
                or "ResearchWarband cancellation cleanup was not proven before timeout"
            ),
        }
    service_status = str(cleanup.get("status") or "")
    if service_status != "cancelled":
        return {
            "ok": False,
            "status": service_status or "conflict",
            "mission_id": mission_id,
            "cleanup_complete": True,
            "error": "ResearchWarband reached a non-cancelled clean terminal state",
        }
    result = _terminal_result(target, task_id, mission_id, "cancelled", {})
    ledger = TaskLedger.load(target / "task_ledger.json")
    ledger.set_result(result)
    ledger.force_status("cancelled", reason=result["summary"])
    _finalize_protocol(target, mission_id, result)
    return {
        "ok": True,
        "status": "cancelled",
        "mission_id": mission_id,
        "cleanup_complete": True,
    }


__all__ = [
    "ResearchWarbandBridgeError",
    "answer_research_warband_mission",
    "cancel_research_warband_mission_for_run",
    "inspect_research_warband_mission",
    "load_research_warband_envelope",
    "research_warband_backend_health",
    "run_via_research_warband",
]
