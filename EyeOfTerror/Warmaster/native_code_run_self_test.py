#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WARM_MASTER_ROOT = Path(__file__).resolve().parent
for candidate in (PROJECT_ROOT, WARM_MASTER_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from EyeOfTerror.common_protocol import commander_order  # noqa: E402
from eye_of_terror.inner_circle.ceraxia import plan_code_task  # noqa: E402
from eye_of_terror.native_code_run import (  # noqa: E402
    NATIVE_CONTRACT_FIELDS,
    NATIVE_EXECUTION,
    build_native_code_contract,
    is_native_code_run,
    load_native_code_run,
    native_governor_plan,
    validate_native_code_contract,
    validate_native_code_run_package,
    write_native_code_run,
)
from eye_of_terror.run_validation import (  # noqa: E402
    run_oversight_diagnostics,
    run_package_action_errors,
    run_package_diagnostics,
    validate_revision_plan,
)


def leadership_directive(task_id: str, mission_id: str) -> dict:
    return {
        "kind": "ceraxia_leadership_directive",
        "version": 1,
        "task_id": task_id,
        "mission_id": mission_id,
        "leader": "Ceraxia",
        "decision": "delegate",
        "delegated_to": "SkitariiWarband",
        "mission_intent": "Deliver the requested verified code outcome.",
        "priorities": ["correctness", "preserve unrelated behavior"],
        "constraints": ["keep the public contract compatible"],
        "success_conditions": ["the requested behavior passes executable checks"],
        "tradeoffs": [],
        "escalation_conditions": ["a product decision changes observable behavior"],
    }


def abaddon_order(task_id: str, mission_id: str) -> dict:
    return commander_order(
        mission_id,
        to="Ceraxia",
        user_request="repair the code",
        commander_intent="Deliver a verified repair.",
        primary_goal="Repair the code without changing unrelated behavior.",
        success_conditions=["the requested behavior is verified"],
        constraints=["preserve compatibility"],
    )


class NativeCodeRunTests(unittest.TestCase):
    def test_contract_is_exact_v2_native_mission(self) -> None:
        contract = build_native_code_contract("Repair the parser", "native-contract")
        self.assertEqual(set(contract), NATIVE_CONTRACT_FIELDS)
        self.assertEqual(contract["version"], 2)
        self.assertEqual(contract["execution"], NATIVE_EXECUTION)
        self.assertEqual(contract["mission_id"], "mission-native-contract")
        self.assertNotIn("worker_plan", contract)
        self.assertNotIn("required_artifacts", contract)

        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_native_code_contract({**contract, "files": ["parser.py"]})
        with self.assertRaisesRegex(ValueError, "mission_id does not match"):
            validate_native_code_contract(contract, expected_mission_id="mission-other")

    def test_implicit_task_ids_include_request_identity(self) -> None:
        first = build_native_code_contract(
            "Create a standalone artifact for collision smoke alpha-one.txt.",
            None,
        )
        second = build_native_code_contract(
            "Create a standalone artifact for collision smoke beta-two.txt.",
            None,
        )
        self.assertNotEqual(first["task_id"], second["task_id"])
        self.assertIn("alpha-one", first["goal"])
        self.assertIn("beta-two", second["goal"])

    def test_governor_plan_is_one_leadership_delegation(self) -> None:
        contract = build_native_code_contract("Repair the parser", "native-plan")
        plan = native_governor_plan(
            contract,
            abaddon_order(contract["task_id"], contract["mission_id"]),
        )
        self.assertEqual(plan["mission_id"], contract["mission_id"])
        self.assertEqual(len(plan["work_plan"]), 1)
        self.assertEqual(
            plan["work_plan"][0],
            {
                "step_id": "skitarii",
                "worker": "SkitariiWarband",
                "goal": "Own detailed planning, implementation, verification, and internal repair.",
                "depends_on": [],
                "expected_artifacts": [],
            },
        )
        serialized = json.dumps(plan, ensure_ascii=False)
        for forbidden in ('"files"', '"commands"', '"modules"', '"implementation_plan"'):
            self.assertNotIn(forbidden, serialized)

    def test_writer_binds_package_and_publishes_status_last(self) -> None:
        task_id = "native-package"
        contract = build_native_code_contract("Repair the parser", task_id)
        directive = leadership_directive(task_id, contract["mission_id"])
        plan = native_governor_plan(contract, abaddon_order(task_id, contract["mission_id"]))
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / task_id
            status = write_native_code_run(
                run_dir,
                contract,
                directive,
                plan,
                prepare_request_sha256="a" * 64,
            )
            self.assertTrue(is_native_code_run(run_dir))
            self.assertEqual(status["step_count"], 1)
            self.assertEqual(status["steps"][0]["step_id"], "skitarii")
            self.assertEqual(status["steps"][0]["worker"], "SkitariiWarband")
            self.assertFalse((run_dir / "dispatch").exists())
            self.assertEqual(validate_native_code_run_package(run_dir), [])
            loaded = load_native_code_run(run_dir)
            self.assertTrue(loaded["ok"])
            self.assertEqual(loaded["contract"], contract)
            self.assertEqual(loaded["receipt"]["prepare_request_sha256"], "a" * 64)
            self.assertEqual(list(run_dir.glob(".*.tmp")), [])
            self.assertEqual(run_package_action_errors(run_dir), [])
            package = run_package_diagnostics(run_dir)
            self.assertTrue(package["ok"])
            self.assertTrue(package["native"])
            self.assertFalse(package["files"]["dispatch_dir"])
            leadership = run_oversight_diagnostics(run_dir)
            self.assertTrue(leadership["ok"])
            self.assertEqual(leadership["summary"]["delegated_to"], "SkitariiWarband")
            self.assertEqual(
                validate_revision_plan(
                    run_dir,
                    {
                        "required": True,
                        "steps": [{"step_id": "skitarii", "worker": "SkitariiWarband"}],
                    },
                ),
                [],
            )

            tampered = dict(contract, goal="a different goal")
            (run_dir / "contract.json").write_text(
                json.dumps(tampered, ensure_ascii=False),
                encoding="utf-8",
            )
            errors = validate_native_code_run_package(run_dir)
            self.assertTrue(any("contract_sha256" in error for error in errors), errors)

    def test_facade_exposes_one_native_step_without_worker_plan(self) -> None:
        plan = plan_code_task("Repair the parser", task_id="native-facade").to_dict()
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["pipeline"]["mode"], "native_skitarii_mission")
        self.assertEqual(plan["pipeline"]["step_count"], 1)
        self.assertEqual(plan["pipeline"]["steps"][0]["worker"], "SkitariiWarband")
        self.assertNotIn("worker_plan", plan["contract"])
        self.assertEqual(plan["governor_plan"]["work_plan"][0]["step_id"], "skitarii")

    def test_port_registry_has_only_native_code_warband(self) -> None:
        registry = json.loads((WARM_MASTER_ROOT / "registry" / "ports.json").read_text(encoding="utf-8"))
        mechanicum = registry["mechanicum"]
        for retired in ("7014", "7015", "7016", "7017", "7018", "7019", "7020"):
            self.assertNotIn(retired, mechanicum)
        self.assertNotIn("7200", mechanicum)
        self.assertEqual(registry["warbands"]["7200"]["name"], "SkitariiWarband")
        self.assertEqual(
            registry["warbands"]["7200"]["supervisor"],
            "skitarii-warband.service",
        )


if __name__ == "__main__":
    unittest.main()
