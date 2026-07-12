#!/usr/bin/env python3
from __future__ import annotations

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
from EyeOfTerror.Warmaster.eye_of_terror.native_code_run import (  # noqa: E402
    NATIVE_EXECUTION as NATIVE_CODE_EXECUTION,
    build_native_code_contract,
    load_native_code_run,
    native_governor_plan,
    validate_native_code_run_package,
    write_native_code_run,
)
from EyeOfTerror.Warmaster.eye_of_terror.native_research_run import (  # noqa: E402
    NATIVE_RESEARCH_EXECUTION,
    build_native_research_contract,
    native_research_governor_plan,
    validate_native_research_run_package,
    write_native_research_run,
)
from EyeOfTerror.Warmaster.eye_of_terror.native_runs import (  # noqa: E402
    MAX_CONTRACT_BYTES,
    NATIVE_CODE_ADAPTER,
    NATIVE_RESEARCH_ADAPTER,
    is_native_warband_run,
    load_native_warband_run,
    native_adapter_for_contract,
    native_adapter_for_execution,
    native_adapter_for_run,
    validate_native_warband_run,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def ceraxia_command(task_id: str) -> dict:
    return commander_order(
        f"mission-{task_id}",
        to="Ceraxia",
        user_request="Repair the parser.",
        commander_intent="Delegate one code mission.",
        primary_goal="Repair and verify the parser.",
        success_conditions=["Executable checks pass."],
        constraints=["Do not refactor unrelated files."],
    )


def ceraxia_directive(task_id: str) -> dict:
    return {
        "kind": "ceraxia_leadership_directive",
        "version": 1,
        "task_id": task_id,
        "mission_id": f"mission-{task_id}",
        "leader": "Ceraxia",
        "decision": "delegate",
        "delegated_to": "SkitariiWarband",
        "mission_intent": "Repair and verify the parser.",
        "priorities": ["Correctness first."],
        "constraints": ["Do not refactor unrelated files."],
        "success_conditions": ["Executable checks pass."],
        "tradeoffs": [],
        "escalation_conditions": [],
    }


def research_command(task_id: str) -> dict:
    return commander_order(
        f"mission-{task_id}",
        to="IskandarKhayon",
        user_request="Verify the documented claim.",
        commander_intent="Delegate one research mission.",
        primary_goal="Produce an evidence-grounded answer.",
        success_conditions=["Major claims have archived evidence."],
        constraints=["Disclose conflicts."],
    )


def research_directive(task_id: str) -> dict:
    return {
        "kind": "iskandar_research_directive",
        "version": 1,
        "task_id": task_id,
        "mission_id": f"mission-{task_id}",
        "leader": "IskandarKhayon",
        "decision": "delegate",
        "delegated_to": "ResearchWarband",
        "research_objective": "Verify the documented claim.",
        "depth": "standard",
        "source_policy": "authoritative_preferred",
        "error_tolerance": "strict",
        "answer_mode": "direct_answer",
        "priorities": ["Archive evidence first."],
        "allowed_source_classes": ["official_documentation"],
        "prohibited_source_classes": [],
        "constraints": ["Disclose conflicts."],
        "success_conditions": ["Major claims have archived evidence."],
        "output_requirements": ["Evidence ledger", "Final report"],
        "escalation_conditions": [],
        "clarification_question": "",
    }


class NativeRunDiscriminatorTests(unittest.TestCase):
    def test_exact_descriptors_select_exact_adapters(self) -> None:
        self.assertIs(
            native_adapter_for_execution(NATIVE_CODE_EXECUTION),
            NATIVE_CODE_ADAPTER,
        )
        self.assertIs(
            native_adapter_for_execution(NATIVE_RESEARCH_EXECUTION),
            NATIVE_RESEARCH_ADAPTER,
        )
        self.assertIs(
            native_adapter_for_contract({"execution": NATIVE_CODE_EXECUTION}),
            NATIVE_CODE_ADAPTER,
        )
        self.assertIs(
            native_adapter_for_contract({"execution": NATIVE_RESEARCH_EXECUTION}),
            NATIVE_RESEARCH_ADAPTER,
        )
        self.assertIsNone(native_adapter_for_contract({"worker_plan": []}))
        self.assertIsNone(native_adapter_for_execution({"kind": "http_worker"}))

    def test_declared_native_intent_is_recognized_only_when_requested(self) -> None:
        malformed_code = {"kind": "skitarii_mission", "step_id": "wrong"}
        malformed_research = {"backend": "ResearchWarband", "step_id": "wrong"}
        self.assertIsNone(native_adapter_for_execution(malformed_code))
        self.assertIsNone(native_adapter_for_execution(malformed_research))
        self.assertIs(
            native_adapter_for_execution(malformed_code, declared=True),
            NATIVE_CODE_ADAPTER,
        )
        self.assertIs(
            native_adapter_for_execution(malformed_research, declared=True),
            NATIVE_RESEARCH_ADAPTER,
        )

    def test_adapter_metadata_keeps_existing_code_identity_unchanged(self) -> None:
        code = NATIVE_CODE_ADAPTER.to_dict()
        self.assertEqual(code["run_kind"], "native_skitarii_code")
        self.assertEqual(code["execution"], NATIVE_CODE_EXECUTION)
        self.assertEqual(code["governor"], "Ceraxia")
        self.assertEqual(code["directive_filename"], "ceraxia_directive.json")
        self.assertEqual(code["ledger_mission_key"], "skitarii_mission")
        self.assertEqual(code["service_port"], 7200)

        research = NATIVE_RESEARCH_ADAPTER.to_dict()
        self.assertEqual(research["execution"], NATIVE_RESEARCH_EXECUTION)
        self.assertEqual(research["governor"], "IskandarKhayon")
        self.assertEqual(research["service_port"], 7201)

    def test_existing_native_code_package_delegates_to_unchanged_api(self) -> None:
        task_id = "adapter-code"
        command = ceraxia_command(task_id)
        contract = build_native_code_contract(
            "Repair the parser.", task_id, mission_id=command["mission_id"],
        )
        plan = native_governor_plan(contract, command)
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw) / task_id
            write_native_code_run(run_dir, contract, ceraxia_directive(task_id), plan)
            adapter = native_adapter_for_run(run_dir)
            self.assertIs(adapter, NATIVE_CODE_ADAPTER)
            self.assertTrue(is_native_warband_run(run_dir))
            self.assertEqual(adapter.validate(run_dir), validate_native_code_run_package(run_dir))
            self.assertEqual(
                adapter.load(run_dir)["contract"],
                load_native_code_run(run_dir)["contract"],
            )
            self.assertEqual(validate_native_warband_run(run_dir), [])
            self.assertEqual(load_native_warband_run(run_dir)["contract"], contract)

    def test_native_research_package_uses_parallel_api(self) -> None:
        task_id = "adapter-research"
        command = research_command(task_id)
        contract = build_native_research_contract(
            "Verify the claim.", task_id, mission_id=command["mission_id"],
        )
        plan = native_research_governor_plan(contract, command)
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw) / task_id
            write_native_research_run(
                run_dir, contract, research_directive(task_id), plan, command,
            )
            adapter = native_adapter_for_run(run_dir)
            self.assertIs(adapter, NATIVE_RESEARCH_ADAPTER)
            self.assertTrue(adapter.is_run(run_dir))
            self.assertEqual(adapter.validate(run_dir), validate_native_research_run_package(run_dir))
            self.assertEqual(validate_native_warband_run(run_dir), [])
            self.assertEqual(load_native_warband_run(run_dir)["contract"], contract)

    def test_malformed_declared_native_run_is_quarantinable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw) / "malformed-native"
            write_json(
                run_dir / "contract.json",
                {
                    "kind": "research",
                    "execution": {
                        "kind": "research_warband_mission",
                        "step_id": "wrong",
                        "backend": "ResearchWarband",
                    },
                },
            )
            self.assertIs(native_adapter_for_run(run_dir), NATIVE_RESEARCH_ADAPTER)
            self.assertTrue(is_native_warband_run(run_dir))
            errors = validate_native_warband_run(run_dir)
            self.assertTrue(errors)
            self.assertNotIn("run does not declare", errors[0])

    def test_unrelated_corrupt_or_oversized_contract_is_not_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw) / "generic"
            run_dir.mkdir()
            (run_dir / "contract.json").write_text("not json", encoding="utf-8")
            self.assertIsNone(native_adapter_for_run(run_dir))
            self.assertFalse(is_native_warband_run(run_dir))
            self.assertTrue(validate_native_warband_run(run_dir))

        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw) / "oversized"
            run_dir.mkdir()
            (run_dir / "contract.json").write_text(
                " " * (MAX_CONTRACT_BYTES + 1), encoding="utf-8",
            )
            self.assertIsNone(native_adapter_for_run(run_dir))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_contract_symlink_is_not_followed_by_discriminator(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            run_dir = root / "linked"
            run_dir.mkdir()
            outside = root / "outside.json"
            write_json(outside, {"execution": NATIVE_RESEARCH_EXECUTION})
            try:
                (run_dir / "contract.json").symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            self.assertIsNone(native_adapter_for_run(run_dir))
            self.assertFalse(is_native_warband_run(run_dir))


if __name__ == "__main__":
    unittest.main(verbosity=2)
