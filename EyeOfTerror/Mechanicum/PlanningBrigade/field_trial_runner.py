#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from planning_brigade import CONTRACT_VERSION, build_planning_packet
from planning_packet_contract import validate_planning_packet


ROOT = Path(__file__).resolve().parent


def require_subset(expected: list[str], actual: list[str], label: str, trial_id: str) -> None:
    missing = [item for item in expected if item not in actual]
    if missing:
        raise AssertionError(f"{trial_id}: missing {label}: {missing}; actual={actual}")


def run_trial(trial: dict[str, Any]) -> dict[str, Any]:
    packet = build_planning_packet({"task": trial["task"], "repo_path": trial.get("repo_path", "")})
    trial_id = str(trial["id"])
    validation_problems = validate_planning_packet(packet)
    expected_decision = trial.get("expected_decision", "ready_for_ceraxia_review")
    if expected_decision != "blocked" and validation_problems:
        raise AssertionError(f"{trial_id}: generated packet failed contract validation: {validation_problems}; packet={packet}")
    require_subset(trial.get("expected_kinds", []), packet["task_triage"]["task_kinds"], "task kinds", trial_id)
    phases = [phase["id"] for phase in packet["work_breakdown"]["phases"]]
    require_subset(trial.get("expected_phases", []), phases, "work phases", trial_id)
    work_packages = packet["implementation_work_packages"]["packages"]
    work_package_ids = [package["id"] for package in work_packages]
    require_subset(trial.get("expected_work_packages", []), work_package_ids, "implementation work packages", trial_id)
    playbook = packet.get("investigation_playbook") if isinstance(packet.get("investigation_playbook"), dict) else {}
    read_stages = playbook.get("read_stages") if isinstance(playbook.get("read_stages"), list) else []
    if len(read_stages) < 5:
        raise AssertionError(f"{trial_id}: investigation playbook must include ordered read stages: {packet}")
    if not isinstance(playbook.get("evidence_questions"), list) or len(playbook.get("evidence_questions", [])) < 4:
        raise AssertionError(f"{trial_id}: investigation playbook must include evidence questions: {packet}")
    if not isinstance(playbook.get("mutation_blockers"), list) or len(playbook.get("mutation_blockers", [])) < 3:
        raise AssertionError(f"{trial_id}: investigation playbook must include mutation blockers: {packet}")
    change_control = packet.get("change_control_plan") if isinstance(packet.get("change_control_plan"), dict) else {}
    if change_control.get("target") != "CodeBrigade":
        raise AssertionError(f"{trial_id}: change control plan must target CodeBrigade: {packet}")
    for key, minimum in [
        ("allowed_change_intents", 3),
        ("protected_invariants", 3),
        ("mutation_requires", 4),
        ("diff_review_questions", 3),
        ("rollback_triggers", 3),
        ("post_change_proofs", 3),
    ]:
        if not isinstance(change_control.get(key), list) or len(change_control.get(key, [])) < minimum:
            raise AssertionError(f"{trial_id}: change control plan missing {key}: {packet}")
    missing_blocking_policy = [package.get("id", "<unknown>") for package in work_packages if not package.get("blocking_policy")]
    if missing_blocking_policy:
        raise AssertionError(f"{trial_id}: work packages missing blocking_policy: {missing_blocking_policy}; packet={packet}")
    if packet["implementation_work_packages"]["review_order"] != work_package_ids:
        raise AssertionError(f"{trial_id}: work package review_order must match package order: {packet}")
    surfaces = [surface["surface"] for surface in packet["impact_analysis"]["surfaces"]]
    covered_surfaces = {
        surface
        for package in work_packages
        for surface in package.get("impact_surfaces", [])
        if isinstance(surface, str)
    }
    missing_surface_packages = sorted(surface for surface in surfaces if surface not in covered_surfaces)
    if missing_surface_packages:
        raise AssertionError(f"{trial_id}: impact surfaces lack implementation work packages: {missing_surface_packages}; packet={packet}")
    package_matrix_surfaces = {
        row.get("surface")
        for row in packet["surface_package_matrix"]["rows"]
        if isinstance(row, dict) and row.get("surface") and row.get("package_ids")
    }
    require_subset(surfaces, package_matrix_surfaces, "surface package matrix", trial_id)
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
        "work_packages": work_package_ids,
        "surfaces": surfaces,
        "highest_risk_surface": packet["impact_analysis"]["highest_risk_surface"],
        "negative_tests": packet["verification_strategy"]["negative_tests"],
        "change_protected_invariants": change_control["protected_invariants"],
        "change_post_change_proofs": change_control["post_change_proofs"],
        "change_rollback_triggers": change_control["rollback_triggers"],
        "change_control_counts": {
            "allowed_change_intents": len(change_control["allowed_change_intents"]),
            "protected_invariants": len(change_control["protected_invariants"]),
            "mutation_requires": len(change_control["mutation_requires"]),
            "diff_review_questions": len(change_control["diff_review_questions"]),
            "rollback_triggers": len(change_control["rollback_triggers"]),
            "post_change_proofs": len(change_control["post_change_proofs"]),
        },
        "validation_problem_count": len(validation_problems),
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    kinds: Counter[str] = Counter()
    phases: Counter[str] = Counter()
    work_packages: Counter[str] = Counter()
    surfaces: Counter[str] = Counter()
    highest_risk_surfaces: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    negative_tests: Counter[str] = Counter()
    change_invariants: Counter[str] = Counter()
    change_post_proofs: Counter[str] = Counter()
    change_rollback_triggers: Counter[str] = Counter()
    minimum_change_control_counts: dict[str, int] = {}
    scores: list[int] = []
    for result in results:
        kinds.update(str(item) for item in result.get("task_kinds", []))
        phases.update(str(item) for item in result.get("phases", []))
        work_packages.update(str(item) for item in result.get("work_packages", []))
        surfaces.update(str(item) for item in result.get("surfaces", []))
        if result.get("highest_risk_surface"):
            highest_risk_surfaces.update([str(result["highest_risk_surface"])])
        decisions.update([str(result.get("decision", ""))])
        negative_tests.update(str(item) for item in result.get("negative_tests", []))
        change_invariants.update(str(item) for item in result.get("change_protected_invariants", []))
        change_post_proofs.update(str(item) for item in result.get("change_post_change_proofs", []))
        change_rollback_triggers.update(str(item) for item in result.get("change_rollback_triggers", []))
        counts = result.get("change_control_counts") if isinstance(result.get("change_control_counts"), dict) else {}
        for key, value in counts.items():
            if isinstance(value, int):
                minimum_change_control_counts[key] = min(minimum_change_control_counts.get(key, value), value)
        if isinstance(result.get("score"), int):
            scores.append(result["score"])
    return {
        "trial_count": len(results),
        "decision_counts": dict(sorted(decisions.items())),
        "task_kind_counts": dict(sorted(kinds.items())),
        "phase_counts": dict(sorted(phases.items())),
        "work_package_counts": dict(sorted(work_packages.items())),
        "surface_counts": dict(sorted(surfaces.items())),
        "highest_risk_surface_counts": dict(sorted(highest_risk_surfaces.items())),
        "negative_test_counts": dict(sorted(negative_tests.items())),
        "change_invariant_counts": dict(sorted(change_invariants.items())),
        "change_post_proof_counts": dict(sorted(change_post_proofs.items())),
        "change_rollback_trigger_counts": dict(sorted(change_rollback_triggers.items())),
        "minimum_change_control_counts": dict(sorted(minimum_change_control_counts.items())),
        "minimum_score": min(scores) if scores else 0,
        "average_score": round(sum(scores) / len(scores), 2) if scores else 0,
    }


