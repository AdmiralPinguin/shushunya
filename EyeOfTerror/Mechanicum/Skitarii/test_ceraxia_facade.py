"""Native Ceraxia leadership boundary and handoff regression tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from EyeOfTerror.common_protocol import commander_order, validate_protocol_payload
from EyeOfTerror.common_protocol.ceraxia_directive import (
    CeraxiaDirectiveError,
    build_ceraxia_directive,
)
from EyeOfTerror.Warmaster.eye_of_terror import skitarii_bridge, task_prepare
from EyeOfTerror.Warmaster.eye_of_terror.inner_circle import ceraxia, ceraxia_service
from EyeOfTerror.Warmaster.eye_of_terror.native_code_run import (
    build_native_code_contract,
    is_native_code_run,
    native_governor_plan,
    validate_native_code_run_package,
    write_native_code_run,
)


def order(task_id: str = "native-facade", *, constraint: str = "Preserve user changes.") -> dict:
    payload = commander_order(
        f"mission-{task_id}",
        to="Ceraxia",
        user_request="Fix the requested behavior.",
        commander_intent="Delegate one bounded coding mission.",
        primary_goal="Fix the requested behavior.",
        success_conditions=["Executable acceptance passes."],
        constraints=[constraint],
        escalate_to_user_if=["A product decision is required."],
    )
    validate_protocol_payload(payload, expected_type="commander_order")
    return payload


def model_payload(decision: str = "delegate", **extra) -> dict:
    content = {
        "decision": decision,
        "mission_intent": "Deliver the requested behavior without scope drift.",
        "priorities": ["Correctness", "Verification"],
        "constraints": ["Preserve user changes."],
        "success_conditions": ["Executable acceptance passes."],
        "tradeoffs": ["Prefer a bounded fix."],
        "escalation_conditions": ["A product decision is required."],
        **extra,
    }
    return {"ok": True, "content": json.dumps(content, ensure_ascii=False)}


def directive(task_id: str = "native-facade", decision: str = "delegate") -> dict:
    return build_ceraxia_directive(
        model_payload(decision),
        task_id=task_id,
        mission_id=f"mission-{task_id}",
        commander_order=order(task_id),
    )


class TestCeraxiaDirective(unittest.TestCase):
    def test_leader_output_is_strict_and_preserves_commander_boundaries(self) -> None:
        result = directive()
        self.assertEqual(result["leader"], "Ceraxia")
        self.assertEqual(result["delegated_to"], "SkitariiWarband")
        self.assertIn("Preserve user changes.", result["constraints"])
        self.assertIn("Executable acceptance passes.", result["success_conditions"])
        self.assertNotIn("files", result)
        self.assertNotIn("steps", result)
        self.assertNotIn("commands", result)

    def test_detailed_plan_and_unknown_fields_are_rejected(self) -> None:
        with self.assertRaises(CeraxiaDirectiveError):
            build_ceraxia_directive(
                model_payload(files=["foo.py"]),
                task_id="native-facade",
                mission_id="mission-native-facade",
                commander_order=order(),
            )
        with self.assertRaises(CeraxiaDirectiveError):
            build_ceraxia_directive(
                model_payload(unexpected="value"),
                task_id="native-facade",
                mission_id="mission-native-facade",
                commander_order=order(),
            )

    def test_non_delegation_never_names_an_execution_backend(self) -> None:
        result = directive(decision="reject")
        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["delegated_to"], "")


class TestNativeCeraxiaFacade(unittest.TestCase):
    def test_facade_has_one_warband_mission_and_no_worker_plan(self) -> None:
        payload = ceraxia.plan_code_task(
            "Fix the requested behavior.",
            task_id="native-facade",
            mission_id="mission-native-facade",
            commander_order=order(),
        ).to_dict()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["pipeline"]["mode"], "native_skitarii_mission")
        self.assertEqual(payload["pipeline"]["step_count"], 1)
        self.assertEqual(payload["pipeline"]["steps"][0]["worker"], "SkitariiWarband")
        self.assertNotIn("worker_plan", payload["contract"])
        self.assertEqual(len(payload["governor_plan"]["work_plan"]), 1)

    def test_capabilities_do_not_advertise_retired_workers(self) -> None:
        with mock.patch.object(
            ceraxia_service,
            "skitarii_backend_health",
            return_value={"healthy": True, "status": "healthy", "name": "SkitariiWarband"},
        ):
            payload = ceraxia_service.service_capabilities()
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(payload["required_workers"], [])
        self.assertEqual(payload["contract_mode"], "native_skitarii_mission_v2")
        for retired in (
            "LogisRepository",
            "MagosStrategos",
            "FerrumPatchwright",
            "OrdinatusVerifier",
            "JudicatorCodicis",
            "SealwrightFinalis",
            "legacy_six_worker_compatibility_adapter",
        ):
            self.assertNotIn(retired, encoded)

    def test_structural_plan_does_not_call_the_model(self) -> None:
        with (
            mock.patch.object(
                ceraxia_service,
                "skitarii_backend_health",
                return_value={"healthy": True, "status": "healthy", "name": "SkitariiWarband"},
            ),
            mock.patch.object(
                ceraxia_service,
                "request_model_decision",
                side_effect=AssertionError("/plan must not invoke Ceraxia"),
            ),
        ):
            payload = ceraxia_service.native_plan_payload(
                "Fix the requested behavior.",
                "native-facade",
                order(),
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["leadership_authorization"], "pending_prepare")


class TestNativeRunPackage(unittest.TestCase):
    def _write(self, root: Path, task_id: str = "native-package") -> Path:
        run_dir = root / task_id
        command = order(task_id)
        contract = build_native_code_contract(
            "Fix the requested behavior.",
            task_id,
            mission_id=f"mission-{task_id}",
        )
        write_native_code_run(
            run_dir,
            contract,
            directive(task_id),
            native_governor_plan(contract, command),
            prepare_request_sha256="a" * 64,
        )
        return run_dir

    def test_package_is_one_step_atomic_and_bridge_loads_directive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self._write(Path(raw))
            self.assertTrue(is_native_code_run(run_dir))
            self.assertEqual(validate_native_code_run_package(run_dir), [])
            self.assertFalse((run_dir / "dispatch").exists())
            loaded = skitarii_bridge._load_ceraxia_directive(run_dir, run_dir.name)
            self.assertEqual(loaded["decision"], "delegate")

    def test_package_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self._write(Path(raw))
            payload = json.loads((run_dir / "contract.json").read_text(encoding="utf-8"))
            payload["goal"] = "tampered"
            (run_dir / "contract.json").write_text(json.dumps(payload), encoding="utf-8")
            errors = validate_native_code_run_package(run_dir)
            self.assertTrue(any("contract_sha256" in error for error in errors), errors)


class TestNativeTaskPrepare(unittest.TestCase):
    def test_prepare_calls_only_authoritative_prepare_endpoint(self) -> None:
        task_id = "native-task-prepare"
        command = order(task_id)
        called: list[str] = []

        def fake_post(url: str, payload: dict, governor_name: str) -> dict:
            called.append(url)
            self.assertTrue(url.endswith("/prepare_run"))
            contract = build_native_code_contract(
                "Fix the requested behavior.",
                task_id,
                mission_id=f"mission-{task_id}",
            )
            leader = directive(task_id)
            plan = native_governor_plan(contract, command)
            status = write_native_code_run(
                Path(payload["run_dir"]),
                contract,
                leader,
                plan,
                prepare_request_sha256=task_prepare._ceraxia_prepare_request_sha256(
                    contract["goal"],
                    task_id,
                    command,
                ),
            )
            return {
                "ok": True,
                "contract": contract,
                "leadership_directive": leader,
                "governor_plan": plan,
                "status": status,
                "prepare_replayed": False,
            }

        with tempfile.TemporaryDirectory() as raw:
            governor = SimpleNamespace(name="Ceraxia", port=7104)
            with mock.patch.object(task_prepare, "_post_governor_json", side_effect=fake_post):
                result = task_prepare.prepare_task_via_governor_service(
                    "Fix the requested behavior.",
                    task_id,
                    Path(raw),
                    governor,
                    commander_order=command,
                    require_commander_order=True,
                )
            self.assertTrue(result["ok"], result)
            self.assertEqual(len(called), 1)
            self.assertTrue(called[0].endswith("/prepare_run"))
            run_dir = Path(raw) / task_id
            self.assertTrue((run_dir / "task_ledger.json").is_file())
            self.assertFalse((run_dir / "dispatch").exists())


if __name__ == "__main__":
    unittest.main()
