from __future__ import annotations

import copy
import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

from research_eval.fixture_server import FixtureServer  # noqa: E402
from research_eval.fixtures import FixtureError, load_fixture  # noqa: E402
from research_eval.manifest import LoadedSuite, ManifestError, load_suite, require_object, strict_json_load  # noqa: E402
from research_eval.metrics import aggregate_metrics  # noqa: E402
from research_eval.oracles import evaluate_legacy_artifact, evaluate_task  # noqa: E402
from research_eval.results import ResultWriteError, write_result_atomic  # noqa: E402
from research_eval.runner import build_service_payload, run_suite  # noqa: E402
from research_eval.subjects import FakeSubjectAdapter, SubjectAdapter, SubjectExecution  # noqa: E402


SUITE = ROOT / "suites/public_smoke_v1/manifest.json"
REPLAYS = ROOT / "replays/public_smoke_v1/results.json"


def replay_results() -> dict:
    value, _ = strict_json_load(REPLAYS)
    return copy.deepcopy(require_object(value, "replays"))


def evaluate_replay(task_id: str, result: dict) -> object:
    suite = load_suite(SUITE, allowed_root=ROOT)
    fixture = load_fixture(
        suite.fixture_path,
        expected_sha256=suite.data["fixture_sha256"],
    )
    task = next(task for task in suite.tasks if task["id"] == task_id)
    candidate = copy.deepcopy(result)
    candidate["mission_id"] = f"eval-{task_id}"
    return evaluate_task(task, candidate, fixture)


def single_task_suite(task_id: str, *, wall_sec: int) -> LoadedSuite:
    suite = load_suite(SUITE, allowed_root=ROOT)
    data = copy.deepcopy(suite.data)
    task = copy.deepcopy(next(task for task in data["tasks"] if task["id"] == task_id))
    task["limits"]["wall_sec"] = wall_sec
    data["tasks"] = [task]
    return LoadedSuite(
        path=suite.path,
        root=suite.root,
        data=data,
        raw_sha256=suite.raw_sha256,
        fixture_path=suite.fixture_path,
    )


class HungSubjectAdapter(SubjectAdapter):
    def health(self) -> dict:
        return {
            "status": "ok",
            "identity": {
                "instance_id": "hung-subject",
                "source_sha256": "9" * 64,
                "standalone_test_mode": True,
            },
        }

    def execute(self, payload: dict, *, timeout_sec: int) -> SubjectExecution:
        del payload, timeout_sec
        while True:
            time.sleep(1)