def assert_coverage(summary: dict[str, Any]) -> None:
    required_kinds = {
        "api_compatibility",
        "bugfix",
        "concurrency",
        "config_runtime",
        "migration",
        "refactor",
        "security",
        "test_repair",
    }
    required_surfaces = {
        "concurrency_runtime",
        "data_compatibility",
        "internal_architecture",
        "public_api_contract",
        "runtime_configuration",
        "security_boundary",
        "source_behavior",
        "test_surface",
    }
    required_work_packages = {
        "compatibility_package",
        "architecture_refactor_package",
        "concurrency_runtime_package",
        "evidence_survey_package",
        "minimal_patch_package",
        "runtime_configuration_package",
        "security_boundary_package",
        "verification_evidence_package",
    }
    kind_counts = summary.get("task_kind_counts") if isinstance(summary.get("task_kind_counts"), dict) else {}
    surface_counts = summary.get("surface_counts") if isinstance(summary.get("surface_counts"), dict) else {}
    work_package_counts = summary.get("work_package_counts") if isinstance(summary.get("work_package_counts"), dict) else {}
    invariant_counts = summary.get("change_invariant_counts") if isinstance(summary.get("change_invariant_counts"), dict) else {}
    post_proof_counts = summary.get("change_post_proof_counts") if isinstance(summary.get("change_post_proof_counts"), dict) else {}
    rollback_counts = summary.get("change_rollback_trigger_counts") if isinstance(summary.get("change_rollback_trigger_counts"), dict) else {}
    minimum_change_counts = summary.get("minimum_change_control_counts") if isinstance(summary.get("minimum_change_control_counts"), dict) else {}
    missing_kinds = sorted(kind for kind in required_kinds if kind not in kind_counts)
    missing_surfaces = sorted(surface for surface in required_surfaces if surface not in surface_counts)
    missing_work_packages = sorted(package for package in required_work_packages if package not in work_package_counts)
    if missing_kinds:
        raise AssertionError(f"field trials are missing task kind coverage: {missing_kinds}")
    if missing_surfaces:
        raise AssertionError(f"field trials are missing surface coverage: {missing_surfaces}")
    if missing_work_packages:
        raise AssertionError(f"field trials are missing implementation work package coverage: {missing_work_packages}")
    decision_counts = summary.get("decision_counts") if isinstance(summary.get("decision_counts"), dict) else {}
    if "blocked" not in decision_counts or "ready_for_ceraxia_review" not in decision_counts:
        raise AssertionError(f"field trials must cover blocked and ready decisions: {summary}")
    required_invariant_fragments = [
        "negative security boundary",
        "old callers, old data",
        "parallel, retry, cache",
        "public entrypoints and dependency edges",
    ]
    missing_invariant_fragments = [
        fragment
        for fragment in required_invariant_fragments
        if not any(fragment in invariant for invariant in invariant_counts)
    ]
    if missing_invariant_fragments:
        raise AssertionError(f"field trials are missing change-control invariant coverage: {missing_invariant_fragments}")
    required_post_proof_fragments = [
        "negative boundary evidence",
        "compatibility evidence",
        "remaining race risk",
        "dependency-edge review",
    ]
    missing_post_proof_fragments = [
        fragment
        for fragment in required_post_proof_fragments
        if not any(fragment in proof for proof in post_proof_counts)
    ]
    if missing_post_proof_fragments:
        raise AssertionError(f"field trials are missing change-control proof coverage: {missing_post_proof_fragments}")
    for key, minimum in {
        "allowed_change_intents": 3,
        "protected_invariants": 3,
        "mutation_requires": 4,
        "diff_review_questions": 3,
        "rollback_triggers": 3,
        "post_change_proofs": 3,
    }.items():
        if minimum_change_counts.get(key, 0) < minimum:
            raise AssertionError(f"field trials have weak minimum change-control count for {key}: {summary}")
    if "verification cannot prove the changed behavior" not in rollback_counts:
        raise AssertionError(f"field trials must preserve verification rollback trigger coverage: {summary}")


def main() -> int:
    spec = json.loads((ROOT / "field_trials.json").read_text(encoding="utf-8"))
    if spec.get("contract_version") != CONTRACT_VERSION:
        raise AssertionError(f"field trial contract version drifted: {spec}")
    results = [run_trial(trial) for trial in spec["trials"]]
    summary = summarize_results(results)
    assert_coverage(summary)
    print(json.dumps({"ok": True, "summary": summary, "trials": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
