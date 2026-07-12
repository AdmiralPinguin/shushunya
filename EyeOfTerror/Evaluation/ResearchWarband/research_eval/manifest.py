"""Strict suite-manifest loading without third-party dependencies."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


MAX_JSON_BYTES = 4 * 1024 * 1024
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    """The suite is malformed, ambiguous, or points outside its allowed root."""


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ManifestError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def strict_json_load(path: Path, *, max_bytes: int = MAX_JSON_BYTES) -> tuple[Any, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManifestError(f"cannot read JSON file {path}: {exc}") from exc
    if len(raw) > max_bytes:
        raise ManifestError(f"JSON file exceeds {max_bytes} bytes: {path}")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"invalid UTF-8 JSON in {path}: {exc}") from exc
    return value, raw


def require_object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{where} must be an object")
    return value


def require_list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(f"{where} must be an array")
    return value


def exact_keys(value: dict[str, Any], *, allowed: Iterable[str], required: Iterable[str], where: str) -> None:
    allowed_set, required_set = set(allowed), set(required)
    unknown = sorted(set(value) - allowed_set)
    missing = sorted(required_set - set(value))
    if unknown:
        raise ManifestError(f"{where} has unknown keys: {', '.join(unknown)}")
    if missing:
        raise ManifestError(f"{where} is missing keys: {', '.join(missing)}")


def _safe_resolve(root: Path, base: Path, relative: Any, where: str) -> Path:
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise ManifestError(f"{where} must be a non-empty relative path")
    candidate = (base / relative).resolve()
    root = root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ManifestError(f"{where} escapes the evaluation root") from exc
    return candidate


def _validate_fact(value: Any, where: str) -> None:
    fact = require_object(value, where)
    exact_keys(
        fact,
        allowed={"id", "claim_contains_all", "final_contains_all", "source_ids", "relations", "span_contains_all"},
        required={"id", "claim_contains_all", "final_contains_all", "source_ids", "relations", "span_contains_all"},
        where=where,
    )
    if not isinstance(fact["id"], str) or not _ID.fullmatch(fact["id"]):
        raise ManifestError(f"{where}.id is invalid")
    for key in ("claim_contains_all", "final_contains_all", "source_ids", "relations", "span_contains_all"):
        items = require_list(fact[key], f"{where}.{key}")
        if not items or any(not isinstance(item, str) or not item for item in items):
            raise ManifestError(f"{where}.{key} must contain non-empty strings")


def _validate_oracle(value: Any, where: str) -> None:
    oracle = require_object(value, where)
    exact_keys(
        oracle,
        allowed={"required_facts", "forbidden_claims", "required_conflicts", "required_gap_codes", "clarification", "max_unsupported_major_claims"},
        required={"required_facts", "forbidden_claims", "required_conflicts", "required_gap_codes", "max_unsupported_major_claims"},
        where=where,
    )
    facts = require_list(oracle["required_facts"], f"{where}.required_facts")
    for index, fact in enumerate(facts):
        _validate_fact(fact, f"{where}.required_facts[{index}]")
    fact_ids = [fact["id"] for fact in facts]
    if len(fact_ids) != len(set(fact_ids)):
        raise ManifestError(f"{where} has duplicate fact ids")
    for index, value in enumerate(require_list(oracle["forbidden_claims"], f"{where}.forbidden_claims")):
        item = require_object(value, f"{where}.forbidden_claims[{index}]")
        exact_keys(item, allowed={"id", "contains_all"}, required={"id", "contains_all"}, where=f"{where}.forbidden_claims[{index}]")
        if not _ID.fullmatch(str(item["id"])):
            raise ManifestError(f"{where}.forbidden_claims[{index}].id is invalid")
        terms = require_list(item["contains_all"], f"{where}.forbidden_claims[{index}].contains_all")
        if not terms or any(not isinstance(term, str) or not term for term in terms):
            raise ManifestError(f"{where}.forbidden_claims[{index}] has invalid terms")
    for index, value in enumerate(require_list(oracle["required_conflicts"], f"{where}.required_conflicts")):
        item = require_object(value, f"{where}.required_conflicts[{index}]")
        exact_keys(item, allowed={"id", "left_fact_id", "right_fact_id"}, required={"id", "left_fact_id", "right_fact_id"}, where=f"{where}.required_conflicts[{index}]")
        if item["left_fact_id"] not in fact_ids or item["right_fact_id"] not in fact_ids:
            raise ManifestError(f"{where}.required_conflicts[{index}] references an unknown fact")
    gaps = require_list(oracle["required_gap_codes"], f"{where}.required_gap_codes")
    if any(not isinstance(code, str) or not _ID.fullmatch(code) for code in gaps):
        raise ManifestError(f"{where}.required_gap_codes contains an invalid code")
    if type(oracle["max_unsupported_major_claims"]) is not int or oracle["max_unsupported_major_claims"] < 0:
        raise ManifestError(f"{where}.max_unsupported_major_claims must be a non-negative integer")
    if "clarification" in oracle:
        clarification = require_object(oracle["clarification"], f"{where}.clarification")
        exact_keys(clarification, allowed={"min_chars", "contains_any"}, required={"min_chars", "contains_any"}, where=f"{where}.clarification")
        if type(clarification["min_chars"]) is not int or clarification["min_chars"] < 1:
            raise ManifestError(f"{where}.clarification.min_chars is invalid")
        terms = require_list(clarification["contains_any"], f"{where}.clarification.contains_any")
        if not terms or any(not isinstance(term, str) or not term for term in terms):
            raise ManifestError(f"{where}.clarification.contains_any is invalid")


@dataclass(frozen=True)
class LoadedSuite:
    path: Path
    root: Path
    data: dict[str, Any]
    raw_sha256: str
    fixture_path: Path

    @property
    def tasks(self) -> list[dict[str, Any]]:
        return self.data["tasks"]


def load_suite(path: str | Path, *, allowed_root: str | Path | None = None) -> LoadedSuite:
    suite_path = Path(path).resolve()
    if allowed_root is None:
        try:
            root = suite_path.parents[2]
        except IndexError as exc:
            raise ManifestError("suite path is too shallow") from exc
    else:
        root = Path(allowed_root).resolve()
    try:
        suite_path.relative_to(root)
    except ValueError as exc:
        raise ManifestError("suite manifest is outside the evaluation root") from exc
    value, raw = strict_json_load(suite_path)
    suite = require_object(value, "suite")
    exact_keys(
        suite,
        allowed={"schema_version", "suite_id", "suite_kind", "visibility", "fixture_manifest", "fixture_sha256", "tasks"},
        required={"schema_version", "suite_id", "suite_kind", "visibility", "fixture_manifest", "fixture_sha256", "tasks"},
        where="suite",
    )
    if suite["schema_version"] != 1:
        raise ManifestError("unsupported suite schema_version")
    if not isinstance(suite["suite_id"], str) or not _ID.fullmatch(suite["suite_id"]):
        raise ManifestError("suite_id is invalid")
    if suite["suite_kind"] not in {"public_smoke", "private_cutover", "canary"}:
        raise ManifestError("suite_kind is invalid")
    if suite["visibility"] not in {"public", "private"}:
        raise ManifestError("visibility is invalid")
    if not isinstance(suite["fixture_sha256"], str) or not _SHA256.fullmatch(suite["fixture_sha256"]):
        raise ManifestError("fixture_sha256 is invalid")
    fixture_path = _safe_resolve(root, suite_path.parent, suite["fixture_manifest"], "fixture_manifest")
    tasks = require_list(suite["tasks"], "suite.tasks")
    if not tasks:
        raise ManifestError("suite.tasks must not be empty")
    ids: list[str] = []
    for index, value in enumerate(tasks):
        where = f"suite.tasks[{index}]"
        task = require_object(value, where)
        exact_keys(task, allowed={"id", "category", "request", "limits", "expected_outcomes", "oracle"}, required={"id", "category", "request", "limits", "expected_outcomes", "oracle"}, where=where)
        if not isinstance(task["id"], str) or not _ID.fullmatch(task["id"]):
            raise ManifestError(f"{where}.id is invalid")
        ids.append(task["id"])
        if not isinstance(task["category"], str) or not _ID.fullmatch(task["category"]):
            raise ManifestError(f"{where}.category is invalid")
        request = require_object(task["request"], f"{where}.request")
        exact_keys(request, allowed={"goal", "output_contract_version"}, required={"goal", "output_contract_version"}, where=f"{where}.request")
        if not isinstance(request["goal"], str) or not request["goal"].strip():
            raise ManifestError(f"{where}.request.goal is invalid")
        if request["output_contract_version"] != "research-result/v1":
            raise ManifestError(f"{where}.request.output_contract_version is unsupported")
        limits = require_object(task["limits"], f"{where}.limits")
        exact_keys(limits, allowed={"wall_sec", "max_result_bytes"}, required={"wall_sec", "max_result_bytes"}, where=f"{where}.limits")
        if any(type(limits[key]) is not int or limits[key] < 1 for key in ("wall_sec", "max_result_bytes")):
            raise ManifestError(f"{where}.limits is invalid")
        outcomes = require_list(task["expected_outcomes"], f"{where}.expected_outcomes")
        if not outcomes or any(outcome not in {"accepted", "needs_user", "blocked"} for outcome in outcomes):
            raise ManifestError(f"{where}.expected_outcomes is invalid")
        _validate_oracle(task["oracle"], f"{where}.oracle")
    if len(ids) != len(set(ids)):
        raise ManifestError("suite has duplicate task ids")
    return LoadedSuite(
        path=suite_path,
        root=root,
        data=suite,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        fixture_path=fixture_path,
    )
