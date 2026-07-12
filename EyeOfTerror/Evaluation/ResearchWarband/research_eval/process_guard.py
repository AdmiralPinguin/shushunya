"""OS-owned process-tree boundary for the external evaluator subject."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import signal
import time
from typing import Any, Callable


class ProcessGuardError(RuntimeError):
    pass


def enter_isolated_group(ready: Any, gate: Any) -> None:
    """Child-side handshake; no subject code runs before the parent owns the tree."""

    if os.name != "nt":
        os.setsid()
    ready.set()
    if not gate.wait(30):
        raise ProcessGuardError("parent did not authorize isolated subject startup")


class _WindowsJob:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
                )
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._accounting = Accounting
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32 = kernel32
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        )
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p
        )
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.handle = kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        limits = ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(self.handle)
            self.handle = None
            raise OSError(error, "SetInformationJobObject failed")

    def assign(self, process: multiprocessing.Process) -> None:
        if not self._kernel32.AssignProcessToJobObject(
            self.handle, self._wintypes.HANDLE(int(process.sentinel))
        ):
            raise OSError(self._ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def active_count(self) -> int:
        info = self._accounting()
        if not self._kernel32.QueryInformationJobObject(
            self.handle, 1, self._ctypes.byref(info), self._ctypes.sizeof(info), None
        ):
            raise OSError(self._ctypes.get_last_error(), "QueryInformationJobObject failed")
        return int(info.ActiveProcesses)

    def terminate(self) -> None:
        if not self._kernel32.TerminateJobObject(self.handle, 1):
            error = self._ctypes.get_last_error()
            if error:
                raise OSError(error, "TerminateJobObject failed")

    def close(self) -> None:
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


class KillableProcessTree:
    """Persistent controller process plus every descendant it creates."""

    def __init__(self, process: multiprocessing.Process, job: _WindowsJob | None) -> None:
        self.process = process
        self._job = job

    @classmethod
    def spawn(
        cls,
        context: multiprocessing.context.BaseContext,
        *,
        target: Callable[..., Any],
        args: tuple[Any, ...],
        name: str,
    ) -> "KillableProcessTree":
        ready = context.Event()
        gate = context.Event()
        process = context.Process(
            target=target,
            args=(*args, ready, gate),
            name=name,
            daemon=False,
        )
        job: _WindowsJob | None = None
        try:
            process.start()
            if os.name == "nt":
                job = _WindowsJob()
                job.assign(process)
            gate.set()
            if not ready.wait(10):
                raise ProcessGuardError("isolated subject did not complete startup handshake")
            return cls(process, job)
        except BaseException:
            gate.set()
            if job is not None:
                try:
                    job.terminate()
                except OSError:
                    pass
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=1)
            if job is not None:
                job.close()
            raise

    @staticmethod
    def _linux_group_count(group_id: int) -> int | None:
        proc = Path("/proc")
        if not proc.is_dir():
            return None
        count = 0
        try:
            entries = list(proc.iterdir())
        except OSError:
            return None
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "stat").read_text(encoding="ascii", errors="strict")
                close = raw.rfind(")")
                fields = raw[close + 2 :].split()
                if close < 0 or len(fields) < 3:
                    return None
                if int(fields[2]) == group_id:
                    count += 1
            except FileNotFoundError:
                continue
            except (PermissionError, UnicodeError, ValueError, OSError):
                return None
        return count

    def active_count(self) -> int | None:
        if os.name == "nt":
            try:
                return self._job.active_count() if self._job is not None else None
            except OSError:
                return None
        if self.process.pid is None:
            return 0
        return self._linux_group_count(self.process.pid)

    def controller_only(self) -> bool:
        return self.process.is_alive() and self.active_count() == 1

    def terminate(self, *, grace_seconds: float = 1.0) -> bool:
        if os.name == "nt" and self._job is not None:
            try:
                self._job.terminate()
            except OSError:
                pass
        elif self.process.pid is not None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError:
                if self.process.is_alive():
                    self.process.terminate()
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while time.monotonic() < deadline:
            self.process.join(timeout=0.02)
            active = self.active_count()
            if active == 0:
                break
        if self.active_count() != 0:
            if os.name == "nt" and self._job is not None:
                try:
                    self._job.terminate()
                except OSError:
                    pass
            elif self.process.pid is not None:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    if self.process.is_alive() and hasattr(self.process, "kill"):
                        self.process.kill()
        self.process.join(timeout=max(1.0, grace_seconds))
        deadline = time.monotonic() + max(1.0, grace_seconds)
        while time.monotonic() < deadline and self.active_count() != 0:
            time.sleep(0.01)
        clean = not self.process.is_alive() and self.active_count() == 0
        if self._job is not None:
            self._job.close()
            self._job = None
        return clean


__all__ = [
    "KillableProcessTree",
    "ProcessGuardError",
    "enter_isolated_group",
]
