"""Execution backends for Skitarii fighters.

A fighter's tools never touch the host directly: they go through an Executor.
- VmExecutor runs everything inside the sandbox VM over SSH (the real isolation).
- LocalExecutor exists only for harness self-tests with harmless tasks.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any


class ExecResult(dict):
    @classmethod
    def make(cls, returncode: int, stdout: str, stderr: str) -> "ExecResult":
        return cls(returncode=returncode, stdout=stdout[-20_000:], stderr=stderr[-20_000:])


class LocalExecutor:
    """Confined-to-workdir local execution. ONLY for harness smoke tests."""

    def __init__(self, workdir: Path):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, rel: str) -> Path:
        path = (self.workdir / rel).resolve()
        if not str(path).startswith(str(self.workdir.resolve())):
            raise ValueError(f"path escapes workdir: {rel}")
        return path

    def bash(self, command: str, timeout: int = 120) -> ExecResult:
        try:
            proc = subprocess.run(
                ["bash", "-c", command], cwd=self.workdir,
                capture_output=True, text=True, timeout=timeout,
            )
            return ExecResult.make(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as exc:
            return ExecResult.make(124, str(exc.stdout or ""), f"timeout after {timeout}s")

    def read_file(self, rel: str, max_bytes: int = 60_000,
                  offset: int = 0, limit: int = 0) -> str:
        text = self._safe_path(rel).read_text(encoding="utf-8", errors="replace")
        if offset or limit:
            lines = text.splitlines()
            end = (offset + limit) if limit else len(lines)
            text = "\n".join(lines[offset:end])
        return text[:max_bytes]

    def child(self, name: str) -> "LocalExecutor":
        return LocalExecutor(self.workdir.parent / f"{self.workdir.name}_wt_{name}")

    def bash_background(self, command: str) -> dict[str, Any]:
        import uuid
        (self.workdir / ".bg").mkdir(exist_ok=True)
        log = f".bg/{uuid.uuid4().hex[:8]}.log"
        with open(self.workdir / log, "w") as fh:
            proc = subprocess.Popen(["bash", "-c", command], cwd=self.workdir,
                                    stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
        return {"pid": str(proc.pid), "log": log, "started": True}

    def write_file(self, rel: str, content: str) -> None:
        path = self._safe_path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def fetch_artifact(self, rel: str) -> bytes:
        return self._safe_path(rel).read_bytes()


class VmExecutor:
    """All tools run inside the sandbox VM via ssh/scp. Physical isolation:
    the guest has no access to the host filesystem at all."""

    def __init__(self, host: str = "127.0.0.1", port: int = 2222,
                 user: str = "skitarii", key: str = "", workdir: str = "/home/skitarii/work"):
        self.host, self.port, self.user, self.key = host, port, user, key
        self.workdir = workdir

    def _ssh_base(self) -> list[str]:
        cmd = ["ssh", "-p", str(self.port),
               "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
               "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"]
        if self.key:
            cmd += ["-i", self.key]
        cmd.append(f"{self.user}@{self.host}")
        return cmd

    def bash(self, command: str, timeout: int = 120) -> ExecResult:
        remote = f"mkdir -p {shlex.quote(self.workdir)} && cd {shlex.quote(self.workdir)} && {command}"
        try:
            proc = subprocess.run(
                self._ssh_base() + [remote],
                capture_output=True, text=True, timeout=timeout + 15,
            )
            return ExecResult.make(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as exc:
            return ExecResult.make(124, str(exc.stdout or ""), f"timeout after {timeout}s")

    def read_file(self, rel: str, max_bytes: int = 60_000,
                  offset: int = 0, limit: int = 0) -> str:
        q = shlex.quote(rel)
        if offset or limit:
            end = (offset + limit) if limit else "$"
            result = self.bash(f"sed -n '{offset + 1},{end}p' {q} | head -c {max_bytes}")
        else:
            result = self.bash(f"head -c {max_bytes} {q}")
        if result["returncode"] != 0:
            raise FileNotFoundError(result["stderr"] or rel)
        return result["stdout"]

    def bash_background(self, command: str) -> dict[str, Any]:
        """Start a long-running process (server, watcher) detached in the VM and return
        its pid + log file. Use bash to poll it (curl, logs) and to kill it later."""
        import uuid
        log = f".bg/{uuid.uuid4().hex[:8]}.log"
        remote = (f"mkdir -p .bg && setsid bash -c {shlex.quote(command)} > {log} 2>&1 & "
                  f"echo $!")
        r = self.bash(remote, timeout=30)
        pid = (r["stdout"] or "").strip().split("\n")[-1]
        return {"pid": pid, "log": log, "started": r["returncode"] == 0}

    def write_file(self, rel: str, content: str) -> None:
        # heredoc-free write: pipe through stdin to survive any content
        remote = (f"mkdir -p {shlex.quote(self.workdir)} && cd {shlex.quote(self.workdir)} && "
                  f"mkdir -p $(dirname {shlex.quote(rel)}) && cat > {shlex.quote(rel)}")
        proc = subprocess.run(self._ssh_base() + [remote], input=content,
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise IOError(proc.stderr or f"write failed: {rel}")

    def fetch_artifact(self, rel: str) -> bytes:
        remote = f"cd {shlex.quote(self.workdir)} && cat {shlex.quote(rel)}"
        proc = subprocess.run(self._ssh_base() + [remote], capture_output=True, timeout=60)
        if proc.returncode != 0:
            raise FileNotFoundError(proc.stderr.decode(errors="replace") or rel)
        return proc.stdout

    def child(self, name: str) -> "VmExecutor":
        safe = "".join(c for c in name if c.isalnum() or c in "-_")
        return VmExecutor(self.host, self.port, self.user, self.key,
                          workdir=f"{self.workdir}_wt_{safe}")

    def alive(self) -> bool:
        try:
            return self.bash("true", timeout=10)["returncode"] == 0
        except Exception:
            return False
