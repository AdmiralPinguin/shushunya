from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import planner  # noqa: E402
import warband  # noqa: E402
import mission_store  # noqa: E402


class _GreenExecutor:
    def bash(self, _cmd: str, timeout: int = 180) -> dict[str, object]:
        return {"returncode": 0, "stdout": "", "stderr": ""}


def _spec_finding() -> dict[str, object]:
    return {
        "code": "acceptance_spec_failure",
        "what_failed": "The acceptance specification was structurally weak.",
        "evidence": "checks are compile/run-only",
        "expected": "A behavioural check exists.",
        "remediation": "Regenerate a behavioural check and rerun acceptance.",
        "revision_owner": "infrastructure",
        "retryable": True,
        "entity_kind": "acceptance",
        "entity_id": "public-acceptance-spec",
    }


class RevisionEvidenceTests(unittest.TestCase):
    def test_green_commands_rejected_by_structural_gate_get_truthful_finding(self) -> None:
        fighter = {
            "ok": True,
            "steps": 1,
            "seconds": 0.1,
            "summary": "All success checks are passing.",
            "artifacts": [],
        }
        weak_spec = {"deliverables": [], "checks": [{"cmd": "true"}]}
        with (
            mock.patch.object(warband, "build_spec", return_value=weak_spec),
            mock.patch.object(warband, "run_fighter", return_value=fighter),
        ):
            verdict = warband.run_mission(
                "build it", _GreenExecutor(), max_fighter_rounds=1,
            )

        self.assertFalse(verdict["accepted"])
        self.assertTrue(all(item["ok"] for item in verdict["acceptance"]["results"]))
        self.assertIn("compile/run-only", verdict["acceptance"]["reason"])
        self.assertEqual(
            verdict["verification_findings"][0]["code"],
            "acceptance_spec_failure",
        )
        self.assertEqual(
            verdict["verification_findings"][0]["revision_owner"],
            "infrastructure",
        )
        self.assertNotIn(fighter["summary"], verdict["summary"])
        self.assertIn("compile/run-only", verdict["summary"])

    def test_planner_preserves_failed_subtask_revision_evidence(self) -> None:
        finding = _spec_finding()
        acceptance = {
            "accepted": False,
            "results": [{"target": "true", "ok": True}],
            "reason": "checks are compile/run-only",
        }
        subtask_failure = {
            "status": "failed",
            "accepted": False,
            "summary": "Acceptance rejected: checks are compile/run-only",
            "artifacts": ["player_controller.gd"],
            "checks": [{"cmd": "true"}],
            "acceptance": acceptance,
            "rounds": [{"round": 2, "acceptance": acceptance}],
            "revision_required": True,
            "verification_findings": [finding],
        }
        subtasks = [
            {"title": "Input", "goal": "make input", "depends_on": []},
            {"title": "Build", "goal": "make apk", "depends_on": [0]},
        ]
        with (
            mock.patch.object(planner, "decompose", return_value=subtasks),
            mock.patch.object(
                planner, "build_spec",
                return_value={"deliverables": ["builds/game.apk"], "checks": [{"cmd": "test -f builds/game.apk"}]},
            ),
            mock.patch.object(
                planner, "_run_wave_parallel", return_value=[subtask_failure],
            ),
        ):
            verdict = planner.plan_and_run("make game", _GreenExecutor())

        self.assertFalse(verdict["accepted"])
        self.assertTrue(verdict["revision_required"])
        self.assertEqual(verdict["verification_findings"], [finding])
        self.assertIs(verdict["acceptance"], acceptance)
        self.assertEqual(verdict["rounds"], subtask_failure["rounds"])
        self.assertEqual(verdict["checks"], [{"cmd": "true"}])
        self.assertEqual(verdict["failed_subtask"], "Input")
        self.assertIsNotNone(mission_store._revision_turn(verdict, 1))

    def test_reconsider_uses_acceptance_reason_when_no_check_is_red(self) -> None:
        captured: list[str] = []

        def planner_chat(prompt: str, max_tokens: int = 1200) -> str:
            captured.append(prompt)
            return "Use a real behavioural test and keep the implementation small."

        failed = {
            "acceptance": {
                "accepted": False,
                "results": [{"target": "true", "ok": True}],
                "reason": "checks are compile/run-only",
            },
            "summary": "All checks pass.",
        }
        with mock.patch.object(planner, "_planner_chat", side_effect=planner_chat):
            planner.reconsider("make game", "make input", failed)

        self.assertIn("checks are compile/run-only", captured[0])
        self.assertNotIn("WHAT FAILED:\nAll checks pass.", captured[0])


if __name__ == "__main__":
    unittest.main()