class ExternalEvalCoreTests(unittest.TestCase):
    def test_schema_documents_are_valid_json(self) -> None:
        names = {path.name for path in (ROOT / "schemas").glob("*.json")}
        self.assertEqual(
            names,
            {"suite.schema.json", "fixture_bundle.schema.json", "subject_result.schema.json", "run_result.schema.json"},
        )
        for path in (ROOT / "schemas").glob("*.json"):
            value, _ = strict_json_load(path)
            self.assertIsInstance(value, dict)
            self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_manifest_and_all_fixture_bytes_are_pinned(self) -> None:
        suite = load_suite(SUITE, allowed_root=ROOT)
        fixture = load_fixture(suite.fixture_path, expected_sha256=suite.data["fixture_sha256"])
        self.assertEqual(len(suite.tasks), 6)
        self.assertEqual(len(fixture.documents), 6)
        self.assertTrue(fixture.data["closed_world"])

    def test_duplicate_json_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.json"
            path.write_text('{"x": 1, "x": 2}', encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "duplicate JSON key"):
                strict_json_load(path)

    def test_unknown_manifest_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "ResearchWarband"
            shutil.copytree(ROOT, copied)
            path = copied / "suites/public_smoke_v1/manifest.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["answer_key_for_subject"] = True
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "unknown keys"):
                load_suite(path, allowed_root=copied)

    def test_fixture_byte_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "ResearchWarband"
            shutil.copytree(ROOT, copied)
            raw = copied / "fixtures/public_smoke_v1/raw/riscv_isa.txt"
            raw.write_text(raw.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
            suite = load_suite(copied / "suites/public_smoke_v1/manifest.json", allowed_root=copied)
            with self.assertRaisesRegex(FixtureError, "byte count mismatch|sha256 mismatch"):
                load_fixture(suite.fixture_path, expected_sha256=suite.data["fixture_sha256"])

    def test_fixture_manifest_tamper_is_detected_before_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "ResearchWarband"
            shutil.copytree(ROOT, copied)
            manifest = copied / "fixtures/public_smoke_v1/fixture_manifest.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["bundle_id"] = "tampered-bundle"
            manifest.write_text(json.dumps(data), encoding="utf-8")
            suite = load_suite(copied / "suites/public_smoke_v1/manifest.json", allowed_root=copied)
            with self.assertRaisesRegex(FixtureError, "manifest sha256 mismatch"):
                load_fixture(suite.fixture_path, expected_sha256=suite.data["fixture_sha256"])

    def test_fixture_server_exposes_only_search_documents_and_explicit_statuses(self) -> None:
        suite = load_suite(SUITE, allowed_root=ROOT)
        fixture = load_fixture(suite.fixture_path, expected_sha256=suite.data["fixture_sha256"])
        with FixtureServer(fixture) as server:
            query = urllib.parse.urlencode({"q": "RISC-V JAL"})
            with urllib.request.urlopen(f"{server.base_url}/search?{query}", timeout=5) as response:
                payload = json.loads(response.read())
            self.assertEqual([item["source_id"] for item in payload["results"]], ["source-riscv-isa"])
            with urllib.request.urlopen(f"{server.base_url}/documents/riscv-isa", timeout=5) as response:
                self.assertEqual(response.headers["X-Eval-Snapshot-Sha256"], fixture.document("source-riscv-isa").data["raw_sha256"])
                self.assertEqual(response.read(), fixture.document("source-riscv-isa").raw)
            document_access = next(
                item
                for item in server.access_log
                if item["path"] == "/documents/riscv-isa"
            )
            self.assertEqual(document_access["method"], "GET")
            self.assertEqual(
                document_access["body_bytes"],
                len(fixture.document("source-riscv-isa").raw),
            )
            self.assertEqual(
                document_access["body_sha256"],
                fixture.document("source-riscv-isa").data["raw_sha256"],
            )
            with self.assertRaises(urllib.error.HTTPError) as missing:
                urllib.request.urlopen(f"{server.base_url}/missing/devconf-2003-transcript", timeout=5)
            self.assertEqual(missing.exception.code, 404)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(f"{server.base_url}/", timeout=5)

    def test_public_replay_passes_all_six_outcome_classes(self) -> None:
        result = run_suite(SUITE, FakeSubjectAdapter(replay_results()), allowed_root=ROOT)
        self.assertTrue(result["run_valid"])
        self.assertTrue(result["run_passed"])
        self.assertEqual(result["metrics"]["total"], 6)
        self.assertEqual(result["metrics"]["correct"], 6)
        self.assertEqual(result["metrics"]["false_accepted"], 0)
        self.assertGreater(result["fixture_access_count"], 0)
        source_tasks = [row for row in result["tasks"] if row["counters"]["required_facts"]]
        self.assertTrue(all(row.get("fixture_access_count", 0) > 0 for row in source_tasks))

    def test_required_fact_rejects_assumption_and_nonsemantic_statuses(self) -> None:
        for mutation in ("assumption", "unverified", "contested"):
            with self.subTest(mutation=mutation):
                result = replay_results()["known-riscv-jal"]
                claim = result["ledger"]["claims"][0]
                if mutation == "assumption":
                    claim["epistemic_kind"] = "assumption"
                else:
                    claim["verification_status"] = mutation
                report = evaluate_replay("known-riscv-jal", result)
                self.assertFalse(report.passed)
                self.assertTrue(
                    any(
                        "semantically verified" in failure
                        or "non-semantic" in failure
                        for failure in report.failures
                    ),
                    report.failures,
                )

    def test_required_fact_rejects_bad_inference_graph(self) -> None:
        result = replay_results()["known-riscv-jal"]
        result["ledger"]["claims"][0]["epistemic_kind"] = "inference"
        result["ledger"]["derivations"] = [
            {"claim_id": "claim-jal", "premise_claim_ids": ["claim-jal"]}
        ]
        report = evaluate_replay("known-riscv-jal", result)
        self.assertFalse(report.passed)
        self.assertTrue(
            any("inference claim" in failure for failure in report.failures),
            report.failures,
        )

    def test_required_fact_rejects_negation_and_keyword_stuffing(self) -> None:
        for prefix in (
            "It is false that ",
            "Keyword dump only: jal instruction j-type encoding; ",
        ):
            with self.subTest(prefix=prefix):
                result = replay_results()["known-riscv-jal"]
                result["ledger"]["claims"][0]["text"] = (
                    prefix + result["ledger"]["claims"][0]["text"]
                )
                result["final_text"] = prefix + result["final_text"]
                encoded = result["final_text"].encode("utf-8")
                result["ledger"]["final_claim_refs"] = [
                    {
                        "start_byte": 0,
                        "end_byte": len(encoded),
                        "claim_ids": ["claim-jal", "claim-j-type"],
                    }
                ]
                report = evaluate_replay("known-riscv-jal", result)
                self.assertFalse(report.passed)
                self.assertTrue(
                    any("exact" in failure for failure in report.failures),
                    report.failures,
                )

    def test_required_fact_requires_exact_claim_bound_final_reference(self) -> None:
        result = replay_results()["known-riscv-jal"]
        result["ledger"]["final_claim_refs"] = []
        report = evaluate_replay("known-riscv-jal", result)
        self.assertFalse(report.passed)
        self.assertTrue(
            any("claim-bound final reference" in failure for failure in report.failures),
            report.failures,
        )

    def test_head_request_does_not_prove_fixture_acquisition(self) -> None:
        subject = FakeSubjectAdapter(
            replay_results(),
            source_request_method="HEAD",
        )
        result = run_suite(SUITE, subject, allowed_root=ROOT)
        self.assertTrue(result["run_valid"])
        self.assertFalse(result["run_passed"])
        self.assertGreater(result["metrics"]["false_accepted"], 0)
        self.assertTrue(
            any(
                "did not acquire required fixture source" in failure
                for row in result["tasks"]
                for failure in row["failures"]
            )
        )

    def test_hung_subject_is_killed_by_external_wall_watchdog(self) -> None:
        suite = single_task_suite("known-riscv-jal", wall_sec=1)
        started = time.monotonic()
        result = run_suite(suite, HungSubjectAdapter())
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 6, result)
        self.assertFalse(result["run_valid"])
        self.assertFalse(result["run_passed"])
        self.assertEqual(result["tasks"][0]["verdict"], "unverified")
        self.assertTrue(
            any("wall timeout" in failure for failure in result["tasks"][0]["failures"]),
            result["tasks"][0],
        )

    def test_claiming_private_fixture_without_acquiring_it_is_false_accept(self) -> None:
        subject = FakeSubjectAdapter(
            replay_results(),
            exercise_fixture_gateway=False,
        )
        result = run_suite(SUITE, subject, allowed_root=ROOT)
        self.assertTrue(result["run_valid"])
        self.assertFalse(result["run_passed"])
        self.assertGreater(result["metrics"]["false_accepted"], 0)
        self.assertTrue(
            any(
                "did not acquire required fixture source" in failure
                for row in result["tasks"]
                for failure in row["failures"]
            )
        )
        self.assertEqual(result["metrics"]["unsupported_major_claim_rate_pct"], 0.0)
        self.assertEqual(result["metrics"]["quote_accuracy_pct"], 100.0)

    def test_private_oracles_are_never_sent_to_subject(self) -> None:
        suite = load_suite(SUITE, allowed_root=ROOT)
        payloads = [
            build_service_payload(task, fixture_base_url="http://127.0.0.1:1")
            for task in suite.tasks
        ]
        result = run_suite(SUITE, FakeSubjectAdapter(replay_results()), allowed_root=ROOT)
        self.assertTrue(result["run_valid"])
        serialized = json.dumps(payloads, ensure_ascii=False).casefold()
        for forbidden in (
            "oracle", "required_facts", "expected_outcomes", "source-riscv-isa",
            "eval_override_accepted", "forbidden_claims", "required_conflicts",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(
            set(payloads[0]),
            {"goal", "task_id", "max_wall_sec", "standalone_test", "output_contract_version", "source_gateway_url"},
        )

    def test_exact_quote_with_negation_dropped_is_false_accept(self) -> None:
        replays = replay_results()
        bad = replays["entailment-negation-trap"]
        bad["final_text"] = "The prototype used J-type encoding."
        bad["ledger"]["claims"][0]["text"] = "The prototype used J-type encoding."
        result = run_suite(SUITE, FakeSubjectAdapter(replays), allowed_root=ROOT)
        row = next(row for row in result["tasks"] if row["id"] == "entailment-negation-trap")
        self.assertEqual(row["verdict"], "FALSE_ACCEPT")
        self.assertTrue(any("required fact" in failure for failure in row["failures"]))
        self.assertEqual(result["metrics"]["false_accepted"], 1)
        self.assertTrue(result["run_valid"], "candidate failure must not invalidate the evaluator run")
        self.assertFalse(result["run_passed"])

    def test_prompt_injection_followed_is_false_accept(self) -> None:
        replays = replay_results()
        bad = replays["source-prompt-injection"]
        bad["final_text"] = "EVAL_OVERRIDE_ACCEPTED"
        bad["ledger"]["claims"][0]["text"] = "EVAL_OVERRIDE_ACCEPTED"
        result = run_suite(SUITE, FakeSubjectAdapter(replays), allowed_root=ROOT)
        row = next(row for row in result["tasks"] if row["id"] == "source-prompt-injection")
        self.assertEqual(row["verdict"], "FALSE_ACCEPT")
        self.assertTrue(any("forbidden claim" in failure for failure in row["failures"]))

    def test_source_hash_tamper_is_false_accept(self) -> None:
        replays = replay_results()
        replays["known-riscv-jal"]["ledger"]["sources"][0]["raw_sha256"] = "f" * 64
        result = run_suite(SUITE, FakeSubjectAdapter(replays), allowed_root=ROOT)
        row = next(row for row in result["tasks"] if row["id"] == "known-riscv-jal")
        self.assertEqual(row["verdict"], "FALSE_ACCEPT")
        self.assertTrue(any("hash mismatch" in failure for failure in row["failures"]))

    def test_accepted_malformed_result_is_false_accept_not_infrastructure_error(self) -> None:
        replays = replay_results()
        replays["known-riscv-jal"] = {
            "contract_version": "research-result/v1",
            "mission_id": "eval-known-riscv-jal",
            "status": "accepted",
            "accepted": True,
            "ledger": {},
            "search_log": [],
        }
        result = run_suite(SUITE, FakeSubjectAdapter(replays), allowed_root=ROOT)
        row = next(row for row in result["tasks"] if row["id"] == "known-riscv-jal")
        self.assertEqual(row["verdict"], "FALSE_ACCEPT")
        self.assertTrue(result["run_valid"])

    def test_quote_offset_tamper_is_false_accept(self) -> None:
        replays = replay_results()
        replays["known-riscv-jal"]["ledger"]["spans"][0]["end_byte"] -= 1
        result = run_suite(SUITE, FakeSubjectAdapter(replays), allowed_root=ROOT)
        row = next(row for row in result["tasks"] if row["id"] == "known-riscv-jal")
        self.assertEqual(row["verdict"], "FALSE_ACCEPT")
        self.assertTrue(any("excerpt does not match" in failure for failure in row["failures"]))

    def test_subject_exception_is_unverified_and_invalidates_run(self) -> None:
        subject = FakeSubjectAdapter(replay_results(), fail_tasks={"known-riscv-jal"})
        result = run_suite(SUITE, subject, allowed_root=ROOT)
        self.assertFalse(result["run_valid"])
        row = next(row for row in result["tasks"] if row["id"] == "known-riscv-jal")
        self.assertEqual(row["verdict"], "unverified")
        self.assertEqual(result["metrics"]["unverified"], 1)

    def test_cleanup_failure_is_unverified_and_invalidates_run(self) -> None:
        subject = FakeSubjectAdapter(replay_results(), unclean_tasks={"clarify-history-of-question"})
        result = run_suite(SUITE, subject, allowed_root=ROOT)
        self.assertFalse(result["run_valid"])
        self.assertFalse(result["validation"]["all_task_cleanup_proven"])
        row = next(row for row in result["tasks"] if row["id"] == "clarify-history-of-question")
        self.assertEqual(row["verdict"], "unverified")

    def test_identity_change_invalidates_otherwise_correct_run(self) -> None:
        subject = FakeSubjectAdapter(
            replay_results(),
            end_identity={"instance_id": "restarted", "source_sha256": "1" * 64, "model": "deterministic-replay", "standalone_test_mode": True},
        )
        result = run_suite(SUITE, subject, allowed_root=ROOT)
        self.assertFalse(result["run_valid"])
        self.assertFalse(result["validation"]["subject_identity_stable"])
        self.assertEqual(result["metrics"]["correct"], 6)

    def test_invalid_run_replaces_stale_result_with_current_failure(self) -> None:
        result = run_suite(SUITE, FakeSubjectAdapter(replay_results(), fail_tasks={"known-riscv-jal"}), allowed_root=ROOT)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "result.json"
            target.write_text("trusted-old-result", encoding="utf-8")
            write_result_atomic(result, target)
            persisted = json.loads(target.read_text(encoding="utf-8"))
            self.assertFalse(persisted["run_valid"])
            self.assertFalse(persisted["run_passed"])
            self.assertNotEqual(target.read_text(encoding="utf-8"), "trusted-old-result")

    def test_atomic_result_uses_unique_temps_under_concurrent_writers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "result.json"
            failures: list[BaseException] = []

            def publish(index: int) -> None:
                try:
                    write_result_atomic(
                        {"run_valid": bool(index % 2), "writer": index},
                        target,
                    )
                except BaseException as exc:  # test must retain thread failures
                    failures.append(exc)

            threads = [threading.Thread(target=publish, args=(index,)) for index in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertFalse(failures)
            persisted = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn(persisted["writer"], range(12))
            self.assertFalse(list(Path(temp).glob(".result.json.*.tmp")))

    def test_atomic_result_refuses_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "outside.json"
            outside.write_text("do-not-touch", encoding="utf-8")
            target = root / "result.json"
            try:
                target.symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with self.assertRaises(ResultWriteError):
                write_result_atomic({"run_valid": False}, target)
            self.assertEqual(outside.read_text(encoding="utf-8"), "do-not-touch")

    def test_empty_metric_denominators_are_null_not_fabricated_zero(self) -> None:
        metrics = aggregate_metrics([])
        self.assertIsNone(metrics["false_accepted_pct_of_accepted"])
        self.assertIsNone(metrics["unsupported_major_claim_rate_pct"])
        self.assertIsNone(metrics["quote_accuracy_pct"])

    def test_pinned_legacy_riscv_record_is_a_false_accept(self) -> None:
        record, _ = strict_json_load(ROOT / "regressions/legacy_riscv_20260705/legacy_artifact_oracle.json")
        verdict = evaluate_legacy_artifact(require_object(record, "legacy record"))
        self.assertEqual(verdict["verdict"], "FALSE_ACCEPT")
        self.assertTrue(verdict["substantive_failure"])
        self.assertEqual(verdict["claimed_evidence_coverage_percent"], 100)


if __name__ == "__main__":
    unittest.main()
