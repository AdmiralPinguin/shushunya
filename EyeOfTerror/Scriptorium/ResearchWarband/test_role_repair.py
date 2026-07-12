from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from typing import Any, Mapping

from ResearchWarband import pipeline as pipeline_module
from ResearchWarband.execution_policy import ExecutionPolicy
from ResearchWarband.model_client import ModelClientError, TrustedReviewBoundary
from ResearchWarband.pipeline import ResearchBudgets, ResearchPipeline, ResearchSpec
from ResearchWarband.snapshot_store import SnapshotStore


class QueueModel:
    def __init__(
        self,
        responses: Mapping[str, list[Any]],
        *,
        stable_identity: str,
        preflight_errors: Mapping[str, Exception] | None = None,
    ) -> None:
        self.responses = {role: list(items) for role, items in responses.items()}
        self.stable_identity = stable_identity
        self.independence_identity = stable_identity
        self.preflight_errors = dict(preflight_errors or {})
        self.preflight_calls: list[tuple[str, Mapping[str, Any]]] = []
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def preflight(self, role: str, payload: Mapping[str, Any]) -> None:
        self.preflight_calls.append((role, payload))
        error = self.preflight_errors.get(role)
        if error is not None:
            raise error

    def decide(self, role: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((role, payload))
        queue = self.responses.get(role, [])
        if not queue:
            raise AssertionError(f"unexpected model call for role {role}")
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        if not isinstance(response, Mapping):
            raise AssertionError("queued model response must be a mapping or exception")
        return response


class NoHitsSearch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, limit: int):
        self.calls.append((query, limit))
        return ()


class NoFetch:
    def fetch(self, _hit: Any, _max_bytes: int):
        raise AssertionError("no-hit tests must never fetch")


def blocked_analyst(query: str) -> dict[str, Any]:
    return {
        "decision": "blocked",
        "reason": "the searched scope did not contain an answer",
        "claims": [],
        "inferences": [],
        "gaps": [
            {
                "id": "gap-unanswered-source",
                "question": "Which allowed source answers the question?",
                "status": "blocked",
                "related_claim_ids": [],
                "search_attempts": [query],
            }
        ],
        "hypothesis_assessments": [],
        "next_queries": [],
    }


class RoleRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = SnapshotStore(Path(self.tempdir.name) / "snapshots")

    @staticmethod
    def spec(
        question: str = "Find the exact answer.", *, depth: str = "brief"
    ) -> ResearchSpec:
        policy = ExecutionPolicy(
            task_id="repair-task",
            mission_id="repair-mission",
            research_objective=question,
            depth=depth,
            source_policy="balanced",
            error_tolerance="strict",
            answer_mode="direct_answer",
            priorities=("accuracy",),
            allowed_source_classes=("official_documentation",),
            prohibited_source_classes=(),
            constraints=("public sources only",),
            success_conditions=("answer has exact evidence",),
            output_requirements=("structured answer",),
            escalation_conditions=("sources unavailable",),
        )
        return ResearchSpec(
            task_id=policy.task_id,
            mission_id=policy.mission_id,
            question=policy.research_objective,
            mode="lookup",
            execution_policy=policy,
            priorities=policy.priorities,
            scope_boundaries=("public sources",),
            source_policy=(policy.source_policy,),
            success_conditions=policy.success_conditions,
        )

    def pipeline(
        self,
        author: QueueModel,
        search: NoHitsSearch | None = None,
        budgets: ResearchBudgets | None = None,
    ) -> tuple[ResearchPipeline, NoHitsSearch]:
        selected_search = search or NoHitsSearch()
        reviewer = QueueModel({}, stable_identity="repair-review-model")
        return (
            ResearchPipeline(
                author_model=author,
                review_boundary=TrustedReviewBoundary(
                    client=reviewer,
                    authority_id="repair-review-authority",
                ),
                search=selected_search,
                fetch=NoFetch(),
                snapshot_store=self.store,
                budgets=budgets or ResearchBudgets.for_depth("brief"),
            ),
            selected_search,
        )

    def test_planner_repairs_missing_clarification_question_once(self) -> None:
        author = QueueModel(
            {
                "planner": [
                    {
                        "decision": "clarify",
                        "reason": "PREVIOUS-PLANNER-OUTPUT-MARKER",
                    },
                    {
                        "decision": "clarify",
                        "clarification_question": "Which product generation is in scope?",
                    },
                ]
            },
            stable_identity="repair-author-model",
        )
        pipeline, search = self.pipeline(author)

        result = pipeline.run(self.spec())

        self.assertEqual("clarify", result.outcome)
        self.assertEqual(2, result.model_calls)
        self.assertEqual([], search.calls)
        planner_calls = [payload for role, payload in author.calls if role == "planner"]
        self.assertEqual(2, len(planner_calls))
        first_payload, repair_payload = planner_calls
        repair_request = repair_payload["repair_request"]
        self.assertEqual(1, repair_request["attempt"])
        self.assertEqual(1, repair_request["max_attempts"])
        self.assertIn("clarification_question", repair_request["validator_error"])
        replay_free_payload = dict(repair_payload)
        replay_free_payload.pop("repair_request")
        self.assertEqual(first_payload, replay_free_payload)
        self.assertNotIn(
            "PREVIOUS-PLANNER-OUTPUT-MARKER",
            json.dumps(repair_payload, ensure_ascii=False, sort_keys=True),
        )
        required_action = repair_request["required_action"]
        self.assertIn("original payload", required_action)
        self.assertIn("application will not supply", required_action)
        self.assertNotIn("infer missing values", required_action)
        contract = first_payload["output_contract"]
        self.assertIn("clarification_question", contract["optional_fields"])
        self.assertIn("clarification_question", contract["decision_rules"]["clarify"])

    def test_planner_repairs_clarification_into_objective_language(self) -> None:
        objective = "Разбери историю вопроса."
        author = QueueModel(
            {
                "planner": [
                    {
                        "decision": "clarify",
                        "clarification_question": "Which exact topic should be analyzed?",
                    },
                    {
                        "decision": "clarify",
                        "clarification_question": "Какой вопрос или какую тему нужно разобрать?",
                    },
                ]
            },
            stable_identity="repair-author-model",
        )
        pipeline, search = self.pipeline(author)

        result = pipeline.run(self.spec(objective))

        self.assertEqual("clarify", result.outcome)
        self.assertIn("Какой вопрос", result.reason)
        planner_calls = [payload for role, payload in author.calls if role == "planner"]
        self.assertEqual(2, len(planner_calls))
        self.assertIn(
            "objective language",
            planner_calls[1]["repair_request"]["validator_error"],
        )
        self.assertEqual([], search.calls)

    def test_planner_empty_queries_are_repaired_before_any_search(self) -> None:
        author = QueueModel(
            {
                "planner": [
                    {"decision": "proceed", "queries": []},
                    {"decision": "proceed", "queries": ["repaired exact query"]},
                ],
                "analyst": [blocked_analyst("repaired exact query")],
            },
            stable_identity="repair-author-model",
        )
        pipeline, search = self.pipeline(author)

        result = pipeline.run(self.spec())

        self.assertEqual("blocked", result.outcome)
        self.assertEqual(["repaired exact query"], [query for query, _ in search.calls])
        self.assertEqual(3, result.model_calls)
        planner_calls = [payload for role, payload in author.calls if role == "planner"]
        self.assertEqual(2, len(planner_calls))
        self.assertIn(
            "no executable search queries",
            planner_calls[1]["repair_request"]["validator_error"],
        )

    def test_planner_repairs_internal_ids_and_missing_english_query(self) -> None:
        russian_question = "Сопоставь архивные версии длительности полёта."
        english_query = "archival flight duration minutes methodology"
        russian_query = "архивные версии длительности полёта минуты"
        author = QueueModel(
            {
                "planner": [
                    {
                        "decision": "proceed",
                        "queries": ["repair-task архивная длительность полёта"],
                    },
                    {
                        "decision": "proceed",
                        "queries": [russian_query, english_query],
                    },
                ],
                "analyst": [blocked_analyst(english_query)],
            },
            stable_identity="repair-author-model",
        )
        pipeline, search = self.pipeline(author)

        result = pipeline.run(self.spec(russian_question))

        self.assertEqual("blocked", result.outcome)
        self.assertEqual(
            [russian_query, english_query],
            [query for query, _limit in search.calls],
        )
        planner_calls = [payload for role, payload in author.calls if role == "planner"]
        self.assertEqual(2, len(planner_calls))
        self.assertIn(
            "internal task or mission id",
            planner_calls[1]["repair_request"]["validator_error"],
        )
        self.assertTrue(
            planner_calls[0]["query_language_policy"][
                "non_english_objective_requires_concise_english_query"
            ]
        )

    def test_planner_repairs_non_english_query_without_separate_english_variant(self) -> None:
        russian_question = "Сообщи код архивной записи."
        russian_query = "код архивной записи"
        english_query = "archive record code"
        author = QueueModel(
            {
                "planner": [
                    {"decision": "proceed", "queries": [russian_query]},
                    {
                        "decision": "proceed",
                        "queries": [russian_query, english_query],
                    },
                ],
                "analyst": [blocked_analyst(english_query)],
            },
            stable_identity="repair-author-model",
        )
        pipeline, search = self.pipeline(author)

        result = pipeline.run(self.spec(russian_question))

        self.assertEqual("blocked", result.outcome)
        self.assertEqual(
            [russian_query, english_query],
            [query for query, _limit in search.calls],
        )
        planner_calls = [payload for role, payload in author.calls if role == "planner"]
        self.assertEqual(2, len(planner_calls))
        self.assertIn(
            "separate objective-language and English search queries",
            planner_calls[1]["repair_request"]["validator_error"],
        )

    def test_query_language_helpers_use_script_and_identifier_boundaries(self) -> None:
        self.assertIsNone(
            pipeline_module._dominant_non_latin_script("Café provenance archive")
        )
        self.assertEqual(
            "CYRILLIC",
            pipeline_module._dominant_non_latin_script("архивная запись RV32I"),
        )
        self.assertEqual(
            "EAST_ASIAN",
            pipeline_module._dominant_non_latin_script("質問の履歴"),
        )
        self.assertFalse(
            pipeline_module._contains_identifier_token("aircraft duration", "air")
        )
        self.assertTrue(
            pipeline_module._contains_identifier_token("archive task-1 record", "task-1")
        )
        self.assertGreaterEqual(
            pipeline_module._latin_search_word_count("Café provenance archive"),
            2,
        )

    def test_analyst_repairs_malformed_gap_without_application_defaults(self) -> None:
        malformed = {
            "decision": "blocked",
            "reason": "PREVIOUS-ANALYST-OUTPUT-MARKER",
            "claims": [],
            "inferences": [],
            "gaps": [{"search_attempts": ["exact query"]}],
            "hypothesis_assessments": [],
            "next_queries": [],
        }
        repaired_gap = blocked_analyst("exact query")
        repaired_gap["gaps"][0]["search_attempts"] = ["forged unexecuted query"]
        author = QueueModel(
            {
                "planner": [{"decision": "proceed", "queries": ["exact query"]}],
                "analyst": [malformed, repaired_gap],
            },
            stable_identity="repair-author-model",
        )
        pipeline, _search = self.pipeline(author)

        result = pipeline.run(self.spec())

        self.assertEqual("blocked", result.outcome)
        self.assertEqual(3, result.model_calls)
        self.assertEqual("gap-unanswered-source", result.ledger.gaps[0].id)
        self.assertEqual(("exact query",), result.ledger.gaps[0].search_attempts)
        analyst_calls = [payload for role, payload in author.calls if role == "analyst"]
        self.assertEqual(2, len(analyst_calls))
        first_payload, repair_payload = analyst_calls
        error = repair_payload["repair_request"]["validator_error"]
        for field in ("id", "question", "related_claim_ids", "status"):
            self.assertIn(field, error)
        replay_free_payload = dict(repair_payload)
        replay_free_payload.pop("repair_request")
        self.assertEqual(first_payload, replay_free_payload)
        self.assertNotIn(
            "PREVIOUS-ANALYST-OUTPUT-MARKER",
            json.dumps(repair_payload, ensure_ascii=False, sort_keys=True),
        )
        self.assertEqual(
            {
                "id",
                "question",
                "status",
                "related_claim_ids",
            },
            set(first_payload["output_contract"]["gap_item"]["required_fields"]),
        )
        self.assertEqual(
            ["search_attempts"],
            first_payload["output_contract"]["gap_item"]["optional_fields"],
        )

    def test_analyst_repairs_early_zero_source_block_into_novel_search(self) -> None:
        search_more = {
            "decision": "search_more",
            "reason": "broaden the failed lookup",
            "claims": [],
            "inferences": [],
            "gaps": [],
            "hypothesis_assessments": [],
            "next_queries": ["archive record code"],
        }
        author = QueueModel(
            {
                "planner": [{"decision": "proceed", "queries": ["archival code"]}],
                "analyst": [
                    blocked_analyst("archival code"),
                    search_more,
                    blocked_analyst("archive record code"),
                ],
            },
            stable_identity="repair-author-model",
        )
        budgets = ResearchBudgets(max_rounds=2, max_search_queries=2)
        pipeline, search = self.pipeline(author, budgets=budgets)

        result = pipeline.run(self.spec(depth="standard"))

        self.assertEqual("blocked", result.outcome)
        self.assertEqual(
            ["archival code", "archive record code"],
            [query for query, _limit in search.calls],
        )
        analyst_calls = [payload for role, payload in author.calls if role == "analyst"]
        self.assertEqual(3, len(analyst_calls))
        self.assertIn(
            "zero-source acquisition",
            analyst_calls[1]["repair_request"]["validator_error"],
        )
        self.assertEqual(4, result.model_calls)

    def test_analyst_repairs_repeated_search_more_query(self) -> None:
        repeated = {
            "decision": "search_more",
            "claims": [],
            "inferences": [],
            "gaps": [],
            "hypothesis_assessments": [],
            "next_queries": ["archival code"],
        }
        novel = {**repeated, "next_queries": ["archive record code"]}
        author = QueueModel(
            {
                "planner": [{"decision": "proceed", "queries": ["archival code"]}],
                "analyst": [
                    repeated,
                    novel,
                    blocked_analyst("archive record code"),
                ],
            },
            stable_identity="repair-author-model",
        )
        budgets = ResearchBudgets(max_rounds=2, max_search_queries=2)
        pipeline, search = self.pipeline(author, budgets=budgets)

        result = pipeline.run(self.spec(depth="standard"))

        self.assertEqual("blocked", result.outcome)
        self.assertEqual(
            ["archival code", "archive record code"],
            [query for query, _limit in search.calls],
        )
        analyst_calls = [payload for role, payload in author.calls if role == "analyst"]
        self.assertIn(
            "must be novel",
            analyst_calls[1]["repair_request"]["validator_error"],
        )
        self.assertEqual(4, result.model_calls)

    def test_second_invalid_response_fails_closed_without_third_call(self) -> None:
        invalid = {"decision": "clarify"}
        author = QueueModel(
            {"planner": [invalid, invalid, {"decision": "blocked"}]},
            stable_identity="repair-author-model",
        )
        pipeline, search = self.pipeline(author)

        result = pipeline.run(self.spec())

        self.assertEqual("blocked", result.outcome)
        self.assertIn("clarification_question", result.reason)
        self.assertEqual(2, result.model_calls)
        self.assertEqual(2, len(author.calls))
        self.assertEqual(1, len(author.responses["planner"]))
        self.assertEqual([], search.calls)

    def test_repair_validator_error_is_bounded(self) -> None:
        invalid = {"decision": "blocked"}
        invalid.update({f"unknown_field_{index:03d}": index for index in range(200)})
        author = QueueModel(
            {"planner": [invalid, {"decision": "blocked"}]},
            stable_identity="repair-author-model",
        )
        pipeline, search = self.pipeline(author)

        result = pipeline.run(self.spec())

        self.assertEqual("blocked", result.outcome)
        planner_calls = [payload for role, payload in author.calls if role == "planner"]
        self.assertEqual(2, len(planner_calls))
        error = planner_calls[1]["repair_request"]["validator_error"]
        self.assertLessEqual(len(error), 512)
        self.assertTrue(error.endswith("…"))
        self.assertEqual([], search.calls)

    def test_preflight_failure_is_not_retried(self) -> None:
        author = QueueModel(
            {"planner": [{"decision": "blocked"}]},
            stable_identity="repair-author-model",
            preflight_errors={"planner": ModelClientError("preflight unavailable")},
        )
        pipeline, search = self.pipeline(author)

        result = pipeline.run(self.spec())

        self.assertEqual("blocked", result.outcome)
        self.assertIn("preflight unavailable", result.reason)
        self.assertEqual(1, result.model_calls)
        self.assertEqual(1, len(author.preflight_calls))
        self.assertEqual(0, len(author.calls))
        self.assertFalse(any("_repair[" in item for item in result.diagnostics))
        self.assertEqual([], search.calls)

    def test_transport_failure_is_not_retried(self) -> None:
        author = QueueModel(
            {"planner": [ModelClientError("transport unavailable")]},
            stable_identity="repair-author-model",
        )
        pipeline, search = self.pipeline(author)

        result = pipeline.run(self.spec())

        self.assertEqual("blocked", result.outcome)
        self.assertIn("transport unavailable", result.reason)
        self.assertEqual(1, result.model_calls)
        self.assertEqual(1, len(author.preflight_calls))
        self.assertEqual(1, len(author.calls))
        self.assertFalse(any("_repair[" in item for item in result.diagnostics))
        self.assertEqual([], search.calls)


if __name__ == "__main__":
    unittest.main()
