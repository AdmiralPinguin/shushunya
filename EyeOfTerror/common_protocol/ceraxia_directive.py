"""Validated leadership handoff from Ceraxia to the Skitarii warband.

The directive deliberately carries only leadership-level intent and acceptance
boundaries.  Repository exploration, file selection, dependency ordering,
commands, and the detailed implementation plan belong to Skitarii.
"""
from __future__ import annotations

import json
import re
from typing import Any


DIRECTIVE_KIND = "ceraxia_leadership_directive"
DIRECTIVE_VERSION = 1
DIRECTIVE_DECISIONS = {"delegate", "needs_clarification", "escalate", "reject"}
MODEL_FIELDS = {
    "decision",
    "mission_intent",
    "priorities",
    "constraints",
    "success_conditions",
    "tradeoffs",
    "escalation_conditions",
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
    "mission_intent",
    "priorities",
    "constraints",
    "success_conditions",
    "tradeoffs",
    "escalation_conditions",
}
DETAILED_PLAN_FIELDS = {
    "steps",
    "work_plan",
    "worker_plan",
    "implementation_plan",
    "file_plan",
    "candidate_files",
    "files",
    "modules",
    "commands",
    "test_commands",
    "work_packages",
    "dependencies",
    "patch_plan",
}
LIST_FIELDS = (
    "priorities",
    "constraints",
    "success_conditions",
    "tradeoffs",
    "escalation_conditions",
)
MAX_TEXT_LENGTH = 4_000
MAX_LIST_ITEMS = 16
_FENCED_JSON_RE = re.compile(
    r"\A```json[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n```[ \t]*\Z",
)


class CeraxiaDirectiveError(ValueError):
    """The leader answer or persisted directive is not safe to delegate."""


def directive_request_payload(
    task: str,
    task_id: str,
    commander_order: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "commander_order": commander_order,
        "delegation_subject": task,
        "required_json_schema": {
            "decision": "delegate | needs_clarification | escalate | reject",
            "mission_intent": "leadership-level engineering outcome",
            "priorities": ["ordered leadership priorities"],
            "constraints": ["hard boundaries; preserve explicit caller constraints"],
            "success_conditions": ["outcome-level acceptance conditions"],
            "tradeoffs": ["leadership decisions and accepted tradeoffs"],
            "escalation_conditions": ["conditions Ceraxia must decide or escalate"],
        },
        "decision_policy": {
            "default": (
                "Delegate with sensible reversible engineering defaults; record those choices "
                "as leadership tradeoffs instead of asking the user."
            ),
            "ask_user_only_if": [
                "a material product preference has no safe default and changes the requested outcome",
                "the action needs user authority, credentials, money, publication, deletion, or another irreversible commitment",
                "a genuinely missing external input cannot be discovered or produced by the warband",
            ],
            "do_not_ask_for": [
                "technology stack, library, project structure, test strategy, or implementation detail",
                "visual style or minor feature choices when a conventional default satisfies the goal",
            ],
            "clarification_encoding": (
                "Only for needs_clarification, put one exact user-facing question first in "
                "escalation_conditions."
            ),
        },
        "forbidden_detailed_plan_fields": sorted(DETAILED_PLAN_FIELDS),
    }


