#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from EyeOfTerror.common_protocol import commander_order  # noqa: E402
from EyeOfTerror.common_protocol.iskandar_directive import (  # noqa: E402
    DETAILED_RESEARCH_FIELDS,
    DIRECTIVE_FIELDS,
    IskandarDirectiveError,
    build_iskandar_directive,
    directive_model_instructions,
    directive_request_payload,
    leadership_context_text,
    validate_directive_for_commander,
    validate_iskandar_directive,
)


def command(mission_id: str = "mission-native-research") -> dict:
    return commander_order(
        mission_id,
        to="IskandarKhayon",
        user_request="Verify the historical claim and show contradictory evidence.",
        commander_intent="Produce a trustworthy, source-grounded answer.",
        primary_goal="Establish what the strongest available evidence supports.",
        success_conditions=["Every major claim has independently checkable evidence."],
        constraints=["Do not use anonymous reposts as factual authority."],
        escalate_to_user_if=["The requested primary source is not publicly accessible."],
    )


def model_payload(decision: str = "delegate") -> dict:
    return {
        "decision": decision,
        "research_objective": "Determine which version of the claim survives source comparison.",
        "depth": "deep",
        "source_policy": "primary_required",
        "error_tolerance": "strict",
        "answer_mode": "investigation",
        "priorities": ["Prefer contemporaneous primary records."],
        "allowed_source_classes": ["primary_source", "peer_reviewed_research"],
        "prohibited_source_classes": ["anonymous_or_unverified_web"],
        "constraints": ["Disclose inaccessible evidence."],
        "success_conditions": ["Conflicting accounts are represented explicitly."],
        "output_requirements": ["Evidence ledger", "Source manifest", "Russian final report"],
        "escalation_conditions": ["Evidence remains irreducibly contradictory."],
        "clarification_question": "",
    }


def build(payload: dict | None = None, cmd: dict | None = None) -> dict:
    cmd = cmd or command()
    return build_iskandar_directive(
        {"ok": True, "content": payload or model_payload()},
        task_id="native-research",
        mission_id=str(cmd["mission_id"]),
        commander_order=cmd,
    )


