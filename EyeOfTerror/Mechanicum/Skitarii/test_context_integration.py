"""Focused identity and context-overflow integration tests (no live models/VM)."""
from __future__ import annotations

import contextlib
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import harness  # noqa: E402
import mission_store  # noqa: E402
import planner  # noqa: E402
import service  # noqa: E402
import warband  # noqa: E402


class ContextIntegrationTests(unittest.TestCase):
    @staticmethod
    def _mission(result: dict) -> SimpleNamespace:
        return SimpleNamespace(_lock=threading.RLock(), result=result)

    def test_warband_passes_distinct_run_and_memory_ids_to_fighter(self) -> None:
        fighter = {
            "ok": True,
            "steps": 1,
            "seconds": 0,
            "summary": "done",
            "artifacts": [],
        }
        with (
            mock.patch.object(warband, "run_fighter", return_value=fighter) as run,
            mock.patch.object(
                warband, "accept",
                # The pre-fighter acceptance must be RED here: a green inherited
                # workspace legitimately skips the fighter (repair mode), and this
                # test exists to verify the ids passed to a fighter that runs.
                side_effect=[
                    {"accepted": False, "results": [], "reason": "not yet"},
                    {"accepted": True, "results": [], "reason": "passed"},
                ],
            ),
        ):
            verdict = warband.run_mission(
                "change app",
                object(),
                checks=[{"cmd": "true"}],
                task_id="attempt-9",
                memory_task_id="stable-goal",
                max_fighter_rounds=1,
            )

        self.assertTrue(verdict["accepted"])
        self.assertEqual(run.call_args.kwargs["task_id"], "attempt-9")
        self.assertEqual(run.call_args.kwargs["memory_task_id"], "stable-goal")

    def test_planner_preserves_both_identities_across_retry_entry(self) -> None:
        accepted = {"status": "done", "accepted": True}
        with (
            mock.patch.object(planner, "decompose", return_value=[]),
            mock.patch.object(planner, "run_mission", return_value=accepted) as run,
        ):
            result = planner.plan_and_run(
                "small change",
                object(),
                task_id="attempt-10",
                memory_task_id="stable-goal",
            )

        self.assertIs(result, accepted)
        self.assertEqual(run.call_args.kwargs["task_id"], "attempt-10")
        self.assertEqual(run.call_args.kwargs["memory_task_id"], "stable-goal")

    def test_service_never_derives_memory_identity_from_attempt_or_delegation(self) -> None:
        self.assertEqual(
            service._task_memory_id_from_payload({
                "task_id": "attempt-11",
                "delegating_task_id": "delegating-attempt",
            }),
            "",
        )
        self.assertEqual(
            service._task_memory_id_from_payload({
                "task_id": "attempt-11",
                "root_task_id": "root-goal",
            }),
            "root-goal",
        )
        self.assertEqual(
            service._task_memory_id_from_payload({
                "task_memory_id": "stable-goal",
                "root_task_id": "root-goal",
            }),
            "stable-goal",
        )

    def test_typed_context_overflow_becomes_actionable_revision(self) -> None:
        exc = harness.LLMRequestError(
            status=400,
            body="request exceeds the available context size",
            retryable=True,
            context_overflow=True,
        )
        checkpoint = {"base_tree": "abc", "unified_diff": ""}
        with mock.patch.object(
            service, "_capture_workspace_checkpoint", return_value=checkpoint,
        ):
            verdict = service._recoverable_pipeline_verdict(
                exc,
                ex=object(),
                base_commit="base",
                task_id="attempt-12",
                task_memory_id="stable-goal",
                root_task_id="root-goal",
            )

        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["error_code"], "context_overflow")
        self.assertTrue(verdict["retryable"])
        self.assertTrue(verdict["revision_required"])
        self.assertEqual(verdict["task_id"], "attempt-12")
        self.assertEqual(verdict["task_memory_id"], "stable-goal")
        self.assertEqual(verdict["root_task_id"], "root-goal")
        self.assertEqual(verdict["workspace_checkpoint"], checkpoint)
        self.assertEqual(
            verdict["verification_findings"][0]["code"], "context_overflow",
        )
        self.assertIsNotNone(mission_store._revision_turn(verdict, 1))

    def test_parent_checkpoint_id_is_strictly_validated(self) -> None:
        error = service.execution_authorization_error(
            {
                "task_memory_id": "stable-goal",
                "root_task_id": "root-goal",
                "parent_skitarii_mission_id": "../escape",
            },
            "attempt-13",
        )
        self.assertIsNotNone(error)
        assert error is not None
        self.assertEqual(error[0], 400)
        self.assertEqual(
            error[1]["error_code"], "invalid_parent_skitarii_mission_id",
        )

    def test_service_requires_existing_canonical_task_page(self) -> None:
        with mock.patch.object(
            service,
            "_task_page_document",
            return_value={"revision": 0, "task_memory_id": None},
        ):
            missing = service._task_memory_page_preflight(
                "stable-goal", "root-goal",
            )
        self.assertIsNotNone(missing)
        assert missing is not None
        self.assertEqual(missing["error_code"], "task_memory_page_not_initialized")
        self.assertTrue(missing["retryable"])

        with mock.patch.object(
            service,
            "_task_page_document",
            return_value={
                "revision": 4,
                "task_memory_id": "stable-goal",
                "root_task_id": "root-goal",
            },
        ):
            valid = service._task_memory_page_preflight(
                "stable-goal", "root-goal",
            )
        self.assertIsNone(valid)

        with mock.patch.object(
            service,
            "_task_page_document",
            return_value={
                "revision": 4,
                "task_memory_id": "stable-goal",
                "root_task_id": "different-root",
            },
        ):
            mismatch = service._task_memory_page_preflight(
                "stable-goal", "root-goal",
            )
        self.assertIsNotNone(mismatch)
        assert mismatch is not None
        self.assertEqual(
            mismatch["error_code"], "task_memory_page_identity_mismatch",
        )
        self.assertFalse(mismatch["retryable"])
        self.assertFalse(mismatch["revision_required"])

    def test_missing_memory_identity_is_rejected_before_execution(self) -> None:
        error = service.execution_authorization_error({}, "attempt-no-memory")
        self.assertIsNotNone(error)
        assert error is not None
        self.assertEqual(error[0], 400)
        self.assertEqual(error[1]["error_code"], "task_memory_identity_required")

    def test_parent_checkpoint_is_same_memory_fallback_only(self) -> None:
        current_checkpoint = {"patch_sha256": "a" * 64}
        parent_checkpoint = {"patch_sha256": "b" * 64}
        current = self._mission({"workspace_checkpoint": current_checkpoint})
        with mock.patch.object(service.mission_store, "get") as get_parent:
            selected = service._workspace_checkpoint_for_attempt(
                {"parent_skitarii_mission_id": "parent-1"},
                current,
                "stable-goal",
                "root-goal",
            )
        self.assertEqual(selected, current_checkpoint)
        get_parent.assert_not_called()

        empty_current = self._mission({})
        matching_parent = self._mission({
            "task_memory_id": "stable-goal",
            "root_task_id": "root-goal",
            "workspace_checkpoint": parent_checkpoint,
        })
        with mock.patch.object(
            service.mission_store, "get", return_value=matching_parent,
        ) as get_parent:
            selected = service._workspace_checkpoint_for_attempt(
                {"parent_skitarii_mission_id": "parent-1"},
                empty_current,
                "stable-goal",
                "root-goal",
            )
        self.assertEqual(selected, parent_checkpoint)
        get_parent.assert_called_once_with("parent-1")

        mismatched_parent = self._mission({
            "task_memory_id": "different-goal",
            "root_task_id": "root-goal",
            "workspace_checkpoint": parent_checkpoint,
        })
        with mock.patch.object(
            service.mission_store, "get", return_value=mismatched_parent,
        ):
            selected = service._workspace_checkpoint_for_attempt(
                {"parent_skitarii_mission_id": "parent-1"},
                empty_current,
                "stable-goal",
                "root-goal",
            )
        self.assertEqual(selected, {})

        same_memory_wrong_root = self._mission({
            "task_memory_id": "stable-goal",
            "root_task_id": "different-root",
            "workspace_checkpoint": parent_checkpoint,
        })
        with mock.patch.object(
            service.mission_store, "get", return_value=same_memory_wrong_root,
        ):
            selected = service._workspace_checkpoint_for_attempt(
                {"parent_skitarii_mission_id": "parent-1"},
                empty_current,
                "stable-goal",
                "root-goal",
            )
        self.assertEqual(selected, {})

    def test_service_finalizer_captures_cancelled_work(self) -> None:
        verdict = {
            "status": "cancelled",
            "accepted": False,
            "summary": "cancelled by user",
        }
        checkpoint = {
            "base_tree": "a" * 40,
            "patch_sha256": "b" * 64,
            "unified_diff": "diff",
            "changed_files": ["app.py"],
        }
        with (
            mock.patch.object(
                service, "_capture_workspace_checkpoint", return_value=checkpoint,
            ) as capture,
            mock.patch.object(service, "_persist_checkpoint"),
        ):
            result = service._finalize_service_verdict(
                verdict,
                ex=object(),
                base_commit="base",
                task_id="attempt-cancelled",
                task_memory_id="stable-goal",
                root_task_id="root-goal",
            )
        capture.assert_called_once()
        self.assertEqual(result["workspace_checkpoint"], checkpoint)

    def test_capture_workspace_checkpoint_uses_poison_capture_window(self) -> None:
        class SalvageExecutor:
            def __init__(self) -> None:
                self.window_open = False
                self.bundle_seen_window: bool | None = None

            @contextlib.contextmanager
            def poison_capture_window(self):
                self.window_open = True
                try:
                    yield
                finally:
                    self.window_open = False

        ex = SalvageExecutor()

        def fake_bundle(executor, base_commit, *, accepted):
            executor.bundle_seen_window = executor.window_open
            return {"unified_diff": "diff", "changed_files": ["app.py"]}

        with (
            mock.patch.object(service, "_build_patch_bundle", side_effect=fake_bundle),
            mock.patch.object(service, "_baseline_tree", return_value="t" * 40),
        ):
            captured = service._capture_workspace_checkpoint(
                ex, "base", task_id="attempt-9",
            )
        self.assertTrue(ex.bundle_seen_window)
        self.assertFalse(ex.window_open)
        self.assertEqual(captured["unified_diff"], "diff")

    def test_service_finalizer_preserves_retry_patch_and_publishes_leader_state(self) -> None:
        verdict = {
            "status": "failed",
            "accepted": False,
            "revision_required": True,
            "retryable": True,
            "summary": "candidate needs another revision",
            "verification_findings": [{
                "evidence": "edge case failed",
                "remediation": "repair the edge case and rerun checks",
                "retryable": True,
            }],
        }
        checkpoint = {
            "base_tree": "a" * 40,
            "patch_sha256": "b" * 64,
            "unified_diff": "diff",
            "changed_files": ["app.py"],
        }
        with (
            mock.patch.object(
                service, "_capture_workspace_checkpoint", return_value=checkpoint,
            ) as capture,
            mock.patch.object(service, "_persist_checkpoint") as persist,
        ):
            result = service._finalize_service_verdict(
                verdict,
                ex=object(),
                base_commit="base",
                task_id="attempt-21",
                task_memory_id="stable-goal",
                root_task_id="root-goal",
            )

        self.assertIs(result, verdict)
        self.assertEqual(result["task_id"], "attempt-21")
        self.assertEqual(result["task_memory_id"], "stable-goal")
        self.assertEqual(result["root_task_id"], "root-goal")
        self.assertEqual(result["workspace_checkpoint"], checkpoint)
        capture.assert_called_once_with(
            mock.ANY,
            "base",
            task_memory_id="stable-goal",
            root_task_id="root-goal",
            parent_task_id="",
            task_id="attempt-21",
        )
        persist.assert_called_once()
        self.assertEqual(persist.call_args.args[0], "stable-goal")
        self.assertEqual(persist.call_args.args[1]["working_set"], ["app.py"])
        self.assertTrue(persist.call_args.kwargs["authoritative"])

    def test_checkpoint_only_retry_never_starts_vm_or_fighter(self) -> None:
        accepted = {
            "status": "done",
            "accepted": True,
            "summary": "verified candidate",
            "verification_findings": [],
            "patch_bundle": {
                "base_commit": "base",
                "changed_files": ["app.py"],
                "unified_diff": "diff",
                "apply_gate": "accepted",
            },
        }
        workspace = {
            "base_tree": "a" * 40,
            "patch_sha256": "b" * 64,
            "unified_diff": "diff",
            "changed_files": ["app.py"],
        }
        with (
            mock.patch.object(service, "_persist_checkpoint", side_effect=OSError("archive down")),
            mock.patch.object(service, "_capture_workspace_checkpoint", return_value=workspace),
        ):
            pending = service._finalize_service_verdict(
                accepted,
                ex=object(),
                base_commit="base",
                task_id="attempt-finalize",
                task_memory_id="stable-goal",
                root_task_id="root-goal",
                parent_task_id="parent-goal",
            )

        self.assertFalse(pending["accepted"])
        self.assertEqual(pending["error_code"], "task_checkpoint_commit_pending")
        self.assertFalse(pending["revision_required"])
        self.assertEqual(pending["parent_task_id"], "parent-goal")
        pending["error"] = "service restarted while mission was active"
        pending["restart_recovery_required"] = True
        mission = self._mission(pending)
        payload = {
            "goal": "must not run again",
            "task_id": "attempt-finalize-retry",
            "task_memory_id": "stable-goal",
            "root_task_id": "root-goal",
            "parent_task_id": "parent-goal",
        }
        with (
            mock.patch.object(service, "_persist_checkpoint", side_effect=OSError("still down")),
            mock.patch.object(service, "_task_memory_page_preflight") as preflight,
            mock.patch.object(service, "_mission_executor") as executor,
        ):
            still_pending = service._execute_mission_body(payload, mission)
        self.assertEqual(still_pending["error_code"], "task_checkpoint_commit_pending")
        preflight.assert_not_called()
        executor.assert_not_called()

        with (
            mock.patch.object(service, "_persist_checkpoint") as persist,
            mock.patch.object(service, "_task_memory_page_preflight") as preflight,
            mock.patch.object(service, "_mission_executor") as executor,
        ):
            recovered = service._execute_mission_body(payload, mission)
        self.assertTrue(recovered["accepted"])
        self.assertEqual(recovered["status"], "done")
        self.assertTrue(recovered["task_checkpoint_recovered"])
        self.assertEqual(recovered["parent_task_id"], "parent-goal")
        self.assertNotIn("error", recovered)
        self.assertNotIn("restart_recovery_required", recovered)
        self.assertNotIn("task_checkpoint_commit_attempts", recovered)
        persist.assert_called_once()
        preflight.assert_not_called()
        executor.assert_not_called()

    def test_service_rejects_unproven_delegation_lineage(self) -> None:
        base = {
            "task_memory_id": "stable-goal",
            "root_task_id": "root-run",
            "standalone_test": True,
        }
        cases = {
            "missing delegator": (
                {**base, "task_id": "root-run"},
                "invalid_delegating_task_id",
            ),
            "missing parent": (
                {**base, "task_id": "child-run", "delegating_task_id": "child-run"},
                "parent_task_id_required",
            ),
            "root has parent": (
                {
                    **base,
                    "task_id": "root-run",
                    "delegating_task_id": "root-run",
                    "parent_task_id": "older-run",
                },
                "root_task_parent_forbidden",
            ),
            "self parent": (
                {
                    **base,
                    "task_id": "child-run",
                    "delegating_task_id": "child-run",
                    "parent_task_id": "child-run",
                },
                "parent_task_id_self_reference",
            ),
        }
        with mock.patch.dict(
            service.os.environ, {"SKITARII_STANDALONE_TEST_MODE": "1"}, clear=False,
        ):
            for label, (payload, expected_code) in cases.items():
                with self.subTest(case=label):
                    error = service.execution_authorization_error(
                        payload, str(payload["task_id"]),
                    )
                    self.assertIsNotNone(error)
                    assert error is not None
                    self.assertEqual(error[1]["error_code"], expected_code)

            self.assertIsNone(service.execution_authorization_error(
                {
                    **base,
                    # Async service mission ids are transport attempts and must
                    # not be confused with the immutable Abaddon run identity.
                    "task_id": "root-run-skitarii-1",
                    "delegating_task_id": "root-run",
                },
                "root-run-skitarii-1",
            ))
            self.assertIsNone(service.execution_authorization_error(
                {
                    **base,
                    "task_id": "child-run",
                    "delegating_task_id": "child-run",
                    "parent_task_id": "root-run",
                },
                "child-run",
            ))

    def test_transport_mission_id_keeps_production_delegating_run_identity(self) -> None:
        directive = {
            "kind": "ceraxia_leadership_directive",
            "version": 1,
            "task_id": "root-run",
            "mission_id": "mission-root-run",
            "leader": "Ceraxia",
            "decision": "delegate",
            "delegated_to": "SkitariiWarband",
            "mission_intent": "Deliver a verified code result",
            "priorities": ["correctness"],
            "constraints": [],
            "success_conditions": ["requested behavior is verified"],
            "tradeoffs": [],
            "escalation_conditions": [],
        }
        acceptance_source = {
            "type": service.ACCEPTANCE_SOURCE_TYPE,
            "protocol_version": service.PROTOCOL_VERSION,
            "mission_id": "mission-root-run",
            "delegating_task_id": "root-run",
            "from": "Warmaster",
            "to": "Ceraxia",
            "user_request": "fix app.py",
        }
        payload = {
            "goal": "fix app.py",
            "task_id": "root-run-skitarii-1",
            "delegating_task_id": "root-run",
            "task_memory_id": "stable-goal",
            "root_task_id": "root-run",
            "parent_task_id": "",
            "leadership_directive": directive,
            "acceptance_source": acceptance_source,
        }
        self.assertIsNone(service.execution_authorization_error(
            payload, "root-run-skitarii-1",
        ))

    def test_outer_post_baseline_exception_preserves_checkpoint_and_parent_lineage(self) -> None:
        executor = object()
        recovered = {
            "status": "failed",
            "accepted": False,
            "revision_required": True,
            "workspace_checkpoint": {"unified_diff": "diff"},
        }

        def explode(_payload, _mission):
            service._EXECUTION_LOCAL.executor = executor
            service._EXECUTION_LOCAL.base_commit = "base"
            raise RuntimeError("post-baseline failure")

        payload = {
            "task_id": "attempt-outer",
            "task_memory_id": "stable-goal",
            "root_task_id": "root-goal",
            "parent_task_id": "parent-goal",
        }
        with (
            mock.patch.object(service, "execution_authorization_error", return_value=None),
            mock.patch.object(service, "_execute_mission_body", side_effect=explode),
            mock.patch.object(
                service, "_recoverable_pipeline_verdict", return_value=recovered,
            ) as recover,
            mock.patch.object(
                service, "_finalize_service_verdict", return_value=recovered,
            ) as finalize,
            mock.patch.object(service, "_cleanup_workspace_processes"),
        ):
            result = service.execute_mission(payload)

        self.assertEqual(result["workspace_checkpoint"]["unified_diff"], "diff")
        self.assertEqual(result["parent_task_id"], "parent-goal")
        self.assertEqual(recover.call_args.kwargs["parent_task_id"], "parent-goal")
        self.assertEqual(finalize.call_args.kwargs["parent_task_id"], "parent-goal")

    def test_restart_salvage_preserves_pending_result_for_checkpoint_only_resume(self) -> None:
        mission = mission_store.Mission("restart-pending", "finish checkpoint")
        pending = {
            "status": "blocked",
            "accepted": False,
            "error_code": "task_checkpoint_commit_pending",
            "task_memory_id": "stable-goal",
            "root_task_id": "root-goal",
            "parent_task_id": "",
            "pending_task_checkpoint": {
                "version": 1,
                "current_state": "verified",
            },
            "pending_task_checkpoint_key": "stable-key",
            "checkpoint_pending_original": {"status": "done", "accepted": True},
            "restart_recovery_required": True,
        }
        mission.status = "blocked"
        mission.result = pending
        mission.cleanup_complete = False
        mission._resume_disabled = True
        mission._gc_after_restart = True
        with (
            mock.patch.dict(mission_store._MISSIONS, {mission.id: mission}, clear=True),
            mock.patch.object(mission, "_persist"),
            mock.patch.object(mission_store, "run_async") as run,
        ):
            prepared = mission_store.prepare_restart_salvage(
                mission.id, expected=mission,
            )
            resumed = mission_store.resume(
                mission.id,
                lambda _mission: {},
                expected=mission,
                preserve_result=True,
            )

        self.assertTrue(prepared)
        self.assertTrue(resumed)
        self.assertIs(mission.result, pending)
        self.assertFalse(mission._resume_disabled)
        self.assertTrue(mission.cleanup_complete)
        run.assert_called_once()

    def test_hidden_revision_network_failure_returns_recoverable_checkpoint(self) -> None:
        recoverable = {
            "status": "failed",
            "accepted": False,
            "revision_required": True,
            "retryable": True,
            "verification_findings": [],
            "workspace_checkpoint": {"unified_diff": "diff"},
        }
        patch_bundle = {
            "base_commit": "base",
            "changed_files": ["app.py"],
            "unified_diff": "diff",
            "apply_gate": "blocked",
        }
        with (
            mock.patch.object(
                service, "run_mission",
                side_effect=urllib.error.URLError("temporary gateway outage"),
            ),
            mock.patch.object(
                service, "_recoverable_pipeline_verdict", return_value=recoverable,
            ) as recover,
            mock.patch.object(service, "_stop_workspace_processes"),
            mock.patch.object(service, "_collect_files", return_value={}),
            mock.patch.object(
                service, "_build_patch_bundle", return_value=patch_bundle,
            ),
            mock.patch.object(service, "_runner_control_violation", return_value=""),
            mock.patch.object(service, "_workspace_symlink_violation", return_value=""),
        ):
            verdict, returned_patch = service._run_hidden_revision_round(
                object(),
                goal="repair app",
                public_checks=[],
                held_out_checks=[],
                base_commit="base",
                task_id="attempt-22",
                task_memory_id="stable-goal",
                root_task_id="root-goal",
                parent_task_id="parent-goal",
                ask_fn=None,
                cancel_fn=None,
                max_steps=2,
                max_wall_sec=10,
            )

        self.assertIs(verdict, recoverable)
        self.assertEqual(returned_patch, patch_bundle)
        recover.assert_called_once()
        self.assertEqual(recover.call_args.kwargs["task_memory_id"], "stable-goal")
        self.assertEqual(recover.call_args.kwargs["root_task_id"], "root-goal")
        self.assertEqual(recover.call_args.kwargs["parent_task_id"], "parent-goal")


if __name__ == "__main__":
    unittest.main()