def directive_model_instructions() -> str:
    literal_schema = (
        '{"decision":"delegate | needs_clarification | escalate | reject",'
        '"mission_intent":"string","priorities":["string"],'
        '"constraints":["string"],"success_conditions":["string"],'
        '"tradeoffs":["string"],"escalation_conditions":["string"]}'
    )
    return (
        "Return one strict JSON object and nothing else. You are Ceraxia, leader of the coding "
        "warband. Make only the leadership-level decision: whether to delegate, what outcome and "
        "priorities matter, which tradeoffs and hard constraints apply, how success is judged, and "
        "what must be escalated. Do not create a repository survey, file/module plan, work packages, "
        "dependency graph, implementation steps, patch instructions, shell commands, or test commands. "
        "Do not invent file-level detail; preserve explicit caller constraints without expanding them. "
        "Skitarii owns repository exploration, detailed planning, implementation, verification, and "
        "internal repair. Delegate with sensible reversible engineering and product defaults. Missing "
        "stack, library, project structure, test strategy, visual style, or minor feature preferences "
        "are leadership choices, not reasons to stop. Use needs_clarification only for a material user "
        "preference with no safe default, user authority/credentials/money/publication/deletion, or a "
        "genuinely unavailable external input. When clarification is truly required, put one exact "
        "user-facing question as the first escalation_conditions item. The object must contain exactly "
        "these seven literal top-level keys and no "
        "others: decision, mission_intent, priorities, constraints, success_conditions, tradeoffs, "
        "escalation_conditions. Do not echo task_id or delegation_subject. Use this exact literal "
        f"shape, replacing only the values: {literal_schema}. Use JSON string arrays only."
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
            raise CeraxiaDirectiveError(
                "Ceraxia answer must be clean JSON or exactly one fenced-json block "
                f"without surrounding prose: {exc}",
            ) from exc
    else:
        raise CeraxiaDirectiveError("Ceraxia answer must be a JSON object")
    if not isinstance(payload, dict):
        raise CeraxiaDirectiveError("Ceraxia answer must be a JSON object")
    return payload


def _text(value: Any, field: str, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise CeraxiaDirectiveError(f"{field} must be a string")
    normalized = value.strip()
    if required and not normalized:
        raise CeraxiaDirectiveError(f"{field} must not be empty")
    if len(normalized) > MAX_TEXT_LENGTH:
        raise CeraxiaDirectiveError(f"{field} exceeds {MAX_TEXT_LENGTH} characters")
    return normalized


def _strings(value: Any, field: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise CeraxiaDirectiveError(f"{field} must be a list")
    if len(value) > MAX_LIST_ITEMS:
        raise CeraxiaDirectiveError(f"{field} has more than {MAX_LIST_ITEMS} items")
    result = [_text(item, f"{field}[{index}]") for index, item in enumerate(value)]
    if required and not result:
        raise CeraxiaDirectiveError(f"{field} must not be empty")
    if len(set(result)) != len(result):
        raise CeraxiaDirectiveError(f"{field} contains duplicate items")
    return result


def _caller_first_union(
    caller_value: Any,
    model_value: Any,
    field: str,
) -> list[str]:
    caller_items = _commander_items(caller_value, field)
    model_items = _strings(model_value, field)
    merged = list(dict.fromkeys([*caller_items, *model_items]))
    if len(merged) > MAX_LIST_ITEMS:
        raise CeraxiaDirectiveError(
            f"merged {field} has more than {MAX_LIST_ITEMS} items",
        )
    return merged


def _commander_items(value: Any, field: str) -> list[str]:
    if value is None:
        value = []
    if not isinstance(value, list):
        raise CeraxiaDirectiveError(f"commander_order.{field} must be a list")
    return list(dict.fromkeys(
        _text(item, f"commander_order.{field}[{index}]")
        for index, item in enumerate(value)
    ))


def validate_ceraxia_directive(
    payload: dict[str, Any],
    *,
    expected_task_id: str = "",
    expected_mission_id: str = "",
    require_delegation: bool = False,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CeraxiaDirectiveError("Ceraxia directive must be an object")
    unknown = sorted(set(payload) - DIRECTIVE_FIELDS)
    if unknown:
        raise CeraxiaDirectiveError(f"Ceraxia directive has unknown fields: {unknown}")
    missing = sorted(DIRECTIVE_FIELDS - set(payload))
    if missing:
        raise CeraxiaDirectiveError(f"Ceraxia directive is missing fields: {missing}")
    if payload.get("kind") != DIRECTIVE_KIND:
        raise CeraxiaDirectiveError(f"kind must be {DIRECTIVE_KIND}")
    if payload.get("version") != DIRECTIVE_VERSION:
        raise CeraxiaDirectiveError(f"version must be {DIRECTIVE_VERSION}")
    task_id = _text(payload.get("task_id"), "task_id")
    mission_id = _text(payload.get("mission_id"), "mission_id")
    if expected_task_id and task_id != expected_task_id:
        raise CeraxiaDirectiveError("directive task_id does not match the run")
    if expected_mission_id and mission_id != expected_mission_id:
        raise CeraxiaDirectiveError("directive mission_id does not match the mission")
    if payload.get("leader") != "Ceraxia":
        raise CeraxiaDirectiveError("leader must be Ceraxia")
    decision = _text(payload.get("decision"), "decision")
    if decision not in DIRECTIVE_DECISIONS:
        raise CeraxiaDirectiveError(f"decision must be one of {sorted(DIRECTIVE_DECISIONS)}")
    delegated_to = _text(payload.get("delegated_to"), "delegated_to", required=False)
    if decision == "delegate" and delegated_to != "SkitariiWarband":
        raise CeraxiaDirectiveError("delegated_to must be SkitariiWarband for delegation")
    if decision != "delegate" and delegated_to:
        raise CeraxiaDirectiveError("non-delegation decisions must not name an execution backend")
    if require_delegation and decision != "delegate":
        raise CeraxiaDirectiveError("Ceraxia did not authorize delegation to Skitarii")
    mission_intent = _text(payload.get("mission_intent"), "mission_intent")
    lists = {
        field: _strings(
            payload.get(field),
            field,
            required=decision == "delegate" and field in {"priorities", "success_conditions"},
        )
        for field in LIST_FIELDS
    }
    return {
        "kind": DIRECTIVE_KIND,
        "version": DIRECTIVE_VERSION,
        "task_id": task_id,
        "mission_id": mission_id,
        "leader": "Ceraxia",
        "decision": decision,
        "delegated_to": delegated_to,
        "mission_intent": mission_intent,
        **lists,
    }


def validate_directive_for_commander(
    payload: dict[str, Any],
    commander_order: dict[str, Any],
    **validation: Any,
) -> dict[str, Any]:
    """Validate identity and prove that command boundaries were not discarded."""
    directive = validate_ceraxia_directive(payload, **validation)
    bindings = (
        ("constraints", "constraints"),
        ("success_conditions", "success_conditions"),
        ("escalate_to_user_if", "escalation_conditions"),
    )
    for command_field, directive_field in bindings:
        caller_items = _commander_items(
            commander_order.get(command_field),
            command_field,
        )
        missing = [item for item in caller_items if item not in directive[directive_field]]
        if missing:
            raise CeraxiaDirectiveError(
                f"directive dropped commander_order.{command_field}: {missing}",
            )
    return directive


def build_ceraxia_directive(
    model_decision: dict[str, Any],
    *,
    task_id: str,
    mission_id: str,
    commander_order: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(model_decision, dict) or model_decision.get("ok") is not True:
        raise CeraxiaDirectiveError("Ceraxia model brain did not answer")
    model_payload = _model_payload(model_decision.get("content"))
    forbidden = sorted(set(model_payload) & DETAILED_PLAN_FIELDS)
    if forbidden:
        raise CeraxiaDirectiveError(
            f"Ceraxia must not produce detailed planning fields: {forbidden}",
        )
    unknown = sorted(set(model_payload) - MODEL_FIELDS - MODEL_ECHO_FIELDS)
    if unknown:
        raise CeraxiaDirectiveError(f"Ceraxia answer has unknown fields: {unknown}")
    model_payload = {
        field: value
        for field, value in model_payload.items()
        if field not in MODEL_ECHO_FIELDS
    }
    missing = sorted(MODEL_FIELDS - set(model_payload))
    if missing:
        raise CeraxiaDirectiveError(f"Ceraxia answer is missing fields: {missing}")
    decision = _text(model_payload.get("decision"), "decision")
    merged_constraints = _caller_first_union(
        commander_order.get("constraints"),
        model_payload.get("constraints"),
        "constraints",
    )
    merged_success = _caller_first_union(
        commander_order.get("success_conditions"),
        model_payload.get("success_conditions"),
        "success_conditions",
    )
    merged_escalations = _caller_first_union(
        commander_order.get("escalate_to_user_if"),
        model_payload.get("escalation_conditions"),
        "escalation_conditions",
    )
    payload = {
        "kind": DIRECTIVE_KIND,
        "version": DIRECTIVE_VERSION,
        "task_id": task_id,
        "mission_id": mission_id,
        "leader": "Ceraxia",
        "decision": decision,
        "delegated_to": "SkitariiWarband" if decision == "delegate" else "",
        "mission_intent": model_payload.get("mission_intent"),
        "priorities": model_payload.get("priorities"),
        "constraints": merged_constraints,
        "success_conditions": merged_success,
        "tradeoffs": model_payload.get("tradeoffs"),
        "escalation_conditions": merged_escalations,
    }
    return validate_directive_for_commander(payload, commander_order)


def leadership_context_text(payload: dict[str, Any]) -> str:
    directive = validate_ceraxia_directive(payload, require_delegation=True)
    sections = [
        "CERAXIA LEADERSHIP DIRECTIVE (leadership context, not an implementation plan):",
        f"Mission intent: {directive['mission_intent']}",
    ]
    labels = (
        ("priorities", "Priorities"),
        ("constraints", "Constraints"),
        ("success_conditions", "Success conditions"),
        ("tradeoffs", "Accepted tradeoffs"),
        ("escalation_conditions", "Escalate to Ceraxia when"),
    )
    for field, label in labels:
        values = directive[field]
        if values:
            sections.append(label + ":\n" + "\n".join(f"- {item}" for item in values))
    sections.append(
        "Skitarii owns repository exploration, file selection, the detailed plan, implementation, "
        "verification, and repair. Do not treat this directive as a file-level plan.",
    )
    return "\n\n".join(sections)
