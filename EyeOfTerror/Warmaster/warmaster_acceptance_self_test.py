#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WARM_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(WARM_ROOT) not in sys.path:
    sys.path.insert(0, str(WARM_ROOT))

from EyeOfTerror.common_protocol import commander_order, validate_protocol_payload
from eye_of_terror.ledger import TaskLedger
from eye_of_terror.mission_control import record_warmaster_acceptance


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_acceptance_fixture(root: Path, suffix: str, result_payload: dict[str, object]) -> tuple[Path, Path, str]:
    mission_dir = root / "missions" / f"mission-{suffix}"
    run_dir = root / "runs" / suffix
    mission_id = mission_dir.name
    order = commander_order(
        mission_id,
        to="Ceraxia",
        user_request="Создай маленький проверяемый CLI проект.",
        commander_intent="Проверить, что финал бригадира проходит приемку Вармастера.",
        primary_goal="Получить структурированный финальный отчет и решение приемки.",
        success_conditions=[
            "governor_report создан",
            "acceptance_review создан",
            "needs_revision не считается пользовательским финалом",
        ],
    )
    validate_protocol_payload(order, expected_type="commander_order")
    write_json(mission_dir / "mission.json", {"mission_id": mission_id, "status": "assigned", "assigned_governor": "Ceraxia"})
    write_json(mission_dir / "commander_order.json", order)
    write_json(run_dir / "mission_ref.json", {"mission_id": mission_id, "mission_dir": str(mission_dir), "assigned_governor": "Ceraxia"})
    write_json(
        run_dir / "status.json",
        {
            "task_id": suffix,
            "steps": [{"step_id": "finalize", "worker": "SealwrightFinalis"}],
        },
    )
    write_json(
        run_dir / "oversight.json",
        {
            "revision_policy": {
                "source_step": "finalize",
                "final_steps": ["finalize"],
                "allowed_steps": ["finalize"],
                "requires_downstream_rerun": True,
            }
        },
    )
    ledger = TaskLedger.create(run_dir / "task_ledger.json", suffix, "Acceptance live smoke", "Ceraxia")
    ledger.set_result(result_payload)
    ledger.set_status("completed")
    return mission_dir, run_dir, mission_id


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        mission_dir, run_dir, _mission_id = write_acceptance_fixture(
            root,
            "acceptance-live-smoke",
            {
                "ok": True,
                "final_step": "finalize",
                "artifacts": [],
                "workspace_root": str(root / "runs" / "acceptance-live-smoke" / "work"),
                "status": "ready",
                "summary": "Минимальный финальный отчет готов для приемки.",
                "revision_plan": {"required": False, "steps": []},
            },
        )
        result = record_warmaster_acceptance(run_dir)
        review = result.get("acceptance_review") if isinstance(result.get("acceptance_review"), dict) else {}
        if not review:
            raise AssertionError(f"acceptance review missing: {result}")
        validate_protocol_payload(review, expected_type="acceptance_review")
        if not (mission_dir / "governor_reports").exists():
            raise AssertionError("governor report directory was not created")
        if review.get("accepted"):
            if not (mission_dir / "final_response.json").exists():
                raise AssertionError("accepted result did not write final_response.json")
        elif not review.get("escalate_to_user"):
            ledger_after = TaskLedger.load(run_dir / "task_ledger.json").to_dict()
            revision_plan = ledger_after.get("result", {}).get("revision_plan", {}) if isinstance(ledger_after.get("result"), dict) else {}
            if not revision_plan.get("required"):
                raise AssertionError("rejected result did not create internal revision_plan")
        revision_mission_dir, revision_run_dir, _ = write_acceptance_fixture(
            root,
            "acceptance-needs-revision",
            {
                "ok": False,
                "final_step": "finalize",
                "artifacts": [],
                "workspace_root": str(root / "runs" / "acceptance-needs-revision" / "work"),
                "status": "needs_revision",
                "summary": "Бригадир требует внутреннюю доработку.",
                "revision_plan": {
                    "required": True,
                    "steps": [
                        {
                            "step_id": "finalize",
                            "worker": "SealwrightFinalis",
                            "reason": "Финальный пакет неполный.",
                            "source": "governor_review",
                            "priority": "blocker",
                        }
                    ],
                },
            },
        )
        revision_result = record_warmaster_acceptance(revision_run_dir)
        revision_review = revision_result.get("acceptance_review") if isinstance(revision_result.get("acceptance_review"), dict) else {}
        validate_protocol_payload(revision_review, expected_type="acceptance_review")
        if revision_review.get("accepted") or revision_review.get("escalate_to_user"):
            raise AssertionError(f"needs_revision must stay internal: {revision_result}")
        if (revision_mission_dir / "final_response.json").exists():
            raise AssertionError("needs_revision incorrectly wrote final_response.json")
        if not list((revision_mission_dir / "revision_orders").glob("revision_order-*.json")):
            raise AssertionError("needs_revision did not write revision_order")
        revision_mission = json.loads((revision_mission_dir / "mission.json").read_text(encoding="utf-8"))
        if revision_mission.get("status") != "revision":
            raise AssertionError(f"needs_revision did not move mission to revision: {revision_mission}")
        print("[ok] Warmaster live acceptance")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
