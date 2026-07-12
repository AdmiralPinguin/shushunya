"""Content-bound deployment manifest verified before every research attempt."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any


SCHEMA_VERSION = 1
MAX_BOUND_FILE_BYTES = 32 * 1024 * 1024
MAX_BOUND_TOTAL_BYTES = 512 * 1024 * 1024
BOUND_ENVIRONMENT = (
    "EYE_MODEL_API_KEY",
    "EYE_MODEL_BASE_URL",
    "GEMMA_LLM_MODEL",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "OPENAI_API_KEY",
    "RESEARCH_GEMMA_MAX_CONTEXT_CHARS",
    "RESEARCH_GEMMA_MAX_TOKENS",
    "RESEARCH_GEMMA_TIMEOUT_SEC",
    "RESEARCH_READER_CHUNK_CHARS",
    "RESEARCH_SOURCE_CLASSIFIER_JSON",
    "RESEARCH_WARBAND_ATTEMPT_TIMEOUT_SECONDS",
    "RESEARCH_WARBAND_BEARER_TOKEN",
    "RESEARCH_WARBAND_CANCEL_GRACE_SECONDS",
    "RESEARCH_WARBAND_COMMON_PROTOCOL_ROOT",
    "RESEARCH_WARBAND_EVENT_MAX_BYTES",
    "RESEARCH_WARBAND_EVENTS_MAX_BYTES",
    "RESEARCH_WARBAND_HOST",
    "RESEARCH_WARBAND_TRUSTED_CONTRACT_FILES",
    "RESEARCH_WARBAND_TRUSTED_SOURCE_FILES",
    "RESEARCH_WARBAND_LLM_MODEL",
    "RESEARCH_WARBAND_LLM_BASE_URL",
    "RESEARCH_WARBAND_MAX_ACTIVE",
    "RESEARCH_WARBAND_MAX_ANSWER_BYTES",
    "RESEARCH_WARBAND_MAX_ATTEMPTS",
    "RESEARCH_WARBAND_MAX_MISSIONS",
    "RESEARCH_WARBAND_MAX_REQUEST_BYTES",
    "RESEARCH_WARBAND_MAX_RESPONSE_BYTES",
    "RESEARCH_WARBAND_MISSION_ROOT",
    "RESEARCH_WARBAND_MODEL_RUNTIME_CONTRACT",
    "RESEARCH_WARBAND_PAYLOAD_MAX_BYTES",
    "RESEARCH_WARBAND_PEER_MISSION_ROOT",
    "RESEARCH_WARBAND_PEER_SNAPSHOT_ROOT",
    "RESEARCH_WARBAND_PORT",
    "RESEARCH_WARBAND_PROFILE",
    "RESEARCH_WARBAND_QUESTION_MAX_BYTES",
    "RESEARCH_WARBAND_RESULT_MAX_BYTES",
    "RESEARCH_WARBAND_REVIEWER_AUTHORITY_ID",
    "RESEARCH_WARBAND_SNAPSHOT_ROOT",
    "RESEARCH_WARBAND_STANDALONE_TEST_MODE",
    "RESEARCH_WARBAND_STATE_MAX_BYTES",
    "RESEARCH_WARBAND_STORE_MAX_BYTES",
    "RESEARCH_WARBAND_TERMINAL_MAX_COUNT",
    "RESEARCH_WARBAND_TERMINAL_TTL_SECONDS",
    "RESEARCH_WARBAND_TERMINATE_GRACE_SECONDS",
    "RESEARCH_WARBAND_TRUSTED_REVIEWER_IDS",
    "RESEARCH_WARBAND_NORMALIZER_ID",
    "RESEARCH_WARBAND_RUNNER",
    "RESEARCH_WARBAND_READINESS_PROBE",
    "SHUSHUNYA_SEARCH_MAX_WEB_BYTES",
    "SHUSHUNYA_SEARCH_BRAVE_API_KEY",
    "SHUSHUNYA_SEARCH_SEARXNG_URL",
    "SHUSHUNYA_SEARCH_PROVIDERS",
    "SHUSHUNYA_SEARCH_WEB_USER_AGENT",
    "SHUSHUNYA_SEARCH_WEB_ACCEPT_LANGUAGE",
)
SECRET_BOUND_ENVIRONMENT = frozenset(
    {
        "EYE_MODEL_API_KEY",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "RESEARCH_WARBAND_BEARER_TOKEN",
        "SHUSHUNYA_SEARCH_BRAVE_API_KEY",
    }
)


class DeploymentIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class GuardSpec:
    package_root: str
    automatic_protocol_roots: tuple[str, ...]
    trusted_files: tuple[str, ...]
    trusted_config_json: str


@dataclass(frozen=True)
class BoundFile:
    path: str
    role: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "role": self.role,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class DeploymentManifest:
    schema_version: int
    spec: GuardSpec
    environment: tuple[tuple[str, str], ...]
    files: tuple[BoundFile, ...]
    digest: str


@dataclass(frozen=True)
class DeploymentStatus:
    ok: bool
    startup_digest: str
    current_digest: str | None
    error: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "startup_digest": self.startup_digest,
            "current_digest": self.current_digest,
            "error": self.error,
        }


def _canonical_json(value: Any, context: str) -> str:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        restored = json.loads(raw)
    except (TypeError, ValueError, RecursionError, json.JSONDecodeError) as exc:
        raise DeploymentIntegrityError(f"{context} is not finite JSON") from exc
    if not isinstance(restored, dict):
        raise DeploymentIntegrityError(f"{context} must be a JSON object")
    return raw


def _is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    junction = getattr(os.path, "isjunction", None)
    return bool(junction(path)) if junction is not None else False


def _assert_real_path(path: Path, *, directory: bool, context: str) -> Path:
    absolute = Path(os.path.abspath(path))
    for component in reversed((absolute, *absolute.parents)):
        if _is_link_like(component):
            raise DeploymentIntegrityError(
                f"{context} contains a symlink or junction: {component}"
            )
    try:
        metadata = absolute.lstat()
    except OSError as exc:
        raise DeploymentIntegrityError(f"{context} is missing or unreadable") from exc
    expected = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if not expected:
        kind = "directory" if directory else "regular file"
        raise DeploymentIntegrityError(f"{context} must be a real {kind}")
    return absolute


def _walk_python(
    root: Path,
    role: str,
    *,
    ignore_cache_directories: bool = False,
) -> list[tuple[Path, str]]:
    root = _assert_real_path(root, directory=True, context=f"{role} root")
    discovered: list[tuple[Path, str]] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise DeploymentIntegrityError(f"cannot enumerate {role} root") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise DeploymentIntegrityError(f"cannot stat bound path: {path}") from exc
            if entry.is_symlink() or _is_link_like(path):
                raise DeploymentIntegrityError(
                    f"{role} tree contains a symlink or junction: {path}"
                )
            if entry.name == "__pycache__":
                if ignore_cache_directories and stat.S_ISDIR(metadata.st_mode):
                    continue
                raise DeploymentIntegrityError(
                    f"{role} tree contains executable bytecode: {path}"
                )
            if path.suffix in {".pyc", ".pyo"}:
                raise DeploymentIntegrityError(
                    f"{role} tree contains executable bytecode: {path}"
                )
            if stat.S_ISDIR(metadata.st_mode):
                stack.append(path)
                continue
            if path.suffix != ".py":
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise DeploymentIntegrityError(f"bound Python path is not regular: {path}")
            discovered.append((path, role))
    if not discovered:
        raise DeploymentIntegrityError(f"{role} root has no production Python files")
    return discovered


def _split_paths(value: str) -> tuple[Path, ...]:
    return tuple(Path(item) for item in value.split(os.pathsep) if item)


def _read_regular_file(path: Path, *, context: str) -> bytes:
    """Read through a non-following descriptor and prove the opened object is regular."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DeploymentIntegrityError(f"{context} is missing or unreadable: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise DeploymentIntegrityError(f"{context} must be a real regular file: {path}")
        if before.st_size > MAX_BOUND_FILE_BYTES:
            raise DeploymentIntegrityError(f"bound file exceeds byte limit: {path}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(MAX_BOUND_FILE_BYTES + 1)
            after = os.fstat(handle.fileno())
        if len(raw) > MAX_BOUND_FILE_BYTES:
            raise DeploymentIntegrityError(f"bound file exceeds byte limit: {path}")
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or len(raw) != after.st_size:
            raise DeploymentIntegrityError(f"bound file changed while being attested: {path}")
        return raw
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _bound_environment_value(name: str) -> str:
    value = os.environ.get(name, "")
    if name in SECRET_BOUND_ENVIRONMENT:
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
    return value


def _automatic_protocol_candidates(package_root: Path) -> tuple[Path, ...]:
    return (
        package_root.parent.parent / "common_protocol",
        package_root.parent / "native_boundary" / "EyeOfTerror" / "common_protocol",
    )


def guard_spec_from_environment(
    package_root: str | os.PathLike[str],
    *,
    trusted_config: dict[str, Any] | None = None,
    trusted_files: tuple[str | os.PathLike[str], ...] = (),
) -> GuardSpec:
    root = Path(package_root)
    candidates = _automatic_protocol_candidates(root)
    return GuardSpec(
        package_root=str(Path(os.path.abspath(root))),
        automatic_protocol_roots=tuple(str(Path(os.path.abspath(item))) for item in candidates),
        trusted_files=tuple(str(Path(os.path.abspath(item))) for item in trusted_files),
        trusted_config_json=_canonical_json(trusted_config or {}, "trusted deployment config"),
    )


def _current_protocol_roots(spec: GuardSpec) -> tuple[Path, ...]:
    explicit = os.environ.get("RESEARCH_WARBAND_COMMON_PROTOCOL_ROOT", "").strip()
    if explicit:
        return (Path(explicit),)
    return tuple(
        Path(item)
        for item in spec.automatic_protocol_roots
        if Path(item).exists() or Path(item).is_symlink()
    )


def build_manifest(spec: GuardSpec) -> DeploymentManifest:
    paths: list[tuple[Path, str]] = []
    paths.extend(_walk_python(Path(spec.package_root), "research_warband_package"))
    paths.extend((Path(path), "trusted_runtime_source") for path in spec.trusted_files)
    for root in _current_protocol_roots(spec):
        paths.extend(
            _walk_python(
                root,
                "common_protocol",
                ignore_cache_directories=True,
            )
        )

    classifier = os.environ.get("RESEARCH_SOURCE_CLASSIFIER_JSON", "").strip()
    if classifier:
        paths.append((Path(classifier), "source_classifier_config"))
    for path in _split_paths(
        os.environ.get("RESEARCH_WARBAND_TRUSTED_CONTRACT_FILES", "")
    ):
        paths.append((path, "trusted_contract"))
    for path in _split_paths(
        os.environ.get("RESEARCH_WARBAND_TRUSTED_SOURCE_FILES", "")
    ):
        paths.append((path, "trusted_source"))

    unique: dict[str, set[str]] = {}
    source_paths: dict[str, Path] = {}
    for raw_path, role in paths:
        path = _assert_real_path(raw_path, directory=False, context=role)
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            raise DeploymentIntegrityError(
                f"bound deployment file must not be executable bytecode: {path}"
            )
        key = os.path.normcase(str(path))
        unique.setdefault(key, set()).add(role)
        source_paths[key] = path

    files: list[BoundFile] = []
    total = 0
    for key in sorted(unique):
        path = source_paths[key]
        raw = _read_regular_file(path, context="bound deployment file")
        total += len(raw)
        if total > MAX_BOUND_TOTAL_BYTES:
            raise DeploymentIntegrityError("deployment manifest exceeds total byte limit")
        files.append(
            BoundFile(
                path=str(path),
                role="+".join(sorted(unique[key])),
                size_bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        )

    environment = tuple((name, _bound_environment_value(name)) for name in BOUND_ENVIRONMENT)
    record = {
        "schema_version": SCHEMA_VERSION,
        "spec": {
            "package_root": spec.package_root,
            "automatic_protocol_roots": list(spec.automatic_protocol_roots),
            "trusted_files": list(spec.trusted_files),
            "trusted_config_json": spec.trusted_config_json,
        },
        "environment": [list(item) for item in environment],
        "files": [item.to_dict() for item in files],
    }
    digest = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return DeploymentManifest(
        schema_version=SCHEMA_VERSION,
        spec=spec,
        environment=environment,
        files=tuple(files),
        digest=digest,
    )


def verify_manifest(expected: DeploymentManifest) -> DeploymentManifest:
    if not isinstance(expected, DeploymentManifest) or expected.schema_version != SCHEMA_VERSION:
        raise DeploymentIntegrityError("deployment manifest schema is invalid")
    current = build_manifest(expected.spec)
    if current.digest != expected.digest:
        expected_files = {item.path: item for item in expected.files}
        current_files = {item.path: item for item in current.files}
        changes: list[str] = []
        for path in sorted(set(expected_files) | set(current_files)):
            before, after = expected_files.get(path), current_files.get(path)
            if before is None:
                changes.append(f"added:{path}")
            elif after is None:
                changes.append(f"missing:{path}")
            elif before.sha256 != after.sha256 or before.size_bytes != after.size_bytes:
                changes.append(f"changed:{path}")
        if current.environment != expected.environment:
            changed_names = [
                name
                for (name, old), (_, new) in zip(expected.environment, current.environment)
                if old != new
            ]
            changes.extend(f"environment:{name}" for name in changed_names)
        detail = ", ".join(changes[:8]) or "manifest digest changed"
        raise DeploymentIntegrityError(f"deployment integrity mismatch: {detail}")
    return current


class DeploymentGuard:
    def __init__(self, manifest: DeploymentManifest) -> None:
        if not isinstance(manifest, DeploymentManifest):
            raise TypeError("manifest must be a DeploymentManifest")
        self.manifest = manifest

    @classmethod
    def from_environment(
        cls,
        package_root: str | os.PathLike[str],
        *,
        trusted_config: dict[str, Any] | None = None,
        trusted_files: tuple[str | os.PathLike[str], ...] = (),
    ) -> "DeploymentGuard":
        spec = guard_spec_from_environment(
            package_root,
            trusted_config=trusted_config,
            trusted_files=trusted_files,
        )
        return cls(build_manifest(spec))

    @property
    def startup_digest(self) -> str:
        return self.manifest.digest

    def verify(self) -> DeploymentManifest:
        return verify_manifest(self.manifest)

    def status(self) -> DeploymentStatus:
        try:
            current = self.verify()
            return DeploymentStatus(True, self.startup_digest, current.digest, "")
        except DeploymentIntegrityError as exc:
            current_digest: str | None = None
            try:
                current_digest = build_manifest(self.manifest.spec).digest
            except DeploymentIntegrityError:
                pass
            return DeploymentStatus(
                False,
                self.startup_digest,
                current_digest,
                str(exc)[:4096],
            )


__all__ = [
    "BOUND_ENVIRONMENT",
    "SECRET_BOUND_ENVIRONMENT",
    "BoundFile",
    "DeploymentGuard",
    "DeploymentIntegrityError",
    "DeploymentManifest",
    "DeploymentStatus",
    "GuardSpec",
    "build_manifest",
    "guard_spec_from_environment",
    "verify_manifest",
]
