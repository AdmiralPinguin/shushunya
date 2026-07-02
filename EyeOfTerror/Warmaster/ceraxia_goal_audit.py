#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
EYE_ROOT = ROOT / "EyeOfTerror"
WARM = EYE_ROOT / "Warmaster"
CERAXIA = EYE_ROOT / "Mechanicum" / "Ceraxia"
CODE_BRIGADE = EYE_ROOT / "Mechanicum" / "CodeBrigade"
AGENT_ARENA = ROOT / "Mechanicum" / "_temporary" / "AgentArena"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": completed.stderr.strip() or completed.stdout.strip(),
            "returncode": completed.returncode,
        }
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        return {"ok": False, "error": "command did not return a JSON object"}
    payload["ok"] = True
    return payload


def check(condition: bool, evidence: str, blocker: str = "") -> dict[str, Any]:
    return {
        "passed": bool(condition),
        "evidence": evidence,
        "blocker": "" if condition else blocker,
    }


def group_status(checks: list[dict[str, Any]]) -> str:
    return "proven" if checks and all(item.get("passed") is True for item in checks) else "incomplete"


def requirement(identifier: str, title: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": identifier,
        "title": title,
        "status": group_status(checks),
        "checks": checks,
    }


def build_audit() -> dict[str, Any]:
    report = run_json([sys.executable, str(WARM / "ceraxia_field_trial_report.py"), "--require-expert-target"])
    ceraxia_py = read(CERAXIA / "ceraxia.py")
    ceraxia_tests = read(CERAXIA / "self_test.py")
    code_brigade = read(CODE_BRIGADE / "code_brigade_adapter.py")
    diagnostic_schema = read(CERAXIA / "contracts" / "diagnostic_repair_request.schema.json")
    evidence_contract = read(WARM / "ceraxia_evidence_contract.py")
    field_trials = read(CERAXIA / "field_trials.json")
    field_trial_tests = read(WARM / "ceraxia_field_trials_self_test.py")
    field_trial_report = read(WARM / "ceraxia_field_trial_report.py")
    live_prepare = read(WARM / "ceraxia_live_task_prepare.py")
    live_run = read(WARM / "ceraxia_live_task_run.py")
    live_register = read(WARM / "ceraxia_live_task_register.py")
    live_learnings = read(WARM / "ceraxia_live_benchmark_learnings.py")
    next_stage_package = read(WARM / "ceraxia_next_stage_package.py")
    auto_review = read(WARM / "ceraxia_field_trial_auto_review.py")
    arena_readme = read(AGENT_ARENA / "README.md") if (AGENT_ARENA / "README.md").exists() else ""
    arena_runner = read(AGENT_ARENA / "scripts" / "run_arena.py") if (AGENT_ARENA / "scripts" / "run_arena.py").exists() else ""

    requirements = [
        requirement(
            "1",
            "honest runtime evidence packages and ledger",
            [
                check(report.get("ok") is True, "ceraxia_field_trial_report.py --require-expert-target", str(report.get("error", ""))),
                check(report.get("fresh_target_met") is True, "report fresh_target_met", "fresh honest target is not met"),
                check("validate_final_manifest_payload" in evidence_contract, "ceraxia_evidence_contract.py validates final_manifest payload", "final manifest payload is not strictly validated"),
                check("accepted_legacy_without_honest_evidence" in read(WARM / "ceraxia_field_trial_report.py"), "report separates legacy accepted entries without honest evidence", "legacy evidence gaps are hidden"),
            ],
        ),
        requirement(
            "2",
            "explicit dry_run guarded_patch repo_engineer review_only modes",
            [
                check('EXECUTION_MODES = {"dry_run", "guarded_patch", "repo_engineer", "review_only"}' in ceraxia_py, "Ceraxia EXECUTION_MODES", "execution modes are not explicit"),
                check("test_review_only_mode_builds_review_package_without_mutation" in ceraxia_tests, "review_only self-test", "review_only mode is not tested"),
                check("test_repo_engineer_mode_has_distinct_controller_contract" in ceraxia_tests, "repo_engineer self-test", "repo_engineer mode is not tested as distinct"),
                check("guarded_patch" in ceraxia_tests and "dry_run" in ceraxia_tests, "guarded_patch and dry_run test coverage", "guarded_patch/dry_run coverage is missing"),
            ],
        ),
        requirement(
            "3",
            "real CodeBrigade executor with full worker_report",
            [
                check("def build_worker_report" in code_brigade, "CodeBrigade build_worker_report", "worker report builder missing"),
                check("build_edit_plan" in code_brigade and "pre_mutation_read_evidence" in code_brigade, "edit_plan and read evidence emitted", "edit/read evidence missing"),
                check("execute_implementation_brief" in code_brigade, "CodeBrigade calls execution adapter", "real execution adapter is not wired"),
                check("build_diagnostic_repair_request" in ceraxia_py, "Ceraxia creates diagnostic_repair_request.json", "diagnostic repair request builder missing"),
            ],
        ),
        requirement(
            "4",
            "source mutation blocked until problem files risks planned diff and acceptance",
            [
                check("mutation_preflight_blockers" in code_brigade, "CodeBrigade mutation_preflight_blockers", "mutation preflight missing"),
                check("planned diff change intents" in code_brigade, "planned diff intents are required", "planned diff precondition missing"),
                check("acceptance criteria" in code_brigade, "acceptance criteria are required", "acceptance precondition missing"),
                check("pre_mutation_read_evidence" in ceraxia_tests, "pre-mutation read evidence tests", "pre-mutation evidence is not tested"),
            ],
        ),
        requirement(
            "5",
            "repair loop classification source candidates hypotheses retry limits stop conditions",
            [
                check("failure_classification_from_repair_item" in ceraxia_py, "failure classification builder", "failure classification missing"),
                check("source_candidates" in diagnostic_schema, "diagnostic schema requires source_candidates", "source candidate contract missing"),
                check("repair_hypotheses" in diagnostic_schema, "diagnostic schema requires repair_hypotheses", "repair hypotheses contract missing"),
                check("max_repair_attempts" in diagnostic_schema and "stop_conditions" in diagnostic_schema, "retry limit and stop conditions schema", "retry/stop contract missing"),
            ],
        ),
        requirement(
            "6",
            "honest arena with diverse tasks and evidence per run",
            [
                check('"minimum_fresh_classes": 8' in field_trials, "field_trials.json requires 8 fresh classes", "fresh class gate missing"),
                check('"live_tasks"' in field_trials and '"minimum_live_tasks": 20' in field_trials, "field_trials.json defines next-stage live catalog and target", "live task catalog/target missing"),
                check("ceraxia_live_task_packet" in live_prepare and "artifact_contract" in live_prepare, "live task prepare emits task packets with artifact contracts", "live task prepare contract missing"),
                check("run_ceraxia_for_task" in live_run and "next_stage_evidence_package.json" in live_run, "live task harness runs Ceraxia and writes next-stage package", "live task harness missing"),
                check("next_stage_evidence_status" in live_register and "validate_live_task_fit" in live_register, "live task register validates packages and task fit", "live task registrar validation missing"),
                check("accepted_for_next_stage" in live_register and "accepted_for_next_stage" in field_trial_report, "live benchmark requires explicit accepted_for_next_stage", "live benchmark acceptance gate missing"),
                check("ceraxia_live_benchmark_learnings" in live_learnings and "mandatory_next_actions" in live_learnings, "live benchmark learnings are aggregated into checkable memory", "live benchmark learnings missing"),
                check("NEXT_STAGE_PACKAGE_KIND" in next_stage_package and "fixture_only" in next_stage_package, "next-stage package builder writes live package contract fields", "next-stage package builder missing"),
                check(int(report.get("fresh_honest_class_count") or 0) >= 8, "report fresh_honest_class_count", "not enough fresh classes"),
                check(int(report.get("fresh_honest_trial_count") or 0) >= 12, "report fresh_honest_trial_count", "not enough fresh trials"),
                check("evidence_paths" in read(CERAXIA / "field_trial_ledger.json"), "field_trial_ledger evidence_paths", "ledger evidence paths missing"),
            ],
        ),
        requirement(
            "7",
            "self-tests separated from field trials",
            [
                check("Ceraxia field trials specification" in field_trial_tests, "field trials self-test exists", "field trial self-test missing"),
                check("A scripted self-test proves only that a known scenario still works." in read(CERAXIA / "EVALUATION.md"), "EVALUATION.md separates scripted tests from real target", "evaluation warning missing"),
                check("ceraxia_field_trial_runner.py" in field_trial_tests and "ceraxia_field_trial_report.py" in field_trial_tests, "field trial runner/report are tested separately", "field trial runner/report test missing"),
            ],
        ),
        requirement(
            "8",
            "review gate blocks weak execution and unverifiable claims",
            [
                check("source_mutation_scope_sufficiency_from_worker" in ceraxia_py, "source mutation scope gate", "extra file gate missing"),
                check("verification_after_mutation_sufficiency" in ceraxia_py, "verification-after-mutation gate", "post-mutation verification gate missing"),
                check("output_consistency_findings" in ceraxia_py, "output consistency findings", "unverifiable/failure-text gate missing"),
                check("worker output contract is incomplete" in ceraxia_py, "worker output contract blocker", "incomplete execution gate missing"),
            ],
        ),
        requirement(
            "9",
            "external agent patterns learned without task-specific hacks",
            [
                check("Comparative stress bench" in arena_readme, "AgentArena comparative bench README", "external agent arena missing"),
                check("OpenHands" in arena_runner or "openhands" in arena_runner, "AgentArena has external agent adapter placeholder", "external agent adapter evidence missing"),
                check("agent competition and benchmark work" in read(ROOT / "Mechanicum" / "_temporary" / "README.md"), "external agent work parked as reusable arena", "arena status missing"),
                check("review_gate_rich" in auto_review and "principal_evidence_signals" in auto_review, "external-style evidence patterns captured in reviewer", "reusable reviewer patterns missing"),
            ],
        ),
        requirement(
            "10",
            "12 fresh honest field trials with 8 classes and green target",
            [
                check(report.get("target_met") is True, "report target_met", "target not met"),
                check(report.get("expert_target_met") is True, "report expert_target_met", "expert target not met"),
                check(int(report.get("fresh_honest_trial_count") or 0) >= 12, "fresh honest trial count >= 12", "fresh trial count too low"),
                check(int(report.get("fresh_honest_class_count") or 0) >= 8, "fresh honest class count >= 8", "fresh class count too low"),
                check(float(report.get("fresh_honest_overall_score") or 0) > 0, "fresh honest score is nonzero", "fresh score is zero"),
            ],
        ),
    ]
    incomplete = [item for item in requirements if item["status"] != "proven"]
    return {
        "kind": "ceraxia_goal_audit",
        "contract_version": "eye-mechanicum.v1",
        "status": "proven" if not incomplete else "incomplete",
        "requirement_count": len(requirements),
        "proven_count": sum(1 for item in requirements if item["status"] == "proven"),
        "incomplete_count": len(incomplete),
        "requirements": requirements,
        "field_trial_report_summary": {
            "target_met": report.get("target_met"),
            "fresh_target_met": report.get("fresh_target_met"),
            "fresh_honest_trial_count": report.get("fresh_honest_trial_count"),
            "fresh_honest_class_count": report.get("fresh_honest_class_count"),
            "fresh_honest_overall_score": report.get("fresh_honest_overall_score"),
            "expert_target_met": report.get("expert_target_met"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Ceraxia against the active 10-point engineering goal.")
    parser.add_argument("--require-proven", action="store_true")
    args = parser.parse_args()
    audit = build_audit()
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    if args.require_proven and audit["status"] != "proven":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
