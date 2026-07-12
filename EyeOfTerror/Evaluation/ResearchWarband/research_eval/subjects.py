"""Subject boundary. The runner never passes oracle data through this interface."""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen


@dataclass(frozen=True)
class SubjectExecution:
    result: dict[str, Any]
    terminal: bool = True
    cleanup_proven: bool = True
    cleanup_detail: str = "cleanup proven"


class SubjectAdapter(ABC):
    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def execute(self, payload: dict[str, Any], *, timeout_sec: int) -> SubjectExecution:
        raise NotImplementedError


class FakeSubjectAdapter(SubjectAdapter):
    """Deterministic replay subject used to harden the evaluator before live models."""

    def __init__(
        self,
        results: dict[str, dict[str, Any]],
        *,
        identity: dict[str, Any] | None = None,
        fail_tasks: set[str] | None = None,
        unclean_tasks: set[str] | None = None,
        end_identity: dict[str, Any] | None = None,
        exercise_fixture_gateway: bool = True,
    ) -> None:
        self.results = copy.deepcopy(results)
        self.recorded_payloads: list[dict[str, Any]] = []
        self.fail_tasks = set(fail_tasks or ())
        self.unclean_tasks = set(unclean_tasks or ())
        self._health_calls = 0
        self.identity = identity or {
            "instance_id": "fake-research-subject-1",
            "source_sha256": "0" * 64,
            "model": "deterministic-replay",
            "standalone_test_mode": True,
        }
        self.end_identity = end_identity
        self.exercise_fixture_gateway = bool(exercise_fixture_gateway)

    def _read_replayed_sources(self, payload: dict[str, Any], result: dict[str, Any]) -> None:
        """Make the replay prove the same source access expected from a real SUT.

        Correct hashes in a returned ledger are not proof that the subject ever
        acquired the private fixture bytes. The fixture server access log is an
        external observation, so the deterministic replay must exercise it too.
        """
        if not self.exercise_fixture_gateway:
            return
        ledger = result.get("ledger") if isinstance(result.get("ledger"), dict) else {}
        sources = ledger.get("sources") if isinstance(ledger.get("sources"), list) else []
        required = {
            str(source.get("source_id") or "")
            for source in sources
            if isinstance(source, dict) and str(source.get("source_id") or "")
        }
        if not required:
            return
        gateway = str(payload.get("source_gateway_url") or "").rstrip("/")
        if not gateway:
            raise ValueError("fixture gateway is required for replayed source access")
        for source_id in sorted(required):
            # Public smoke fixture routes deliberately mirror their synthetic
            # source ids. This is a harness subject, not a model/search oracle;
            # the external runner independently proves the required route was
            # accessed and checks its bytes.
            slug = source_id.removeprefix("source-")
            with urlopen(gateway + "/documents/" + quote(slug, safe="-"), timeout=5) as response:
                response.read()

    def health(self) -> dict[str, Any]:
        self._health_calls += 1
        identity = self.end_identity if self._health_calls > 1 and self.end_identity is not None else self.identity
        return {"status": "ok", "service": "FakeResearchSubject", "identity": copy.deepcopy(identity)}

    def execute(self, payload: dict[str, Any], *, timeout_sec: int) -> SubjectExecution:
        del timeout_sec
        captured = copy.deepcopy(payload)
        self.recorded_payloads.append(captured)
        task_id = str(payload.get("task_id") or "")
        case_id = task_id.removeprefix("eval-")
        if case_id in self.fail_tasks:
            raise RuntimeError(f"injected subject failure for {case_id}")
        if case_id not in self.results:
            raise KeyError(f"no fake result for {case_id}")
        result = copy.deepcopy(self.results[case_id])
        result["mission_id"] = task_id
        self._read_replayed_sources(payload, result)
        clean = case_id not in self.unclean_tasks
        return SubjectExecution(
            result=result,
            terminal=True,
            cleanup_proven=clean,
            cleanup_detail="cleanup proven" if clean else "injected cleanup failure",
        )
