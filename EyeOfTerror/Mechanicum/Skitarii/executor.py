"""Execution backends for Skitarii fighters.

A fighter's tools never touch the host directly: they go through an Executor.
- VmExecutor runs everything inside the sandbox VM over SSH (the real isolation).
- LocalExecutor exists only for harness self-tests with harmless tasks.
"""
from __future__ import annotations

import errno
import hashlib
import posixpath
import os
import shlex
import signal
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable


_BOUNDARY_ACQUIRE_LOCK = threading.Lock()
_QUARANTINED_BOUNDARIES: list["_ProcessBoundaryLease"] = []
MAX_COMMAND_OUTPUT_BYTES = int(os.environ.get("SKITARII_MAX_COMMAND_OUTPUT_BYTES", "4000000"))
# PERSISTENT VM: the fighter is SUPPOSED to accumulate toolchains and caches
# (an Android SDK alone is ~1GB / ~100k files), so the old 1GB/50k guards —
# sized for the ephemeral tmpfs era — would veto every command on a healthy
# sandbox. The VM disk is 40G with ~6G used by the OS; cap near the disk.
MAX_SANDBOX_STORAGE_BYTES = int(os.environ.get("SKITARII_MAX_STORAGE_BYTES", "32000000000"))
MAX_SANDBOX_FILES = int(os.environ.get("SKITARII_MAX_STORAGE_FILES", "2000000"))
BOUNDARY_HELPER_VERSION = "skitarii-boundary-v3"
BOUNDARY_HELPER_SHA256 = "3d41c67e619aa0260201137094b25c1d1bfcf9167916bfecd81cfb4a23aafda2"
_ARTIFACT_POLICY_ERRNOS = {
    errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EISDIR,
    errno.EACCES, errno.EPERM, errno.EINVAL, errno.ENXIO, errno.ENODEV,
}


_ATOMIC_REGULAR_READ_SCRIPT = r"""
import errno
import os
import stat
import sys

path = sys.argv[1]
limit = int(sys.argv[2])
parts = path.split('/')
fds = []
try:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    directory_fd = os.open('.', directory_flags)
    fds.append(directory_fd)
    for part in parts[:-1]:
        directory_fd = os.open(part, directory_flags, dir_fd=directory_fd)
        fds.append(directory_fd)
    file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
    fds.append(file_fd)
    info = os.fstat(file_fd)
    if not stat.S_ISREG(info.st_mode):
        raise OSError(errno.EINVAL, 'artifact is not a regular file')
    remaining = limit + 1
    chunks = []
    while remaining:
        chunk = os.read(file_fd, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b''.join(chunks)
    while data:
        written = os.write(1, data)
        data = data[written:]
except OSError as exc:
    expected = {
        errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EISDIR,
        errno.EACCES, errno.EPERM, errno.EINVAL, errno.ENXIO, errno.ENODEV,
    }
    sys.stderr.write(str(exc))
    raise SystemExit(3 if exc.errno in expected else 4)
finally:
    for descriptor in reversed(fds):
        try:
            os.close(descriptor)
        except OSError:
            pass
"""


def _systemd_exec_literal(value: str) -> str:
    """Preserve shell dollars through systemd's ExecStart environment expansion."""
    # Transient services apply the same ${NAME}/$NAME expansion as ExecStart=.
    # Unknown braced names are replaced with an empty string, which corrupts
    # legitimate bash forms such as ${#value} and ${value#prefix}.  systemd's
    # documented literal-dollar spelling is $$.
    return value.replace("$", "$$")


class _ProcessBoundaryLease:
    """A host-side inter-process flock held for one whole untrusted lifecycle."""

    def __init__(self, handle):
        self.handle = handle
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self._released = True


class _BoundaryRuntimeState:
    """Mutable launch/cancel state shared by a mission executor and its children."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.cancel_generation = 0
        self.poisoned = False
        self.local_processes: dict[str, subprocess.Popen] = {}
        self.units_lock = threading.Lock()
        self.units: set[str] = set()


def _quarantine_boundary(lease: "_ProcessBoundaryLease" | None) -> None:
    """Keep a failed-cleanup lock alive until the service is restarted."""
    if lease is not None and lease not in _QUARANTINED_BOUNDARIES:
        _QUARANTINED_BOUNDARIES.append(lease)


def _run_capped_process(argv: list[str], *, timeout: int,
                        max_output_bytes: int = MAX_COMMAND_OUTPUT_BYTES,
                        spawn_lock: threading.RLock | None = None,
                        pre_spawn: Callable[[], bool] | None = None,
                        on_started: Callable[[subprocess.Popen], None] | None = None) -> ExecResult:
    """Stream child output with a hard host-memory bound."""
    if spawn_lock is not None:
        spawn_lock.acquire()
    try:
        if pre_spawn is not None and not pre_spawn():
            return ExecResult.make(125, "", "sandbox command was cancelled before launch")
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if on_started is not None:
            on_started(proc)
    finally:
        if spawn_lock is not None:
            spawn_lock.release()
    tail_limit = 20_000
    stdout_tail = bytearray()
    stderr_tail = bytearray()
    total = 0
    total_lock = threading.Lock()
    overflow = threading.Event()

    def reader(stream, tail: bytearray) -> None:
        nonlocal total
        try:
            while True:
                chunk = stream.read(65_536)
                if not chunk:
                    return
                with total_lock:
                    total += len(chunk)
                    if total > max_output_bytes:
                        overflow.set()
                    tail.extend(chunk)
                    if len(tail) > tail_limit:
                        del tail[:-tail_limit]
                if overflow.is_set():
                    return
        finally:
            try:
                stream.close()
            except OSError:
                pass

    threads = [
        threading.Thread(target=reader, args=(proc.stdout, stdout_tail), daemon=True),
        threading.Thread(target=reader, args=(proc.stderr, stderr_tail), daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    timed_out = False
    while proc.poll() is None:
        if overflow.is_set() or time.monotonic() >= deadline:
            timed_out = not overflow.is_set()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                proc.kill()
            break
        time.sleep(0.02)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=2)
    stdout = bytes(stdout_tail).decode("utf-8", errors="replace")
    stderr = bytes(stderr_tail).decode("utf-8", errors="replace")
    if overflow.is_set():
        return ExecResult.make(125, stdout, f"{stderr}\noutput exceeded {max_output_bytes} bytes")
    if timed_out:
        return ExecResult.make(124, stdout, f"{stderr}\ntimeout after {timeout}s")
    return ExecResult.make(int(proc.returncode or 0), stdout, stderr)


class ExecResult(dict):
    @classmethod
    def make(cls, returncode: int, stdout: str, stderr: str) -> "ExecResult":
        return cls(returncode=returncode, stdout=stdout[-20_000:], stderr=stderr[-20_000:])


def _regular_artifact_parts(rel: str) -> tuple[str, ...]:
    value = str(rel)
    path = PurePosixPath(value)
    if (
        not value or "\\" in value or any(ord(char) < 32 for char in value)
        or path.is_absolute() or not path.parts or ".." in path.parts
        or path.parts[0].endswith(":") or path.as_posix() in {"", "."}
    ):
        raise ValueError(f"unsafe artifact path: {rel}")
    return tuple(path.parts)


def _regular_artifact_limit(max_bytes: int) -> int:
    try:
        limit = int(max_bytes)
    except (TypeError, ValueError) as exc:
        raise ValueError("artifact byte limit must be an integer") from exc
    if limit < 0 or limit >= MAX_COMMAND_OUTPUT_BYTES:
        raise ValueError("artifact byte limit is outside the bounded read contract")
    return limit


def _read_regular_artifact_local(root: Path, rel: str, max_bytes: int) -> bytes:
    """Open every path component without following links, then read the same leaf fd."""
    parts = _regular_artifact_parts(rel)
    limit = _regular_artifact_limit(max_bytes)
    required_flags = ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required_flags):
        raise OSError(errno.ENOTSUP, "atomic no-follow artifact reads are unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptors: list[int] = []
    try:
        directory_fd = os.open(root, directory_flags)
        descriptors.append(directory_fd)
        for part in parts[:-1]:
            directory_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            descriptors.append(directory_fd)
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        descriptors.append(file_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "artifact is not a regular file")
        remaining = limit + 1
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(file_fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    except OSError as exc:
        if exc.errno in _ARTIFACT_POLICY_ERRNOS:
            raise ValueError(f"artifact is missing, non-regular, or linked: {exc}") from exc
        raise
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


class ProcessBoundaryBusy(RuntimeError):
    """Another process owns the single sandbox mission lifecycle lock."""


class ProcessBoundaryQuarantined(RuntimeError):
    """Remote boundary initialization is uncertain and its lease is quarantined."""


class _LocalRuntimeState:
    """Foreground-independent process ownership shared by local verifier siblings."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.background_processes: list[subprocess.Popen] = []


