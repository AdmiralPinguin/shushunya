"""Loopback-only asynchronous HTTP facade for ResearchWarband.

The service owns transport and lifecycle only.  Its injected runner owns the
research pipeline.  Pipeline results are persisted exactly and this module maps
only the pipeline's public ``outcome`` to a lifecycle status.

Production requests require the exact Iskandar handoff envelope.  Undirected
requests are available only when both the daemon environment and the individual
request opt into standalone test mode.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
import unicodedata
from urllib.parse import urlsplit
import uuid

try:  # package import in production/tests
    from . import mission_store
    from .deployment_guard import DeploymentGuard, DeploymentIntegrityError
except ImportError:  # direct ``python service.py`` execution
    import mission_store  # type: ignore[no-redef]
    from deployment_guard import (  # type: ignore[no-redef]
        DeploymentGuard,
        DeploymentIntegrityError,
    )


PRODUCTION_FIELDS = frozenset(
    {"mission_id", "task_id", "leadership_directive", "commander_order"}
)
STANDALONE_FIELDS = frozenset(
    {
        "goal",
        "task_id",
        "max_wall_sec",
        "standalone_test",
        "output_contract_version",
        "source_gateway_url",
    }
)
REQUEST_FIELDS = PRODUCTION_FIELDS | STANDALONE_FIELDS
ANSWER_FIELDS = frozenset({"answer"})
CLIENT_FORBIDDEN_AUTHORITY_FIELDS = frozenset(
    {
        "trusted_reviewer_ids",
        "trusted_reviewers",
        "attestations",
        "review_attestations",
        "normalizer",
        "normalizer_callback",
        "normalizer_id",
        "normalizer_registry",
        "normalizers",
        "registered_normalizers",
        "verifier",
        "verifier_config",
        "verifier_configuration",
        "evidence_verifier",
        "semantic_verifier",
    }
)
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")

SERVICE_STARTED_AT = int(time.time())
SERVICE_INSTANCE_ID = uuid.uuid4().hex
def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _source_sha256(package_root: Path | None = None) -> str:
    """Hash every production Python module in the package deterministically."""

    digest = hashlib.sha256()
    root = package_root or Path(__file__).resolve().parent
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("service package root cannot be attested")
    paths = sorted(
        (
            path
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts and not path.name.startswith("test_")
        ),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    if not paths:
        raise RuntimeError("service package contains no attestable production modules")
    for path in paths:
        name = path.relative_to(root).as_posix()
        digest.update(name.encode("utf-8") + b"\0")
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"service source cannot be attested: {name}")
        raw = path.read_bytes()
        if len(raw) > 32_000_000:
            raise RuntimeError(f"service source exceeds attestation limit: {name}")
        digest.update(raw)
    return digest.hexdigest()


SERVICE_SOURCE_SHA256 = _source_sha256()


def _deployment_source_sha256(
    runner: mission_store.RunnerSpec,
    store: mission_store.MissionStore,
    *,
    standalone_test_mode: bool,
) -> str:
    """Bind health identity to executed code plus trust-sensitive configuration."""

    runner = mission_store.attest_runner(runner)
    contract_files: list[dict[str, str]] = []
    service_root = Path(__file__).resolve().parent
    protocol_root = service_root.parent.parent / "common_protocol"
    automatic: list[Path] = []
    if protocol_root.exists() or protocol_root.is_symlink():
        if protocol_root.is_symlink() or not protocol_root.is_dir():
            raise RuntimeError("trusted common_protocol root cannot be attested")
        automatic = sorted(
            (
                path
                for path in protocol_root.iterdir()
                if path.suffix == ".py" and not path.name.startswith("test_")
            ),
            key=lambda item: item.name,
        )
    configured = os.environ.get("RESEARCH_WARBAND_TRUSTED_CONTRACT_FILES", "")
    explicit = [Path(item) for item in configured.split(os.pathsep) if item]
    seen: set[Path] = set()
    for path in [*automatic, *explicit]:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"trusted contract cannot be attested: {path}")
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        raw = path.read_bytes()
        if len(raw) > 16_000_000:
            raise RuntimeError(f"trusted contract exceeds attestation limit: {path}")
        contract_files.append(
            {"path": str(resolved), "sha256": hashlib.sha256(raw).hexdigest()}
        )
    manifest = {
        "service_source_sha256": _source_sha256(),
        "runner": {
            "target": runner.target,
            "module_path": runner.module_path,
            "module_sha256": runner.module_sha256,
            "callable_sha256": runner.callable_sha256,
        },
        "contracts": {
            "production_fields": sorted(PRODUCTION_FIELDS),
            "standalone_fields": sorted(STANDALONE_FIELDS),
            "outcomes": dict(sorted(mission_store.OUTCOME_STATUS.items())),
            "output_contract_version": "research-result/v1",
            "files": contract_files,
        },
        "trusted_config": {
            "standalone_test_mode": bool(standalone_test_mode),
            "research_model": os.environ.get("RESEARCH_WARBAND_LLM_MODEL", ""),
            "research_base_url": os.environ.get("RESEARCH_WARBAND_LLM_BASE_URL", ""),
            "verifier_model": os.environ.get("RESEARCH_WARBAND_VERIFIER_MODEL", ""),
            "verifier_base_url": os.environ.get("RESEARCH_WARBAND_VERIFIER_BASE_URL", ""),
            "trusted_reviewer_ids": os.environ.get(
                "RESEARCH_WARBAND_TRUSTED_REVIEWER_IDS", ""
            ),
            "normalizer_id": os.environ.get("RESEARCH_WARBAND_NORMALIZER_ID", ""),
            "max_active": store.max_active,
            "attempt_timeout_seconds": store.attempt_timeout_seconds,
            "cancel_grace_seconds": store.cancel_grace_seconds,
            "terminate_grace_seconds": store.terminate_grace_seconds,
        },
    }
    return hashlib.sha256(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


class RequestValidationError(ValueError):
    pass


class ServiceNotReadyError(RuntimeError):
    pass


def _standalone_gateway(value: Any) -> str:
    if type(value) is not str:
        raise RequestValidationError("source_gateway_url must be a loopback HTTP URL")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise RequestValidationError("source_gateway_url is invalid") from exc
    if (
        parsed.scheme != "http"
        or host is None
        or not ipaddress.ip_address(host).is_loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is None
        or not 1 <= port <= 65535
    ):
        raise RequestValidationError("source_gateway_url must be a literal loopback HTTP base URL")
    return value.rstrip("/")


def _reject_client_authority_objects(payload: dict[str, Any]) -> None:
    """Keep reviewer/normalizer/verifier authority inside the trusted runner.

    The check is recursive because hiding a registry below ``commander_order``
    would otherwise bypass a top-level allowlist.  It inspects keys only; normal
    user prose may still discuss a verifier or an attestation.
    """
    pending: list[tuple[Any, int, str]] = [(payload, 0, "$")]
    visited = 0
    while pending:
        value, depth, path = pending.pop()
        visited += 1
        if visited > 20_000 or depth > 64:
            raise RequestValidationError("mission request nesting is too complex")
        if isinstance(value, dict):
            for raw_key, child in value.items():
                normalized = unicodedata.normalize("NFKC", str(raw_key)).casefold()
                normalized = normalized.replace("-", "_")
                if normalized in CLIENT_FORBIDDEN_AUTHORITY_FIELDS:
                    raise RequestValidationError(
                        f"client cannot configure trusted authority object at {path}.{raw_key}"
                    )
                pending.append((child, depth + 1, f"{path}.{raw_key}"))
        elif isinstance(value, list):
            pending.extend(
                (child, depth + 1, f"{path}[{index}]")
                for index, child in enumerate(value)
            )


class ResearchServiceRuntime:
    """Dependency-injected service state; safe to construct repeatedly in tests."""

    def __init__(
        self,
        *,
        store: mission_store.MissionStore,
        runner: mission_store.Runner,
        standalone_test_mode: bool | None = None,
        readiness_probe: mission_store.Runner | None = None,
    ) -> None:
        if not callable(runner):
            if not isinstance(runner, (str, mission_store.RunnerSpec)):
                raise TypeError("runner must be an importable runner specification")
        self.store = store
        self.runner = mission_store.attest_runner(runner)
        self.standalone_test_mode = (
            os.environ.get("RESEARCH_WARBAND_STANDALONE_TEST_MODE", "0") == "1"
            if standalone_test_mode is None
            else bool(standalone_test_mode)
        )
        self._started = False
        self._tokenless_test_only = False
        self._lock = threading.Lock()
        self.readiness_spec = (
            mission_store.attest_runner(readiness_probe)
            if readiness_probe is not None
            else None
        )
        self.deployment_profile = os.environ.get("RESEARCH_WARBAND_PROFILE", "").strip()
        deployed_profile = self.deployment_profile in {
            "shadow-production",
            "external-evaluator",
        }
        if deployed_profile and (
            sys.pycache_prefix != "/dev/null" or sys.dont_write_bytecode is not True
        ):
            raise RuntimeError(
                "deployed ResearchWarband requires PYTHONPYCACHEPREFIX=/dev/null "
                "and PYTHONDONTWRITEBYTECODE=1"
            )
        self.require_readiness_attestation = bool(
            deployed_profile or not self.standalone_test_mode
        )
        self.require_linux_cgroup = bool(os.name != "nt" and deployed_profile)
        if self.require_readiness_attestation and self.readiness_spec is None:
            raise RuntimeError(
                "production ResearchWarband requires an attested readiness probe"
            )
        self._isolation_error = ""
        if self.require_linux_cgroup:
            try:
                mission_store.verify_linux_cgroup_delegation()
            except Exception as exc:
                self._isolation_error = (
                    "per-attempt delegated cgroup is unavailable "
                    f"({type(exc).__name__})"
                )
        self.store.bind_runner(self.runner)
        trusted_config = {
            "runner": {
                "target": self.runner.target,
                "module_sha256": self.runner.module_sha256,
                "callable_sha256": self.runner.callable_sha256,
            },
            "readiness_probe": (
                {
                    "target": self.readiness_spec.target,
                    "module_sha256": self.readiness_spec.module_sha256,
                    "callable_sha256": self.readiness_spec.callable_sha256,
                }
                if self.readiness_spec is not None
                else None
            ),
            "standalone_test_mode": self.standalone_test_mode,
            "deployment_profile": self.deployment_profile,
            "require_readiness_attestation": self.require_readiness_attestation,
            "require_linux_cgroup": self.require_linux_cgroup,
            "production_fields": sorted(PRODUCTION_FIELDS),
            "standalone_fields": sorted(STANDALONE_FIELDS),
            "outcomes": dict(sorted(mission_store.OUTCOME_STATUS.items())),
            "max_active": self.store.max_active,
            "attempt_timeout_seconds": self.store.attempt_timeout_seconds,
            "cancel_grace_seconds": self.store.cancel_grace_seconds,
            "terminate_grace_seconds": self.store.terminate_grace_seconds,
        }
        trusted_files = [self.runner.module_path]
        if self.readiness_spec is not None:
            trusted_files.append(self.readiness_spec.module_path)
        self.deployment_guard = DeploymentGuard.from_environment(
            Path(__file__).resolve().parent,
            trusted_config=trusted_config,
            trusted_files=tuple(trusted_files),
        )
        self.store.bind_deployment_guard(self.deployment_guard)
        self.store.bind_readiness_probe(
            self.readiness_spec,
            require_attestation=self.require_readiness_attestation,
            require_linux_cgroup=self.require_linux_cgroup,
        )
        self.source_sha256 = self.deployment_guard.startup_digest

    def _runner_readiness(self) -> dict[str, Any]:
        spec = self.readiness_spec
        if spec is None:
            return {"configured": False, "ready": True}
        try:
            exact = mission_store.read_runtime_readiness(
                spec,
                require_attestation=self.require_readiness_attestation,
            )
            attestation = exact.get("attestation_sha256")
            public = {
                "configured": True,
                "ready": exact["ready"],
                "reason": (
                    ""
                    if exact["ready"]
                    else "runner deployment attestation reported not ready"
                ),
            }
            if attestation is not None:
                public["attestation_sha256"] = attestation
            return public
        except Exception as exc:
            return {
                "configured": True,
                "ready": False,
                "reason": f"runner readiness probe failed ({type(exc).__name__})",
            }

    def readiness(self) -> dict[str, Any]:
        deployment = self.deployment_guard.status()
        runner = self._runner_readiness() if deployment.ok else {
            "configured": self.readiness_spec is not None,
            "ready": False,
            "reason": "deployment integrity is not verified",
        }
        recovery = self.store.recovery_status()
        isolation_ready = not self._isolation_error
        ready = bool(
            deployment.ok and runner["ready"] and recovery["safe"] and isolation_ready
        )
        return {
            "ready": ready,
            "deployment_integrity": deployment.to_dict(),
            "runner_deployment": runner,
            "store_safe": bool(recovery["safe"]),
            "process_isolation": {
                "required": self.require_linux_cgroup,
                "ready": isolation_ready,
                "reason": self._isolation_error,
            },
        }

    def require_ready(self) -> None:
        status = self.readiness()
        if not status["ready"]:
            raise ServiceNotReadyError("ResearchWarband is not ready")

    def configure_transport_auth(self, bearer_token: str) -> None:
        """Fail closed unless tokenless operation is explicitly evaluator-only."""

        if type(bearer_token) is not str:
            raise TypeError("bearer token must be a string")
        if not bearer_token and not self.standalone_test_mode:
            raise RuntimeError("production ResearchWarband requires a bearer token")
        if not bearer_token:
            unsafe = [
                mission.id
                for mission in self.store.missions.values()
                if json.loads(mission.payload_bytes).get("standalone_test") is not True
            ]
            if unsafe:
                raise RuntimeError(
                    "tokenless standalone mode requires a dedicated evaluator store; "
                    "non-evaluator missions are present"
                )
        self._tokenless_test_only = not bool(bearer_token)

    def start(self) -> list[str]:
        """Adopt persisted work exactly once for this process instance."""
        with self._lock:
            if self._started:
                return []
            self.store.acquire_service_lease()
            try:
                adopted = self.store.adopt_pending(self.runner)
            except Exception:
                self.store.release_service_lease()
                raise
            self._started = True
            return adopted

    def close(self) -> bool:
        with self._lock:
            released = self.store.release_service_lease()
            if released:
                self._started = False
            return released

    def validate_mission_request(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RequestValidationError("JSON body must be an object")
        unknown = set(payload) - REQUEST_FIELDS
        if unknown:
            raise RequestValidationError(
                "unknown mission request fields: " + ", ".join(sorted(unknown))
            )
        _reject_client_authority_objects(payload)
        standalone = payload.get("standalone_test") is True
        if standalone:
            if not self.standalone_test_mode:
                raise RequestValidationError(
                    "standalone_test requires RESEARCH_WARBAND_STANDALONE_TEST_MODE=1"
                )
            if set(payload) != STANDALONE_FIELDS:
                raise RequestValidationError(
                    "standalone_test request does not match the evaluator allowlist"
                )
        elif self._tokenless_test_only:
            raise RequestValidationError(
                "tokenless standalone daemon accepts evaluator requests only"
            )
        elif set(payload) != PRODUCTION_FIELDS:
            missing = sorted(PRODUCTION_FIELDS - set(payload))
            extra = sorted(set(payload) - PRODUCTION_FIELDS)
            detail = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(extra))
            raise RequestValidationError(
                "production request must contain the exact Iskandar handoff envelope"
                + (": " + "; ".join(detail) if detail else "")
            )
        for field in (("task_id",) if standalone else ("mission_id", "task_id")):
            value = payload.get(field)
            if type(value) is not str or not ID_RE.fullmatch(value):
                raise RequestValidationError(f"{field} is invalid")
        if standalone:
            goal = payload.get("goal")
            if type(goal) is not str or not goal.strip():
                raise RequestValidationError("goal must be a non-empty string")
            wall = payload.get("max_wall_sec")
            if type(wall) is not int or isinstance(wall, bool) or not 1 <= wall <= 86_400:
                raise RequestValidationError("max_wall_sec must be an integer from 1 to 86400")
            if payload.get("output_contract_version") != "research-result/v1":
                raise RequestValidationError("output_contract_version is unsupported")
            payload = dict(payload)
            payload["source_gateway_url"] = _standalone_gateway(
                payload.get("source_gateway_url")
            )
            # Fixture gateways use an ephemeral port on every evaluator run.
            # Bind identity to the complete public envelope so reruns cannot
            # collide with an older durable mission for the same task id.
            envelope_hash = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()[:16]
            payload["mission_id"] = f"{payload['task_id'][:110]}-{envelope_hash}"
        else:
            directive = payload.get("leadership_directive")
            order = payload.get("commander_order")
            if not isinstance(directive, dict) or not isinstance(order, dict):
                raise RequestValidationError(
                    "leadership_directive and commander_order must be objects"
                )
            # This is identity binding, not duplicate directive semantics.  Full
            # schema/authority validation belongs to the injected pipeline adapter.
            for nested, label in ((directive, "leadership_directive"), (order, "commander_order")):
                nested_mission = nested.get("mission_id")
                if nested_mission is not None and nested_mission != payload["mission_id"]:
                    raise RequestValidationError(f"{label}.mission_id does not match envelope")
            nested_task = directive.get("task_id")
            if nested_task is not None and nested_task != payload["task_id"]:
                raise RequestValidationError(
                    "leadership_directive.task_id does not match envelope"
                )
        # JSON round-trip freezes caller-owned data before hashing/persistence.
        try:
            return json.loads(
                json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RequestValidationError("mission request must contain finite JSON data") from exc

    def create(self, payload: dict[str, Any]) -> tuple[mission_store.Mission, bool]:
        exact = self.validate_mission_request(payload)
        self.require_ready()
        mission, created = self.store.create_or_get(exact["mission_id"], exact)
        if created:
            # A terminal worker may still be leaving its thread for a few
            # microseconds. The durable queued mission is picked up by the
            # store's slot scheduler instead of being exposed as a false 429.
            self.store.launch(mission, self.runner)
        return mission, created

    def provide_answer(self, mission: mission_store.Mission, answer: str) -> bool:
        self.require_ready()
        return self.store.provide_answer(
            mission.id, answer, self.runner, expected=mission
        )

    def resume(self, mission: mission_store.Mission) -> bool:
        self.require_ready()
        return self.store.resume(mission.id, self.runner, expected=mission)

    def identity(
        self,
        *,
        bearer_required: bool,
        readiness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        readiness = self.readiness() if readiness is None else readiness
        deployment_ok = bool(readiness["deployment_integrity"]["ok"])
        lead_model = os.environ.get("RESEARCH_WARBAND_LLM_MODEL", "")
        lead_base = os.environ.get("RESEARCH_WARBAND_LLM_BASE_URL", "")
        verifier_model = os.environ.get("RESEARCH_WARBAND_VERIFIER_MODEL", lead_model)
        verifier_base = os.environ.get("RESEARCH_WARBAND_VERIFIER_BASE_URL", lead_base)
        return {
            "source_sha256": self.source_sha256 if deployment_ok else None,
            "authorized_source_sha256": self.source_sha256,
            "instance_id": SERVICE_INSTANCE_ID,
            "store_instance_id": self.store.instance_id,
            "store_id": self.store.store_id,
            "started_at": SERVICE_STARTED_AT,
            "bearer_auth_required": bearer_required,
            "standalone_test_mode": self.standalone_test_mode,
            "runner": {
                "target": self.runner.target,
                "module_sha256": self.runner.module_sha256,
                "callable_sha256": self.runner.callable_sha256,
            },
            "execution_authorization": {
                "iskandar_handoff_required": True,
                "standalone_test_mode_enabled": self.standalone_test_mode,
                "standalone_test_payload_flag_required": True,
                "tokenless_evaluator_only": self._tokenless_test_only,
            },
            "models": {
                "research": {"model": lead_model, "base_url": lead_base},
                "semantic_verifier": {
                    "model": verifier_model,
                    "base_url": verifier_base,
                },
            },
            "store_recovery": self.store.recovery_status(),
            "readiness": readiness,
        }

    def capabilities(self) -> dict[str, Any]:
        readiness = self.readiness()
        return {
            "service": "ResearchWarband",
            "api_version": 1,
            "asynchronous": True,
            "endpoints": [
                "GET /health",
                "GET /capabilities",
                "POST /missions",
                "GET /missions/{id}",
                "GET /missions/{id}/events",
                "POST /missions/{id}/answer",
                "POST /missions/{id}/cancel",
                "POST /missions/{id}/resume",
            ],
            "pipeline_outcomes": sorted(mission_store.OUTCOME_STATUS),
            "lifecycle_statuses": sorted(mission_store.ALL_STATUSES),
            "max_active": self.store.max_active,
            "max_missions": self.store.max_missions,
            "exact_request_idempotency": True,
            "startup_adoption": True,
            "ready": readiness["ready"],
            "deployment_integrity_verified": readiness["deployment_integrity"]["ok"],
            "runner_deployment_ready": readiness["runner_deployment"]["ready"],
        }


class _ServerContext:
    def __init__(
        self,
        runtime: ResearchServiceRuntime,
        *,
        bearer_token: str,
        max_request_bytes: int,
        max_response_bytes: int,
        max_answer_bytes: int,
    ) -> None:
        self.runtime = runtime
        self.bearer_token = bearer_token
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.max_answer_bytes = max_answer_bytes


class ResearchHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], context: _ServerContext):
        self.context = context
        super().__init__(address, Handler)

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self.context.runtime.close()


def _literal_loopback_authority(value: str) -> str | None:
    raw = value.strip()
    if not raw or "@" in raw or any(char in raw for char in "/?#\\"):
        return None
    try:
        parsed = urlsplit("//" + raw)
        if parsed.username is not None or parsed.password is not None:
            return None
        host = parsed.hostname
        if host is None or not ipaddress.ip_address(host).is_loopback:
            return None
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return None
    except (ValueError, ipaddress.AddressValueError):
        return None
    return raw.lower()


def _trusted_origin(value: str, authority: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or parsed.path not in {"", "/"}:
        return False
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        return False
    return _literal_loopback_authority(parsed.netloc) == authority


class Handler(BaseHTTPRequestHandler):
    server: ResearchHTTPServer

    def log_message(self, *_args: Any) -> None:
        pass

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionError):
            self.close_connection = True

    @property
    def context(self) -> _ServerContext:
        return self.server.context

    def _send(self, code: int, value: dict[str, Any]) -> None:
        try:
            raw = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            code = 500
            raw = b'{"error":"response is not finite JSON"}'
        if len(raw) > self.context.max_response_bytes:
            code = 507
            raw = b'{"error":"response exceeds configured byte limit"}'
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionError):
            pass

    def _gate(self, *, json_body: bool = False) -> bool:
        try:
            peer = ipaddress.ip_address(str(self.client_address[0]))
        except (IndexError, TypeError, ValueError):
            self._send(403, {"error": "loopback client required"})
            return False
        if not peer.is_loopback:
            self._send(403, {"error": "loopback client required"})
            return False

        hosts = self.headers.get_all("Host") or []
        authority = _literal_loopback_authority(hosts[0]) if len(hosts) == 1 else None
        if authority is None:
            self._send(421, {"error": "one literal loopback Host is required"})
            return False
        origins = self.headers.get_all("Origin") or []
        if len(origins) > 1 or (origins and not _trusted_origin(origins[0].strip(), authority)):
            self._send(403, {"error": "untrusted Origin"})
            return False
        fetch_sites = self.headers.get_all("Sec-Fetch-Site") or []
        if len(fetch_sites) > 1 or (
            fetch_sites
            and fetch_sites[0].strip().lower() not in {"same-origin", "same-site", "none"}
        ):
            self._send(403, {"error": "cross-site request rejected"})
            return False

        token = self.context.bearer_token
        if token:
            auth = self.headers.get_all("Authorization") or []
            expected = f"Bearer {token}"
            if len(auth) != 1 or not hmac.compare_digest(auth[0], expected):
                self._send(401, {"error": "bearer authorization required"})
                return False
        if json_body:
            content_types = self.headers.get_all("Content-Type") or []
            media = (
                content_types[0].split(";", 1)[0].strip().lower()
                if len(content_types) == 1
                else ""
            )
            if media != "application/json":
                self._send(415, {"error": "Content-Type application/json required"})
                return False
            if self.headers.get_all("Transfer-Encoding"):
                self._send(400, {"error": "Transfer-Encoding is not supported"})
                return False
        return True

    def _body(self) -> dict[str, Any]:
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise RequestValidationError("exactly one decimal Content-Length is required")
        length = int(lengths[0])
        if length > self.context.max_request_bytes:
            raise PayloadTooLargeHTTP(
                f"request body exceeds {self.context.max_request_bytes} bytes"
            )
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestValidationError("request body ended before Content-Length")
        try:
            value = json.loads(raw or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise RequestValidationError(f"malformed JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise RequestValidationError("JSON body must be an object")
        return value

    def _path_parts(self) -> list[str] | None:
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            return None
        return [part for part in parsed.path.split("/") if part]

    def do_GET(self) -> None:
        if not self._gate():
            return
        parts = self._path_parts()
        if parts == ["health"]:
            readiness = self.context.runtime.readiness()
            self._send(
                200 if readiness["ready"] else 503,
                {
                    "status": "ok" if readiness["ready"] else "degraded",
                    "ok": readiness["ready"],
                    "service": "ResearchWarband",
                    "identity": self.context.runtime.identity(
                        bearer_required=bool(self.context.bearer_token),
                        readiness=readiness,
                    ),
                },
            )
            return
        if parts == ["capabilities"]:
            self._send(200, self.context.runtime.capabilities())
            return
        if parts and len(parts) in {2, 3} and parts[0] == "missions":
            mission = self.context.runtime.store.get(parts[1])
            if mission is None:
                self._send(404, {"error": "mission not found"})
                return
            if len(parts) == 3:
                if parts[2] != "events":
                    self._send(404, {"error": "not found"})
                    return
                self._send(200, {"id": mission.id, "events": mission.events_snapshot()})
                return
            self._send(200, mission.snapshot(event_limit=50))
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._gate(json_body=True):
            return
        parts = self._path_parts()
        if parts is None:
            self._send(404, {"error": "not found"})
            return
        try:
            payload = self._body()
            if parts == ["missions"]:
                mission, created = self.context.runtime.create(payload)
                self._send(
                    202 if created else 200,
                    {
                        "mission_id": mission.id,
                        "status": mission.status,
                        "request_sha256": mission.request_sha256,
                        "idempotent": not created,
                    },
                )
                return
            if len(parts) == 3 and parts[0] == "missions":
                mission = self.context.runtime.store.get(parts[1])
                if mission is None:
                    self._send(404, {"error": "mission not found"})
                    return
                action = parts[2]
                if action == "answer":
                    if set(payload) != ANSWER_FIELDS:
                        raise RequestValidationError("answer body must contain exactly answer")
                    answer = payload.get("answer")
                    if type(answer) is not str or not answer.strip():
                        raise RequestValidationError("answer must be a non-empty string")
                    if len(answer.encode("utf-8")) > self.context.max_answer_bytes:
                        raise PayloadTooLargeHTTP(
                            f"answer exceeds {self.context.max_answer_bytes} bytes"
                        )
                    ok = self.context.runtime.provide_answer(mission, answer)
                    self._send(200 if ok else 409, {"ok": ok, "status": mission.status})
                    return
                if action in {"cancel", "resume"}:
                    if payload:
                        raise RequestValidationError(f"{action} body must be an empty object")
                    if action == "cancel":
                        ok = self.context.runtime.store.cancel(mission.id, expected=mission)
                    else:
                        ok = self.context.runtime.resume(mission)
                    self._send(200 if ok else 409, {"ok": ok, "status": mission.status})
                    return
            self._send(404, {"error": "not found"})
        except PayloadTooLargeHTTP as exc:
            self._send(413, {"error": str(exc)})
        except mission_store.PayloadTooLargeError as exc:
            self._send(413, {"error": str(exc)})
        except mission_store.MissionConflictError as exc:
            self._send(409, {"error": str(exc)})
        except mission_store.MissionExistsError as exc:
            self._send(409, {"error": str(exc)})
        except mission_store.MissionCapacityError as exc:
            self._send(429, {"error": str(exc), "retryable": True})
        except mission_store.MissionPersistenceError as exc:
            self._send(507, {"error": str(exc)})
        except (
            ServiceNotReadyError,
            DeploymentIntegrityError,
            mission_store.RunnerReadinessError,
        ):
            self._send(503, {"error": "ResearchWarband is not ready", "retryable": True})
        except (mission_store.MissionStoreError, OSError) as exc:
            self._send(507, {"error": str(exc)})
        except (RequestValidationError, ValueError, TypeError) as exc:
            self._send(400, {"error": str(exc)})

    def do_OPTIONS(self) -> None:
        if not self._gate():
            return
        self._send(405, {"error": "CORS preflight is not supported"})

    def do_PUT(self) -> None:
        if not self._gate():
            return
        self._send(405, {"error": "method not allowed"})

    def do_DELETE(self) -> None:
        if not self._gate():
            return
        self._send(405, {"error": "method not allowed"})

    def do_HEAD(self) -> None:
        if not self._gate():
            return
        self._send(405, {"error": "method not allowed"})


class PayloadTooLargeHTTP(ValueError):
    pass


def build_server(
    runtime: ResearchServiceRuntime,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    bearer_token: str | None = None,
    max_request_bytes: int | None = None,
    max_response_bytes: int | None = None,
    max_answer_bytes: int | None = None,
) -> ResearchHTTPServer:
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("ResearchWarband must bind to a literal loopback address") from exc
    token = (
        os.environ.get("RESEARCH_WARBAND_BEARER_TOKEN", "")
        if bearer_token is None
        else bearer_token
    )
    runtime.configure_transport_auth(token)
    context = _ServerContext(
        runtime,
        bearer_token=token,
        max_request_bytes=max_request_bytes
        or _env_int("RESEARCH_WARBAND_MAX_REQUEST_BYTES", 2_000_000, 1024),
        max_response_bytes=max_response_bytes
        or _env_int("RESEARCH_WARBAND_MAX_RESPONSE_BYTES", 66_000_000, 4096),
        max_answer_bytes=min(
            max_answer_bytes
            or _env_int("RESEARCH_WARBAND_MAX_ANSWER_BYTES", 8_000, 1),
            runtime.store.max_answer_bytes,
        ),
    )
    server = ResearchHTTPServer((host, port), context)
    try:
        runtime.start()
    except Exception:
        server.server_close()
        raise
    return server


def load_runner(path: str | None = None) -> mission_store.RunnerSpec:
    """Load the production pipeline adapter from ``module:callable``.

    The adapter is deliberately explicit: this lifecycle layer cannot invent
    model/search/fetch dependencies or substitute a fake accepted result.
    """
    target = path or os.environ.get("RESEARCH_WARBAND_RUNNER", "")
    if not target or ":" not in target:
        raise RuntimeError(
            "RESEARCH_WARBAND_RUNNER=module:callable is required for service startup"
        )
    try:
        return mission_store.attest_runner(target)
    except (ImportError, AttributeError, TypeError, RuntimeError, OSError) as exc:
        raise RuntimeError(f"configured ResearchWarband runner is unattestable: {exc}") from exc


def load_readiness_probe(path: str | None = None) -> mission_store.RunnerSpec | None:
    target = (
        os.environ.get("RESEARCH_WARBAND_READINESS_PROBE", "")
        if path is None
        else path
    )
    if not target:
        return None
    if ":" not in target:
        raise RuntimeError(
            "RESEARCH_WARBAND_READINESS_PROBE must be module:callable"
        )
    try:
        return mission_store.attest_runner(target)
    except (ImportError, AttributeError, TypeError, RuntimeError, OSError) as exc:
        raise RuntimeError(
            f"configured readiness probe is unattestable: {exc}"
        ) from exc


def main() -> None:
    store = mission_store.default_store()
    runtime = ResearchServiceRuntime(
        store=store,
        runner=load_runner(),
        readiness_probe=load_readiness_probe(),
    )
    host = os.environ.get("RESEARCH_WARBAND_HOST", "127.0.0.1")
    port = _env_int("RESEARCH_WARBAND_PORT", 7201)
    server = build_server(runtime, host=host, port=port)
    print(f"ResearchWarband listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()


__all__ = [
    "Handler",
    "ResearchHTTPServer",
    "ResearchServiceRuntime",
    "RequestValidationError",
    "SERVICE_INSTANCE_ID",
    "SERVICE_SOURCE_SHA256",
    "build_server",
    "load_runner",
    "main",
]
