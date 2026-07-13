#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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
    native_adapter_for_route,
    validate_native_warband_run,
)
from EyeOfTerror.Warmaster.eye_of_terror import http_executor, local_executor  # noqa: E402
from EyeOfTerror.Warmaster.eye_of_terror import (  # noqa: E402
    orchestrator,
    research_warband_bridge,
    run_state,
)
from EyeOfTerror.Warmaster.eye_of_terror.brigade import contract_summary  # noqa: E402
from EyeOfTerror.Warmaster.eye_of_terror.ledger import TaskLedger  # noqa: E402
from EyeOfTerror.Warmaster.eye_of_terror.orchestrator import (  # noqa: E402
    _execution_timeout_for_run,
    execution_backend_route,
)
from EyeOfTerror.Warmaster.eye_of_terror.run_validation import (  # noqa: E402
    run_package_action_errors,
    run_package_diagnostics,
    run_oversight_diagnostics,
    validate_revision_plan,
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


def link_native_mission(run_root: Path, run_dir: Path, mission_id: str) -> None:
    mission_dir = run_root / "_missions" / mission_id
    write_json(
        mission_dir / "mission.json",
        {
            "mission_id": mission_id,
            "task_id": run_dir.name,
            "status": "assigned",
        },
    )
    write_json(
        run_dir / "mission_ref.json",
        {
            "mission_id": mission_id,
            "mission_dir": str(mission_dir.resolve()),
        },
    )


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

    def test_legacy_iskandar_worker_plan_is_claimed_and_quarantined(self) -> None:
        legacy_contract = {
            "kind": "research",
            "assigned_governor": "IskandarKhayon",
            "worker_plan": [{"step_id": "source_discovery", "worker": "Lexmechanic"}],
        }
        self.assertIsNone(native_adapter_for_contract(legacy_contract))
        self.assertIs(
            native_adapter_for_contract(legacy_contract, declared=True),
            NATIVE_RESEARCH_ADAPTER,
        )
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw) / "legacy-iskandar"
            write_json(run_dir / "contract.json", legacy_contract)
            route = execution_backend_route(run_dir)
            self.assertFalse(route["ok"])
            self.assertEqual(route["error_code"], "legacy_iskandar_run_removed")
            with self.assertRaisesRegex(RuntimeError, "ResearchWarband backend router"):
                local_executor.execute_run(
                    REPO_ROOT, run_dir, run_dir / "work", timeout_sec=1,
                )

    def test_adapter_metadata_keeps_existing_code_identity_unchanged(self) -> None:
        code = NATIVE_CODE_ADAPTER.to_dict()
        self.assertEqual(code["run_kind"], "native_skitarii_code")
        self.assertEqual(code["execution"], NATIVE_CODE_EXECUTION)
        self.assertEqual(code["governor"], "Ceraxia")
        self.assertEqual(code["directive_filename"], "ceraxia_directive.json")
        self.assertEqual(code["ledger_mission_key"], "skitarii_mission")
        self.assertEqual(code["service_port"], 7200)
        self.assertEqual(code["route_kind"], "native_code_run")
        self.assertEqual(code["leadership_kind"], "native_code_leadership")
        self.assertEqual(code["invalid_error_code"], "native_code_run_invalid")

        research = NATIVE_RESEARCH_ADAPTER.to_dict()
        self.assertEqual(research["execution"], NATIVE_RESEARCH_EXECUTION)
        self.assertEqual(research["governor"], "IskandarKhayon")
        self.assertEqual(research["service_port"], 7201)
        self.assertEqual(research["route_kind"], "native_research_run")
        self.assertEqual(research["leadership_kind"], "native_research_leadership")
        self.assertEqual(research["invalid_error_code"], "native_research_run_invalid")

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
            route = execution_backend_route(run_dir)
            self.assertTrue(route["ok"])
            self.assertTrue(route["native"])
            self.assertEqual(route["kind"], "native_research_run")
            self.assertEqual(route["backend"], "ResearchWarband")
            self.assertIs(native_adapter_for_route(route), NATIVE_RESEARCH_ADAPTER)
            self.assertEqual(
                contract_summary(contract)["steps"],
                [{
                    "step_id": "research_warband",
                    "worker": "ResearchWarband",
                    "depends_on": [],
                    "expected_artifacts": [],
                    "expected_artifact_count": 0,
                }],
            )
            self.assertEqual(run_package_action_errors(run_dir), [])
            diagnostics = run_package_diagnostics(run_dir)
            self.assertTrue(diagnostics["ok"])
            self.assertTrue(diagnostics["native"])
            self.assertTrue(diagnostics["files"]["leadership_directive"])
            oversight = run_oversight_diagnostics(run_dir)
            self.assertEqual(oversight["summary"]["governor"], "IskandarKhayon")
            self.assertEqual(
                oversight["summary"]["kind"], "native_research_leadership",
            )
            self.assertEqual(
                validate_revision_plan(
                    run_dir,
                    {
                        "required": True,
                        "steps": [{
                            "step_id": "research_warband",
                            "worker": "ResearchWarband",
                            "reason": "retry",
                        }],
                    },
                ),
                [],
            )
            with self.assertRaisesRegex(RuntimeError, "ResearchWarband backend router"):
                http_executor.execute_run(run_dir)
            with self.assertRaisesRegex(RuntimeError, "ResearchWarband backend router"):
                local_executor.execute_run(
                    REPO_ROOT, run_dir, run_dir / "work", timeout_sec=1,
                )

    def test_research_runtime_inspection_uses_authenticated_exact_bridge(self) -> None:
        task_id = "research-runtime-inspection"
        command = research_command(task_id)
        contract = build_native_research_contract(
            "Verify the claim.", task_id, mission_id=command["mission_id"],
        )
        request_sha256 = "a" * 64
        remote_snapshot = {
            "id": command["mission_id"],
            "request_sha256": request_sha256,
            "status": "running",
            "inflight": True,
            "cleanup_complete": False,
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            run_dir = root / task_id
            write_native_research_run(
                run_dir,
                contract,
                research_directive(task_id),
                native_research_governor_plan(contract, command),
                command,
            )
            ledger = TaskLedger.create(
                run_dir / "task_ledger.json",
                task_id,
                str(contract["goal"]),
                "IskandarKhayon",
            )
            ledger.data["research_warband_mission"] = {
                "id": command["mission_id"],
                "request_sha256": request_sha256,
                "status": "running",
            }
            ledger.save()

            with (
                patch.object(
                    research_warband_bridge,
                    "inspect_research_warband_mission",
                    return_value=remote_snapshot,
                ) as inspect,
                patch.object(
                    run_state.urllib.request,
                    "urlopen",
                    side_effect=AssertionError(
                        "ResearchWarband runtime inspection used a bare URL"
                    ),
                ),
            ):
                result = run_state.run_worker_tasks(
                    run_dir, include_health=True, host="127.0.0.1",
                )

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["native"], result)
            self.assertEqual(
                result["worker_tasks"][0]["runtime"], remote_snapshot,
            )
            inspect.assert_called_once_with(
                command["mission_id"], request_sha256, timeout_sec=1.0,
            )

    def test_research_execute_health_race_is_retryable_and_non_terminal(self) -> None:
        task_id = "research-health-race"
        command = research_command(task_id)
        contract = build_native_research_contract(
            "Verify the claim.", task_id, mission_id=command["mission_id"],
        )
        unhealthy = {
            "ok": False,
            "backend": "ResearchWarband",
            "status": "unavailable",
            "error": "temporary connection refusal",
        }
        completed = {
            "ok": True,
            "phase": "completed",
            "status": "completed",
            "summary": "verified",
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            run_dir = root / task_id
            write_native_research_run(
                run_dir,
                contract,
                research_directive(task_id),
                native_research_governor_plan(contract, command),
                command,
            )
            link_native_mission(root, run_dir, command["mission_id"])
            TaskLedger.create(
                run_dir / "task_ledger.json",
                task_id,
                str(contract["goal"]),
                "IskandarKhayon",
            )

            with patch.object(
                orchestrator,
                "_native_backend_health",
                return_value=unhealthy,
            ):
                first = orchestrator.execute_routed_run(
                    run_dir,
                    run_mode="http",
                    host="127.0.0.1",
                    timeout_sec=5,
                )

            self.assertFalse(first["ok"], first)
            self.assertTrue(first["retryable"], first)
            self.assertEqual(first["error_code"], "native_backend_unavailable")
            after_failure = TaskLedger.load(
                run_dir / "task_ledger.json"
            ).to_dict()
            self.assertEqual(after_failure["status"], "created")
            self.assertNotIn("result", after_failure)
            self.assertTrue(
                any(
                    event.get("type") == "native_backend_preflight_failed"
                    and event.get("payload", {}).get("retryable") is True
                    for event in after_failure.get("events", [])
                ),
                after_failure,
            )

            with (
                patch.object(
                    orchestrator,
                    "_native_backend_health",
                    return_value={"ok": True, "backend": "ResearchWarband"},
                ),
                patch.object(
                    research_warband_bridge,
                    "run_via_research_warband",
                    return_value=completed,
                ) as execute,
            ):
                retried = orchestrator.execute_routed_run(
                    run_dir,
                    run_mode="http",
                    host="127.0.0.1",
                    timeout_sec=5,
                )
            self.assertTrue(retried["ok"], retried)
            execute.assert_called_once_with(run_dir, task_id, timeout_sec=5)

    def test_skitarii_execute_health_failure_keeps_existing_terminal_semantics(self) -> None:
        task_id = "skitarii-health-race"
        command = ceraxia_command(task_id)
        contract = build_native_code_contract(
            "Repair the parser.", task_id, mission_id=command["mission_id"],
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            run_dir = root / task_id
            write_native_code_run(
                run_dir,
                contract,
                ceraxia_directive(task_id),
                native_governor_plan(contract, command),
            )
            link_native_mission(root, run_dir, command["mission_id"])
            TaskLedger.create(
                run_dir / "task_ledger.json",
                task_id,
                str(contract["goal"]),
                "Ceraxia",
            )

            with patch.object(
                orchestrator,
                "_native_backend_health",
                return_value={
                    "ok": False,
                    "backend": "SkitariiWarband",
                    "error": "temporary connection refusal",
                },
            ):
                failure = orchestrator.execute_routed_run(
                    run_dir,
                    run_mode="http",
                    host="127.0.0.1",
                    timeout_sec=5,
                )
            self.assertFalse(failure["ok"], failure)
            persisted = TaskLedger.load(run_dir / "task_ledger.json").to_dict()
            self.assertEqual(persisted["status"], "failed")
            self.assertEqual(
                persisted.get("result", {}).get("error_code"),
                "native_backend_unavailable",
            )

    def test_execution_timeout_is_backend_specific(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            code_task = "timeout-code"
            code_command = ceraxia_command(code_task)
            code_contract = build_native_code_contract(
                "Repair the parser.",
                code_task,
                mission_id=code_command["mission_id"],
            )
            write_native_code_run(
                root / code_task,
                code_contract,
                ceraxia_directive(code_task),
                native_governor_plan(code_contract, code_command),
            )

            research_task = "timeout-research"
            research_command_payload = research_command(research_task)
            research_contract = build_native_research_contract(
                "Verify the claim.",
                research_task,
                mission_id=research_command_payload["mission_id"],
            )
            write_native_research_run(
                root / research_task,
                research_contract,
                research_directive(research_task),
                native_research_governor_plan(
                    research_contract, research_command_payload
                ),
                research_command_payload,
            )

            self.assertEqual(
                _execution_timeout_for_run(root / code_task, 604_800), 7_200
            )
            self.assertEqual(
                _execution_timeout_for_run(root / research_task, 604_800),
                604_800,
            )

    def test_malformed_declared_native_run_is_quarantinable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw) / "malformed-native"
            write_json(
                run_dir / "contract.json",
                {
                    "kind": "research",
                    "assigned_governor": "IskandarKhayon",
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
            route = execution_backend_route(run_dir)
            self.assertFalse(route["ok"])
            self.assertEqual(route["error_code"], "native_research_run_invalid")
            with self.assertRaisesRegex(RuntimeError, "ResearchWarband backend router"):
                http_executor.execute_run(run_dir)

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
