"""Content-addressed storage for raw and normalized source snapshots."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import stat
import tempfile
from typing import Callable, Final, Iterable

from .schema import MEDIA, SourceSnapshot


DEFAULT_MAX_RAW_BYTES: Final[int] = 64 * 1024 * 1024
DEFAULT_MAX_NORMALIZED_BYTES: Final[int] = 32 * 1024 * 1024


class SnapshotStoreError(RuntimeError):
    """Base error for snapshot persistence and validation."""


class SnapshotByteLimitError(SnapshotStoreError):
    """A caller or stored object exceeded a configured byte limit."""


class SnapshotSecurityError(SnapshotStoreError):
    """A path, symlink, or non-regular file violated containment rules."""


class SnapshotIntegrityError(SnapshotStoreError):
    """Stored bytes no longer match their immutable snapshot metadata."""


class UnknownNormalizerError(SnapshotStoreError):
    """A snapshot named a normalizer outside the trusted local registry."""


class SnapshotNormalizationError(SnapshotStoreError):
    """Trusted normalization failed or disagreed with caller-supplied text."""


NormalizerCallback = Callable[[bytes, str], str]


@dataclass(frozen=True, slots=True)
class RegisteredNormalizer:
    """A versioned normalizer installed by the application trust boundary.

    ``callback`` receives immutable raw bytes and the declared medium. Its ID
    must change whenever normalization behavior changes.
    """

    id: str
    media: frozenset[str]
    callback: NormalizerCallback

    def __post_init__(self) -> None:
        if type(self.id) is not str or not self.id.strip():
            raise ValueError("normalizer id must be a non-empty string")
        if type(self.media) is not frozenset or not self.media:
            raise ValueError("normalizer media must be a non-empty frozenset")
        if not self.media <= MEDIA:
            raise ValueError("normalizer contains an unsupported medium")
        if not callable(self.callback):
            raise TypeError("normalizer callback must be callable")


def sha256_bytes(payload: bytes) -> str:
    """Return the lowercase SHA256 digest for bytes."""

    if type(payload) is not bytes:
        raise TypeError("payload must be bytes")
    return hashlib.sha256(payload).hexdigest()


class SnapshotStore:
    """Immutable, content-addressed raw and normalized object storage.

    Object paths are derived solely from their digests. Writes use a temporary
    file in the destination directory followed by ``os.replace``. Reads reject
    traversal, symlinks, non-regular files, oversized content, size drift, and
    digest drift.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        max_raw_bytes: int = DEFAULT_MAX_RAW_BYTES,
        max_normalized_bytes: int = DEFAULT_MAX_NORMALIZED_BYTES,
        normalizers: Iterable[RegisteredNormalizer] = (),
    ) -> None:
        if type(max_raw_bytes) is not int or max_raw_bytes <= 0:
            raise ValueError("max_raw_bytes must be a positive integer")
        if type(max_normalized_bytes) is not int or max_normalized_bytes <= 0:
            raise ValueError("max_normalized_bytes must be a positive integer")

        candidate = Path(root)
        if candidate.exists() and candidate.is_symlink():
            raise SnapshotSecurityError("snapshot store root must not be a symlink")
        candidate.mkdir(parents=True, exist_ok=True)
        if candidate.is_symlink():
            raise SnapshotSecurityError("snapshot store root must not be a symlink")
        if not candidate.is_dir():
            raise SnapshotSecurityError("snapshot store root must be a directory")

        self.root = candidate.resolve(strict=True)
        self.max_raw_bytes = max_raw_bytes
        self.max_normalized_bytes = max_normalized_bytes
        registry: dict[str, RegisteredNormalizer] = {}
        for normalizer in normalizers:
            if not isinstance(normalizer, RegisteredNormalizer):
                raise TypeError("normalizers must contain RegisteredNormalizer objects")
            if normalizer.id in registry:
                raise ValueError(f"duplicate registered normalizer: {normalizer.id}")
            registry[normalizer.id] = normalizer
        self._normalizers = registry

    @property
    def registered_normalizer_ids(self) -> frozenset[str]:
        return frozenset(self._normalizers)

    def _derive_normalized(
        self, *, raw: bytes, medium: str, normalizer_id: str
    ) -> str:
        normalizer = self._normalizers.get(normalizer_id)
        if normalizer is None:
            raise UnknownNormalizerError(
                f"normalizer is not in trusted registry: {normalizer_id}"
            )
        if medium not in normalizer.media:
            raise UnknownNormalizerError(
                f"normalizer {normalizer_id} is not registered for medium {medium}"
            )
        try:
            normalized = normalizer.callback(raw, medium)
        except Exception as exc:
            raise SnapshotNormalizationError(
                f"trusted normalizer {normalizer_id} failed"
            ) from exc
        if type(normalized) is not str:
            raise SnapshotNormalizationError(
                f"trusted normalizer {normalizer_id} did not return text"
            )
        return normalized

    @staticmethod
    def _raw_relative(digest: str) -> str:
        return f"objects/raw/{digest[:2]}/{digest}.bin"

    @staticmethod
    def _normalized_relative(digest: str) -> str:
        return f"objects/normalized/{digest[:2]}/{digest}.utf8"

    @staticmethod
    def _path_parts(relative: str) -> tuple[str, ...]:
        if type(relative) is not str or not relative:
            raise SnapshotSecurityError("object path must be a non-empty string")
        if "\\" in relative:
            raise SnapshotSecurityError("object path must use '/' separators")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or not pure.parts:
            raise SnapshotSecurityError("object path must be relative")
        if any(part in {"", ".", ".."} or ":" in part for part in pure.parts):
            raise SnapshotSecurityError("unsafe object path component")
        return tuple(pure.parts)

    def _contained_path(self, relative: str, *, create_parents: bool) -> Path:
        parts = self._path_parts(relative)
        current = self.root
        for index, part in enumerate(parts):
            current = current / part
            final = index == len(parts) - 1
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                if not final and create_parents:
                    try:
                        current.mkdir()
                    except FileExistsError:
                        metadata = os.lstat(current)
                        if stat.S_ISLNK(metadata.st_mode):
                            raise SnapshotSecurityError(
                                f"symlink component in object path: {relative}"
                            )
                        if not stat.S_ISDIR(metadata.st_mode):
                            raise SnapshotSecurityError(
                                f"non-directory component in object path: {relative}"
                            )
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise SnapshotSecurityError(
                    f"symlink component in object path: {relative}"
                )
            if not final and not stat.S_ISDIR(metadata.st_mode):
                raise SnapshotSecurityError(
                    f"non-directory component in object path: {relative}"
                )

        try:
            current.resolve(strict=False).relative_to(self.root)
        except ValueError as exc:
            raise SnapshotSecurityError("object path escapes snapshot store") from exc
        return current

    def object_path(self, relative: str) -> Path:
        """Resolve a relative object path after containment checks."""

        return self._contained_path(relative, create_parents=False)

    @staticmethod
    def _check_payload(payload: bytes, *, limit: int, label: str) -> None:
        if len(payload) > limit:
            raise SnapshotByteLimitError(
                f"{label} payload is {len(payload)} bytes; limit is {limit}"
            )

    def _read_relative(self, relative: str, *, limit: int) -> bytes:
        path = self._contained_path(relative, create_parents=False)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise SnapshotIntegrityError(f"snapshot object is missing: {relative}") from exc
        except OSError as exc:
            raise SnapshotSecurityError(
                f"snapshot object could not be opened safely: {relative}"
            ) from exc

        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise SnapshotSecurityError(
                    f"snapshot object is not a regular file: {relative}"
                )
            if metadata.st_size > limit:
                raise SnapshotByteLimitError(
                    f"stored object exceeds {limit} byte limit: {relative}"
                )
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = -1
                payload = stream.read(limit + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

        if len(payload) > limit:
            raise SnapshotByteLimitError(
                f"stored object exceeds {limit} byte limit: {relative}"
            )
        return payload

    def _atomic_write(self, relative: str, payload: bytes, *, limit: int) -> None:
        self._check_payload(payload, limit=limit, label="snapshot")
        destination = self._contained_path(relative, create_parents=True)

        try:
            existing_metadata = os.lstat(destination)
        except FileNotFoundError:
            existing_metadata = None
        if existing_metadata is not None:
            if stat.S_ISLNK(existing_metadata.st_mode):
                raise SnapshotSecurityError(f"object destination is a symlink: {relative}")
            existing = self._read_relative(relative, limit=limit)
            if existing != payload:
                raise SnapshotIntegrityError(
                    f"content-addressed object already exists with different bytes: {relative}"
                )
            return

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".snapshot-", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())

            # Recheck all path components immediately before publication.
            self._contained_path(relative, create_parents=False)
            try:
                existing_metadata = os.lstat(destination)
            except FileNotFoundError:
                existing_metadata = None
            if existing_metadata is not None:
                if stat.S_ISLNK(existing_metadata.st_mode):
                    raise SnapshotSecurityError(
                        f"object destination became a symlink: {relative}"
                    )
                existing = self._read_relative(relative, limit=limit)
                if existing != payload:
                    raise SnapshotIntegrityError(
                        "concurrent content-addressed write produced different bytes"
                    )
                return
            os.replace(temporary, destination)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def put(
        self,
        *,
        snapshot_id: str,
        uri: str,
        fetched_at: str,
        medium: str,
        raw: bytes,
        normalized: str | None = None,
        normalizer_version: str,
    ) -> SourceSnapshot:
        """Persist immutable bytes and return their strict snapshot metadata."""

        if type(raw) is not bytes:
            raise TypeError("raw must be bytes")
        derived_normalized = self._derive_normalized(
            raw=raw, medium=medium, normalizer_id=normalizer_version
        )
        if normalized is not None:
            if type(normalized) is not str:
                raise TypeError("normalized must be a string or null")
            if normalized != derived_normalized:
                raise SnapshotNormalizationError(
                    "caller-supplied normalized text disagrees with trusted normalizer"
                )
        normalized_bytes = derived_normalized.encode("utf-8", errors="strict")
        self._check_payload(raw, limit=self.max_raw_bytes, label="raw")
        self._check_payload(
            normalized_bytes,
            limit=self.max_normalized_bytes,
            label="normalized",
        )

        raw_digest = sha256_bytes(raw)
        normalized_digest = sha256_bytes(normalized_bytes)
        raw_path = self._raw_relative(raw_digest)
        normalized_path = self._normalized_relative(normalized_digest)
        self._atomic_write(raw_path, raw, limit=self.max_raw_bytes)
        self._atomic_write(
            normalized_path,
            normalized_bytes,
            limit=self.max_normalized_bytes,
        )

        snapshot = SourceSnapshot(
            id=snapshot_id,
            uri=uri,
            fetched_at=fetched_at,
            medium=medium,
            raw_sha256=raw_digest,
            normalized_sha256=normalized_digest,
            raw_size=len(raw),
            normalized_size=len(normalized_bytes),
            raw_path=raw_path,
            normalized_path=normalized_path,
            normalizer_version=normalizer_version,
        )
        self.verify(snapshot)
        return snapshot

    store_snapshot = put

    def _verified_payloads(self, snapshot: SourceSnapshot) -> tuple[bytes, bytes]:
        if not isinstance(snapshot, SourceSnapshot):
            raise TypeError("snapshot must be a SourceSnapshot")
        if snapshot.raw_size > self.max_raw_bytes:
            raise SnapshotByteLimitError("snapshot raw_size exceeds store limit")
        if snapshot.normalized_size > self.max_normalized_bytes:
            raise SnapshotByteLimitError("snapshot normalized_size exceeds store limit")

        expected_raw_path = self._raw_relative(snapshot.raw_sha256)
        expected_normalized_path = self._normalized_relative(
            snapshot.normalized_sha256
        )
        if snapshot.raw_path != expected_raw_path:
            raise SnapshotSecurityError(
                "raw_path does not match its content-addressed digest path"
            )
        if snapshot.normalized_path != expected_normalized_path:
            raise SnapshotSecurityError(
                "normalized_path does not match its content-addressed digest path"
            )

        raw = self._read_relative(snapshot.raw_path, limit=self.max_raw_bytes)
        normalized = self._read_relative(
            snapshot.normalized_path, limit=self.max_normalized_bytes
        )
        if len(raw) != snapshot.raw_size:
            raise SnapshotIntegrityError("raw snapshot size mismatch")
        if len(normalized) != snapshot.normalized_size:
            raise SnapshotIntegrityError("normalized snapshot size mismatch")
        if sha256_bytes(raw) != snapshot.raw_sha256:
            raise SnapshotIntegrityError("raw snapshot SHA256 mismatch")
        if sha256_bytes(normalized) != snapshot.normalized_sha256:
            raise SnapshotIntegrityError("normalized snapshot SHA256 mismatch")
        try:
            normalized_text = normalized.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SnapshotIntegrityError("normalized snapshot is not strict UTF-8") from exc
        recomputed = self._derive_normalized(
            raw=raw,
            medium=snapshot.medium,
            normalizer_id=snapshot.normalizer_version,
        )
        if recomputed != normalized_text:
            raise SnapshotIntegrityError(
                "stored normalized snapshot does not match trusted raw normalization"
            )
        return raw, normalized

    def verify(self, snapshot: SourceSnapshot) -> None:
        """Raise unless paths, sizes, digests, and UTF-8 bytes are intact."""

        self._verified_payloads(snapshot)

    def read_raw(self, snapshot: SourceSnapshot) -> bytes:
        return self._verified_payloads(snapshot)[0]

    def read_normalized(self, snapshot: SourceSnapshot) -> str:
        return self._verified_payloads(snapshot)[1].decode("utf-8", errors="strict")
