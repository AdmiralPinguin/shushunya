"""Validated leadership handoff from Iskandar to the Research Warband.

The directive deliberately stops at the leadership boundary.  Iskandar may
choose the research objective, depth, admissible source classes, error
tolerance, output shape, and escalation policy.  Search queries, URLs,
subquestions, hypotheses, claims, citations, locators, and the detailed work
plan belong exclusively to ResearchWarband.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


DIRECTIVE_KIND = "iskandar_research_directive"
DIRECTIVE_VERSION = 1
DIRECTIVE_DECISIONS = {"delegate", "needs_clarification", "escalate", "reject"}
RESEARCH_DEPTHS = {"brief", "standard", "deep", "exhaustive"}
SOURCE_POLICIES = {
    "primary_required",
    "authoritative_preferred",
    "balanced",
    "open_discovery",
}
ERROR_TOLERANCES = {"strict", "balanced", "exploratory"}
ANSWER_MODES = {
    "direct_answer",
    "research_brief",
    "investigation",
    "comparative_review",
    "source_map",
    "translation_analysis",
}
SOURCE_CLASSES = {
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

MODEL_FIELDS = {
    "decision",
    "research_objective",
    "depth",
    "source_policy",
    "error_tolerance",
    "answer_mode",
    "priorities",
    "allowed_source_classes",
    "prohibited_source_classes",
    "constraints",
    "success_conditions",
    "output_requirements",
    "escalation_conditions",
    "clarification_question",
}
MODEL_ECHO_FIELDS = {"task_id", "delegation_subject"}
DIRECTIVE_FIELDS = {
    "kind",
    "version",
    "task_id",
    "mission_id",
    "leader",
    "decision",
    "delegated_to",
    *MODEL_FIELDS - {"decision"},
}

# These fields are called out separately so a model receives a precise boundary
# error rather than only a generic unknown-field error.
DETAILED_RESEARCH_FIELDS = {
    "steps",
    "work_plan",
    "worker_plan",
    "research_plan",
    "search_plan",
    "reader_plan",
    "queries",
    "query_plan",
    "urls",
    "source_urls",
    "sources",
    "selected_sources",
    "subquestions",
    "question_tree",
    "hypotheses",
    "claims",
    "evidence",
    "citations",
    "excerpts",
    "locators",
    "timeline",
    "artifacts",
    "expected_artifacts",
}
LIST_FIELDS = (
    "priorities",
    "allowed_source_classes",
    "prohibited_source_classes",
    "constraints",
    "success_conditions",
    "output_requirements",
    "escalation_conditions",
)
MAX_TEXT_LENGTH = 4_000
MAX_LIST_ITEMS = 24
_FENCED_JSON_RE = re.compile(
    r"\A```json[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n```[ \t]*\Z",
)
_MODEL_DETAIL_PATTERNS = (
    ("URL", re.compile(r"(?i)\b(?:https?|hxxps?|ftp|file)\s*:\s*//")),
    ("URL", re.compile(r"(?i)\bwww\s*\.")),
    (
        "domain",
        re.compile(
            r"(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
            r"(?:com|org|net|edu|gov|mil|int|io|ai|dev|info|ru|uk|de|fr|jp|kr|cn)\b",
        ),
    ),
    ("DOI", re.compile(r"(?i)\bdoi\s*:\s*|\b10\.\d{4,9}/\S+")),
    ("arXiv identifier", re.compile(r"(?i)\barxiv\s*:\s*\d")),
    (
        "search operator",
        re.compile(
            r"(?i)(?<![a-z0-9_])(?:site|filetype|inurl|intitle|allinurl|allintitle|"
            r"cache|related)\s*:\s*\S+",
        ),
    ),
    (
        "explicit query",
        re.compile(r"(?i)\b(?:search\s+query|query|search|google|bing)\s*[:=]\s*\S+"),
    ),
    (
        "source selection",
        re.compile(r"(?i)\b(?:source|sources|url|uri|domain|website)\s*[:=]\s*\S+"),
    ),
    (
        "boolean query",
        re.compile(r"(?:\"[^\"\r\n]+\"|'[^'\r\n]+')\s+(?:AND|OR)\s+", re.IGNORECASE),
    ),
)
_CALLER_URL_TOKEN_RE = re.compile(
    r"(?i)(?:\b(?:https?|hxxps?|ftp|file)\s*:\s*//|\bwww\s*\.)\S+"
)
_CALLER_DETAIL_MARKER = "[caller-provided research detail]"


class IskandarDirectiveError(ValueError):
    """The leader answer or persisted directive is not safe to delegate."""


def _leadership_projection(value: Any) -> Any:
    """Hide worker-owned research detail from the leader model only.

    The exact commander order remains immutable and is still used for binding,
    persistence, and ResearchWarband handoff. Iskandar receives a semantic
    projection so it can choose policy without copying URLs or search syntax
    into its leadership directive.
    """

    if isinstance(value, dict):
        return {key: _leadership_projection(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_leadership_projection(item) for item in value]
    if not isinstance(value, str):
        return value
    projected = _CALLER_URL_TOKEN_RE.sub(_CALLER_DETAIL_MARKER, value)
    for _label, pattern in _MODEL_DETAIL_PATTERNS:
        projected = pattern.sub(_CALLER_DETAIL_MARKER, projected)
    return projected


def directive_request_payload(
    task: str,
    task_id: str,
    commander_order: dict[str, Any],
) -> dict[str, Any]:
    """Build the bounded model request for one leadership decision."""
    return {
        "task_id": task_id,
        "commander_order": _leadership_projection(commander_order),
        "delegation_subject": _leadership_projection(task),
        "caller_detail_projection": (
            "Exact caller URLs and search syntax are intentionally represented by "
            f"{_CALLER_DETAIL_MARKER}; ResearchWarband receives the immutable originals."
        ),
        "required_json_schema": {
            "decision": "delegate | needs_clarification | escalate | reject",
            "research_objective": "leadership-level research outcome",
            "depth": "brief | standard | deep | exhaustive",
            "source_policy": (
                "primary_required | authoritative_preferred | balanced | open_discovery"
            ),
            "error_tolerance": "strict | balanced | exploratory",
            "answer_mode": (
                "direct_answer | research_brief | investigation | comparative_review | "
                "source_map | translation_analysis"
            ),
            "priorities": ["ordered leadership priorities"],
            "allowed_source_classes": sorted(SOURCE_CLASSES),
            "prohibited_source_classes": sorted(SOURCE_CLASSES),
            "constraints": ["hard boundaries"],
            "success_conditions": ["outcome-level acceptance conditions"],
            "output_requirements": ["required result forms, not artifact paths"],
            "escalation_conditions": ["conditions requiring Iskandar or user input"],
            "clarification_question": (
                "one exact question only for needs_clarification; otherwise empty"
            ),
        },
        "decision_policy": {
            "default": (
                "Delegate with defensible research defaults and record them as policy choices."
            ),
            "ask_user_only_if": [
                "jurisdiction, edition, identity, or date range materially changes the answer and cannot be inferred",
                "the work needs private corpus access, credentials, paid acquisition, or user authority",
                "two legitimate interpretations lead to materially different deliverables and neither is a safe default",
            ],
            "do_not_ask_for": [
                "search queries, source selection, research depth, answer formatting, or other worker-owned details",
                "preferences for which a conventional and reversible default satisfies the request",
            ],
        },
        "forbidden_detailed_plan_fields": sorted(DETAILED_RESEARCH_FIELDS),
    }


def directive_model_instructions() -> str:
    literal_schema = (
        '{"decision":"delegate | needs_clarification | escalate | reject",'
        '"research_objective":"string","depth":"brief | standard | deep | exhaustive",'
        '"source_policy":"primary_required | authoritative_preferred | balanced | '
        'open_discovery","error_tolerance":"strict | balanced | exploratory",'
        '"answer_mode":"direct_answer | research_brief | investigation | comparative_review | '
        'source_map | translation_analysis","priorities":["string"],'
        '"allowed_source_classes":["strict source-class enum"],'
        '"prohibited_source_classes":["strict source-class enum"],'
        '"constraints":["string"],"success_conditions":["string"],'
        '"output_requirements":["string"],"escalation_conditions":["string"],'
        '"clarification_question":"string"}'
    )
    return (
        "Return one strict JSON object and nothing else. You are Iskandar, leader of the "
        "research warband. Make only the leadership-level decision: whether to delegate, "
        "the research objective and depth, admissible source classes, error tolerance, "
        "answer mode, priorities, hard constraints, success and escalation conditions. "
        "Do not produce search queries, URLs, selected sources, subquestions, hypotheses, "
        "claims, evidence, citations, excerpts, locators, a timeline, artifact paths, or a "
        "detailed work plan. ResearchWarband owns detailed planning, search, reading, "
        "evidence construction, analysis, writing, verification, and repair. Source class "
        "choices, search strategy, depth, and answer formatting have sensible defaults and are not "
        "reasons to stop. Use needs_clarification only when a material jurisdiction/edition/identity/"
        "date-range ambiguity cannot be inferred, private access or user authority is required, or two "
        "legitimate interpretations lead to materially different outcomes with no safe default. "
        f"items must be chosen only from {sorted(SOURCE_CLASSES)}. Preserve "
        "explicit caller constraints without expanding them into a plan. The object must "
        "contain exactly the fourteen literal top-level keys in this shape and no others: "
        f"{literal_schema}. Use JSON string arrays only. For needs_clarification provide one "
        "precise clarification_question; for every other decision use an empty string."
    )


def _model_payload(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        payload = content
    elif isinstance(content, str):
        raw = content.strip()
        fenced = _FENCED_JSON_RE.fullmatch(raw)
        serialized = fenced.group("body") if fenced else raw
        try:
            payload = json.loads(serialized)
        except json.JSONDecodeError as exc:
            raise IskandarDirectiveError(
                "Iskandar answer must be clean JSON or exactly one fenced-json block "
                f"without surrounding prose: {exc}",
            ) from exc
    else:
        raise IskandarDirectiveError("Iskandar answer must be a JSON object")
    if not isinstance(payload, dict):
        raise IskandarDirectiveError("Iskandar answer must be a JSON object")
    return payload


def _text(value: Any, field: str, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise IskandarDirectiveError(f"{field} must be a string")
    normalized = value.strip()
    if required and not normalized:
        raise IskandarDirectiveError(f"{field} must not be empty")
    if len(normalized) > MAX_TEXT_LENGTH:
        raise IskandarDirectiveError(f"{field} exceeds {MAX_TEXT_LENGTH} characters")
    if any(ord(char) < 32 and char not in "\r\n\t" for char in normalized):
        raise IskandarDirectiveError(f"{field} contains control characters")
    return normalized


def _enum(value: Any, field: str, allowed: set[str]) -> str:
    normalized = _text(value, field)
    if normalized not in allowed:
        raise IskandarDirectiveError(f"{field} must be one of {sorted(allowed)}")
    return normalized


def _strings(value: Any, field: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise IskandarDirectiveError(f"{field} must be a list")
    if len(value) > MAX_LIST_ITEMS:
        raise IskandarDirectiveError(f"{field} has more than {MAX_LIST_ITEMS} items")
    result = [_text(item, f"{field}[{index}]") for index, item in enumerate(value)]
    if required and not result:
        raise IskandarDirectiveError(f"{field} must not be empty")
    if len(set(result)) != len(result):
        raise IskandarDirectiveError(f"{field} contains duplicate items")
    return result


def _source_classes(value: Any, field: str) -> list[str]:
    values = _strings(value, field)
    invalid = sorted(item for item in values if item not in SOURCE_CLASSES)
    if invalid:
        raise IskandarDirectiveError(
            f"{field} must contain only source classes from {sorted(SOURCE_CLASSES)}; "
            f"invalid={invalid}",
        )
    return values


def _normalized_model_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return normalized.translate({
        ord("\u200b"): None,
        ord("\u200c"): None,
        ord("\u200d"): None,
        ord("\ufeff"): None,
    })


def _reject_model_detail_values(value: Any, path: str) -> None:
    """Reject detailed research hidden below otherwise valid model fields.

    This scans only the model answer.  Commander text is preserved separately
    and may legitimately contain a URL or exact document identifier supplied by
    the caller; it is never silently removed from the persisted order/directive.
    """
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized_key = unicodedata.normalize("NFKC", key).casefold()
            if normalized_key in DETAILED_RESEARCH_FIELDS:
                raise IskandarDirectiveError(
                    f"model content at {path}.{key} embeds forbidden detailed research field",
                )
            _reject_model_detail_values(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_model_detail_values(item, f"{path}[{index}]")
        return
    if not isinstance(value, str):
        return
    normalized = _normalized_model_text(value)
    for label, pattern in _MODEL_DETAIL_PATTERNS:
        if pattern.search(normalized):
            raise IskandarDirectiveError(
                f"model content at {path} embeds forbidden {label} or source-selection detail",
            )


def _validate_model_content_boundary(model_payload: dict[str, Any]) -> None:
    for field, value in model_payload.items():
        _reject_model_detail_values(value, field)
    _source_classes(
        model_payload.get("allowed_source_classes"), "allowed_source_classes",
    )
    _source_classes(
        model_payload.get("prohibited_source_classes"), "prohibited_source_classes",
    )


def _commander_items(value: Any, field: str) -> list[str]:
    if value is None:
        value = []
    if not isinstance(value, list):
        raise IskandarDirectiveError(f"commander_order.{field} must be a list")
    return list(dict.fromkeys(
        _text(item, f"commander_order.{field}[{index}]")
        for index, item in enumerate(value)
    ))


def _caller_first_union(caller_value: Any, model_value: Any, field: str) -> list[str]:
    caller_items = _commander_items(caller_value, field)
    model_items = _strings(model_value, field)
    merged = list(dict.fromkeys([*caller_items, *model_items]))
    if len(merged) > MAX_LIST_ITEMS:
        raise IskandarDirectiveError(
            f"merged {field} has more than {MAX_LIST_ITEMS} items",
        )
    return merged


def validate_iskandar_directive(
    payload: dict[str, Any],
    *,
    expected_task_id: str = "",
    expected_mission_id: str = "",
    require_delegation: bool = False,
) -> dict[str, Any]:
    """Validate and normalize an exact persisted Iskandar directive."""
    if not isinstance(payload, dict):
        raise IskandarDirectiveError("Iskandar directive must be an object")
    unknown = sorted(set(payload) - DIRECTIVE_FIELDS)
    if unknown:
        raise IskandarDirectiveError(f"Iskandar directive has unknown fields: {unknown}")
    missing = sorted(DIRECTIVE_FIELDS - set(payload))
    if missing:
        raise IskandarDirectiveError(f"Iskandar directive is missing fields: {missing}")
    if payload.get("kind") != DIRECTIVE_KIND:
        raise IskandarDirectiveError(f"kind must be {DIRECTIVE_KIND}")
    if payload.get("version") != DIRECTIVE_VERSION:
        raise IskandarDirectiveError(f"version must be {DIRECTIVE_VERSION}")

    task_id = _text(payload.get("task_id"), "task_id")
    mission_id = _text(payload.get("mission_id"), "mission_id")
    if expected_task_id and task_id != expected_task_id:
        raise IskandarDirectiveError("directive task_id does not match the run")
    if expected_mission_id and mission_id != expected_mission_id:
        raise IskandarDirectiveError("directive mission_id does not match the mission")
    if payload.get("leader") != "IskandarKhayon":
        raise IskandarDirectiveError("leader must be IskandarKhayon")

    decision = _enum(payload.get("decision"), "decision", DIRECTIVE_DECISIONS)
    delegated_to = _text(payload.get("delegated_to"), "delegated_to", required=False)
    if decision == "delegate" and delegated_to != "ResearchWarband":
        raise IskandarDirectiveError("delegated_to must be ResearchWarband for delegation")
    if decision != "delegate" and delegated_to:
        raise IskandarDirectiveError("non-delegation decisions must not name an execution backend")
    if require_delegation and decision != "delegate":
        raise IskandarDirectiveError("Iskandar did not authorize delegation to ResearchWarband")

    clarification_question = _text(
        payload.get("clarification_question"),
        "clarification_question",
        required=False,
    )
    if decision == "needs_clarification" and not clarification_question:
        raise IskandarDirectiveError(
            "needs_clarification requires one non-empty clarification_question",
        )
    if decision != "needs_clarification" and clarification_question:
        raise IskandarDirectiveError(
            "clarification_question must be empty unless decision is needs_clarification",
        )

    lists = {
        field: (
            _source_classes(payload.get(field), field)
            if field in {"allowed_source_classes", "prohibited_source_classes"}
            else _strings(
                payload.get(field),
                field,
                required=decision == "delegate" and field in {
                    "priorities",
                    "success_conditions",
                    "output_requirements",
                },
            )
        )
        for field in LIST_FIELDS
    }
    overlap = set(lists["allowed_source_classes"]) & set(
        lists["prohibited_source_classes"],
    )
    if overlap:
        raise IskandarDirectiveError(
            f"source classes cannot be both allowed and prohibited: {sorted(overlap)}",
        )

    return {
        "kind": DIRECTIVE_KIND,
        "version": DIRECTIVE_VERSION,
        "task_id": task_id,
        "mission_id": mission_id,
        "leader": "IskandarKhayon",
        "decision": decision,
        "delegated_to": delegated_to,
        "research_objective": _text(payload.get("research_objective"), "research_objective"),
        "depth": _enum(payload.get("depth"), "depth", RESEARCH_DEPTHS),
        "source_policy": _enum(payload.get("source_policy"), "source_policy", SOURCE_POLICIES),
        "error_tolerance": _enum(
            payload.get("error_tolerance"), "error_tolerance", ERROR_TOLERANCES,
        ),
        "answer_mode": _enum(payload.get("answer_mode"), "answer_mode", ANSWER_MODES),
        **lists,
        "clarification_question": clarification_question,
    }


def validate_directive_for_commander(
    payload: dict[str, Any],
    commander_order: dict[str, Any],
    **validation: Any,
) -> dict[str, Any]:
    """Validate identity and prove that commander boundaries were not dropped."""
    if not isinstance(commander_order, dict):
        raise IskandarDirectiveError("commander_order must be an object")
    directive = validate_iskandar_directive(payload, **validation)
    if str(commander_order.get("mission_id") or "") != directive["mission_id"]:
        raise IskandarDirectiveError("commander_order mission_id does not match the directive")
    if commander_order.get("from") != "Warmaster" or commander_order.get("to") != "IskandarKhayon":
        raise IskandarDirectiveError(
            "commander_order authority must be Warmaster -> IskandarKhayon",
        )
    bindings = (
        ("constraints", "constraints"),
        ("success_conditions", "success_conditions"),
        ("escalate_to_user_if", "escalation_conditions"),
    )
    for command_field, directive_field in bindings:
        caller_items = _commander_items(commander_order.get(command_field), command_field)
        missing = [item for item in caller_items if item not in directive[directive_field]]
        if missing:
            raise IskandarDirectiveError(
                f"directive dropped commander_order.{command_field}: {missing}",
            )

    # Re-apply the leadership/detail boundary to persisted directives.  The
    # original model response was checked while the directive was built, but a
    # production consumer must not trust that historical call site alone.
    # Commander-authored constraints may legitimately contain a caller URL or
    # exact document identifier, so remove only those exact bound values before
    # scanning the remaining (model-authored) leadership content.
    caller_bound = {
        "constraints": set(_commander_items(
            commander_order.get("constraints"), "constraints",
        )),
        "success_conditions": set(_commander_items(
            commander_order.get("success_conditions"), "success_conditions",
        )),
        "escalation_conditions": set(_commander_items(
            commander_order.get("escalate_to_user_if"), "escalate_to_user_if",
        )),
    }
    persisted_model_content = {
        field: (
            [
                item
                for item in directive[field]
                if item not in caller_bound.get(field, set())
            ]
            if field in LIST_FIELDS
            else directive[field]
        )
        for field in MODEL_FIELDS
        if field != "decision"
    }
    _validate_model_content_boundary(persisted_model_content)
    return directive


def build_iskandar_directive(
    model_decision: dict[str, Any],
    *,
    task_id: str,
    mission_id: str,
    commander_order: dict[str, Any],
) -> dict[str, Any]:
    """Turn one model answer into a commander-bound research directive."""
    if not isinstance(model_decision, dict) or model_decision.get("ok") is not True:
        raise IskandarDirectiveError("Iskandar model brain did not answer")
    model_payload = _model_payload(model_decision.get("content"))
    forbidden = sorted(set(model_payload) & DETAILED_RESEARCH_FIELDS)
    if forbidden:
        raise IskandarDirectiveError(
            f"Iskandar must not produce detailed research fields: {forbidden}",
        )
    unknown = sorted(set(model_payload) - MODEL_FIELDS - MODEL_ECHO_FIELDS)
    if unknown:
        raise IskandarDirectiveError(f"Iskandar answer has unknown fields: {unknown}")
    model_payload = {
        field: value
        for field, value in model_payload.items()
        if field not in MODEL_ECHO_FIELDS
    }
    missing = sorted(MODEL_FIELDS - set(model_payload))
    if missing:
        raise IskandarDirectiveError(f"Iskandar answer is missing fields: {missing}")
    _validate_model_content_boundary(model_payload)

    decision = _enum(model_payload.get("decision"), "decision", DIRECTIVE_DECISIONS)
    payload = {
        "kind": DIRECTIVE_KIND,
        "version": DIRECTIVE_VERSION,
        "task_id": task_id,
        "mission_id": mission_id,
        "leader": "IskandarKhayon",
        "decision": decision,
        "delegated_to": "ResearchWarband" if decision == "delegate" else "",
        "research_objective": model_payload.get("research_objective"),
        "depth": model_payload.get("depth"),
        "source_policy": model_payload.get("source_policy"),
        "error_tolerance": model_payload.get("error_tolerance"),
        "answer_mode": model_payload.get("answer_mode"),
        "priorities": model_payload.get("priorities"),
        "allowed_source_classes": model_payload.get("allowed_source_classes"),
        "prohibited_source_classes": model_payload.get("prohibited_source_classes"),
        "constraints": _caller_first_union(
            commander_order.get("constraints"), model_payload.get("constraints"), "constraints",
        ),
        "success_conditions": _caller_first_union(
            commander_order.get("success_conditions"),
            model_payload.get("success_conditions"),
            "success_conditions",
        ),
        "output_requirements": model_payload.get("output_requirements"),
        "escalation_conditions": _caller_first_union(
            commander_order.get("escalate_to_user_if"),
            model_payload.get("escalation_conditions"),
            "escalation_conditions",
        ),
        "clarification_question": model_payload.get("clarification_question"),
    }
    return validate_directive_for_commander(payload, commander_order)


def leadership_context_text(payload: dict[str, Any]) -> str:
    """Render validated leadership context without turning it into a plan."""
    directive = validate_iskandar_directive(payload, require_delegation=True)
    sections = [
        "ISKANDAR RESEARCH DIRECTIVE (leadership context, not a research plan):",
        f"Research objective: {directive['research_objective']}",
        f"Depth: {directive['depth']}",
        f"Source policy: {directive['source_policy']}",
        f"Error tolerance: {directive['error_tolerance']}",
        f"Answer mode: {directive['answer_mode']}",
    ]
    labels = (
        ("priorities", "Priorities"),
        ("allowed_source_classes", "Allowed source classes"),
        ("prohibited_source_classes", "Prohibited source classes"),
        ("constraints", "Constraints"),
        ("success_conditions", "Success conditions"),
        ("output_requirements", "Output requirements"),
        ("escalation_conditions", "Escalate when"),
    )
    for field, label in labels:
        values = directive[field]
        if values:
            sections.append(label + ":\n" + "\n".join(f"- {item}" for item in values))
    sections.append(
        "ResearchWarband owns subquestions, hypotheses, search queries, source selection, "
        "reading, claim/evidence construction, analysis, writing, verification, and repair. "
        "Do not treat this directive as a detailed research plan.",
    )
    return "\n\n".join(sections)


__all__ = [
    "ANSWER_MODES",
    "DETAILED_RESEARCH_FIELDS",
    "DIRECTIVE_DECISIONS",
    "DIRECTIVE_KIND",
    "DIRECTIVE_VERSION",
    "ERROR_TOLERANCES",
    "IskandarDirectiveError",
    "RESEARCH_DEPTHS",
    "SOURCE_CLASSES",
    "SOURCE_POLICIES",
    "build_iskandar_directive",
    "directive_model_instructions",
    "directive_request_payload",
    "leadership_context_text",
    "validate_directive_for_commander",
    "validate_iskandar_directive",
]
