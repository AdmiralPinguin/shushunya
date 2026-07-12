"""Typed, lossless Iskandar directive binding and source/depth policy."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .model_client import canonical_json_sha256
from .schema import SOURCE_CLASSES


DIRECTIVE_KIND = "iskandar_research_directive"
DIRECTIVE_VERSION = 1
RESEARCH_DEPTHS = frozenset({"brief", "standard", "deep", "exhaustive"})
SOURCE_POLICIES = frozenset(
    {"primary_required", "authoritative_preferred", "balanced", "open_discovery"}
)
ERROR_TOLERANCES = frozenset({"strict", "balanced", "exploratory"})
ANSWER_MODES = frozenset(
    {
        "direct_answer",
        "research_brief",
        "investigation",
        "comparative_review",
        "source_map",
        "translation_analysis",
    }
)
ANSWER_MODE_TO_RESEARCH_MODE = {
    "direct_answer": "lookup",
    "research_brief": "synthesis",
    "investigation": "investigation",
    "comparative_review": "interpretation",
    "source_map": "synthesis",
    "translation_analysis": "translation",
}
DIRECTIVE_FIELDS = frozenset(
    {
        "kind",
        "version",
        "task_id",
        "mission_id",
        "leader",
        "decision",
        "delegated_to",
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
)
PRIMARY_SOURCE_CLASSES = frozenset(
    {
        "primary_source",
        "official_documentation",
        "standards_specification",
        "legal_or_regulatory",
        "peer_reviewed_research",
        "archival_catalog",
        "user_provided_corpus",
    }
)
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MISSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")


class ExecutionPolicyError(ValueError):
    """A directive could not be preserved as an executable policy."""


def _run_identifier(value: Any, context: str, *, mission: bool) -> str:
    pattern = _MISSION_ID_RE if mission else _TASK_ID_RE
    if type(value) is not str or not pattern.fullmatch(value) or ".." in value:
        kind = "mission" if mission else "task"
        raise ExecutionPolicyError(f"{context} must be a valid {kind} identifier")
    return value


def _text(value: Any, context: str) -> str:
    if type(value) is not str or not value.strip():
        raise ExecutionPolicyError(f"{context} must be a non-empty string")
    if len(value) > 4_000:
        raise ExecutionPolicyError(f"{context} exceeds 4000 characters")
    return value.strip()


def _items(value: Any, context: str, *, required: bool = False) -> tuple[str, ...]:
    if type(value) is not list:
        raise ExecutionPolicyError(f"{context} must be an array")
    result = tuple(_text(item, f"{context} item") for item in value)
    if required and not result:
        raise ExecutionPolicyError(f"{context} must not be empty")
    if len(result) > 24 or len(set(result)) != len(result):
        raise ExecutionPolicyError(f"{context} is oversized or contains duplicates")
    return result


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """Every governor field required to execute and review one mission."""

    task_id: str
    mission_id: str
    research_objective: str
    depth: str
    source_policy: str
    error_tolerance: str
    answer_mode: str
    priorities: tuple[str, ...]
    allowed_source_classes: tuple[str, ...]
    prohibited_source_classes: tuple[str, ...]
    constraints: tuple[str, ...]
    success_conditions: tuple[str, ...]
    output_requirements: tuple[str, ...]
    escalation_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        _run_identifier(self.task_id, "ExecutionPolicy.task_id", mission=False)
        _run_identifier(self.mission_id, "ExecutionPolicy.mission_id", mission=True)
        _text(self.research_objective, "ExecutionPolicy.research_objective")
        if self.depth not in RESEARCH_DEPTHS:
            raise ExecutionPolicyError("ExecutionPolicy.depth is unsupported")
        if self.source_policy not in SOURCE_POLICIES:
            raise ExecutionPolicyError("ExecutionPolicy.source_policy is unsupported")
        if self.error_tolerance not in ERROR_TOLERANCES:
            raise ExecutionPolicyError("ExecutionPolicy.error_tolerance is unsupported")
        if self.answer_mode not in ANSWER_MODES:
            raise ExecutionPolicyError("ExecutionPolicy.answer_mode is unsupported")
        for name in (
            "priorities",
            "allowed_source_classes",
            "prohibited_source_classes",
            "constraints",
            "success_conditions",
            "output_requirements",
            "escalation_conditions",
        ):
            values = getattr(self, name)
            if type(values) is not tuple:
                raise ExecutionPolicyError(f"ExecutionPolicy.{name} must be a tuple")
            if len(values) > 24 or len(set(values)) != len(values):
                raise ExecutionPolicyError(
                    f"ExecutionPolicy.{name} is oversized or contains duplicates"
                )
            for item in values:
                _text(item, f"ExecutionPolicy.{name} item")
        for name in ("allowed_source_classes", "prohibited_source_classes"):
            if set(getattr(self, name)) - set(SOURCE_CLASSES):
                raise ExecutionPolicyError(
                    f"ExecutionPolicy.{name} contains unknown source classes"
                )
        if set(self.allowed_source_classes) & set(self.prohibited_source_classes):
            raise ExecutionPolicyError("source classes cannot be both allowed and prohibited")
        if not self.priorities or not self.success_conditions or not self.output_requirements:
            raise ExecutionPolicyError(
                "delegated policy requires priorities, success_conditions, and output_requirements"
            )

    @classmethod
    def from_directive(
        cls,
        directive: Mapping[str, Any],
        *,
        expected_task_id: str = "",
        expected_mission_id: str = "",
    ) -> "ExecutionPolicy":
        if not isinstance(directive, Mapping) or any(
            type(key) is not str for key in directive
        ):
            raise ExecutionPolicyError("Iskandar directive must be an object")
        keys = set(directive)
        if keys != set(DIRECTIVE_FIELDS):
            missing = sorted(set(DIRECTIVE_FIELDS) - keys)
            unknown = sorted(keys - set(DIRECTIVE_FIELDS))
            raise ExecutionPolicyError(
                f"Iskandar directive fields mismatch; missing={missing}, unknown={unknown}"
            )
        if directive["kind"] != DIRECTIVE_KIND or directive["version"] != DIRECTIVE_VERSION:
            raise ExecutionPolicyError("unsupported Iskandar directive kind/version")
        if directive["leader"] != "IskandarKhayon":
            raise ExecutionPolicyError("directive leader must be IskandarKhayon")
        if directive["decision"] != "delegate" or (
            directive["delegated_to"] != "ResearchWarband"
        ):
            raise ExecutionPolicyError("directive did not delegate to ResearchWarband")
        if directive["clarification_question"] != "":
            raise ExecutionPolicyError(
                "delegated directive cannot contain clarification_question"
            )
        task_id = _run_identifier(
            directive["task_id"], "directive.task_id", mission=False
        )
        mission_id = _run_identifier(
            directive["mission_id"], "directive.mission_id", mission=True
        )
        if expected_task_id and task_id != expected_task_id:
            raise ExecutionPolicyError("directive task_id does not match the run")
        if expected_mission_id and mission_id != expected_mission_id:
            raise ExecutionPolicyError("directive mission_id does not match the run")
        depth = _text(directive["depth"], "directive.depth")
        source_policy = _text(directive["source_policy"], "directive.source_policy")
        error_tolerance = _text(
            directive["error_tolerance"], "directive.error_tolerance"
        )
        answer_mode = _text(directive["answer_mode"], "directive.answer_mode")
        if depth not in RESEARCH_DEPTHS or source_policy not in SOURCE_POLICIES:
            raise ExecutionPolicyError("directive depth/source_policy enum is invalid")
        if error_tolerance not in ERROR_TOLERANCES or answer_mode not in ANSWER_MODES:
            raise ExecutionPolicyError("directive error_tolerance/answer_mode enum is invalid")
        return cls(
            task_id=task_id,
            mission_id=mission_id,
            research_objective=_text(
                directive["research_objective"], "directive.research_objective"
            ),
            depth=depth,
            source_policy=source_policy,
            error_tolerance=error_tolerance,
            answer_mode=answer_mode,
            priorities=_items(directive["priorities"], "directive.priorities", required=True),
            allowed_source_classes=_items(
                directive["allowed_source_classes"], "directive.allowed_source_classes"
            ),
            prohibited_source_classes=_items(
                directive["prohibited_source_classes"],
                "directive.prohibited_source_classes",
            ),
            constraints=_items(directive["constraints"], "directive.constraints"),
            success_conditions=_items(
                directive["success_conditions"],
                "directive.success_conditions",
                required=True,
            ),
            output_requirements=_items(
                directive["output_requirements"],
                "directive.output_requirements",
                required=True,
            ),
            escalation_conditions=_items(
                directive["escalation_conditions"], "directive.escalation_conditions"
            ),
        )

    @property
    def research_mode(self) -> str:
        return ANSWER_MODE_TO_RESEARCH_MODE[self.answer_mode]

    def allows_source_class(self, source_class: str) -> bool:
        if source_class not in SOURCE_CLASSES:
            return False
        if source_class in self.prohibited_source_classes:
            return False
        if self.allowed_source_classes and source_class not in self.allowed_source_classes:
            return False
        if self.source_policy == "primary_required" and (
            source_class not in PRIMARY_SOURCE_CLASSES
        ):
            return False
        return True

    def to_directive_dict(self) -> dict[str, Any]:
        return {
            "kind": DIRECTIVE_KIND,
            "version": DIRECTIVE_VERSION,
            "task_id": self.task_id,
            "mission_id": self.mission_id,
            "leader": "IskandarKhayon",
            "decision": "delegate",
            "delegated_to": "ResearchWarband",
            "research_objective": self.research_objective,
            "depth": self.depth,
            "source_policy": self.source_policy,
            "error_tolerance": self.error_tolerance,
            "answer_mode": self.answer_mode,
            "priorities": list(self.priorities),
            "allowed_source_classes": list(self.allowed_source_classes),
            "prohibited_source_classes": list(self.prohibited_source_classes),
            "constraints": list(self.constraints),
            "success_conditions": list(self.success_conditions),
            "output_requirements": list(self.output_requirements),
            "escalation_conditions": list(self.escalation_conditions),
            "clarification_question": "",
        }

    @property
    def directive_sha256(self) -> str:
        return canonical_json_sha256(self.to_directive_dict(), "execution policy")

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.to_directive_dict(),
            "directive_sha256": self.directive_sha256,
        }


__all__ = [
    "ANSWER_MODES",
    "ANSWER_MODE_TO_RESEARCH_MODE",
    "DIRECTIVE_FIELDS",
    "DIRECTIVE_KIND",
    "DIRECTIVE_VERSION",
    "ERROR_TOLERANCES",
    "ExecutionPolicy",
    "ExecutionPolicyError",
    "PRIMARY_SOURCE_CLASSES",
    "RESEARCH_DEPTHS",
    "SOURCE_POLICIES",
]
