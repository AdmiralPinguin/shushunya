#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import threading
import types
import urllib.error
import urllib.request
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


WARMMASTER_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = WARMMASTER_ROOT.parent
for import_root in (PROJECT_ROOT, WARMMASTER_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

# The focused snapshot does not carry the model service module. The native
# execution router does not use it, but generic imports reference its API.
if "EyeOfTerror.model_brain" not in sys.modules:
    model_brain = types.ModuleType("EyeOfTerror.model_brain")
    model_brain.attach_model_brain = lambda payload, *_args, **_kwargs: payload
    model_brain.request_model_decision = lambda *_args, **_kwargs: {
        "ok": True,
        "status": "answered",
        "content": "{}",
    }
    model_brain.model_contract = lambda *_args, **_kwargs: {}
    sys.modules["EyeOfTerror.model_brain"] = model_brain

import eye_of_terror.http_executor as http_executor
import eye_of_terror.local_executor as local_executor
import eye_of_terror.orchestrator as orchestrator
import eye_of_terror.warmaster_gateway as warmaster_gateway
from eye_of_terror.ledger import TaskLedger
from eye_of_terror.native_code_run import (
    build_native_code_contract,
    native_governor_plan,
    write_native_code_run,
)


def _directive(task_id: str, mission_id: str) -> dict:
    return {
        "kind": "ceraxia_leadership_directive",
        "version": 1,
        "task_id": task_id,
        "mission_id": mission_id,
        "leader": "Ceraxia",
        "decision": "delegate",
        "delegated_to": "SkitariiWarband",
        "mission_intent": "Complete the requested safe code change.",
        "priorities": ["Correctness", "Narrow scope"],
        "constraints": ["Preserve unrelated behavior."],
        "success_conditions": ["Return executable verification evidence."],
        "tradeoffs": [],
        "escalation_conditions": ["A real user decision is required."],
    }


def _native_run(run_root: Path, task_id: str) -> Path:
    run_dir = run_root / task_id
    mission_id = f"mission-{task_id}"
    mission_dir = run_root / "_missions" / mission_id
    mission_dir.mkdir(parents=True, exist_ok=True)
    (mission_dir / "mission.json").write_text(
        json.dumps(
            {
                "mission_id": mission_id,
                "task_id": task_id,
                "assigned_governor": "Ceraxia",
                "status": "assigned",
            }
        ),
        encoding="utf-8",
    )
    contract = build_native_code_contract(
        "Create one tiny deterministic smoke artifact and verify it.",
        task_id,
        mission_id,
    )
    write_native_code_run(
        run_dir,
        contract,
        _directive(task_id, mission_id),
        native_governor_plan(contract, None),
    )
    (run_dir / "mission_ref.json").write_text(
        json.dumps(
            {
                "mission_id": mission_id,
                "mission_dir": str(mission_dir.resolve()),
                "assigned_governor": "Ceraxia",
            }
        ),
        encoding="utf-8",
    )
    TaskLedger.create(
        run_dir / "task_ledger.json",
        task_id,
        str(contract["goal"]),
        "Ceraxia",
    )
    return run_dir


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _sync_background(_task_id: str, target) -> bool:
    target()
    return True


def _phantom_worker_call(*_args, **_kwargs):
    raise AssertionError("native code run reached a phantom worker path")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="native-backend-router-") as raw_root:
        run_root = Path(raw_root)
        auto_run = run_root / "native-auto-start-smoke"
        auto_mission_dir = run_root / "_missions" / "mission-native-auto-start-smoke"
        direct_run = _native_run(run_root, "native-direct-start-smoke")
        recovery_run = _native_run(run_root, "native-recovery-smoke")
        blocked_run = _native_run(run_root, "native-blocked-preflight-smoke")
        completed_run = _native_run(run_root, "native-completed-preflight-smoke")
        missing_ref_run = _native_run(run_root, "native-missing-ref-smoke")
        mismatched_ref_run = _native_run(run_root, "native-mismatched-ref-smoke")
        missing_mission_dir_run = _native_run(run_root, "native-missing-mission-dir-smoke")
        raw_guard_run = _native_run(run_root, "native-raw-guard-smoke")
        TaskLedger.load(recovery_run / "task_ledger.json").set_status("interrupted")
        TaskLedger.load(blocked_run / "task_ledger.json").force_status(
            "blocked",
            reason="terminal preflight invariant",
        )
        TaskLedger.load(completed_run / "task_ledger.json").force_status(
            "completed",
            reason="terminal preflight invariant",
        )
        (missing_ref_run / "mission_ref.json").unlink()
        mismatched_ref = json.loads(
            (mismatched_ref_run / "mission_ref.json").read_text(encoding="utf-8")
        )
        mismatched_ref["mission_id"] = "mission-someone-else"
        (mismatched_ref_run / "mission_ref.json").write_text(
            json.dumps(mismatched_ref),
            encoding="utf-8",
        )
        missing_dir_ref = json.loads(
            (missing_mission_dir_run / "mission_ref.json").read_text(encoding="utf-8")
        )
        missing_dir_ref["mission_dir"] = str(run_root / "does-not-exist")
        (missing_mission_dir_run / "mission_ref.json").write_text(
            json.dumps(missing_dir_ref),
            encoding="utf-8",
        )

        assert orchestrator._native_mission_ref_errors(missing_ref_run)
        assert orchestrator._native_mission_ref_errors(mismatched_ref_run)
        assert orchestrator._native_mission_ref_errors(missing_mission_dir_run)

        skitarii_calls: list[str] = []
        linked_before_start: set[str] = set()

        def fake_skitarii(run_dir: Path, task_id: str, timeout_sec: int = 0) -> dict:
            assert run_dir.name == task_id
            assert timeout_sec > 0
            assert (run_dir / "mission_ref.json").is_file(), "executor started before mission_ref was durable"
            if task_id == auto_run.name:
                assert task_id in linked_before_start, "executor started before link_run_to_mission completed"
            skitarii_calls.append(task_id)
            return {
                "ok": True,
                "phase": "completed",
                "status": "completed",
                "task_id": task_id,
                "via": "skitarii",
            }

        healthy = {
            "ok": True,
            "backend": "SkitariiWarband",
            "service": "http://127.0.0.1:7200",
            "status": "ok",
            "error": "",
        }

        with ExitStack() as stack:
            stack.enter_context(patch.object(orchestrator, "run_via_skitarii", fake_skitarii))
            stack.enter_context(patch.object(orchestrator, "_skitarii_backend_health", lambda _timeout: dict(healthy)))
            stack.enter_context(patch.object(orchestrator, "preflight_http_workers", _phantom_worker_call))
            stack.enter_context(patch.object(orchestrator, "execute_http_run", _phantom_worker_call))
            stack.enter_context(patch.object(orchestrator, "execute_local_run", _phantom_worker_call))
            stack.enter_context(patch.object(orchestrator, "start_background", _sync_background))

            # Omit auto_start deliberately: this proves the public default reaches
            # the descriptor router and not the retired six-worker pipeline.
            stack.enter_context(
                patch.object(
                    orchestrator,
                    "open_mission",
                    lambda *_args, **_kwargs: {
                        "ok": True,
                        "mission_id": "mission-native-auto-start-smoke",
                        "mission_dir": str(auto_mission_dir.resolve()),
                        "governor_task": "tiny native smoke",
                        "commander_order": {"to": "Ceraxia"},
                    },
                )
            )
            def prepare_auto_run(*_args, **_kwargs) -> dict:
                _native_run(run_root, auto_run.name)
                (auto_run / "mission_ref.json").unlink()
                return {
                    "ok": True,
                    "phase": "ready_to_start",
                    "task_id": auto_run.name,
                    "trace": [],
                    "next_action": {},
                }

            stack.enter_context(
                patch.object(
                    orchestrator,
                    "orchestrate_prepare_task",
                    side_effect=prepare_auto_run,
                )
            )
            def persist_mission_link(run_dir: Path, mission: dict) -> None:
                (run_dir / "mission_ref.json").write_text(
                    json.dumps(
                        {
                            "mission_id": str(mission.get("mission_id") or ""),
                            "mission_dir": str(mission.get("mission_dir") or ""),
                            "assigned_governor": "Ceraxia",
                        }
                    ),
                    encoding="utf-8",
                )
                linked_before_start.add(run_dir.name)

            stack.enter_context(patch.object(orchestrator, "link_run_to_mission", persist_mission_link))
            auto_result = orchestrator.orchestrate_run_task(
                "tiny native smoke",
                auto_run.name,
                run_root,
                governor_transport="http",
                run_mode="http",
            )
            assert auto_result.get("ok") is True, auto_result
            assert auto_result.get("phase") == "started", auto_result
            assert auto_result.get("start", {}).get("backend_route", {}).get("backend") == "SkitariiWarband", auto_result
            assert skitarii_calls == [auto_run.name], skitarii_calls

            # Exercise the public HTTP preflight and direct start endpoints. Both
            # are black-box-ish: only the gateway response and backend call are
            # observed, while every phantom worker function is armed to explode.
            stack.enter_context(patch.object(warmaster_gateway, "start_background", _sync_background))
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                warmaster_gateway.make_handler(run_root),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                preflight_status, preflight = _post_json(
                    f"{base}/runs/{direct_run.name}/preflight_http",
                    {},
                )
                assert preflight_status == 200, preflight
                assert preflight.get("ok") is True, preflight
                assert preflight.get("execution", {}).get("backend") == "SkitariiWarband", preflight
                assert preflight.get("step_ids") == ["skitarii"], preflight
                assert preflight.get("dispatch_errors") == [], preflight
                assert preflight.get("worker_preflight_failures") == [], preflight
                assert preflight.get("backend_health", {}).get("ok") is True, preflight

                invalid_ref_status, invalid_ref_preflight = _post_json(
                    f"{base}/runs/{missing_ref_run.name}/preflight_http",
                    {"force": True},
                )
                assert invalid_ref_status == 409, invalid_ref_preflight
                assert invalid_ref_preflight.get("ok") is False, invalid_ref_preflight
                assert invalid_ref_preflight.get("mission_ref_errors"), invalid_ref_preflight
                assert invalid_ref_preflight.get("actions", {}).get("can_start_run") is False, invalid_ref_preflight

                # Native terminal evidence is immutable. Even an explicit force
                # request must not rewrite an accepted/blocked mission in place.
                for terminal_run in (blocked_run, completed_run):
                    terminal_status, terminal_preflight = _post_json(
                        f"{base}/runs/{terminal_run.name}/preflight_http",
                        {},
                    )
                    assert terminal_status == 200, terminal_preflight
                    assert terminal_preflight.get("ok") is True, terminal_preflight
                    terminal_actions = terminal_preflight.get("actions", {})
                    assert terminal_actions.get("can_start_run") is False, terminal_preflight
                    assert terminal_actions.get("next_action", {}).get("kind") != "start_native_code_run", terminal_preflight

                    forced_status, forced_preflight = _post_json(
                        f"{base}/runs/{terminal_run.name}/preflight_http",
                        {"force": True},
                    )
                    assert forced_status == 200, forced_preflight
                    forced_actions = forced_preflight.get("actions", {})
                    assert forced_actions.get("can_start_run") is False, forced_preflight
                    assert forced_actions.get("terminal_run_immutable") is True, forced_preflight
                    assert forced_actions.get("next_action", {}).get("kind") != "start_native_code_run", forced_preflight

                    force_start_status, force_started = _post_json(
                        f"{base}/runs/{terminal_run.name}/start_http",
                        {"force": True},
                    )
                    assert force_start_status == 409, force_started
                    assert force_started.get("error_code") == "native_preflight_failed", force_started

                for invalid_run in (missing_ref_run, mismatched_ref_run, missing_mission_dir_run):
                    invalid_start_status, invalid_start = _post_json(
                        f"{base}/runs/{invalid_run.name}/start_http",
                        {},
                    )
                    assert invalid_start_status == 409, invalid_start
                    assert invalid_start.get("error_code") == "native_preflight_failed", invalid_start
                    assert invalid_start.get("run_preflight", {}).get("mission_ref_errors"), invalid_start

                start_status, started = _post_json(
                    f"{base}/runs/{direct_run.name}/start_http",
                    {},
                )
                assert start_status == 202, started
                assert started.get("ok") is True, started
                assert started.get("backend_route", {}).get("backend") == "SkitariiWarband", started
                assert skitarii_calls == [auto_run.name, direct_run.name], skitarii_calls

                guarded = orchestrator.execute_routed_run(
                    missing_ref_run,
                    run_mode="http",
                    host="127.0.0.1",
                    timeout_sec=10,
                )
                assert guarded.get("error_code") == "native_mission_link_invalid", guarded
                assert skitarii_calls == [auto_run.name, direct_run.name], skitarii_calls
            finally:
                server.shutdown()
                thread.join(timeout=15)
                server.server_close()

            recovery = orchestrator.recovery_summary(
                [orchestrator.run_summary(recovery_run)]
            )
            assert recovery.get("startable") == 1, recovery
            assert recovery.get("candidates", [{}])[0].get("backend_route", {}).get("backend") == "SkitariiWarband", recovery
            TaskLedger.load(missing_ref_run / "task_ledger.json").set_status("interrupted")
            resumed = orchestrator.start_recoverable_runs(run_root, "http")
            assert resumed.get("started") == 1, resumed
            missing_result = next(
                item
                for item in resumed.get("results", [])
                if item.get("task_id") == missing_ref_run.name
            )
            assert missing_result.get("ok") is False, missing_result
            assert missing_result.get("error_code") == "native_preflight_failed", missing_result
            assert missing_result.get("run_preflight", {}).get("mission_ref_errors"), missing_result
            assert TaskLedger.load(missing_ref_run / "task_ledger.json").data.get("status") == "interrupted"
            assert skitarii_calls == [auto_run.name, direct_run.name, recovery_run.name], skitarii_calls

        # The lower-level executors independently fail closed even if a caller
        # bypasses every public gateway/orchestrator route.
        for raw_call in (
            lambda: http_executor.execute_run(raw_guard_run),
            lambda: local_executor.execute_run(
                PROJECT_ROOT,
                raw_guard_run,
                raw_guard_run / "work",
            ),
        ):
            try:
                raw_call()
            except RuntimeError as exc:
                assert "centralized Skitarii backend router" in str(exc), exc
            else:
                raise AssertionError("raw executor accepted a native code run")

        # Old six-worker Ceraxia packages are quarantined without manufacturing
        # a leadership directive or touching an executor.
        legacy_run = run_root / "legacy-ceraxia-smoke"
        legacy_run.mkdir()
        (legacy_run / "contract.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "task_id": legacy_run.name,
                    "kind": "code",
                    "goal": "legacy code task",
                    "assigned_governor": "Ceraxia",
                    "completion_criteria": ["done"],
                    "worker_plan": [
                        {"worker": "LogisRepository"},
                        {"worker": "MagosStrategos"},
                        {"worker": "FerrumPatchwright"},
                        {"worker": "OrdinatusVerifier"},
                        {"worker": "JudicatorCodicis"},
                        {"worker": "SealwrightFinalis"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        legacy = orchestrator.orchestrate_start_run(run_root, legacy_run.name)
        assert legacy.get("error_code") == "legacy_ceraxia_reprepare_required", legacy
        assert legacy.get("next_action", {}).get("kind") == "legacy_ceraxia_reprepare_required", legacy
        assert legacy.get("next_action", {}).get("endpoint") == "POST /orchestrate_run", legacy
        assert legacy.get("next_action", {}).get("body", {}).get("auto_start") is True, legacy
        assert legacy.get("next_action", {}).get("body", {}).get("task_id") != legacy_run.name, legacy
        assert legacy.get("next_action", {}).get("body", {}).get("governor_transport") == "http", legacy
        assert not (legacy_run / "ceraxia_directive.json").exists(), legacy

        generic_run = run_root / "generic-research-smoke"
        generic_run.mkdir()
        (generic_run / "contract.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "task_id": generic_run.name,
                    "kind": "research",
                    "goal": "generic research task",
                    "assigned_governor": "IskandarKhayon",
                    "completion_criteria": ["done"],
                }
            ),
            encoding="utf-8",
        )
        generic_route = orchestrator.execution_backend_route(generic_run)
        assert generic_route.get("ok") is True, generic_route
        assert generic_route.get("backend") == "legacy_pipeline", generic_route
        with patch.object(
            orchestrator,
            "execute_http_run",
            lambda *_args, **_kwargs: {"ok": True, "backend": "legacy_pipeline"},
        ):
            generic_execution = orchestrator.execute_routed_run(
                generic_run,
                run_mode="http",
                host="127.0.0.1",
                timeout_sec=10,
            )
        assert generic_execution == {"ok": True, "backend": "legacy_pipeline"}, generic_execution

    print("[ok] native backend router")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
