"""Subject boundary. The runner never passes oracle data through this interface."""

from __future__ import annotations

import copy
import multiprocessing
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SubjectExecution:
    result: dict[str, Any]
    terminal: bool = True
    cleanup_proven: bool = True
    cleanup_detail: str = "cleanup proven"


class SubjectAdapter(ABC):
    """Spawn-pickleable adapter executed inside a killable worker process."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def execute(self, payload: dict[str, Any], *, timeout_sec: int) -> SubjectExecution:
        raise NotImplementedError


class SubjectBoundaryError(RuntimeError):
    """The isolated subject process failed or became unavailable."""


class SubjectTimeoutError(SubjectBoundaryError):
    """The subject exceeded an externally enforced wall-clock deadline."""


def _subject_worker(connection: Any, subject: SubjectAdapter) -> None:
    """Run adapter calls in a process the evaluator can forcibly terminate."""

    try:
        while True:
            command = connection.recv()
            if not isinstance(command, tuple) or len(command) != 3:
                raise ValueError("invalid subject-boundary command")
            operation, payload, timeout_sec = command
            if operation == "stop":
                connection.send(("ok", None))
                return
            try:
                if operation == "health":
                    value = subject.health()
                elif operation == "execute":
                    value = subject.execute(payload, timeout_sec=timeout_sec)
                else:
                    raise ValueError(f"unknown subject-boundary operation: {operation}")
                connection.send(("ok", value))
            except BaseException as exc:  # the parent must receive every adapter failure
                connection.send(
                    (
                        "error",
                        {
                            "type": type(exc).__name__,
                            "message": str(exc)[:500],
                        },
                    )
                )
    except (EOFError, BrokenPipeError, OSError):
        return
    finally:
        connection.close()


class SubjectProcessBoundary:
    """Persistent spawn-process boundary with kill-on-timeout semantics.

    A generic Python call cannot be safely cancelled in a thread.  Requiring the
    adapter to be spawn-pickleable gives the runner a concrete OS process it can
    terminate, while keeping adapter identity and lifecycle state across calls.
    """

    def __init__(self, subject: SubjectAdapter) -> None:
        if not isinstance(subject, SubjectAdapter):
            raise TypeError("subject must implement SubjectAdapter")
        self.subject = subject
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        self._connection = parent
        self._process = context.Process(
            target=_subject_worker,
            args=(child, subject),
            name="research-eval-subject",
            daemon=True,
        )
        try:
            self._process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        self._closed = False

    def _terminate(self) -> None:
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=1)
        if self._process.is_alive() and hasattr(self._process, "kill"):
            self._process.kill()
            self._process.join(timeout=1)

    def _call(
        self,
        operation: str,
        payload: dict[str, Any] | None,
        *,
        timeout_sec: float,
    ) -> Any:
        if self._closed or not self._process.is_alive():
            raise SubjectBoundaryError("isolated subject process is unavailable")
        try:
            self._connection.send((operation, payload, timeout_sec))
        except (BrokenPipeError, EOFError, OSError) as exc:
            self._terminate()
            raise SubjectBoundaryError("cannot submit call to isolated subject") from exc
        if not self._connection.poll(timeout_sec):
            self._terminate()
            raise SubjectTimeoutError(
                f"subject {operation} exceeded {timeout_sec:g} seconds"
            )
        try:
            kind, value = self._connection.recv()
        except (BrokenPipeError, EOFError, OSError) as exc:
            self._terminate()
            raise SubjectBoundaryError("isolated subject exited without a result") from exc
        if kind == "error":
            error_type = str((value or {}).get("type") or "SubjectError")
            message = str((value or {}).get("message") or "")
            raise SubjectBoundaryError(f"{error_type}: {message}")
        if kind != "ok":
            raise SubjectBoundaryError("isolated subject returned an invalid envelope")
        return value

    def health(self, *, timeout_sec: float) -> dict[str, Any]:
        value = self._call("health", None, timeout_sec=timeout_sec)
        if not isinstance(value, dict):
            raise SubjectBoundaryError("subject health returned a non-object")
        return value

    def execute(
        self,
        payload: dict[str, Any],
        *,
        timeout_sec: int,
    ) -> SubjectExecution:
        value = self._call("execute", payload, timeout_sec=float(timeout_sec))
        if not isinstance(value, SubjectExecution):
            raise SubjectBoundaryError("subject execute returned an invalid envelope")
        return value

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process.is_alive():
            try:
                self._connection.send(("stop", None, 1.0))
                if self._connection.poll(1.0):
                    self._connection.recv()
            except (BrokenPipeError, EOFError, OSError):
                pass
        self._process.join(timeout=1)
        self._terminate()
        self._connection.close()

    def __enter__(self) -> "SubjectProcessBoundary":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()


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
        source_request_method: str = "GET",
    ) -> None:
        self.results = copy.deepcopy(results)
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
        if source_request_method not in {"GET", "HEAD"}:
            raise ValueError("source_request_method must be GET or HEAD")
        self.source_request_method = source_request_method

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
            request = Request(
                gateway + "/documents/" + quote(slug, safe="-"),
                method=self.source_request_method,
            )
            with urlopen(request, timeout=5) as response:
                response.read()

    def health(self) -> dict[str, Any]:
        self._health_calls += 1
        identity = self.end_identity if self._health_calls > 1 and self.end_identity is not None else self.identity
        return {"status": "ok", "service": "FakeResearchSubject", "identity": copy.deepcopy(identity)}

    def execute(self, payload: dict[str, Any], *, timeout_sec: int) -> SubjectExecution:
        del timeout_sec
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
