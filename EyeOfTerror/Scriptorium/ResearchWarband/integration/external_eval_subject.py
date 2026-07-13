"""HTTP SubjectAdapter for the dedicated tokenless evaluator daemon on 7202."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from typing import Any
from urllib.parse import quote

from research_eval.subjects import SubjectAdapter, SubjectExecution

from ..production_runner import (
    RUNNER_CONTRACT_VERSION,
    validate_external_evaluator_result,
)
from .loopback_http import LoopbackHTTPError, LoopbackJSONClient


_PAYLOAD_FIELDS = frozenset(
    {
        "goal",
        "task_id",
        "max_wall_sec",
        "standalone_test",
        "output_contract_version",
        "source_gateway_url",
    }
)
_QUIESCENT_STATUSES = frozenset(
    {"done", "needs_user", "needs_revision", "blocked", "failed", "cancelled"}
)
_SERVICE_STATUSES = frozenset(
    {
        "queued", "running", "cancelling", "done", "needs_user",
        "needs_revision", "blocked", "failed", "cancelled",
    }
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class HTTPExternalEvalSubject(SubjectAdapter):
    """Drive the real async service while preserving evaluator wall time.

    The adapter is intentionally bound to 7202.  It cannot be pointed at the
    bearer-protected production shadow or at legacy Iskandar on 7101.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:7202",
        *,
        bearer_token: str = "",
        poll_interval_sec: float = 0.1,
        max_response_bytes: int = 66_000_000,
    ) -> None:
        if not isinstance(poll_interval_sec, (int, float)) or not (
            0.02 <= float(poll_interval_sec) <= 1.0
        ):
            raise ValueError("poll_interval_sec must be between 0.02 and 1.0")
        self.client = LoopbackJSONClient(
            base_url,
            bearer_token=bearer_token,
            max_response_bytes=max_response_bytes,
            expected_port=7202,
        )
        self.poll_interval_sec = float(poll_interval_sec)

    def health(self) -> dict[str, Any]:
        value = self.client.request_json("GET", "/health", timeout_sec=5)
        identity = value.get("identity") if isinstance(value, dict) else None
        if (
            value.get("status") != "ok"
            or value.get("service") != "ResearchWarband"
            or not isinstance(identity, dict)
            or identity.get("standalone_test_mode") is not True
            or identity.get("bearer_auth_required") is not bool(
                self.client.bearer_token
            )
        ):
            raise RuntimeError("7202 is not the dedicated evaluator ResearchWarband")
        readiness = identity.get("readiness")
        runner_readiness = (
            readiness.get("runner_deployment")
            if isinstance(readiness, dict)
            else None
        )
        deployment = (
            readiness.get("deployment_integrity")
            if isinstance(readiness, dict)
            else None
        )
        attestation = (
            runner_readiness.get("attestation_sha256")
            if isinstance(runner_readiness, dict)
            else None
        )
        if (
            not isinstance(readiness, dict)
            or readiness.get("ready") is not True
            or not isinstance(runner_readiness, dict)
            or runner_readiness.get("configured") is not True
            or runner_readiness.get("ready") is not True
            or type(attestation) is not str
            or _SHA256_RE.fullmatch(attestation) is None
            or not isinstance(deployment, dict)
            or deployment.get("ok") is not True
            or deployment.get("startup_digest") != deployment.get("current_digest")
        ):
            raise RuntimeError("7202 runtime/deployment attestation is not ready")
        # Raw health contains mutable store counters.  The evaluator needs a
        # stable execution identity, not a frozen mission-count snapshot.
        stable_identity = {
            key: copy.deepcopy(identity.get(key))
            for key in (
                "source_sha256",
                "authorized_source_sha256",
                "instance_id",
                "store_instance_id",
                "store_id",
                "started_at",
                "bearer_auth_required",
                "standalone_test_mode",
                "runner",
                "execution_authorization",
                "models",
            )
        }
        stable_identity["runtime_attestation_sha256"] = attestation
        stable_identity["deployment_integrity_sha256"] = deployment[
            "startup_digest"
        ]
        return {
            "status": "ok",
            "service": "ResearchWarbandHTTPSubject",
            "identity": stable_identity,
        }

    @staticmethod
    def _validate_payload(payload: Any, timeout_sec: int) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != _PAYLOAD_FIELDS:
            raise ValueError("external evaluator payload has missing or unknown fields")
        if payload.get("standalone_test") is not True:
            raise ValueError("external evaluator requires standalone_test=true")
        if payload.get("output_contract_version") != "research-result/v1":
            raise ValueError("external evaluator output contract is unsupported")
        if payload.get("max_wall_sec") != timeout_sec:
            raise ValueError("adapter timeout must equal the submitted evaluator wall limit")
        if type(payload.get("task_id")) is not str or not payload["task_id"]:
            raise ValueError("external evaluator task_id is invalid")
        return copy.deepcopy(payload)

    @staticmethod
    def _expected_internal_mission_id(payload: dict[str, Any]) -> str:
        canonical = copy.deepcopy(payload)
        canonical["source_gateway_url"] = str(
            canonical["source_gateway_url"]
        ).rstrip("/")
        raw = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return f"{str(canonical['task_id'])[:110]}-{hashlib.sha256(raw).hexdigest()[:16]}"

    def _get_mission(self, mission_id: str, *, timeout_sec: float) -> dict[str, Any]:
        return self.client.request_json(
            "GET",
            "/missions/" + quote(mission_id, safe=""),
            timeout_sec=max(0.05, timeout_sec),
        )

    @staticmethod
    def _quiescent(snapshot: dict[str, Any]) -> bool:
        return (
            snapshot.get("status") in _QUIESCENT_STATUSES
            and snapshot.get("inflight") is False
            and snapshot.get("cleanup_complete") is True
        )

    def _cancel(self, mission_id: str, *, timeout_sec: float) -> None:
        try:
            self.client.request_json(
                "POST",
                "/missions/" + quote(mission_id, safe="") + "/cancel",
                payload={},
                timeout_sec=max(0.05, timeout_sec),
            )
        except LoopbackHTTPError as exc:
            # 409 is a race with a terminal result.  The following GET proves
            # which state won; every other transport failure remains fatal.
            if exc.status != 409:
                raise

    def _cancel_and_prove(self, mission_id: str, *, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("subject wall limit expired before cancellation")
        self._cancel(mission_id, timeout_sec=min(1.0, remaining))
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            snapshot = self._get_mission(
                mission_id, timeout_sec=min(1.0, remaining)
            )
            if self._quiescent(snapshot):
                raise TimeoutError(
                    "subject wall limit reached; remote mission cleanup was proven"
                )
            time.sleep(min(self.poll_interval_sec, max(0.0, remaining)))
        raise RuntimeError("subject wall limit reached and remote cleanup is unproven")

    @staticmethod
    def _execution(
        snapshot: dict[str, Any], internal_mission_id: str, external_mission_id: str
    ) -> SubjectExecution:
        lifecycle = snapshot.get("status")
        wrapper = snapshot.get("result")
        if lifecycle == "cancelled":
            raise RuntimeError("evaluator mission was cancelled without a result")
        if not isinstance(wrapper, dict):
            raise RuntimeError("quiescent evaluator mission has no runner result")
        if wrapper.get("runner_contract_version") != RUNNER_CONTRACT_VERSION:
            raise RuntimeError("evaluator mission runner contract is invalid")
        external = validate_external_evaluator_result(
            wrapper.get("external_evaluator_result")
        )
        if snapshot.get("id") != internal_mission_id:
            raise RuntimeError("service snapshot changed its internal mission identity")
        if external.get("mission_id") != external_mission_id:
            raise RuntimeError("external evaluator result changed mission identity")
        expected = {
            "done": "accepted",
            "needs_user": "needs_user",
            "needs_revision": "needs_revision",
            "blocked": "blocked",
            "failed": "failed",
        }.get(str(lifecycle))
        if external.get("status") != expected:
            raise RuntimeError("service lifecycle and external result disagree")
        return SubjectExecution(
            result=external,
            terminal=True,
            cleanup_proven=True,
            cleanup_detail="remote service reports quiescent process-tree cleanup",
        )

    def execute(
        self, payload: dict[str, Any], *, timeout_sec: int
    ) -> SubjectExecution:
        exact = self._validate_payload(payload, timeout_sec)
        started = time.monotonic()
        deadline = started + float(timeout_sec)
        cleanup_reserve = min(5.0, max(1.0, float(timeout_sec) / 4.0))
        cancel_at = deadline - cleanup_reserve
        external_mission_id = str(exact["task_id"])
        internal_mission_id = self._expected_internal_mission_id(exact)
        created = self.client.request_json(
            "POST",
            "/missions",
            payload=exact,
            timeout_sec=min(5.0, max(0.05, cancel_at - time.monotonic())),
        )
        if set(created) != {"mission_id", "status", "request_sha256", "idempotent"}:
            raise RuntimeError("service create response has missing or unknown fields")
        if (
            created.get("mission_id") != internal_mission_id
            or created.get("status") not in _SERVICE_STATUSES
            or type(created.get("idempotent")) is not bool
            or type(created.get("request_sha256")) is not str
            or _SHA256_RE.fullmatch(created["request_sha256"]) is None
        ):
            raise RuntimeError("service create response is inconsistent with the submitted envelope")
        while True:
            now = time.monotonic()
            if now >= cancel_at:
                self._cancel_and_prove(internal_mission_id, deadline=deadline)
            snapshot = self._get_mission(
                internal_mission_id,
                timeout_sec=min(5.0, max(0.05, cancel_at - now)),
            )
            if snapshot.get("id") != internal_mission_id:
                raise RuntimeError("service mission snapshot changed identity")
            if self._quiescent(snapshot):
                return self._execution(
                    snapshot, internal_mission_id, external_mission_id
                )
            if snapshot.get("status") not in {"queued", "running", "cancelling"}:
                raise RuntimeError("service returned a non-quiescent invalid lifecycle state")
            time.sleep(
                min(self.poll_interval_sec, max(0.0, cancel_at - time.monotonic()))
            )


__all__ = ["HTTPExternalEvalSubject"]
