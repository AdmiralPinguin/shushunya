#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from EyeOfTerror.common_protocol import commander_order  # noqa: E402
from EyeOfTerror.common_protocol.iskandar_directive import (  # noqa: E402
    build_iskandar_directive,
    validate_iskandar_directive,
)
from EyeOfTerror.Warmaster.eye_of_terror.native_research_run import (  # noqa: E402
    MAX_COMMANDER_ORDER_BYTES,
    NATIVE_RESEARCH_EXECUTION,
    NATIVE_RESEARCH_RUN_KIND,
    build_native_research_contract,
    is_native_research_run,
    load_native_research_run,
    native_research_governor_plan,
    native_research_prepare_request_sha256,
    validate_native_research_commander_order,
    validate_native_research_contract,
    validate_native_research_run_package,
    write_native_research_run,
)


TASK_ID = "native-research-test"
MISSION_ID = "mission-native-research-test"


def command() -> dict:
    return commander_order(
        MISSION_ID,
        to="IskandarKhayon",
        user_request="Establish the documented answer and preserve conflicting evidence.",
        commander_intent="Delegate one bounded research mission.",
        primary_goal="Produce a verified research brief.",
        success_conditions=["Major claims have archived evidence."],
        constraints=["Disclose source conflicts."],
        escalate_to_user_if=["The question cannot be disambiguated."],
    )


def model_payload(decision: str = "delegate") -> dict:
    return {
        "decision": decision,
        "research_objective": "Determine what reliable evidence supports.",
        "depth": "deep",
        "source_policy": "authoritative_preferred",
        "error_tolerance": "strict",
        "answer_mode": "research_brief",
        "priorities": ["Archive evidence before synthesis."],
        "allowed_source_classes": ["primary_source", "official_documentation"],
        "prohibited_source_classes": ["anonymous_or_unverified_web"],
        "constraints": [],
        "success_conditions": ["Conflicts and gaps are explicit."],
        "output_requirements": ["Evidence ledger", "Source manifest", "Final report"],
        "escalation_conditions": [],
        "clarification_question": "",
    }


def directive(decision: str = "delegate") -> dict:
    payload = model_payload(decision)
    if decision == "needs_clarification":
        payload["clarification_question"] = "Which edition should be treated as authoritative?"
    return build_iskandar_directive(
        {"ok": True, "content": payload},
        task_id=TASK_ID,
        mission_id=MISSION_ID,
        commander_order=command(),
    )


