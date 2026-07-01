#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from planning_brigade import CONTRACT_VERSION, build_planning_packet


ROOT = Path(__file__).resolve().parent


def require_subset(expected: list[str], actual: list[str], label: str, trial_id: str) -> None:
    missing = [item for item in expected if item not in actual]
    if missing:
        raise AssertionError(f"{trial_id}: missing {label}: {missing}; actual={actual}")


def run_trial(trial: dict[str, Any]) -> dict[str, Any]:
    packet = build_planning_packet({"task": trial["task"], "repo_path": trial.get("repo_path", "")})
    trial_id = str(trial["id"])
    require_subset(trial.get("expected_kinds", []), packet["task_triage"]["task_kinds"], "task kinds", trial_id)
    phases = [phase["id"] for phase in packet["work_breakdown"]["phases"]]
    require_subset(trial.get("expected_phases", []), phases, "work phases", trial_id)
    surfaces = [surface["surface"] for surface in packet["impact_analysis"]["surfaces"]]
    require_subset(trial.get("expected_surfaces", []), surfaces, "impact surfaces", trial_id)
    expected_highest_risk_surface = trial.get("expected_highest_risk_surface")
    if expected_highest_risk_surface and packet["impact_analysis"]["highest_risk_surface"] != expected_highest_risk_surface:
        raise AssertionError(
            f"{trial_id}: expected highest risk surface {expected_highest_risk_surface}, "
            f"got {packet['impact_analysis']['highest_risk_surface']}: {packet}"
        )
    if not packet["surface_verification_matrix"]["complete"]:
        raise AssertionError(f"{trial_id}: surface verification matrix should be complete: {packet}")
    matrix_surfaces = [row["surface"] for row in packet["surface_verification_matrix"]["rows"]]
    require_subset(trial.get("expected_surfaces", []), matrix_surfaces, "surface verification rows", trial_id)
    require_subset(
        trial.get("expected_negative_tests", []),
        packet["verification_strategy"]["negative_tests"],
        "negative tests",
        trial_id,
    )
    decision = packet["planning_review_gate"]["decision"]
    expected_decision = trial.get("expected_decision", "ready_for_ceraxia_review")
    if decision != expected_decision:
        raise AssertionError(f"{trial_id}: expected decision {expected_decision}, got {decision}: {packet}")
    minimum_score = int(trial.get("minimum_score", 0))
    if packet["planning_review_gate"]["score"] < minimum_score:
        raise AssertionError(f"{trial_id}: planning score below {minimum_score}: {packet}")
    return {
        "id": trial_id,
        "decision": decision,
        "score": packet["planning_review_gate"]["score"],
        "task_kinds": packet["task_triage"]["task_kinds"],
        "phases": phases,
        "surfaces": surfaces,
    }


def main() -> int:
    spec = json.loads((ROOT / "field_trials.json").read_text(encoding="utf-8"))
    if spec.get("contract_version") != CONTRACT_VERSION:
        raise AssertionError(f"field trial contract version drifted: {spec}")
    results = [run_trial(trial) for trial in spec["trials"]]
    print(json.dumps({"ok": True, "trials": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
