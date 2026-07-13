"""Small discriminator for native warband run-package implementations.

This module does not reinterpret either package contract.  It selects the
existing native-code API or the parallel native-research API from the persisted
execution descriptor, then delegates loading and validation unchanged.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_CONTRACT_BYTES = 2_000_000


@dataclass(frozen=True)
class NativeRunAdapter:
    name: str
    run_kind: str
    contract_kind: str
    governor: str
    backend: str
    execution_kind: str
    step_id: str
    directive_filename: str
    ledger_mission_key: str
    service_port: int
    module_name: str
    is_run_name: str
    load_name: str
    validate_name: str

    @property
    def execution(self) -> dict[str, str]:
        return {
            "kind": self.execution_kind,
            "step_id": self.step_id,
            "backend": self.backend,
        }

    @property
    def route_kind(self) -> str:
        return f"{self.name}_run"

    @property
    def leadership_kind(self) -> str:
        return f"{self.name}_leadership"

    @property
    def invalid_error_code(self) -> str:
        return f"{self.route_kind}_invalid"

    @property
    def raw_executor_error(self) -> str:
        # Preserve the established Ceraxia diagnostic verbatim.  Other native
        # backends get an equally explicit failure without pretending they are
        # Skitarii missions.
        if self.name == "native_code":
            return "native code run must use the centralized Skitarii backend router"
        return (
            f"{self.name.replace('_', ' ')} run must use the centralized "
            f"{self.backend} backend router"
        )

    def _call(self, name: str, run_dir: Path) -> Any:
        module = importlib.import_module(self.module_name)
        target = getattr(module, name)
        return target(Path(run_dir))

    def is_run(self, run_dir: Path) -> bool:
        return bool(self._call(self.is_run_name, run_dir))

    def load(self, run_dir: Path) -> dict[str, Any]:
        payload = self._call(self.load_name, run_dir)
        if not isinstance(payload, dict):
            raise ValueError(f"{self.name} loader returned a non-object")
        return payload

    def validate(self, run_dir: Path) -> list[str]:
        errors = self._call(self.validate_name, run_dir)
        if not isinstance(errors, list):
            raise ValueError(f"{self.name} validator returned a non-list")
        return [str(error) for error in errors if str(error)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "run_kind": self.run_kind,
            "contract_kind": self.contract_kind,
            "governor": self.governor,
            "backend": self.backend,
            "execution": self.execution,
            "step_id": self.step_id,
            "directive_filename": self.directive_filename,
            "ledger_mission_key": self.ledger_mission_key,
            "service_port": self.service_port,
            "route_kind": self.route_kind,
            "leadership_kind": self.leadership_kind,
            "invalid_error_code": self.invalid_error_code,
        }


# The code descriptor and function names are intentionally identical to the
# existing native_code_run implementation.  The adapter only delegates.
NATIVE_CODE_ADAPTER = NativeRunAdapter(
    name="native_code",
    run_kind="native_skitarii_code",
    contract_kind="code",
    governor="Ceraxia",
    backend="SkitariiWarband",
    execution_kind="skitarii_mission",
    step_id="skitarii",
    directive_filename="ceraxia_directive.json",
    ledger_mission_key="skitarii_mission",
    service_port=7200,
    module_name="EyeOfTerror.Warmaster.eye_of_terror.native_code_run",
    is_run_name="is_native_code_run",
    load_name="load_native_code_run",
    validate_name="validate_native_code_run_package",
)

NATIVE_RESEARCH_ADAPTER = NativeRunAdapter(
    name="native_research",
    run_kind="native_research_warband",
    contract_kind="research",
    governor="IskandarKhayon",
    backend="ResearchWarband",
    execution_kind="research_warband_mission",
    step_id="research_warband",
    directive_filename="iskandar_directive.json",
    ledger_mission_key="research_warband_mission",
    service_port=7201,
    module_name="EyeOfTerror.Warmaster.eye_of_terror.native_research_run",
    is_run_name="is_native_research_run",
    load_name="load_native_research_run",
    validate_name="validate_native_research_run_package",
)

NATIVE_RUN_ADAPTERS = (NATIVE_CODE_ADAPTER, NATIVE_RESEARCH_ADAPTER)


def _read_contract(run_dir: Path) -> dict[str, Any]:
    path = Path(run_dir) / "contract.json"
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        if path.stat().st_size > MAX_CONTRACT_BYTES:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def native_adapter_for_execution(
    execution: Any,
    *,
    declared: bool = False,
) -> NativeRunAdapter | None:
    """Return an adapter for an exact descriptor or declared native intent.

    ``declared=True`` intentionally recognizes a known native kind/backend even
    when another descriptor field is malformed.  Routers and raw executors can
    then quarantine the package instead of accidentally falling back to the
    legacy worker pipeline.  Validation remains owned by the selected package
    implementation.
    """
    if not isinstance(execution, dict):
        return None
    for adapter in NATIVE_RUN_ADAPTERS:
        if execution == adapter.execution:
            return adapter
    if declared:
        execution_kind = str(execution.get("kind") or "")
        backend = str(execution.get("backend") or "")
        for adapter in NATIVE_RUN_ADAPTERS:
            if execution_kind == adapter.execution_kind or backend == adapter.backend:
                return adapter
    return None


def native_adapter_for_contract(
    contract: Any,
    *,
    declared: bool = False,
) -> NativeRunAdapter | None:
    if not isinstance(contract, dict):
        return None
    adapter = native_adapter_for_execution(contract.get("execution"), declared=declared)
    if adapter is not None:
        return adapter
    # Iskandar no longer has a generic worker-plan executor.  Claim legacy
    # research contracts at the raw-executor boundary so a hand-crafted or
    # historical package cannot silently revive the deleted ten-worker path.
    if (
        declared
        and str(contract.get("kind") or "").lower() == NATIVE_RESEARCH_ADAPTER.contract_kind
        and str(contract.get("assigned_governor") or "") == NATIVE_RESEARCH_ADAPTER.governor
    ):
        return NATIVE_RESEARCH_ADAPTER
    return None


def native_adapter_for_run(
    run_dir: Path,
    *,
    declared: bool = True,
) -> NativeRunAdapter | None:
    return native_adapter_for_contract(_read_contract(Path(run_dir)), declared=declared)


def native_adapter_for_route(route: Any) -> NativeRunAdapter | None:
    """Recover the exact adapter selected by an executor route payload."""
    if not isinstance(route, dict) or route.get("native") is not True:
        return None
    return native_adapter_for_execution(route.get("execution"), declared=True)


def is_native_warband_run(run_dir: Path) -> bool:
    """Recognize declared native packages, including malformed ones to fail closed."""
    return native_adapter_for_run(run_dir, declared=True) is not None


def load_native_warband_run(run_dir: Path) -> dict[str, Any]:
    adapter = native_adapter_for_run(run_dir, declared=True)
    if adapter is None:
        raise ValueError("run does not declare a known native warband backend")
    return adapter.load(run_dir)


def validate_native_warband_run(run_dir: Path) -> list[str]:
    adapter = native_adapter_for_run(run_dir, declared=True)
    if adapter is None:
        return ["run does not declare a known native warband backend"]
    try:
        return adapter.validate(run_dir)
    except (ImportError, AttributeError, OSError, ValueError) as exc:
        return [f"{adapter.name} validation unavailable: {exc}"]


__all__ = [
    "MAX_CONTRACT_BYTES",
    "NATIVE_CODE_ADAPTER",
    "NATIVE_RESEARCH_ADAPTER",
    "NATIVE_RUN_ADAPTERS",
    "NativeRunAdapter",
    "is_native_warband_run",
    "load_native_warband_run",
    "native_adapter_for_contract",
    "native_adapter_for_execution",
    "native_adapter_for_run",
    "native_adapter_for_route",
    "validate_native_warband_run",
]