def package_inputs() -> tuple[dict, dict, dict]:
    contract = build_native_research_contract(
        "Research the documented answer.", TASK_ID, mission_id=MISSION_ID,
    )
    return contract, directive(), native_research_governor_plan(contract, command())


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class NativeResearchContractTests(unittest.TestCase):
    def test_contract_is_one_native_warband_mission_without_worker_plan(self) -> None:
        contract = build_native_research_contract(
            "Research the documented answer.", TASK_ID, mission_id=MISSION_ID,
        )
        self.assertEqual(contract["kind"], "research")
        self.assertEqual(contract["assigned_governor"], "IskandarKhayon")
        self.assertEqual(contract["execution"], NATIVE_RESEARCH_EXECUTION)
        self.assertNotIn("worker_plan", contract)
        self.assertNotIn("required_artifacts", contract)
        serialized = json.dumps(contract, ensure_ascii=False).lower()
        for forbidden in ("queries", "urls", "hypotheses", "citations", "expected_artifacts"):
            self.assertNotIn(f'"{forbidden}"', serialized)

    def test_contract_rejects_detailed_or_legacy_fields(self) -> None:
        contract = build_native_research_contract("Research it", TASK_ID, MISSION_ID)
        for field, value in (
            ("worker_plan", []),
            ("required_artifacts", []),
            ("queries", ["query"]),
            ("sources", ["https://example.invalid"]),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unknown fields"):
                validate_native_research_contract({**contract, field: value})
        with self.assertRaisesRegex(ValueError, "execution must be exactly"):
            validate_native_research_contract(
                {**contract, "execution": {**NATIVE_RESEARCH_EXECUTION, "step_id": "search"}},
            )

    def test_generated_task_id_is_stable_and_bounded(self) -> None:
        first = build_native_research_contract("Точный вопрос без ASCII", None)
        second = build_native_research_contract("Точный вопрос без ASCII", None)
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertLessEqual(len(first["task_id"]), 128)
        self.assertTrue(first["task_id"].startswith("iskandar-"))

    def test_governor_plan_has_exactly_one_opaque_delegation(self) -> None:
        contract, _directive, plan = package_inputs()
        self.assertEqual(plan["governor"], "IskandarKhayon")
        self.assertEqual(len(plan["work_plan"]), 1)
        step = plan["work_plan"][0]
        self.assertEqual(step["step_id"], "research_warband")
        self.assertEqual(step["worker"], "ResearchWarband")
        self.assertEqual(step["expected_artifacts"], [])
        self.assertNotIn("queries", step)
        self.assertNotIn("sources", step)

        wrong_command = {**command(), "to": "Ceraxia"}
        with self.assertRaisesRegex(ValueError, "IskandarKhayon"):
            native_research_governor_plan(contract, wrong_command)


class NativeResearchPackageTests(unittest.TestCase):
    def _write(self, parent: Path, *, request_hash: str = "") -> Path:
        run_dir = parent / TASK_ID
        contract, leadership, plan = package_inputs()
        write_native_research_run(
            run_dir,
            contract,
            leadership,
            plan,
            command(),
            prepare_request_sha256=request_hash,
        )
        return run_dir

    def test_package_round_trip_hashes_identity_and_status(self) -> None:
        contract, _leadership, _plan = package_inputs()
        request_hash = native_research_prepare_request_sha256(contract, command())
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self._write(Path(raw), request_hash=request_hash)
            self.assertTrue(is_native_research_run(run_dir))
            self.assertEqual(validate_native_research_run_package(run_dir), [])
            self.assertFalse((run_dir / "dispatch").exists())
            self.assertEqual(
                {path.name for path in run_dir.iterdir()},
                {
                    "contract.json",
                    "commander_order.json",
                    "iskandar_directive.json",
                    "governor_plan.json",
                    "native_run_receipt.json",
                    "status.json",
                },
            )
            loaded = load_native_research_run(run_dir)
            self.assertTrue(loaded["ok"])
            self.assertEqual(loaded["contract"]["execution"], NATIVE_RESEARCH_EXECUTION)
            self.assertEqual(loaded["receipt"]["prepare_request_sha256"], request_hash)
            self.assertEqual(
                loaded["receipt"]["commander_order_sha256"],
                hashlib.sha256(
                    json.dumps(
                        loaded["commander_order"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                ).hexdigest(),
            )
            self.assertEqual(loaded["status"]["run_kind"], NATIVE_RESEARCH_RUN_KIND)
            self.assertEqual(loaded["status"]["step_count"], 1)
            self.assertEqual(loaded["status"]["steps"][0]["worker"], "ResearchWarband")
            self.assertEqual(loaded["status"]["directive_path"], str(run_dir / "iskandar_directive.json"))
            self.assertEqual(
                loaded["status"]["commander_order_path"], str(run_dir / "commander_order.json"),
            )

    def test_every_hashed_payload_tamper_is_detected(self) -> None:
        mutations = {
            "contract.json": lambda value: value["non_goals"].append("tampered"),
            "commander_order.json": lambda value: value.__setitem__(
                "user_request", value["user_request"] + " tampered",
            ),
            "iskandar_directive.json": lambda value: value["priorities"].append("tampered"),
            "governor_plan.json": lambda value: value.__setitem__("understanding", "tampered"),
        }
        for filename, mutate in mutations.items():
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as raw:
                run_dir = self._write(Path(raw))
                path = run_dir / filename
                payload = read_json(path)
                mutate(payload)
                write_json(path, payload)
                errors = validate_native_research_run_package(run_dir)
                self.assertTrue(errors)
                self.assertTrue(
                    any("receipt" in error or "does not match" in error for error in errors),
                    errors,
                )

    def test_status_path_and_step_tamper_are_detected(self) -> None:
        for mutation in ("path", "step"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as raw:
                run_dir = self._write(Path(raw))
                path = run_dir / "status.json"
                status = read_json(path)
                if mutation == "path":
                    status["directive_path"] = str(run_dir / "other.json")
                else:
                    status["steps"][0]["worker"] = "Lexmechanic"
                write_json(path, status)
                self.assertTrue(validate_native_research_run_package(run_dir))

    def test_non_delegated_directive_cannot_be_persisted_as_executable(self) -> None:
        contract = build_native_research_contract("Research it", TASK_ID, MISSION_ID)
        plan = native_research_governor_plan(contract, command())
        with tempfile.TemporaryDirectory() as raw, self.assertRaisesRegex(
            ValueError, "did not authorize delegation",
        ):
            write_native_research_run(
                Path(raw) / TASK_ID,
                contract,
                directive("needs_clarification"),
                plan,
                command(),
            )

    def test_schema_valid_directive_that_drops_caller_constraint_cannot_be_written(self) -> None:
        contract, leadership, plan = package_inputs()
        dropped = copy.deepcopy(leadership)
        caller_constraint = command()["constraints"][0]
        dropped["constraints"].remove(caller_constraint)
        self.assertEqual(
            validate_iskandar_directive(dropped, require_delegation=True),
            dropped,
            "the persisted schema alone cannot prove caller preservation",
        )
        with tempfile.TemporaryDirectory() as raw, self.assertRaisesRegex(
            ValueError, "dropped commander_order.constraints",
        ):
            write_native_research_run(
                Path(raw) / TASK_ID, contract, dropped, plan, command(),
            )

    def test_commander_order_is_exact_bounded_and_prepare_hash_bound(self) -> None:
        contract, leadership, plan = package_inputs()
        cmd = command()
        normalized = validate_native_research_commander_order(
            cmd, expected_mission_id=MISSION_ID,
        )
        self.assertEqual(normalized, cmd)
        first_hash = native_research_prepare_request_sha256(contract, cmd)
        changed = copy.deepcopy(cmd)
        changed["user_request"] += " Additional caller acceptance detail."
        second_hash = native_research_prepare_request_sha256(contract, changed)
        self.assertNotEqual(first_hash, second_hash)
        with tempfile.TemporaryDirectory() as raw, self.assertRaisesRegex(
            ValueError, "commander-bound prepare identity",
        ):
            write_native_research_run(
                Path(raw) / TASK_ID,
                contract,
                leadership,
                plan,
                changed,
                prepare_request_sha256=first_hash,
            )

        for label, broken in (
            ("unknown", {**cmd, "extra": True}),
            ("missing", {key: value for key, value in cmd.items() if key != "constraints"}),
            ("authority", {**cmd, "from": "IskandarKhayon"}),
            ("whitespace", {**cmd, "primary_goal": cmd["primary_goal"] + " "}),
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_native_research_commander_order(
                    broken, expected_mission_id=MISSION_ID,
                )

        oversized = copy.deepcopy(cmd)
        oversized["user_request"] = "x" * 100_000
        oversized["constraints"] = [
            f"{index:02d}" + ("x" * 3_998)
            for index in range(8)
        ]
        self.assertGreater(
            len(json.dumps(
                oversized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")),
            MAX_COMMANDER_ORDER_BYTES,
        )
        with self.assertRaisesRegex(ValueError, "canonical bytes"):
            validate_native_research_commander_order(
                oversized, expected_mission_id=MISSION_ID,
            )

    def test_dispatch_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            run_dir = parent / TASK_ID
            (run_dir / "dispatch").mkdir(parents=True)
            contract, leadership, plan = package_inputs()
            with self.assertRaisesRegex(ValueError, "must not contain a dispatch directory"):
                write_native_research_run(run_dir, contract, leadership, plan, command())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_run_and_package_member_symlinks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            real = parent / "real"
            real.mkdir()
            alias = parent / TASK_ID
            try:
                alias.symlink_to(real, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            contract, leadership, plan = package_inputs()
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                write_native_research_run(alias, contract, leadership, plan, command())

        for filename in ("iskandar_directive.json", "commander_order.json"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as raw:
                run_dir = self._write(Path(raw))
                member = run_dir / filename
                outside = Path(raw) / "outside.json"
                outside.write_text("{}\n", encoding="utf-8")
                member.unlink()
                try:
                    member.symlink_to(outside)
                except OSError as exc:
                    self.skipTest(f"symlinks unavailable: {exc}")
                errors = validate_native_research_run_package(run_dir)
                self.assertTrue(
                    any("missing or is not a regular file" in error for error in errors), errors,
                )

    def test_run_directory_identity_and_unfinished_write_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self._write(Path(raw))
            moved = Path(raw) / "different-task-id"
            run_dir.rename(moved)
            errors = validate_native_research_run_package(moved)
            self.assertTrue(any("task_id does not match" in error for error in errors), errors)

        with tempfile.TemporaryDirectory() as raw:
            run_dir = self._write(Path(raw))
            (run_dir / ".status.json.crash.tmp").write_text("partial", encoding="utf-8")
            errors = validate_native_research_run_package(run_dir)
            self.assertTrue(any("unfinished atomic writes" in error for error in errors), errors)

    def test_bad_prepare_hash_and_missing_files_fail_closed(self) -> None:
        contract, leadership, plan = package_inputs()
        with tempfile.TemporaryDirectory() as raw, self.assertRaisesRegex(
            ValueError, "prepare_request_sha256",
        ):
            write_native_research_run(
                Path(raw) / TASK_ID,
                contract,
                leadership,
                plan,
                command(),
                prepare_request_sha256="NOT-A-HASH",
            )
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self._write(Path(raw))
            (run_dir / "native_run_receipt.json").unlink()
            loaded = load_native_research_run(run_dir)
            self.assertFalse(loaded["ok"])
            self.assertTrue(validate_native_research_run_package(run_dir))

    def test_governor_plan_tamper_cannot_be_written(self) -> None:
        contract, leadership, plan = package_inputs()
        tampered = copy.deepcopy(plan)
        tampered["work_plan"][0]["expected_artifacts"] = ["evidence_ledger.json"]
        with tempfile.TemporaryDirectory() as raw, self.assertRaisesRegex(
            ValueError, "exact ResearchWarband boundary",
        ):
            write_native_research_run(
                Path(raw) / TASK_ID, contract, leadership, tampered, command(),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