class LocalExecutor:
    """Confined-to-workdir local execution. ONLY for harness smoke tests."""

    def __init__(self, workdir: Path, *, command_env: dict[str, str] | None = None,
                 _runtime_state: _LocalRuntimeState | None = None):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._local_runtime = _runtime_state or _LocalRuntimeState()
        # Retain the old attribute for tests/callers while keeping one shared list.
        self._background_processes = self._local_runtime.background_processes
        self._owned_child_workdirs: set[Path] = set()
        self.command_env = dict(command_env or {})

    def _safe_path(self, rel: str) -> Path:
        root = self.workdir.resolve()
        path = (self.workdir / rel).resolve()
        if path == root or root not in path.parents:
            raise ValueError(f"path escapes workdir: {rel}")
        return path

    def bash(self, command: str, timeout: int = 120) -> ExecResult:
        try:
            env = os.environ.copy()
            env.update(self.command_env)
            proc = subprocess.run(
                ["bash", "-c", command], cwd=self.workdir,
                capture_output=True, text=True, timeout=timeout, env=env,
            )
            return ExecResult.make(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
            return ExecResult.make(124, stdout, f"timeout after {timeout}s")

    def read_file(self, rel: str, max_bytes: int = 60_000,
                  offset: int = 0, limit: int = 0) -> str:
        text = self._safe_path(rel).read_text(encoding="utf-8", errors="replace")
        if offset or limit:
            lines = text.splitlines()
            end = (offset + limit) if limit else len(lines)
            text = "\n".join(lines[offset:end])
        return text[:max_bytes]

    def read_regular_artifact(self, rel: str, max_bytes: int) -> bytes:
        """Atomically read one bounded regular artifact without following links."""
        return _read_regular_artifact_local(self.workdir, rel, max_bytes)

    def child(self, name: str) -> "LocalExecutor":
        child_workdir = self.workdir.parent / f"mission-{uuid.uuid4().hex[:16]}"
        self._owned_child_workdirs.add(child_workdir)
        return LocalExecutor(
            child_workdir,
            command_env=self.command_env,
            _runtime_state=self._local_runtime,
        )

    def bash_background(self, command: str) -> dict[str, Any]:
        import uuid
        with self._local_runtime.lock:
            (self.workdir / ".git" / "skitarii-bg").mkdir(parents=True, exist_ok=True)
            log = f".git/skitarii-bg/{uuid.uuid4().hex[:8]}.log"
            with open(self.workdir / log, "w") as fh:
                proc = subprocess.Popen(
                    ["bash", "-c", command], cwd=self.workdir,
                    stdout=fh, stderr=subprocess.STDOUT, start_new_session=True,
                )
            self._background_processes[:] = [
                existing for existing in self._background_processes
                if existing.poll() is None
            ]
            self._background_processes.append(proc)
        return {"pid": str(proc.pid), "log": log, "started": True}

    def close(self) -> None:
        """Reap local test helpers so background smoke tests do not leak processes."""
        with self._local_runtime.lock:
            for proc in list(self._background_processes):
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        proc.kill()
                    proc.wait(timeout=2)
            self._background_processes.clear()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def write_file(self, rel: str, content: str) -> None:
        path = self._safe_path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_bytes(self, rel: str, content: bytes) -> None:
        path = self._safe_path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def fetch_artifact(self, rel: str, max_bytes: int | None = None) -> bytes:
        content = self._safe_path(rel).read_bytes()
        return content if max_bytes is None else content[:max_bytes + 1]


class VmExecutor:
    """All tools run inside the sandbox VM via ssh/scp. Physical isolation:
    the guest has no access to the host filesystem at all."""

    def __init__(self, host: str = "127.0.0.1", port: int = 2222,
                 user: str = "skitarii", key: str = "", workdir: str = "/home/skitarii/work",
                 *, mission_marker: str | None = None,
                 command_env: dict[str, str] | None = None,
                 process_boundary: bool = False,
                 boundary_runtime_sec: int = 7200,
                 boundary_process_baseline: dict[str, str] | None = None,
                 boundary_auth_state: str = "",
                 boundary_lease: _ProcessBoundaryLease | None = None,
                 boundary_release_on_cleanup: bool = True,
                 _boundary_runtime_state: _BoundaryRuntimeState | None = None):
        self.host, self.port, self.user, self.key = host, port, user, key
        self.workdir = workdir
        self.mission_marker = mission_marker or f"skitarii-{uuid.uuid4().hex}"
        self.command_env = dict(command_env or {})
        self.process_boundary = bool(process_boundary)
        self.boundary_runtime_sec = max(60, int(boundary_runtime_sec))
        marker_hash = hashlib.sha256(self.mission_marker.encode("utf-8")).hexdigest()[:24]
        self.process_unit_prefix = f"skitarii-mission-{marker_hash}"
        self.process_slice = f"{self.process_unit_prefix}.slice"
        self.boundary_process_baseline = (
            dict(boundary_process_baseline)
            if boundary_process_baseline is not None else None
        )
        self.boundary_auth_state = str(boundary_auth_state or "")
        self.boundary_lease = boundary_lease
        self.boundary_release_on_cleanup = bool(boundary_release_on_cleanup)
        self._boundary_runtime = _boundary_runtime_state or _BoundaryRuntimeState()
        self._boundary_state_lock = self._boundary_runtime.lock
        self._current_local_processes = self._boundary_runtime.local_processes
        self._current_units_lock = self._boundary_runtime.units_lock
        self._current_units = self._boundary_runtime.units

    @property
    def boundary_poisoned(self) -> bool:
        return self._boundary_runtime.poisoned

    @boundary_poisoned.setter
    def boundary_poisoned(self, value: bool) -> None:
        self._boundary_runtime.poisoned = bool(value)

    def _ssh_base(self) -> list[str]:
        cmd = ["ssh", "-p", str(self.port),
               "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
               "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"]
        if self.key:
            cmd += ["-i", self.key]
        cmd.append(f"{self.user}@{self.host}")
        return cmd

    @staticmethod
    def _safe_rel(rel: str) -> str:
        value = str(rel).replace("\\", "/")
        path = PurePosixPath(value)
        if (not value or "\x00" in value or path.is_absolute() or not path.parts
                or any(part == ".." for part in path.parts) or path.parts[0].endswith(":")):
            raise ValueError(f"path escapes workdir: {rel}")
        return path.as_posix()

    def _environment_prefix(self) -> str:
        exported = {"SKITARII_MISSION_MARKER": self.mission_marker, **self.command_env}
        return " ".join(
            f"export {key}={shlex.quote(str(value))};"
            for key, value in exported.items()
        )

    def _next_process_unit(self, kind: str) -> str:
        return f"{self.process_unit_prefix}-{kind}-{uuid.uuid4().hex[:12]}"

    def _systemd_properties(self, runtime_sec: int) -> str:
        bind_paths = (
            "/home/skitarii/work/.skitarii-tmp/tmp:/tmp "
            "/home/skitarii/work/.skitarii-tmp/var-tmp:/var/tmp "
            "/home/skitarii/work/.skitarii-tmp/dev-shm:/dev/shm "
            "/home/skitarii/work/.skitarii-tmp/run-user:/run/user"
        )
        return (
            f"--property=Slice={shlex.quote(self.process_slice)} "
            "--property=KillMode=control-group "
            "--property=SendSIGKILL=yes "
            "--property=TimeoutStopSec=2s "
            "--property=NoNewPrivileges=yes "
            "--property=RestrictSUIDSGID=yes "
            "--property=ProtectSystem=strict "
            "--property=ProtectHome=read-only "
            "--property=ReadWritePaths=/home/skitarii/work "
            f"{shlex.quote('--property=InaccessiblePaths=/run/systemd/journal /dev/log')} "
            "--property=IPAddressDeny=10.0.2.2/32 "
            "--property=MemoryMax=4294967296 "
            "--property=TasksMax=512 "
            "--property=CPUQuota=400% "
            "--property=LimitFSIZE=1073741824 "
            "--property=LimitCORE=0 "
            f"{shlex.quote('--property=BindPaths=' + bind_paths)} "
            f"--property=RuntimeMaxSec={max(1, int(runtime_sec))}s"
        )

    def _acquire_boundary_lease(self, *, strict: bool) -> bool:
        if self.boundary_lease is not None:
            return True
        lock_path = Path(os.environ.get(
            "SKITARII_PROCESS_LOCK", "/tmp/skitarii-mission-boundary.lock",
        ))
        timeout_sec = max(1, int(os.environ.get(
            "SKITARII_PROCESS_LOCK_TIMEOUT_SEC", "30",
        )))
        handle = None
        descriptor: int | None = None
        failure: Exception | None = None
        detail = "mission process lock timed out"
        try:
            import fcntl
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(lock_path, flags, 0o600)
            handle = os.fdopen(descriptor, "a+", encoding="utf-8")
            descriptor = None
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or (
                hasattr(os, "geteuid") and info.st_uid != os.geteuid()
            ):
                raise OSError("sandbox mission lock is not a service-owned regular file")
            os.fchmod(handle.fileno(), 0o600)
            deadline = time.monotonic() + timeout_sec
            with _BOUNDARY_ACQUIRE_LOCK:
                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(detail)
                        time.sleep(0.1)
            self.boundary_lease = _ProcessBoundaryLease(handle)
            return True
        except (OSError, TimeoutError, ImportError) as exc:
            failure = exc
            detail = str(exc)
            if handle is not None:
                handle.close()
            elif descriptor is not None:
                os.close(descriptor)
        if strict:
            if isinstance(failure, TimeoutError):
                raise ProcessBoundaryBusy(detail) from failure
            raise RuntimeError(f"could not acquire global sandbox mission lock: {detail}")
        return False

    def initialize_process_boundary(self, *, strict: bool = False) -> bool:
        """Lock the VM, harden the guest account and snapshot existing uid processes."""
        if not self.process_boundary:
            return True
        if self.boundary_process_baseline is not None:
            return self._acquire_boundary_lease(strict=strict)
        if not self._acquire_boundary_lease(strict=strict):
            return False
        q_user = shlex.quote(self.user)
        expected_root = PurePosixPath(f"/home/{self.user}/work")
        work_path = PurePosixPath(self.workdir)
        if work_path == expected_root or expected_root not in work_path.parents:
            self.boundary_lease.release()
            self.boundary_lease = None
            if strict:
                raise RuntimeError(f"unsafe bounded workdir: {self.workdir}")
            return False
        q_workdir = shlex.quote(work_path.as_posix())
        remote = r"""
set -u
helper=/usr/local/sbin/skitarii-boundary
[ -x "$helper" ] || exit 127
helper_version=$("$helper" --version 2>/dev/null) || exit 126
[ "$helper_version" = "skitarii-boundary-v3" ] || exit 126
helper_sha=$(/usr/bin/sha256sum "$helper" | /usr/bin/awk '{print $1}') || exit 126
[ "$helper_sha" = "__HELPER_SHA__" ] || exit 126
set -e
uid=$(id -u)
home=$(getent passwd "$uid" | cut -d: -f6)
test -n "$home"
test "$(cat /proc/sys/kernel/core_pattern)" = '|/bin/false'
test "$(cat /proc/sys/fs/suid_dumpable)" = 0
test -d "$home" && test ! -L "$home"
if [ -e "$home/.ssh" ] || [ -L "$home/.ssh" ]; then
  test -d "$home/.ssh" && test ! -L "$home/.ssh"
else
  sudo -n /usr/local/sbin/skitarii-boundary mkdir -p "$home/.ssh"
fi
if [ -e "$home/.ssh/authorized_keys" ] || [ -L "$home/.ssh/authorized_keys" ]; then
  test -f "$home/.ssh/authorized_keys" && test ! -L "$home/.ssh/authorized_keys"
fi
sudo -n /usr/local/sbin/skitarii-boundary systemctl mask --runtime "user@${uid}.service" >/dev/null
sudo -n /usr/local/sbin/skitarii-boundary systemctl stop "user@${uid}.service" >/dev/null 2>&1
test "$(sudo -n /usr/local/sbin/skitarii-boundary systemctl is-enabled "user@${uid}.service" 2>/dev/null)" = masked
! sudo -n /usr/local/sbin/skitarii-boundary systemctl is-active --quiet "user@${uid}.service"
lineage=""
p=$$
while [ "$p" -gt 1 ] 2>/dev/null; do
  lineage="$lineage $p"
  statline=$(cat "/proc/$p/stat" 2>/dev/null || true)
  [ -n "$statline" ] || break
  rest=${statline#*) }
  p=$(printf '%s\n' "$rest" | awk '{print $2}')
done
in_lineage() {
  case " $lineage " in *" $1 "*) return 0;; *) return 1;; esac
}
for signal in TERM KILL; do
  for proc in /proc/[0-9]*; do
    pid=${proc##*/}
    [ "$(stat -c %u "$proc" 2>/dev/null || echo x)" = "$uid" ] || continue
    in_lineage "$pid" && continue
    kill -"$signal" "$pid" 2>/dev/null || true
  done
  sleep 0.15
done
for proc in /proc/[0-9]*; do
  pid=${proc##*/}
  [ "$(stat -c %u "$proc" 2>/dev/null || echo x)" = "$uid" ] || continue
  in_lineage "$pid" && continue
  echo "pre-existing sandbox uid process survived: $pid" >&2
  exit 1
done
# PERSISTENT VM (owner's design): NOTHING is deleted between missions. No /tmp
# sweep, no home wipe, no work reset, no tmpfs. Tools the fighter installed, its
# caches and old mission workdirs all stay on disk so the next mission finds and
# reuses them. Only orphan processes are killed (above) and the SSH door is
# re-hardened (below). The one legacy umount converts an old tmpfs work mount
# back to the plain persistent directory.
if sudo -n /usr/local/sbin/skitarii-boundary mountpoint -q -- "$home/work"; then
  sudo -n /usr/local/sbin/skitarii-boundary umount -- "$home/work"
fi
! sudo -n /usr/local/sbin/skitarii-boundary mountpoint -q -- "$home/work"
sudo -n /usr/local/sbin/skitarii-boundary find "$home/.ssh" -mindepth 1 -maxdepth 1 ! -name authorized_keys -exec /usr/bin/rm -rf -- {} +
sudo -n /usr/local/sbin/skitarii-boundary chmod 0755 "$home"
sudo -n /usr/local/sbin/skitarii-boundary chmod 0755 "$home/.ssh"
if [ -e "$home/.ssh/authorized_keys" ]; then
  sudo -n /usr/local/sbin/skitarii-boundary chown root:root "$home/.ssh/authorized_keys"
  sudo -n /usr/local/sbin/skitarii-boundary chmod 0644 "$home/.ssh/authorized_keys"
fi
sudo -n /usr/local/sbin/skitarii-boundary mkdir -p -- "$home/work"
sudo -n /usr/local/sbin/skitarii-boundary chown root:root -- "$home/work"
sudo -n /usr/local/sbin/skitarii-boundary chmod 0711 -- "$home/work"
# Persistent fighter HOME: mission commands run with ProtectHome=read-only plus
# ReadWritePaths=/home/skitarii/work, and the passwd home is root-owned — so JVM
# tools (gradle native services, the Android plugin's ~/.android) need a real
# writable home INSIDE work. Created once, owned by the fighter, never wiped.
if [ ! -d "$home/work/home" ]; then
  sudo -n /usr/local/sbin/skitarii-boundary mkdir -p -- "$home/work/home"
  sudo -n /usr/local/sbin/skitarii-boundary chown -hR skitarii:skitarii -- "$home/work/home"
  sudo -n /usr/local/sbin/skitarii-boundary chmod 0700 -- "$home/work/home"
fi
sudo -n /usr/local/sbin/skitarii-boundary mkdir -p -- "$home/work/.skitarii-tmp"
sudo -n /usr/local/sbin/skitarii-boundary chmod 0755 -- "$home/work/.skitarii-tmp"
for tmp_name in tmp var-tmp dev-shm run-user; do
  sudo -n /usr/local/sbin/skitarii-boundary mkdir -p -- "$home/work/.skitarii-tmp/$tmp_name"
  sudo -n /usr/local/sbin/skitarii-boundary chmod 01777 -- "$home/work/.skitarii-tmp/$tmp_name"
done
sudo -n /usr/local/sbin/skitarii-boundary mkdir -p -- __WORKDIR__
sudo -n /usr/local/sbin/skitarii-boundary chown -hR __SKITARII_USER__:__SKITARII_USER__ -- __WORKDIR__
sudo -n /usr/local/sbin/skitarii-boundary chmod 0700 -- __WORKDIR__
sudo -n /usr/local/sbin/skitarii-boundary systemctl set-property --runtime __SLICE__ MemoryMax=4294967296 TasksMax=512 CPUQuota=400%
! sudo -n /usr/local/sbin/skitarii-boundary systemctl is-active --quiet "user@${uid}.service"
if [ -e "$home/.ssh/authorized_keys" ]; then
  auth=$(sudo -n /usr/local/sbin/skitarii-boundary sha256sum "$home/.ssh/authorized_keys" | awk '{print $1}')
  meta=$(sudo -n /usr/local/sbin/skitarii-boundary stat -c '%u:%g:%a' "$home/.ssh/authorized_keys")
  printf 'AUTH\t%s:%s\n' "$auth" "$meta"
else
  printf 'AUTH\tMISSING\n'
fi
for pid in $lineage; do
  proc=/proc/$pid
  [ "$(stat -c %u "$proc" 2>/dev/null || echo x)" = "$uid" ] || continue
  statline=$(cat "$proc/stat" 2>/dev/null || true)
  [ -n "$statline" ] || continue
  rest=${statline#*) }
  start=$(printf '%s\n' "$rest" | awk '{print $20}')
  case "$start" in ''|*[!0-9]*) continue;; esac
  printf 'PROC\t%s\t%s\n' "$pid" "$start"
done
""".replace("__SKITARII_USER__", q_user).replace("__WORKDIR__", q_workdir).replace(
            "__SLICE__", shlex.quote(self.process_slice)
        ).replace("__HELPER_SHA__", BOUNDARY_HELPER_SHA256)
        try:
            proc = subprocess.run(
                self._ssh_base() + [remote],
                capture_output=True, text=True, timeout=45,
            )
            ok = proc.returncode == 0
            detail = (proc.stderr or proc.stdout or "unit baseline failed").strip()
            # Missing/non-executable (127) or wrong/untrusted (126) helpers fail
            # before the script's first mutation. Any other nonzero SSH result
            # may follow a mount/slice/account change and is quarantined.
            uncertain_remote_state = proc.returncode not in {0, 126, 127}
        except subprocess.TimeoutExpired as exc:
            ok = False
            detail = str(exc)
            proc = None
            uncertain_remote_state = True
        except OSError as exc:
            ok = False
            detail = str(exc)
            proc = None
            uncertain_remote_state = False
        if ok and proc is not None:
            baseline: dict[str, str] = {}
            auth_state = ""
            for line in proc.stdout.splitlines():
                parts = line.strip().split("\t")
                if len(parts) == 2 and parts[0] == "AUTH":
                    auth_state = parts[1]
                elif len(parts) == 3 and parts[0] == "PROC":
                    baseline[parts[1]] = parts[2]
            if not auth_state:
                detail = "guest account integrity snapshot is missing"
                ok = False
                uncertain_remote_state = True
            else:
                self.boundary_process_baseline = baseline
                self.boundary_auth_state = auth_state
                return True
        if self.boundary_lease is not None:
            # Helper preflight failures are synchronous and pre-mutation. All
            # ambiguous or partially completed remote states keep the host flock
            # quarantined until service restart.
            if uncertain_remote_state:
                self.quarantine_process_boundary()
            else:
                self.boundary_lease.release()
                self.boundary_lease = None
        if strict:
            error = f"could not initialize sandbox process boundary: {detail[-500:]}"
            if uncertain_remote_state:
                raise ProcessBoundaryQuarantined(error)
            raise RuntimeError(error)
        return False

    def release_process_boundary(self, *, strict: bool = True) -> bool:
        with self._boundary_state_lock:
            return self._release_process_boundary_locked(strict=strict)

    def _release_process_boundary_locked(self, *, strict: bool = True) -> bool:
        if not self.boundary_release_on_cleanup or self.boundary_lease is None:
            return True
        remote = (
            "uid=$(id -u); "
            "! sudo -n /usr/local/sbin/skitarii-boundary mountpoint -q -- /home/skitarii/work && "
            f"! sudo -n /usr/local/sbin/skitarii-boundary systemctl is-active --quiet {shlex.quote(self.process_slice)} && "
            "test \"$(sudo -n /usr/local/sbin/skitarii-boundary systemctl is-enabled \"user@${uid}.service\" 2>/dev/null)\" = masked && "
            "! sudo -n /usr/local/sbin/skitarii-boundary systemctl is-active --quiet \"user@${uid}.service\""
        )
        try:
            proc = subprocess.run(
                self._ssh_base() + [remote],
                capture_output=True, text=True, timeout=30,
            )
            ok = proc.returncode == 0
            detail = (proc.stderr or proc.stdout or "could not restore user manager").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok = False
            detail = str(exc)
        if not ok:
            self.quarantine_process_boundary()
            if strict:
                raise RuntimeError(f"sandbox boundary restore failed: {detail[-500:]}")
            return False
        self.boundary_lease.release()
        self.boundary_lease = None
        return True

    def quarantine_process_boundary(self) -> None:
        if self.boundary_release_on_cleanup:
            _quarantine_boundary(self.boundary_lease)

    def prepare_boundary_workdir(self, *, strict: bool = True) -> bool:
        """Provision an exact verifier workdir under the root-owned work parent."""
        expected_root = PurePosixPath(f"/home/{self.user}/work")
        work_path = PurePosixPath(self.workdir)
        if work_path == expected_root or expected_root not in work_path.parents:
            if strict:
                raise RuntimeError(f"unsafe bounded workdir: {self.workdir}")
            return False
        q_workdir = shlex.quote(work_path.as_posix())
        q_user = shlex.quote(self.user)
        remote = (
            f"sudo -n /usr/local/sbin/skitarii-boundary rm -rf -- {q_workdir} && sudo -n /usr/local/sbin/skitarii-boundary mkdir -p -- {q_workdir} && "
            f"sudo -n /usr/local/sbin/skitarii-boundary chown -hR {q_user}:{q_user} -- {q_workdir} && "
            f"sudo -n /usr/local/sbin/skitarii-boundary chmod 0700 -- {q_workdir}"
        )
        try:
            proc = subprocess.run(
                self._ssh_base() + [remote],
                capture_output=True, text=True, timeout=30,
            )
            ok = proc.returncode == 0
            detail = (proc.stderr or proc.stdout or "workdir provision failed").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok = False
            detail = str(exc)
        if strict and not ok:
            raise RuntimeError(f"could not provision bounded workdir: {detail[-500:]}")
        return ok

    def remove_boundary_storage(self, *, strict: bool = True) -> bool:
        """Release the process boundary WITHOUT deleting any guest data.

        PERSISTENT VM (owner's design): mission workdirs, caches and every file
        the fighter created stay on disk forever so later missions reuse them.
        This step only stops the mission slice and reverts its runtime
        properties; the old storage-wipe (work reset, cache/tmp sweep) is gone."""
        work_root = shlex.quote(f"/home/{self.user}/work")
        if self.boundary_release_on_cleanup:
            remote = (
                "set -e; "
                + f"sudo -n /usr/local/sbin/skitarii-boundary systemctl stop {shlex.quote(self.process_slice)} >/dev/null 2>&1; "
                + f"if sudo -n /usr/local/sbin/skitarii-boundary mountpoint -q -- {work_root}; then "
                + f"sudo -n /usr/local/sbin/skitarii-boundary umount -- {work_root}; fi; "
                + f"! sudo -n /usr/local/sbin/skitarii-boundary mountpoint -q -- {work_root}; "
                + f"sudo -n /usr/local/sbin/skitarii-boundary mkdir -p -- {work_root}; "
                + f"sudo -n /usr/local/sbin/skitarii-boundary systemctl revert {shlex.quote(self.process_slice)}; "
                + f"! sudo -n /usr/local/sbin/skitarii-boundary systemctl is-active --quiet {shlex.quote(self.process_slice)}"
            )
        else:
            remote = "true"
        try:
            proc = subprocess.run(
                self._ssh_base() + [remote],
                capture_output=True, text=True, timeout=45,
            )
            ok = proc.returncode == 0
            detail = (proc.stderr or proc.stdout or "storage cleanup failed").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok = False
            detail = str(exc)
        if strict and not ok:
            raise RuntimeError(f"could not remove bounded storage: {detail[-500:]}")
        return ok

    def scrub_boundary_temp(self, *, strict: bool = True) -> bool:
        """Trusted inter-stage scrub: remove every sandbox-UID temp artifact."""
        if not self.process_boundary or self.boundary_lease is None:
            if strict and self.process_boundary:
                raise RuntimeError("sandbox boundary lease is unavailable for temp scrub")
            return not self.process_boundary
        # PERSISTENT VM (owner's design): temp artifacts are the fighter's data too
        # (downloaded tools live in /tmp) and must survive between stages and
        # missions. The scrub is intentionally a no-op.
        remote = "true"
        try:
            proc = subprocess.run(
                self._ssh_base() + [remote], capture_output=True, text=True, timeout=45,
            )
            ok = proc.returncode == 0
            detail = (proc.stderr or proc.stdout or "temporary storage scrub failed").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok = False
            detail = str(exc)
        if strict and not ok:
            raise RuntimeError(f"could not scrub sandbox temporary storage: {detail[-500:]}")
        return ok

    def _stop_one_process_unit(self, unit: str) -> None:
        q_unit = shlex.quote(unit if unit.endswith(".service") else f"{unit}.service")
        remote = (
            f"sudo -n /usr/local/sbin/skitarii-boundary systemctl stop {q_unit} >/dev/null 2>&1 || true; "
            f"sudo -n /usr/local/sbin/skitarii-boundary systemctl kill --kill-whom=all --signal=KILL {q_unit} "
            ">/dev/null 2>&1 || true"
        )
        try:
            subprocess.run(
                self._ssh_base() + [remote], capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def cancel_current_commands(self) -> None:
        """Interrupt foreground/background work without releasing the lifecycle lock."""
        with self._boundary_state_lock:
            self._boundary_runtime.cancel_generation += 1
            self.boundary_poisoned = True
            if self.boundary_lease is None or self.boundary_process_baseline is None:
                return
            with self._current_units_lock:
                units = list(self._current_units)
            for proc in list(self._current_local_processes.values()):
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        try:
                            proc.kill()
                        except OSError:
                            pass
            for unit in units:
                self._stop_one_process_unit(unit)
            # A killed SSH may have delivered systemd-run just before disconnect.
            # Repeated full-prefix sweeps close that registration race; the normal
            # outer strict cleanup remains the final proof before lease release.
            for delay in (0.0, 0.1, 0.25):
                if delay:
                    time.sleep(delay)
                self.stop_process_boundary(strict=False)

    def stop_process_boundary(self, *, strict: bool = False) -> bool:
        with self._boundary_state_lock:
            return self._stop_process_boundary_locked(strict=strict)

    def _stop_process_boundary_locked(self, *, strict: bool = False) -> bool:
        """Reap mission units and every post-baseline process of the sandbox uid."""
        if not self.process_boundary:
            return True
        if not self.initialize_process_boundary(strict=strict):
            return False
        pattern = shlex.quote(f"{self.process_unit_prefix}-*.service")
        baseline = shlex.quote(" ".join(
            f"{pid}:{start}"
            for pid, start in sorted((self.boundary_process_baseline or {}).items())
        ))
        expected_auth = shlex.quote(self.boundary_auth_state)
        remote = r"""
set -u
pattern=__PATTERN__
slice=__SLICE__
baseline=__BASELINE__
expected_auth=__AUTH__
uid=$(id -u)
home=$(getent passwd "$uid" | cut -d: -f6)
in_words() {
  needle=$1; words=$2
  case " $words " in *" $needle "*) return 0;; *) return 1;; esac
}
proc_start() {
  statline=$(cat "/proc/$1/stat" 2>/dev/null || true)
  [ -n "$statline" ] || return 1
  rest=${statline#*) }
  printf '%s\n' "$rest" | awk '{print $20}'
}
lineage=""
p=$$
while [ "$p" -gt 1 ] 2>/dev/null; do
  lineage="$lineage $p"
  statline=$(cat "/proc/$p/stat" 2>/dev/null || true)
  [ -n "$statline" ] || break
  rest=${statline#*) }
  p=$(printf '%s\n' "$rest" | awk '{print $2}')
done
should_reap_pid() {
  pid=$1
  [ "$(stat -c %u "/proc/$pid" 2>/dev/null || echo x)" = "$uid" ] || return 1
  in_words "$pid" "$lineage" && return 1
  start=$(proc_start "$pid" 2>/dev/null || true)
  case "$start" in ''|*[!0-9]*) return 1;; esac
  in_words "$pid:$start" "$baseline" && return 1
  return 0
}
list_units() {
  sudo -n /usr/local/sbin/skitarii-boundary systemctl list-units --all --full --plain --no-legend "$pattern" 2>/dev/null | awk 'NF {print $1}'
}
sudo -n /usr/local/sbin/skitarii-boundary systemctl stop "$slice" >/dev/null 2>&1 || true
for unit in $(list_units); do
  sudo -n /usr/local/sbin/skitarii-boundary systemctl stop "$unit" >/dev/null 2>&1 || true
done
sleep 0.1
for unit in $(list_units); do
  sudo -n /usr/local/sbin/skitarii-boundary systemctl kill --kill-whom=all --signal=KILL "$unit" >/dev/null 2>&1 || true
  cg=$(sudo -n /usr/local/sbin/skitarii-boundary systemctl show --property=ControlGroup --value "$unit" 2>/dev/null || true)
  if [ -n "$cg" ] && [ -r "/sys/fs/cgroup${cg}/cgroup.procs" ]; then
    for pid in $(cat "/sys/fs/cgroup${cg}/cgroup.procs"); do
      kill -KILL "$pid" 2>/dev/null || true
    done
  fi
done
for signal in TERM KILL; do
  for proc in /proc/[0-9]*; do
    pid=${proc##*/}
    should_reap_pid "$pid" || continue
    kill -"$signal" "$pid" 2>/dev/null || true
  done
  sleep 0.15
done
survivors=0
for unit in $(list_units); do
  cg=$(sudo -n /usr/local/sbin/skitarii-boundary systemctl show --property=ControlGroup --value "$unit" 2>/dev/null || true)
  main_pid=$(sudo -n /usr/local/sbin/skitarii-boundary systemctl show --property=MainPID --value "$unit" 2>/dev/null || true)
  invocation=$(sudo -n /usr/local/sbin/skitarii-boundary systemctl show --property=InvocationID --value "$unit" 2>/dev/null || true)
  state=$(sudo -n /usr/local/sbin/skitarii-boundary systemctl show --property=ActiveState --value "$unit" 2>/dev/null || true)
  if [ -n "$cg" ] && [ -s "/sys/fs/cgroup${cg}/cgroup.procs" ]; then
    echo "$unit: invocation=$invocation cgroup still has processes" >&2
    survivors=1
  fi
  case "$main_pid" in ''|0) ;; *)
    if kill -0 "$main_pid" 2>/dev/null; then
      echo "$unit: invocation=$invocation MainPID=$main_pid survived" >&2
      survivors=1
    fi;;
  esac
  case "$state" in active|activating|deactivating|reloading)
    echo "$unit: invocation=$invocation state=$state" >&2
    survivors=1;;
  esac
  sudo -n /usr/local/sbin/skitarii-boundary systemctl reset-failed "$unit" >/dev/null 2>&1 || true
done
for proc in /proc/[0-9]*; do
  pid=${proc##*/}
  should_reap_pid "$pid" || continue
  start=$(proc_start "$pid" 2>/dev/null || true)
  echo "uid process survived: pid=$pid start=$start" >&2
  survivors=1
done
if [ -e "$home/.ssh/authorized_keys" ]; then
  auth=$(sudo -n /usr/local/sbin/skitarii-boundary sha256sum "$home/.ssh/authorized_keys" | awk '{print $1}')
  meta=$(sudo -n /usr/local/sbin/skitarii-boundary stat -c '%u:%g:%a' "$home/.ssh/authorized_keys")
  current_auth="$auth:$meta"
else
  current_auth=MISSING
fi
if [ "$current_auth" != "$expected_auth" ]; then
  echo "authorized_keys integrity changed" >&2
  survivors=1
fi
[ "$survivors" -eq 0 ]
"""
        remote = (
            remote.replace("__PATTERN__", pattern)
            .replace("__SLICE__", shlex.quote(self.process_slice))
            .replace("__BASELINE__", baseline)
            .replace("__AUTH__", expected_auth)
        )
        try:
            proc = subprocess.run(
                self._ssh_base() + [remote],
                capture_output=True, text=True, timeout=45,
            )
            ok = proc.returncode == 0
            detail = (proc.stderr or proc.stdout or "process boundary cleanup failed").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok = False
            detail = str(exc)
        if strict and not ok:
            raise RuntimeError(f"mission cgroup processes survived cleanup: {detail[-500:]}")
        return ok

    def _check_storage_bounds(self) -> tuple[bool, str]:
        if not self.process_boundary:
            return True, ""
        q_workdir = shlex.quote(f"/home/{self.user}/work")
        remote = (
            f"uid=$(id -u); bytes=$(du -sb -- {q_workdir} 2>/dev/null | awk '{{print $1+0}}'); "
            f"files=$(find {q_workdir} -xdev -mindepth 1 2>/dev/null | wc -l); "
            "tmpbytes=$(find /tmp /var/tmp /dev/shm \"/run/user/$uid\" -xdev -user \"$uid\" "
            "-type f -printf '%s\\n' 2>/dev/null | awk '{n+=$1} END {print n+0}'); "
            "tmpfiles=$(find /tmp /var/tmp /dev/shm \"/run/user/$uid\" -xdev -user \"$uid\" "
            "-mindepth 1 2>/dev/null | wc -l); "
            "printf '%s %s %s %s\\n' \"${bytes:-0}\" \"${files:-0}\" "
            "\"${tmpbytes:-0}\" \"${tmpfiles:-0}\""
        )
        try:
            proc = subprocess.run(
                self._ssh_base() + [remote],
                capture_output=True, text=True, timeout=30,
            )
            values = (proc.stdout or "").strip().split()
            if proc.returncode != 0 or len(values) != 4:
                return False, (proc.stderr or "storage accounting failed").strip()[-500:]
            work_bytes, files, temp_bytes, temp_files = (int(value) for value in values)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        total_bytes = work_bytes + temp_bytes
        if total_bytes > MAX_SANDBOX_STORAGE_BYTES:
            return False, f"sandbox storage exceeds {MAX_SANDBOX_STORAGE_BYTES} bytes"
        if files + temp_files > MAX_SANDBOX_FILES:
            return False, f"workspace file count exceeds {MAX_SANDBOX_FILES}"
        return True, ""

    def bash(self, command: str, timeout: int = 120) -> ExecResult:
        if self.boundary_poisoned:
            return ExecResult.make(125, "", "sandbox lifecycle is poisoned and requires cleanup")
        if self.process_boundary and not self.initialize_process_boundary(strict=False):
            return ExecResult.make(125, "", "could not initialize mission process boundary")
        with self._boundary_state_lock:
            launch_generation = self._boundary_runtime.cancel_generation
        env_prefix = self._environment_prefix()
        inner = (
            f"mkdir -p {shlex.quote(self.workdir)} && cd {shlex.quote(self.workdir)} && "
            f"{env_prefix} {command}"
        )
        unit = ""
        if self.process_boundary:
            unit = self._next_process_unit("cmd")
            systemd_inner = _systemd_exec_literal(inner)
            remote = (
                "sudo -n /usr/local/sbin/skitarii-boundary systemd-run --quiet --wait --collect --pipe --service-type=exec "
                f"--unit={shlex.quote(unit)} {self._systemd_properties(timeout)} "
                f"--uid={shlex.quote(self.user)} --gid={shlex.quote(self.user)} "
                f"/bin/bash -c {shlex.quote(systemd_inner)}"
            )
        else:
            remote = inner
        if unit:
            with self._current_units_lock:
                self._current_units.add(unit)
        try:
            kwargs: dict[str, Any] = {}
            if unit:
                kwargs = {
                    "spawn_lock": self._boundary_state_lock,
                    "pre_spawn": lambda: (
                        launch_generation == self._boundary_runtime.cancel_generation
                        and not self.boundary_poisoned
                        and self.boundary_lease is not None
                    ),
                    "on_started": lambda proc: self._current_local_processes.__setitem__(unit, proc),
                }
            result = _run_capped_process(
                self._ssh_base() + [remote], timeout=timeout + 15, **kwargs,
            )
        finally:
            if unit:
                with self._boundary_state_lock:
                    self._current_local_processes.pop(unit, None)
                with self._current_units_lock:
                    self._current_units.discard(unit)
        if unit and result.get("returncode") in {124, 125}:
            self._stop_one_process_unit(unit)
        if result.get("returncode") == 125:
            # 125 is the boundary's internal sentinel, but systemd-run also passes
            # through the PAYLOAD's own exit status — a fighter script, `timeout`
            # or git returning 125 must NOT poison the whole sandbox lifecycle.
            # (That false poison cancelled missions all night: poisoned bash makes
            # the workspace checkpoint uncapturable, so every next attempt started
            # from scratch.) Poison only on evidence the UNIT itself failed to run.
            err = str(result.get("stderr") or "")
            unit_launch_failure = (
                "Failed to start transient service" in err
                or "Failed to connect to bus" in err
                or "skitarii-boundary" in err
                or "sudo:" in err
            )
            if unit_launch_failure:
                self.boundary_poisoned = True
                self.stop_process_boundary(strict=False)
        storage_ok, storage_error = self._check_storage_bounds()
        if not storage_ok:
            self.boundary_poisoned = True
            if unit:
                self._stop_one_process_unit(unit)
            self.stop_process_boundary(strict=False)
            return ExecResult.make(125, result.get("stdout") or "", storage_error)
        return result

    def read_file(self, rel: str, max_bytes: int = 60_000,
                  offset: int = 0, limit: int = 0) -> str:
        safe = self._safe_rel(rel)
        q = shlex.quote(safe)
        if offset or limit:
            end = (offset + limit) if limit else "$"
            result = self.bash(f"sed -n '{offset + 1},{end}p' {q} | head -c {max_bytes}")
        else:
            result = self.bash(f"head -c {max_bytes} {q}")
        if result["returncode"] != 0:
            raise FileNotFoundError(result["stderr"] or rel)
        return result["stdout"]

    def read_regular_artifact(self, rel: str, max_bytes: int) -> bytes:
        """Bounded same-fd read with O_NOFOLLOW on every guest path component."""
        safe = self._safe_rel(rel)
        _regular_artifact_parts(safe)
        limit = _regular_artifact_limit(max_bytes)
        remote = (
            f"cd {shlex.quote(self.workdir)} && "
            f"/usr/bin/python3 -I -S -c {shlex.quote(_ATOMIC_REGULAR_READ_SCRIPT)} "
            f"{shlex.quote(safe)} {limit}"
        )
        try:
            proc = subprocess.run(
                self._ssh_base() + [remote], capture_output=True, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OSError(f"atomic artifact read transport failed: {exc}") from exc
        stderr = proc.stderr.decode("utf-8", errors="replace")[-500:]
        if proc.returncode == 3:
            raise ValueError(stderr or "artifact is missing, non-regular, or linked")
        if proc.returncode != 0:
            raise OSError(stderr or f"atomic artifact reader failed with {proc.returncode}")
        if len(proc.stdout) > limit + 1:
            raise OSError("atomic artifact reader exceeded its byte contract")
        return proc.stdout

    def bash_background(self, command: str) -> dict[str, Any]:
        """Start a long-running process (server, watcher) detached in the VM and return
        its pid + log file. Use bash to poll it (curl, logs) and to kill it later."""
        if self.process_boundary:
            if self.boundary_poisoned:
                return {
                    "pid": "", "log": "", "unit": "", "started": False,
                    "error": "sandbox lifecycle is poisoned and requires cleanup",
                }
            if not self.initialize_process_boundary(strict=False):
                return {
                    "pid": "", "log": "", "unit": "", "started": False,
                    "error": "could not initialize mission process boundary",
                }
            return self._bash_background_in_boundary(command)
        log = f".git/skitarii-bg/{uuid.uuid4().hex[:8]}.log"
        pidfile = f".git/skitarii-bg/{uuid.uuid4().hex[:8]}.pid"
        launch = f"echo $$ > {shlex.quote(pidfile)}; exec bash -c {shlex.quote(command)}"
        remote = (
            f"mkdir -p .git/skitarii-bg && rm -f -- {shlex.quote(pidfile)} && "
            f"setsid -f bash -c {shlex.quote(launch)} </dev/null > {shlex.quote(log)} 2>&1 && "
            f"for i in $(seq 1 50); do [ -s {shlex.quote(pidfile)} ] && break; sleep 0.02; done && "
            f"cat -- {shlex.quote(pidfile)}"
        )
        r = self.bash(remote, timeout=30)
        pid = (r["stdout"] or "").strip().split("\n")[-1]
        return {"pid": pid, "log": log, "started": r["returncode"] == 0 and pid.isdigit()}

    def _bash_background_in_boundary(self, command: str) -> dict[str, Any]:
        """Start a deliberate long-running helper in the mission's cgroup family."""
        log = f".git/skitarii-bg/{uuid.uuid4().hex[:8]}.log"
        pidfile = f".git/skitarii-bg/{uuid.uuid4().hex[:8]}.pid"
        unit = self._next_process_unit("bg")
        with self._boundary_state_lock:
            launch_generation = self._boundary_runtime.cancel_generation
        unit_name = f"{unit}.service"
        log_abs = posixpath.join(self.workdir, log)
        pidfile_abs = posixpath.join(self.workdir, pidfile)
        inner = (
            f"cd {shlex.quote(self.workdir)} || exit 1; {self._environment_prefix()} "
            f"echo $$ > {shlex.quote(pidfile_abs)}; "
            f"exec /bin/bash -c {shlex.quote(command)} </dev/null "
            f">> {shlex.quote(log_abs)} 2>&1"
        )
        q_unit = shlex.quote(f"{unit}.service")
        systemd_inner = _systemd_exec_literal(inner)
        remote = (
            f"mkdir -p {shlex.quote(posixpath.join(self.workdir, '.git/skitarii-bg'))} && "
            f"rm -f -- {shlex.quote(pidfile_abs)} && "
            "sudo -n /usr/local/sbin/skitarii-boundary systemd-run --quiet --collect --service-type=exec "
            f"--unit={shlex.quote(unit)} "
            f"{self._systemd_properties(self.boundary_runtime_sec)} "
            f"--uid={shlex.quote(self.user)} --gid={shlex.quote(self.user)} "
            f"/bin/bash -c {shlex.quote(systemd_inner)} && "
            "pid=; for i in $(seq 1 50); do "
            f"pid=$(sudo -n /usr/local/sbin/skitarii-boundary systemctl show --property=MainPID --value {q_unit} "
            "2>/dev/null || true); case \"$pid\" in ''|0) sleep 0.02;; *) break;; esac; done; "
            "case \"$pid\" in ''|0) exit 1;; *) printf '%s\\n' \"$pid\";; esac"
        )
        with self._current_units_lock:
            self._current_units.add(unit)
        try:
            result = _run_capped_process(
                self._ssh_base() + [remote], timeout=30,
                spawn_lock=self._boundary_state_lock,
                pre_spawn=lambda: (
                    launch_generation == self._boundary_runtime.cancel_generation
                    and not self.boundary_poisoned
                    and self.boundary_lease is not None
                ),
                on_started=lambda proc: self._current_local_processes.__setitem__(unit, proc),
            )
            pid = (result.get("stdout") or "").strip().split("\n")[-1]
            started = result.get("returncode") == 0 and pid.isdigit() and int(pid) > 0
        except OSError:
            pid = ""
            started = False
        finally:
            with self._boundary_state_lock:
                self._current_local_processes.pop(unit, None)
        if not started:
            with self._current_units_lock:
                self._current_units.discard(unit)
            self._stop_one_process_unit(unit)
        return {
            "pid": pid, "log": log, "unit": unit_name, "started": started,
        }

    def write_file(self, rel: str, content: str) -> None:
        # heredoc-free write: pipe through stdin to survive any content
        safe = self._safe_rel(rel)
        parent = posixpath.dirname(safe) or "."
        remote = (f"mkdir -p {shlex.quote(self.workdir)} && cd {shlex.quote(self.workdir)} && "
                  f"mkdir -p -- {shlex.quote(parent)} && cat > {shlex.quote(safe)}")
        proc = subprocess.run(self._ssh_base() + [remote], input=content,
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise IOError(proc.stderr or f"write failed: {rel}")

    def write_bytes(self, rel: str, content: bytes) -> None:
        safe = self._safe_rel(rel)
        parent = posixpath.dirname(safe) or "."
        remote = (f"mkdir -p {shlex.quote(self.workdir)} && cd {shlex.quote(self.workdir)} && "
                  f"mkdir -p -- {shlex.quote(parent)} && cat > {shlex.quote(safe)}")
        proc = subprocess.run(self._ssh_base() + [remote], input=content,
                              capture_output=True, timeout=60)
        if proc.returncode != 0:
            raise IOError(proc.stderr.decode(errors="replace") or f"binary write failed: {rel}")

    def fetch_artifact(self, rel: str, max_bytes: int | None = None) -> bytes:
        safe = self._safe_rel(rel)
        read = (f"head -c {int(max_bytes) + 1} -- {shlex.quote(safe)}"
                if max_bytes is not None else f"cat -- {shlex.quote(safe)}")
        remote = f"cd {shlex.quote(self.workdir)} && {read}"
        proc = subprocess.run(self._ssh_base() + [remote], capture_output=True, timeout=60)
        if proc.returncode != 0:
            raise FileNotFoundError(proc.stderr.decode(errors="replace") or rel)
        return proc.stdout

    def child(self, name: str) -> "VmExecutor":
        child = VmExecutor(
            self.host, self.port, self.user, self.key,
            workdir=f"/home/{self.user}/work/mission-{uuid.uuid4().hex[:16]}",
            mission_marker=self.mission_marker,
            command_env=self.command_env,
            process_boundary=self.process_boundary,
            boundary_runtime_sec=self.boundary_runtime_sec,
            boundary_process_baseline=self.boundary_process_baseline,
            boundary_auth_state=self.boundary_auth_state,
            boundary_lease=self.boundary_lease,
            boundary_release_on_cleanup=False,
            _boundary_runtime_state=self._boundary_runtime,
        )
        if child.process_boundary:
            child.prepare_boundary_workdir(strict=True)
        return child

    def alive(self) -> bool:
        try:
            return self.bash("true", timeout=10)["returncode"] == 0
        except Exception:
            return False

    def boundary_ready(self) -> bool:
        """Attest the versioned helper, narrow sudo, and masked user manager."""
        remote = (
            f"uid=$(id -u); expected={shlex.quote(BOUNDARY_HELPER_VERSION)}; "
            "test \"$(cat /proc/sys/kernel/core_pattern)\" = '|/bin/false' && "
            "test \"$(cat /proc/sys/fs/suid_dumpable)\" = 0 && "
            "test \"$(/usr/local/sbin/skitarii-boundary --version)\" = \"$expected\" && "
            "test \"$(sha256sum /usr/local/sbin/skitarii-boundary | awk '{print $1}')\" = "
            f"{BOUNDARY_HELPER_SHA256} && "
            "test \"$(sudo -n /usr/local/sbin/skitarii-boundary --version)\" = \"$expected\" && "
            "test \"$(sudo -n /usr/local/sbin/skitarii-boundary systemctl is-enabled "
            "\"user@${uid}.service\" 2>/dev/null)\" = masked && "
            "! sudo -n /usr/bin/true >/dev/null 2>&1"
        )
        try:
            proc = subprocess.run(
                self._ssh_base() + [remote],
                capture_output=True, text=True, timeout=15,
            )
            return proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
