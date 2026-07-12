"""Immutable fixture-bundle validation and access."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import ManifestError, exact_keys, require_list, require_object, strict_json_load


MAX_FIXTURE_FILE_BYTES = 8 * 1024 * 1024
MAX_FIXTURE_TOTAL_BYTES = 64 * 1024 * 1024
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FixtureError(ManifestError):
    """Fixture bytes disagree with their immutable manifest."""


def _contained(root: Path, relative: Any, where: str) -> Path:
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise FixtureError(f"{where} must be a non-empty relative path")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise FixtureError(f"{where} escapes the fixture bundle") from exc
    return candidate


@dataclass(frozen=True)
class FixtureDocument:
    data: dict[str, Any]
    raw_path: Path
    normalized_path: Path
    raw: bytes
    normalized: bytes

    @property
    def source_id(self) -> str:
        return self.data["source_id"]


@dataclass(frozen=True)
class LoadedFixture:
    path: Path
    root: Path
    data: dict[str, Any]
    raw_sha256: str
    documents: dict[str, FixtureDocument]

    def document(self, source_id: str) -> FixtureDocument:
        try:
            return self.documents[source_id]
        except KeyError as exc:
            raise FixtureError(f"unknown fixture source: {source_id}") from exc


def load_fixture(path: str | Path, *, expected_sha256: str = "") -> LoadedFixture:
    manifest_path = Path(path).resolve()
    value, manifest_raw = strict_json_load(manifest_path)
    actual_manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    if expected_sha256 and actual_manifest_sha != expected_sha256:
        raise FixtureError("fixture manifest sha256 mismatch")
    manifest = require_object(value, "fixture")
    exact_keys(
        manifest,
        allowed={"schema_version", "bundle_id", "closed_world", "normalizer_id", "documents", "search_rules", "explicit_routes"},
        required={"schema_version", "bundle_id", "closed_world", "normalizer_id", "documents", "search_rules", "explicit_routes"},
        where="fixture",
    )
    if manifest["schema_version"] != 1 or manifest["closed_world"] is not True:
        raise FixtureError("fixture must be schema v1 and closed_world=true")
    if not isinstance(manifest["bundle_id"], str) or not _ID.fullmatch(manifest["bundle_id"]):
        raise FixtureError("fixture.bundle_id is invalid")
    if manifest["normalizer_id"] != "identity-utf8-v1":
        raise FixtureError("unsupported fixture normalizer")
    root = manifest_path.parent.resolve()
    documents: dict[str, FixtureDocument] = {}
    routes: set[str] = set()
    total_bytes = 0
    document_keys = {"source_id", "route", "original_url", "status", "mime", "raw_path", "raw_sha256", "raw_bytes", "normalized_path", "normalized_sha256", "normalized_bytes"}
    for index, value in enumerate(require_list(manifest["documents"], "fixture.documents")):
        where = f"fixture.documents[{index}]"
        item = require_object(value, where)
        exact_keys(item, allowed=document_keys, required=document_keys, where=where)
        source_id = item["source_id"]
        if not isinstance(source_id, str) or not _ID.fullmatch(source_id) or source_id in documents:
            raise FixtureError(f"{where}.source_id is invalid or duplicate")
        route = item["route"]
        if not isinstance(route, str) or not route.startswith("/documents/") or route in routes or "?" in route:
            raise FixtureError(f"{where}.route is invalid or duplicate")
        routes.add(route)
        if item["status"] != 200 or not isinstance(item["mime"], str):
            raise FixtureError(f"{where} has unsupported HTTP metadata")
        for hash_key in ("raw_sha256", "normalized_sha256"):
            if not isinstance(item[hash_key], str) or not _SHA256.fullmatch(item[hash_key]):
                raise FixtureError(f"{where}.{hash_key} is invalid")
        raw_path = _contained(root, item["raw_path"], f"{where}.raw_path")
        normalized_path = _contained(root, item["normalized_path"], f"{where}.normalized_path")
        try:
            raw, normalized = raw_path.read_bytes(), normalized_path.read_bytes()
        except OSError as exc:
            raise FixtureError(f"cannot read fixture bytes for {source_id}: {exc}") from exc
        for kind, content, size_key, hash_key in (
            ("raw", raw, "raw_bytes", "raw_sha256"),
            ("normalized", normalized, "normalized_bytes", "normalized_sha256"),
        ):
            if type(item[size_key]) is not int or item[size_key] != len(content):
                raise FixtureError(f"{source_id} {kind} byte count mismatch")
            if len(content) > MAX_FIXTURE_FILE_BYTES:
                raise FixtureError(f"{source_id} {kind} exceeds the file limit")
            if hashlib.sha256(content).hexdigest() != item[hash_key]:
                raise FixtureError(f"{source_id} {kind} sha256 mismatch")
        try:
            normalized.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise FixtureError(f"{source_id} normalized representation is not UTF-8") from exc
        total_bytes += len(raw) + (0 if raw_path == normalized_path else len(normalized))
        if total_bytes > MAX_FIXTURE_TOTAL_BYTES:
            raise FixtureError("fixture bundle exceeds the total byte limit")
        documents[source_id] = FixtureDocument(item, raw_path, normalized_path, raw, normalized)
    if not documents:
        raise FixtureError("fixture.documents must not be empty")
    for index, value in enumerate(require_list(manifest["search_rules"], "fixture.search_rules")):
        where = f"fixture.search_rules[{index}]"
        item = require_object(value, where)
        exact_keys(item, allowed={"query_terms", "source_ids"}, required={"query_terms", "source_ids"}, where=where)
        terms = require_list(item["query_terms"], f"{where}.query_terms")
        source_ids = require_list(item["source_ids"], f"{where}.source_ids")
        if not terms or any(not isinstance(term, str) or not term for term in terms):
            raise FixtureError(f"{where}.query_terms is invalid")
        if not source_ids or any(source_id not in documents for source_id in source_ids):
            raise FixtureError(f"{where}.source_ids is invalid")
    explicit_paths: set[str] = set()
    for index, value in enumerate(require_list(manifest["explicit_routes"], "fixture.explicit_routes")):
        where = f"fixture.explicit_routes[{index}]"
        item = require_object(value, where)
        exact_keys(item, allowed={"path", "status"}, required={"path", "status"}, where=where)
        if not isinstance(item["path"], str) or not item["path"].startswith("/") or item["path"] in routes | explicit_paths:
            raise FixtureError(f"{where}.path is invalid or duplicate")
        if item["status"] not in {404, 410}:
            raise FixtureError(f"{where}.status is unsupported")
        explicit_paths.add(item["path"])
    return LoadedFixture(manifest_path, root, manifest, actual_manifest_sha, documents)
