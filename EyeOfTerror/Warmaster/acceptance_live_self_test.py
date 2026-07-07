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


def write_acceptance_fixture(root: Path) -> tuple[Path, Path, str]:
    task_id = "acceptance-live-llm-gate"
    mission_id = f"mission-{task_id}"
    mission_dir = root / "missions" / mission_id
    run_dir = root / "runs" / task_id
    order = commander_order(
        mission_id,
        to="Ceraxia",
        user_request="Собери краткий проверочный отчет о протокольной приемке.",
        commander_intent="Проверить, что финал бригадира проходит живую приемку Вармастера.",
        primary_goal="Вернуть короткий структурированный отчет, который явно удовлетворяет условиям приемки.",
        success_conditions=[
            "governor_report содержит понятный итог",
            "quality_review не требует внутренней ревизии",
            "final_response создается только после acceptance_review.accepted=true",
        ],
        constraints=["Не обходить WarmasterAcceptance и не создавать пользовательский финал без приемки."],
    )
    validate_protocol_payload(order, expected_type="commander_order")
    write_json(mission_dir / "mission.json", {"mission_id": mission_id, "status": "assigned", "assigned_governor": "Ceraxia"})
    write_json(mission_dir / "commander_order.json", order)
    write_json(run_dir / "mission_ref.json", {"mission_id": mission_id, "mission_dir": str(mission_dir), "assigned_governor": "Ceraxia"})
    write_json(
        run_dir / "status.json",
        {
            "task_id": task_id,
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
    ledger = TaskLedger.create(run_dir / "task_ledger.json", task_id, "Acceptance live LLM gate", "Ceraxia")
    ledger.set_result(
        {
            "ok": True,
            "final_step": "finalize",
            "artifacts": ["/work/acceptance/final_manifest.json"],
            "workspace_root": str(run_dir / "work"),
            "status": "ready",
            "summary": (
                "Губернатор подготовил короткий проверочный отчет: итог понятен, "
                "условия приемки перечислены, внутренняя ревизия не требуется."
            ),
            "revision_plan": {"required": False, "steps": []},
        }
    )
    ledger.set_status("completed")
    return mission_dir, run_dir, mission_id


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        mission_dir, run_dir, mission_id = write_acceptance_fixture(root)
        result = record_warmaster_acceptance(run_dir)
        review = result.get("acceptance_review") if isinstance(result.get("acceptance_review"), dict) else {}
        validate_protocol_payload(review, expected_type="acceptance_review")
        decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
        model_brain = decision.get("model_brain") if isinstance(decision.get("model_brain"), dict) else {}
        if model_brain.get("owner") != "WarmasterAcceptance" or model_brain.get("status") != "answered":
            raise AssertionError(f"WarmasterAcceptance did not use live model brain: {result}")
        if review.get("mission_id") != mission_id or review.get("reviewer") != "Warmaster":
            raise AssertionError(f"acceptance_review authority boundary drifted: {review}")
        final_path = mission_dir / "final_response.json"
        revision_path = mission_dir / "revision_order.json"
        if review.get("accepted"):
            if not final_path.exists():
                raise AssertionError(f"accepted live acceptance did not write final_response: {result}")
            final = json.loads(final_path.read_text(encoding="utf-8"))
            validate_protocol_payload(final, expected_type="final_response")
            if revision_path.exists():
                raise AssertionError("accepted live acceptance also wrote revision_order")
        else:
            if final_path.exists():
                raise AssertionError("rejected live acceptance wrote final_response")
            if not revision_path.exists() and not review.get("escalate_to_user"):
                raise AssertionError(f"rejected live acceptance did not write internal revision_order: {result}")
            if revision_path.exists():
                revision = json.loads(revision_path.read_text(encoding="utf-8"))
                validate_protocol_payload(revision, expected_type="revision_order")
        print("[ok] Warmaster live acceptance gate")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