class IskandarDirectiveTests(unittest.TestCase):
    def test_delegate_is_exact_and_preserves_commander_boundaries(self) -> None:
        cmd = command()
        directive = build(cmd=cmd)
        self.assertEqual(set(directive), DIRECTIVE_FIELDS)
        self.assertEqual(directive["leader"], "IskandarKhayon")
        self.assertEqual(directive["delegated_to"], "ResearchWarband")
        self.assertEqual(directive["decision"], "delegate")
        self.assertNotIn("worker_plan", directive)
        self.assertFalse(set(directive) & DETAILED_RESEARCH_FIELDS)
        self.assertEqual(
            directive["constraints"][:1],
            cmd["constraints"],
            "commander constraints must remain first and verbatim",
        )
        self.assertEqual(directive["success_conditions"][:1], cmd["success_conditions"])
        self.assertEqual(directive["escalation_conditions"][:1], cmd["escalate_to_user_if"])
        self.assertEqual(
            validate_directive_for_commander(
                directive,
                cmd,
                expected_task_id="native-research",
                expected_mission_id=cmd["mission_id"],
                require_delegation=True,
            ),
            directive,
        )

    def test_detailed_research_fields_are_rejected_before_unknown_fields(self) -> None:
        for field in sorted(DETAILED_RESEARCH_FIELDS):
            with self.subTest(field=field):
                payload = model_payload()
                payload[field] = ["forbidden detail"]
                with self.assertRaisesRegex(
                    IskandarDirectiveError,
                    "must not produce detailed research fields",
                ):
                    build(payload)

    def test_unknown_or_missing_model_fields_fail_closed(self) -> None:
        unknown = model_payload()
        unknown["confidence"] = "high"
        with self.assertRaisesRegex(IskandarDirectiveError, "unknown fields"):
            build(unknown)
        missing = model_payload()
        missing.pop("source_policy")
        with self.assertRaisesRegex(IskandarDirectiveError, "missing fields"):
            build(missing)

    def test_needs_clarification_requires_exactly_one_question(self) -> None:
        payload = model_payload("needs_clarification")
        payload["clarification_question"] = "Which jurisdiction and date range should be covered?"
        directive = build(payload)
        self.assertEqual(directive["decision"], "needs_clarification")
        self.assertEqual(directive["delegated_to"], "")
        self.assertIn("jurisdiction", directive["clarification_question"])
        with self.assertRaisesRegex(IskandarDirectiveError, "did not authorize delegation"):
            validate_iskandar_directive(directive, require_delegation=True)

        no_question = model_payload("needs_clarification")
        with self.assertRaisesRegex(IskandarDirectiveError, "requires one non-empty"):
            build(no_question)

        delegated_with_question = model_payload("delegate")
        delegated_with_question["clarification_question"] = "A question that must not survive."
        with self.assertRaisesRegex(IskandarDirectiveError, "must be empty"):
            build(delegated_with_question)

    def test_invalid_enums_and_source_policy_conflict_fail_closed(self) -> None:
        for field, value in (
            ("depth", "infinite"),
            ("source_policy", "whatever-is-first"),
            ("error_tolerance", "zero"),
            ("answer_mode", "novel"),
        ):
            with self.subTest(field=field):
                payload = model_payload()
                payload[field] = value
                with self.assertRaisesRegex(IskandarDirectiveError, "must be one of"):
                    build(payload)
        overlap = model_payload()
        overlap["prohibited_source_classes"].append("primary_source")
        with self.assertRaisesRegex(IskandarDirectiveError, "both allowed and prohibited"):
            build(overlap)

        invalid_class = model_payload()
        invalid_class["allowed_source_classes"] = ["specific-blog.example"]
        with self.assertRaisesRegex(IskandarDirectiveError, "URL|domain|source classes"):
            build(invalid_class)

    def test_model_value_cannot_smuggle_urls_queries_or_source_selection(self) -> None:
        cases = (
            ("research_objective", "Read https://example.com/exact-page before answering."),
            ("research_objective", "Read ｈｔｔｐｓ：／／example.com/fullwidth."),
            ("priorities", ["site:example.org exact phrase"]),
            ("priorities", ["filetype:pdf chronology"]),
            ("output_requirements", ["search query: \"exact phrase\" AND archive"]),
            ("output_requirements", ["source: RFC-3629"]),
            ("allowed_source_classes", ["https://example.org/source"]),
            ("prohibited_source_classes", ["inurl:mirror"]),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                payload = model_payload()
                payload[field] = value
                with self.assertRaisesRegex(
                    IskandarDirectiveError,
                    "forbidden|source classes",
                ):
                    build(payload)

    def test_nested_model_value_cannot_bypass_detailed_boundary(self) -> None:
        nested_cases = (
            ("priorities", [{"query": "safe-looking words"}]),
            ("output_requirements", [["https://example.org/nested"]]),
            ("constraints", [{"wrapper": {"sources": ["named source"]}}]),
        )
        for field, value in nested_cases:
            with self.subTest(field=field):
                payload = model_payload()
                payload[field] = value
                with self.assertRaisesRegex(IskandarDirectiveError, "forbidden|must be a string"):
                    build(payload)

    def test_caller_url_and_exact_query_remain_in_separate_authority(self) -> None:
        cmd = command()
        caller_constraint = "Inspect the user-supplied source https://example.org/input exactly."
        cmd["constraints"].append(caller_constraint)
        cmd["user_request"] = 'Check the caller query site:example.org "exact phrase".'
        original = copy.deepcopy(cmd)
        model_request = directive_request_payload(
            "Use https://example.org/input and site:example.org exact phrase.",
            "native-research",
            cmd,
        )
        serialized_request = json.dumps(model_request, ensure_ascii=False)
        self.assertNotIn("https://example.org", serialized_request)
        self.assertNotIn("site:example.org", serialized_request)
        self.assertIn("[caller-provided research detail]", serialized_request)
        self.assertEqual(original, cmd, "leadership projection must not mutate the order")
        directive = build(cmd=cmd)
        self.assertIn(caller_constraint, directive["constraints"])
        self.assertEqual(cmd["user_request"], 'Check the caller query site:example.org "exact phrase".')
        self.assertEqual(validate_directive_for_commander(directive, cmd), directive)

    def test_persisted_directive_is_exact_and_commander_bound(self) -> None:
        cmd = command()
        directive = build(cmd=cmd)
        with self.assertRaisesRegex(IskandarDirectiveError, "unknown fields"):
            validate_iskandar_directive({**directive, "queries": ["hidden query"]})

        dropped = copy.deepcopy(directive)
        dropped["constraints"].remove(cmd["constraints"][0])
        with self.assertRaisesRegex(IskandarDirectiveError, "dropped commander_order.constraints"):
            validate_directive_for_commander(dropped, cmd)

        wrong_authority = {**cmd, "to": "Ceraxia"}
        with self.assertRaisesRegex(IskandarDirectiveError, "authority"):
            validate_directive_for_commander(directive, wrong_authority)

        wrong_mission = {**cmd, "mission_id": "mission-other"}
        with self.assertRaisesRegex(IskandarDirectiveError, "mission_id"):
            validate_directive_for_commander(directive, wrong_mission)

    def test_persisted_directive_reapplies_model_detail_boundary(self) -> None:
        cmd = command()
        directive = build(cmd=cmd)

        tampered_objective = copy.deepcopy(directive)
        tampered_objective["research_objective"] = (
            "Read https://attacker.invalid/source before answering."
        )
        with self.assertRaisesRegex(IskandarDirectiveError, "forbidden URL"):
            validate_directive_for_commander(tampered_objective, cmd)

        tampered_constraint = copy.deepcopy(directive)
        tampered_constraint["constraints"].append(
            "search query: site:attacker.invalid hidden plan"
        )
        with self.assertRaisesRegex(IskandarDirectiveError, "forbidden"):
            validate_directive_for_commander(tampered_constraint, cmd)

    def test_clean_and_exactly_fenced_json_are_accepted(self) -> None:
        raw = json.dumps(model_payload(), ensure_ascii=False)
        plain = build_iskandar_directive(
            {"ok": True, "content": raw},
            task_id="native-research",
            mission_id=command()["mission_id"],
            commander_order=command(),
        )
        fenced = build_iskandar_directive(
            {"ok": True, "content": f"```json\n{raw}\n```"},
            task_id="native-research",
            mission_id=command()["mission_id"],
            commander_order=command(),
        )
        self.assertEqual(
            {key: value for key, value in plain.items()},
            {key: value for key, value in fenced.items()},
        )
        with self.assertRaisesRegex(IskandarDirectiveError, "clean JSON"):
            build_iskandar_directive(
                {"ok": True, "content": f"Here is the plan:\n{raw}"},
                task_id="native-research",
                mission_id=command()["mission_id"],
                commander_order=command(),
            )

    def test_prompt_and_rendered_context_keep_the_authority_boundary(self) -> None:
        request = directive_request_payload("Research the claim", "native-research", command())
        self.assertEqual(request["delegation_subject"], "Research the claim")
        self.assertIn("queries", request["forbidden_detailed_plan_fields"])
        instructions = directive_model_instructions()
        self.assertIn("ResearchWarband owns detailed planning", instructions)
        self.assertIn("Do not produce search queries", instructions)

        context = leadership_context_text(build())
        self.assertIn("leadership context, not a research plan", context)
        self.assertIn("ResearchWarband owns subquestions", context)
        self.assertNotIn("https://", context)

    def test_model_failure_is_not_converted_into_a_directive(self) -> None:
        with self.assertRaisesRegex(IskandarDirectiveError, "did not answer"):
            build_iskandar_directive(
                {"ok": False, "content": model_payload()},
                task_id="native-research",
                mission_id=command()["mission_id"],
                commander_order=command(),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
