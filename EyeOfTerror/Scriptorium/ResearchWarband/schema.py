"""Strict, stdlib-only evidence ledger schemas.

The schemas deliberately distinguish provenance, semantic entailment, and truth.
They can record that a context-isolated review pass judged an excerpt to entail a claim;
they do not assert that either the source or the claim is true.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
import re
from typing import Any, ClassVar, Mapping, TypeAlias


SCHEMA_VERSION = "1.0"
MEDIA = frozenset({"text", "html", "pdf", "epub", "fb2"})
SOURCE_CLASSES = frozenset(
    {
        "primary_source",
        "official_documentation",
        "standards_specification",
        "legal_or_regulatory",
        "peer_reviewed_research",
        "scholarly_secondary",
        "reputable_journalism",
        "archival_catalog",
        "user_provided_corpus",
        "community_source",
        "anonymous_or_unverified_web",
        "machine_generated_summary",
    }
)
CLAIM_KINDS = frozenset(
    {"source_assertion", "direct_observation", "inference", "assumption"}
)
CLAIM_IMPORTANCE = frozenset({"major", "minor"})
VERIFICATION_STATUSES = frozenset(
    {"unverified", "entailed", "not_entailed", "uncertain", "contested"}
)
EVIDENCE_RELATIONS = frozenset(
    {"reports", "supports", "refutes", "qualifies", "context"}
)
ENTAILMENT_STATUSES = frozenset(
    {"unreviewed", "entailed", "not_entailed", "uncertain"}
)
CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})
GAP_STATUSES = frozenset({"open", "blocked", "resolved"})
HYPOTHESIS_STATUSES = frozenset(
    {"proposed", "supported", "weakened", "rejected", "undetermined"}
)

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SchemaError(ValueError):
    """Raised when untrusted ledger data violates the schema."""


def _strict_mapping(
    value: Any, *, required: frozenset[str], context: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaError(f"{context} must be an object")
    if any(type(key) is not str for key in value):
        raise SchemaError(f"{context} keys must be strings")
    keys = set(value)
    missing = required - keys
    unknown = keys - required
    if missing:
        raise SchemaError(f"{context} missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise SchemaError(f"{context} unknown fields: {', '.join(sorted(unknown))}")
    return value


def _string(value: Any, context: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise SchemaError(f"{context} must be a string")
    if not allow_empty and not value.strip():
        raise SchemaError(f"{context} must not be empty")
    return value


def _optional_string(value: Any, context: str) -> str | None:
    if value is None:
        return None
    return _string(value, context)


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    if type(value) is not int:
        raise SchemaError(f"{context} must be an integer")
    if value < minimum:
        raise SchemaError(f"{context} must be >= {minimum}")
    return value


def _identifier(value: Any, context: str) -> str:
    result = _string(value, context)
    if not _ID_RE.fullmatch(result):
        raise SchemaError(f"{context} is not a valid identifier")
    return result


def _choice(value: Any, choices: frozenset[str], context: str) -> str:
    result = _string(value, context)
    if result not in choices:
        raise SchemaError(
            f"{context} must be one of: {', '.join(sorted(choices))}"
        )
    return result


def _sha256(value: Any, context: str) -> str:
    result = _string(value, context)
    if not _SHA256_RE.fullmatch(result):
        raise SchemaError(f"{context} must be a lowercase SHA256 hex digest")
    return result


def _timestamp(value: Any, context: str) -> str:
    result = _string(value, context)
    candidate = result[:-1] + "+00:00" if result.endswith("Z") else result
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise SchemaError(f"{context} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SchemaError(f"{context} must include a UTC offset")
    return result


def _relative_store_path(value: Any, context: str) -> str:
    result = _string(value, context)
    if "\\" in result:
        raise SchemaError(f"{context} must use '/' separators")
    path = PurePosixPath(result)
    if path.is_absolute() or not path.parts:
        raise SchemaError(f"{context} must be relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise SchemaError(f"{context} contains an unsafe path component")
    return result


def _tuple_of_strings(value: Any, context: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise SchemaError(f"{context} must be a tuple")
    result = tuple(_string(item, f"{context} item") for item in value)
    if len(set(result)) != len(result):
        raise SchemaError(f"{context} must not contain duplicates")
    return result


def _tuple_of_ids(value: Any, context: str) -> tuple[str, ...]:
    result = _tuple_of_strings(value, context)
    for item in result:
        _identifier(item, f"{context} item")
    return result


def _json_string_tuple(value: Any, context: str, *, ids: bool = False) -> tuple[str, ...]:
    if type(value) is not list:
        raise SchemaError(f"{context} must be an array")
    converted = tuple(value)
    return _tuple_of_ids(converted, context) if ids else _tuple_of_strings(converted, context)


def _bounds(start: Any, end: Any, context: str) -> tuple[int, int]:
    checked_start = _integer(start, f"{context}.start_char")
    checked_end = _integer(end, f"{context}.end_char")
    if checked_end <= checked_start:
        raise SchemaError(f"{context}.end_char must be greater than start_char")
    return checked_start, checked_end


@dataclass(frozen=True, slots=True)
class TextLocator:
    start_char: int
    end_char: int
    medium: ClassVar[str] = "text"

    def __post_init__(self) -> None:
        _bounds(self.start_char, self.end_char, "TextLocator")

    def to_dict(self) -> dict[str, Any]:
        return {"medium": self.medium, "start_char": self.start_char, "end_char": self.end_char}

    @classmethod
    def from_dict(cls, value: Any) -> "TextLocator":
        data = _strict_mapping(
            value,
            required=frozenset({"medium", "start_char", "end_char"}),
            context="TextLocator",
        )
        if data["medium"] != cls.medium:
            raise SchemaError("TextLocator.medium must be 'text'")
        return cls(data["start_char"], data["end_char"])


@dataclass(frozen=True, slots=True)
class HtmlLocator:
    selector: str
    start_char: int
    end_char: int
    medium: ClassVar[str] = "html"

    def __post_init__(self) -> None:
        _string(self.selector, "HtmlLocator.selector")
        _bounds(self.start_char, self.end_char, "HtmlLocator")

    def to_dict(self) -> dict[str, Any]:
        return {
            "medium": self.medium,
            "selector": self.selector,
            "start_char": self.start_char,
            "end_char": self.end_char,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "HtmlLocator":
        data = _strict_mapping(
            value,
            required=frozenset({"medium", "selector", "start_char", "end_char"}),
            context="HtmlLocator",
        )
        if data["medium"] != cls.medium:
            raise SchemaError("HtmlLocator.medium must be 'html'")
        return cls(data["selector"], data["start_char"], data["end_char"])


@dataclass(frozen=True, slots=True)
class PdfLocator:
    page: int
    start_char: int
    end_char: int
    medium: ClassVar[str] = "pdf"

    def __post_init__(self) -> None:
        _integer(self.page, "PdfLocator.page", minimum=1)
        _bounds(self.start_char, self.end_char, "PdfLocator")

    def to_dict(self) -> dict[str, Any]:
        return {
            "medium": self.medium,
            "page": self.page,
            "start_char": self.start_char,
            "end_char": self.end_char,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "PdfLocator":
        data = _strict_mapping(
            value,
            required=frozenset({"medium", "page", "start_char", "end_char"}),
            context="PdfLocator",
        )
        if data["medium"] != cls.medium:
            raise SchemaError("PdfLocator.medium must be 'pdf'")
        return cls(data["page"], data["start_char"], data["end_char"])


@dataclass(frozen=True, slots=True)
class EpubLocator:
    spine_index: int
    href: str
    start_char: int
    end_char: int
    medium: ClassVar[str] = "epub"

    def __post_init__(self) -> None:
        _integer(self.spine_index, "EpubLocator.spine_index")
        _string(self.href, "EpubLocator.href")
        _bounds(self.start_char, self.end_char, "EpubLocator")

    def to_dict(self) -> dict[str, Any]:
        return {
            "medium": self.medium,
            "spine_index": self.spine_index,
            "href": self.href,
            "start_char": self.start_char,
            "end_char": self.end_char,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "EpubLocator":
        data = _strict_mapping(
            value,
            required=frozenset(
                {"medium", "spine_index", "href", "start_char", "end_char"}
            ),
            context="EpubLocator",
        )
        if data["medium"] != cls.medium:
            raise SchemaError("EpubLocator.medium must be 'epub'")
        return cls(
            data["spine_index"], data["href"], data["start_char"], data["end_char"]
        )


@dataclass(frozen=True, slots=True)
class Fb2Locator:
    section_index: int
    paragraph_index: int
    start_char: int
    end_char: int
    medium: ClassVar[str] = "fb2"

    def __post_init__(self) -> None:
        _integer(self.section_index, "Fb2Locator.section_index")
        _integer(self.paragraph_index, "Fb2Locator.paragraph_index")
        _bounds(self.start_char, self.end_char, "Fb2Locator")

    def to_dict(self) -> dict[str, Any]:
        return {
            "medium": self.medium,
            "section_index": self.section_index,
            "paragraph_index": self.paragraph_index,
            "start_char": self.start_char,
            "end_char": self.end_char,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "Fb2Locator":
        data = _strict_mapping(
            value,
            required=frozenset(
                {
                    "medium",
                    "section_index",
                    "paragraph_index",
                    "start_char",
                    "end_char",
                }
            ),
            context="Fb2Locator",
        )
        if data["medium"] != cls.medium:
            raise SchemaError("Fb2Locator.medium must be 'fb2'")
        return cls(
            data["section_index"],
            data["paragraph_index"],
            data["start_char"],
            data["end_char"],
        )


Locator: TypeAlias = TextLocator | HtmlLocator | PdfLocator | EpubLocator | Fb2Locator
_LOCATOR_TYPES = {
    "text": TextLocator,
    "html": HtmlLocator,
    "pdf": PdfLocator,
    "epub": EpubLocator,
    "fb2": Fb2Locator,
}


def locator_from_dict(value: Any) -> Locator:
    if not isinstance(value, Mapping):
        raise SchemaError("locator must be an object")
    medium = value.get("medium")
    if type(medium) is not str or medium not in _LOCATOR_TYPES:
        raise SchemaError("locator.medium is missing or unsupported")
    return _LOCATOR_TYPES[medium].from_dict(value)


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    id: str
    uri: str
    fetched_at: str
    medium: str
    raw_sha256: str
    normalized_sha256: str
    raw_size: int
    normalized_size: int
    raw_path: str
    normalized_path: str
    normalizer_version: str
    # ``unknown`` exists only for backwards-compatible evidence-core objects.
    # The execution engine rejects it before acquisition or acceptance.
    source_class: str = "unknown"
    source_classifier_id: str = "unknown"

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "uri",
            "fetched_at",
            "medium",
            "raw_sha256",
            "normalized_sha256",
            "raw_size",
            "normalized_size",
            "raw_path",
            "normalized_path",
            "normalizer_version",
            "source_class",
            "source_classifier_id",
        }
    )

    def __post_init__(self) -> None:
        _identifier(self.id, "SourceSnapshot.id")
        _string(self.uri, "SourceSnapshot.uri")
        _timestamp(self.fetched_at, "SourceSnapshot.fetched_at")
        _choice(self.medium, MEDIA, "SourceSnapshot.medium")
        _sha256(self.raw_sha256, "SourceSnapshot.raw_sha256")
        _sha256(self.normalized_sha256, "SourceSnapshot.normalized_sha256")
        _integer(self.raw_size, "SourceSnapshot.raw_size")
        _integer(self.normalized_size, "SourceSnapshot.normalized_size")
        _relative_store_path(self.raw_path, "SourceSnapshot.raw_path")
        _relative_store_path(self.normalized_path, "SourceSnapshot.normalized_path")
        _string(self.normalizer_version, "SourceSnapshot.normalizer_version")
        if self.source_class != "unknown":
            _choice(self.source_class, SOURCE_CLASSES, "SourceSnapshot.source_class")
        _string(self.source_classifier_id, "SourceSnapshot.source_classifier_id")

    def to_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in self._FIELDS}

    @classmethod
    def from_dict(cls, value: Any) -> "SourceSnapshot":
        data = _strict_mapping(value, required=cls._FIELDS, context="SourceSnapshot")
        return cls(**{field: data[field] for field in cls._FIELDS})


@dataclass(frozen=True, slots=True)
class SourceSpan:
    id: str
    snapshot_id: str
    locator: Locator
    excerpt: str

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"id", "snapshot_id", "locator", "excerpt"}
    )

    def __post_init__(self) -> None:
        _identifier(self.id, "SourceSpan.id")
        _identifier(self.snapshot_id, "SourceSpan.snapshot_id")
        if not isinstance(self.locator, tuple(_LOCATOR_TYPES.values())):
            raise SchemaError("SourceSpan.locator has an unsupported type")
        _string(self.excerpt, "SourceSpan.excerpt")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "snapshot_id": self.snapshot_id,
            "locator": self.locator.to_dict(),
            "excerpt": self.excerpt,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SourceSpan":
        data = _strict_mapping(value, required=cls._FIELDS, context="SourceSpan")
        return cls(
            id=data["id"],
            snapshot_id=data["snapshot_id"],
            locator=locator_from_dict(data["locator"]),
            excerpt=data["excerpt"],
        )


@dataclass(frozen=True, slots=True)
class Claim:
    id: str
    text: str
    kind: str
    importance: str
    verification_status: str
    authored_by: str
    verified_by: str | None
    confidence: str
    conflict_claim_ids: tuple[str, ...]

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "text",
            "kind",
            "importance",
            "verification_status",
            "authored_by",
            "verified_by",
            "confidence",
            "conflict_claim_ids",
        }
    )

    def __post_init__(self) -> None:
        _identifier(self.id, "Claim.id")
        _string(self.text, "Claim.text")
        _choice(self.kind, CLAIM_KINDS, "Claim.kind")
        _choice(self.importance, CLAIM_IMPORTANCE, "Claim.importance")
        _choice(
            self.verification_status,
            VERIFICATION_STATUSES,
            "Claim.verification_status",
        )
        _identifier(self.authored_by, "Claim.authored_by")
        _optional_string(self.verified_by, "Claim.verified_by")
        if self.verified_by is not None:
            _identifier(self.verified_by, "Claim.verified_by")
        if self.verification_status == "unverified" and self.verified_by is not None:
            raise SchemaError("Claim.verified_by must be null while status is unverified")
        if self.verification_status != "unverified" and self.verified_by is None:
            raise SchemaError("Claim.verified_by is required for a reviewed status")
        _choice(self.confidence, CONFIDENCE_LEVELS, "Claim.confidence")
        _tuple_of_ids(self.conflict_claim_ids, "Claim.conflict_claim_ids")
        if self.id in self.conflict_claim_ids:
            raise SchemaError("Claim cannot conflict with itself")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "kind": self.kind,
            "importance": self.importance,
            "verification_status": self.verification_status,
            "authored_by": self.authored_by,
            "verified_by": self.verified_by,
            "confidence": self.confidence,
            "conflict_claim_ids": list(self.conflict_claim_ids),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "Claim":
        data = _strict_mapping(value, required=cls._FIELDS, context="Claim")
        return cls(
            id=data["id"],
            text=data["text"],
            kind=data["kind"],
            importance=data["importance"],
            verification_status=data["verification_status"],
            authored_by=data["authored_by"],
            verified_by=data["verified_by"],
            confidence=data["confidence"],
            conflict_claim_ids=_json_string_tuple(
                data["conflict_claim_ids"], "Claim.conflict_claim_ids", ids=True
            ),
        )


@dataclass(frozen=True, slots=True)
class EvidenceEdge:
    id: str
    claim_id: str
    span_id: str
    relation: str
    entailment_status: str
    assessed_by: str | None

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"id", "claim_id", "span_id", "relation", "entailment_status", "assessed_by"}
    )

    def __post_init__(self) -> None:
        _identifier(self.id, "EvidenceEdge.id")
        _identifier(self.claim_id, "EvidenceEdge.claim_id")
        _identifier(self.span_id, "EvidenceEdge.span_id")
        _choice(self.relation, EVIDENCE_RELATIONS, "EvidenceEdge.relation")
        _choice(
            self.entailment_status,
            ENTAILMENT_STATUSES,
            "EvidenceEdge.entailment_status",
        )
        _optional_string(self.assessed_by, "EvidenceEdge.assessed_by")
        if self.assessed_by is not None:
            _identifier(self.assessed_by, "EvidenceEdge.assessed_by")
        if self.entailment_status == "unreviewed" and self.assessed_by is not None:
            raise SchemaError(
                "EvidenceEdge.assessed_by must be null while entailment is unreviewed"
            )
        if self.entailment_status != "unreviewed" and self.assessed_by is None:
            raise SchemaError(
                "EvidenceEdge.assessed_by is required for reviewed entailment"
            )

    def to_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in self._FIELDS}

    @classmethod
    def from_dict(cls, value: Any) -> "EvidenceEdge":
        data = _strict_mapping(value, required=cls._FIELDS, context="EvidenceEdge")
        return cls(**{field: data[field] for field in cls._FIELDS})


@dataclass(frozen=True, slots=True)
class Inference:
    id: str
    conclusion_claim_id: str
    premise_claim_ids: tuple[str, ...]
    rationale: str
    authored_by: str

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"id", "conclusion_claim_id", "premise_claim_ids", "rationale", "authored_by"}
    )

    def __post_init__(self) -> None:
        _identifier(self.id, "Inference.id")
        _identifier(self.conclusion_claim_id, "Inference.conclusion_claim_id")
        premises = _tuple_of_ids(self.premise_claim_ids, "Inference.premise_claim_ids")
        if not premises:
            raise SchemaError("Inference.premise_claim_ids must not be empty")
        if self.conclusion_claim_id in premises:
            raise SchemaError("Inference conclusion cannot be its own premise")
        _string(self.rationale, "Inference.rationale")
        _identifier(self.authored_by, "Inference.authored_by")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conclusion_claim_id": self.conclusion_claim_id,
            "premise_claim_ids": list(self.premise_claim_ids),
            "rationale": self.rationale,
            "authored_by": self.authored_by,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "Inference":
        data = _strict_mapping(value, required=cls._FIELDS, context="Inference")
        return cls(
            id=data["id"],
            conclusion_claim_id=data["conclusion_claim_id"],
            premise_claim_ids=_json_string_tuple(
                data["premise_claim_ids"], "Inference.premise_claim_ids", ids=True
            ),
            rationale=data["rationale"],
            authored_by=data["authored_by"],
        )


@dataclass(frozen=True, slots=True)
class Gap:
    id: str
    question: str
    status: str
    related_claim_ids: tuple[str, ...]
    search_attempts: tuple[str, ...]

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"id", "question", "status", "related_claim_ids", "search_attempts"}
    )

    def __post_init__(self) -> None:
        _identifier(self.id, "Gap.id")
        _string(self.question, "Gap.question")
        _choice(self.status, GAP_STATUSES, "Gap.status")
        _tuple_of_ids(self.related_claim_ids, "Gap.related_claim_ids")
        _tuple_of_strings(self.search_attempts, "Gap.search_attempts")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "status": self.status,
            "related_claim_ids": list(self.related_claim_ids),
            "search_attempts": list(self.search_attempts),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "Gap":
        data = _strict_mapping(value, required=cls._FIELDS, context="Gap")
        return cls(
            id=data["id"],
            question=data["question"],
            status=data["status"],
            related_claim_ids=_json_string_tuple(
                data["related_claim_ids"], "Gap.related_claim_ids", ids=True
            ),
            search_attempts=_json_string_tuple(
                data["search_attempts"], "Gap.search_attempts"
            ),
        )


@dataclass(frozen=True, slots=True)
class Hypothesis:
    id: str
    text: str
    status: str
    supporting_claim_ids: tuple[str, ...]
    challenging_claim_ids: tuple[str, ...]
    gap_ids: tuple[str, ...]

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "text",
            "status",
            "supporting_claim_ids",
            "challenging_claim_ids",
            "gap_ids",
        }
    )

    def __post_init__(self) -> None:
        _identifier(self.id, "Hypothesis.id")
        _string(self.text, "Hypothesis.text")
        _choice(self.status, HYPOTHESIS_STATUSES, "Hypothesis.status")
        supporting = _tuple_of_ids(
            self.supporting_claim_ids, "Hypothesis.supporting_claim_ids"
        )
        challenging = _tuple_of_ids(
            self.challenging_claim_ids, "Hypothesis.challenging_claim_ids"
        )
        _tuple_of_ids(self.gap_ids, "Hypothesis.gap_ids")
        overlap = set(supporting) & set(challenging)
        if overlap:
            raise SchemaError(
                "Hypothesis claims cannot be both supporting and challenging"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "status": self.status,
            "supporting_claim_ids": list(self.supporting_claim_ids),
            "challenging_claim_ids": list(self.challenging_claim_ids),
            "gap_ids": list(self.gap_ids),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "Hypothesis":
        data = _strict_mapping(value, required=cls._FIELDS, context="Hypothesis")
        return cls(
            id=data["id"],
            text=data["text"],
            status=data["status"],
            supporting_claim_ids=_json_string_tuple(
                data["supporting_claim_ids"],
                "Hypothesis.supporting_claim_ids",
                ids=True,
            ),
            challenging_claim_ids=_json_string_tuple(
                data["challenging_claim_ids"],
                "Hypothesis.challenging_claim_ids",
                ids=True,
            ),
            gap_ids=_json_string_tuple(data["gap_ids"], "Hypothesis.gap_ids", ids=True),
        )


@dataclass(frozen=True, slots=True)
class EvidenceLedger:
    schema_version: str
    snapshots: tuple[SourceSnapshot, ...]
    spans: tuple[SourceSpan, ...]
    claims: tuple[Claim, ...]
    edges: tuple[EvidenceEdge, ...]
    inferences: tuple[Inference, ...]
    gaps: tuple[Gap, ...]
    hypotheses: tuple[Hypothesis, ...]

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "snapshots",
            "spans",
            "claims",
            "edges",
            "inferences",
            "gaps",
            "hypotheses",
        }
    )

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError(f"unsupported schema_version: {self.schema_version!r}")
        collections = (
            ("snapshots", self.snapshots, SourceSnapshot),
            ("spans", self.spans, SourceSpan),
            ("claims", self.claims, Claim),
            ("edges", self.edges, EvidenceEdge),
            ("inferences", self.inferences, Inference),
            ("gaps", self.gaps, Gap),
            ("hypotheses", self.hypotheses, Hypothesis),
        )
        for name, values, expected in collections:
            if type(values) is not tuple:
                raise SchemaError(f"EvidenceLedger.{name} must be a tuple")
            if any(not isinstance(item, expected) for item in values):
                raise SchemaError(f"EvidenceLedger.{name} contains an invalid item")

        typed_ids: dict[str, set[str]] = {}
        global_ids: dict[str, str] = {}
        for name, values, _ in collections:
            ids: set[str] = set()
            for item in values:
                if item.id in ids:
                    raise SchemaError(f"duplicate {name} id: {item.id}")
                if item.id in global_ids:
                    raise SchemaError(
                        f"id {item.id!r} is shared by {global_ids[item.id]} and {name}"
                    )
                ids.add(item.id)
                global_ids[item.id] = name
            typed_ids[name] = ids

        snapshots_by_id = {item.id: item for item in self.snapshots}
        claims_by_id = {item.id: item for item in self.claims}
        for span in self.spans:
            snapshot = snapshots_by_id.get(span.snapshot_id)
            if snapshot is None:
                raise SchemaError(
                    f"SourceSpan {span.id} references missing snapshot {span.snapshot_id}"
                )
            if span.locator.medium != snapshot.medium:
                raise SchemaError(
                    f"SourceSpan {span.id} locator medium does not match snapshot"
                )

        for edge in self.edges:
            if edge.claim_id not in typed_ids["claims"]:
                raise SchemaError(
                    f"EvidenceEdge {edge.id} references missing claim {edge.claim_id}"
                )
            if edge.span_id not in typed_ids["spans"]:
                raise SchemaError(
                    f"EvidenceEdge {edge.id} references missing span {edge.span_id}"
                )

        for claim in self.claims:
            missing = set(claim.conflict_claim_ids) - typed_ids["claims"]
            if missing:
                raise SchemaError(
                    f"Claim {claim.id} has missing conflict refs: {', '.join(sorted(missing))}"
                )
            asymmetric = {
                other_id
                for other_id in claim.conflict_claim_ids
                if claim.id not in claims_by_id[other_id].conflict_claim_ids
            }
            if asymmetric:
                raise SchemaError(
                    f"Claim {claim.id} has asymmetric conflict refs: {', '.join(sorted(asymmetric))}"
                )

        conclusion_ids: set[str] = set()
        inference_by_conclusion: dict[str, Inference] = {}
        for inference in self.inferences:
            conclusion = claims_by_id.get(inference.conclusion_claim_id)
            if conclusion is None:
                raise SchemaError(
                    f"Inference {inference.id} references missing conclusion claim"
                )
            if conclusion.kind != "inference":
                raise SchemaError(
                    f"Inference {inference.id} conclusion claim is not kind=inference"
                )
            if inference.conclusion_claim_id in conclusion_ids:
                raise SchemaError(
                    f"multiple Inference records conclude {inference.conclusion_claim_id}"
                )
            missing = set(inference.premise_claim_ids) - typed_ids["claims"]
            if missing:
                raise SchemaError(
                    f"Inference {inference.id} has missing premises: {', '.join(sorted(missing))}"
                )
            conclusion_ids.add(inference.conclusion_claim_id)
            inference_by_conclusion[inference.conclusion_claim_id] = inference

        self._reject_inference_cycles(inference_by_conclusion)

        for gap in self.gaps:
            missing = set(gap.related_claim_ids) - typed_ids["claims"]
            if missing:
                raise SchemaError(
                    f"Gap {gap.id} has missing claim refs: {', '.join(sorted(missing))}"
                )

        for hypothesis in self.hypotheses:
            claim_refs = set(hypothesis.supporting_claim_ids) | set(
                hypothesis.challenging_claim_ids
            )
            missing_claims = claim_refs - typed_ids["claims"]
            if missing_claims:
                raise SchemaError(
                    f"Hypothesis {hypothesis.id} has missing claim refs: "
                    f"{', '.join(sorted(missing_claims))}"
                )
            missing_gaps = set(hypothesis.gap_ids) - typed_ids["gaps"]
            if missing_gaps:
                raise SchemaError(
                    f"Hypothesis {hypothesis.id} has missing gap refs: "
                    f"{', '.join(sorted(missing_gaps))}"
                )

    @staticmethod
    def _reject_inference_cycles(
        inference_by_conclusion: Mapping[str, Inference]
    ) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(claim_id: str) -> None:
            if claim_id in visiting:
                raise SchemaError(f"inference cycle detected at claim {claim_id}")
            if claim_id in visited:
                return
            visiting.add(claim_id)
            inference = inference_by_conclusion.get(claim_id)
            if inference is not None:
                for premise_id in inference.premise_claim_ids:
                    if premise_id in inference_by_conclusion:
                        visit(premise_id)
            visiting.remove(claim_id)
            visited.add(claim_id)

        for conclusion_id in inference_by_conclusion:
            visit(conclusion_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshots": [item.to_dict() for item in self.snapshots],
            "spans": [item.to_dict() for item in self.spans],
            "claims": [item.to_dict() for item in self.claims],
            "edges": [item.to_dict() for item in self.edges],
            "inferences": [item.to_dict() for item in self.inferences],
            "gaps": [item.to_dict() for item in self.gaps],
            "hypotheses": [item.to_dict() for item in self.hypotheses],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "EvidenceLedger":
        data = _strict_mapping(value, required=cls._FIELDS, context="EvidenceLedger")

        def parse_array(field: str, parser: Any) -> tuple[Any, ...]:
            raw = data[field]
            if type(raw) is not list:
                raise SchemaError(f"EvidenceLedger.{field} must be an array")
            return tuple(parser(item) for item in raw)

        return cls(
            schema_version=data["schema_version"],
            snapshots=parse_array("snapshots", SourceSnapshot.from_dict),
            spans=parse_array("spans", SourceSpan.from_dict),
            claims=parse_array("claims", Claim.from_dict),
            edges=parse_array("edges", EvidenceEdge.from_dict),
            inferences=parse_array("inferences", Inference.from_dict),
            gaps=parse_array("gaps", Gap.from_dict),
            hypotheses=parse_array("hypotheses", Hypothesis.from_dict),
        )
