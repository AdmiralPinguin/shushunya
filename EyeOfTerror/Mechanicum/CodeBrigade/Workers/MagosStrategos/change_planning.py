from __future__ import annotations

"""Change planning role implementation."""

from common.codewright_core import *  # noqa: F403 - role modules use the shared Codewright helper surface.






def problem_statement_from_evidence(request: dict[str, Any], survey: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request) or str(survey.get("goal") or "")
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    investigation = survey.get("engineering_investigation") if isinstance(survey.get("engineering_investigation"), dict) else {}
    readiness = survey.get("engineering_readiness") if isinstance(survey.get("engineering_readiness"), dict) else {}
    ranked_files = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    hypotheses = investigation.get("hypotheses") if isinstance(investigation.get("hypotheses"), list) else []
    test_strategy = readiness.get("test_strategy") if isinstance(readiness.get("test_strategy"), dict) else {}
    verification_candidates: list[str] = []
    for key in ("primary_commands", "linked_test_targets", "fallback_checks"):
        values = test_strategy.get(key) if isinstance(test_strategy.get(key), list) else []
        verification_candidates.extend(str(item) for item in values[:6])
    return {
        "status": "recorded",
        "goal": goal,
        "observed_problem": "Infer the concrete behavior gap from the task text, repository survey, tests, docs, and diagnostics before mutation.",
        "success_criteria": [
            "changed files directly address the requested behavior",
            "source scope is justified by repo survey or explicit patch contract",
            "verification evidence is preserved after the final mutation",
            "review either approves with evidence or blocks with focused revision steps",
        ],
        "evidence_to_inspect": [
            str(item.get("path"))
            for item in ranked_files[:10]
            if isinstance(item, dict) and item.get("path")
        ],
        "working_hypotheses": [
            str(item.get("hypothesis"))
            for item in hypotheses[:8]
            if isinstance(item, dict) and item.get("hypothesis")
        ],
        "verification_candidates": verification_candidates[:12],
        "ambiguity_policy": "If multiple incompatible interpretations remain after survey, block with a focused clarification instead of broad mutation.",
    }


def architecture_options_from_evidence(request: dict[str, Any], survey: dict[str, Any]) -> dict[str, Any]:
    problem_statement = problem_statement_from_evidence(request, survey)
    readiness = survey.get("engineering_readiness") if isinstance(survey.get("engineering_readiness"), dict) else {}
    impact_matrix = readiness.get("impact_matrix") if isinstance(readiness.get("impact_matrix"), list) else []
    high_impact_paths = [
        str(item.get("path"))
        for item in impact_matrix
        if isinstance(item, dict) and item.get("impact_level") in {"high", "medium"} and item.get("path")
    ][:10]
    return {
        "status": "recorded",
        "problem_status": problem_statement.get("status"),
        "options": [
            {
                "id": "minimal_targeted_patch",
                "summary": "Patch the narrowest source surface that satisfies the observed behavior gap.",
                "recommended": True,
                "tradeoffs": ["lowest churn", "requires focused verification to prove behavior"],
                "applies_to": high_impact_paths,
            },
            {
                "id": "compatibility_wrapper",
                "summary": "Preserve old callers while adding the new behavior behind a compatible boundary.",
                "recommended": any("api" in str(kind) for kind in task_profile_from_request(request).get("kinds", []))
                if isinstance(task_profile_from_request(request).get("kinds"), list)
                else False,
                "tradeoffs": ["safer public API evolution", "may require deprecation docs and caller tests"],
                "applies_to": high_impact_paths,
            },
            {
                "id": "broad_rewrite",
                "summary": "Rewrite the surrounding module or subsystem.",
                "recommended": False,
                "tradeoffs": ["higher regression risk", "harder review", "should be rejected unless the task explicitly demands it"],
                "applies_to": high_impact_paths,
            },
        ],
        "selection_rule": "Choose the first option that satisfies the task with the least public-surface churn and adequate verification.",
    }

def run_change_planning(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    goal = request_goal(request) or str(survey.get("goal") or "")
    role_policy = role_policy_from_request(request)
    task_profile = task_profile_from_request(request)
    worker_brief = worker_brief_from_request(request)
    candidates = survey.get("candidate_files") if isinstance(survey.get("candidate_files"), list) else []
    tests = survey.get("test_files") if isinstance(survey.get("test_files"), list) else []
    symbols = survey.get("python_symbols") if isinstance(survey.get("python_symbols"), list) else []
    suggested_commands = survey.get("suggested_verification_commands") if isinstance(survey.get("suggested_verification_commands"), list) else []
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    investigation = survey.get("engineering_investigation") if isinstance(survey.get("engineering_investigation"), dict) else {}
    readiness = survey.get("engineering_readiness") if isinstance(survey.get("engineering_readiness"), dict) else {}
    ranked_files = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    test_source_links = repo_map.get("test_source_links") if isinstance(repo_map.get("test_source_links"), list) else []
    read_order = repo_map.get("recommended_read_order") if isinstance(repo_map.get("recommended_read_order"), list) else []
    targeted_reads = investigation.get("targeted_reading_plan") if isinstance(investigation.get("targeted_reading_plan"), list) else []
    hypotheses = investigation.get("hypotheses") if isinstance(investigation.get("hypotheses"), list) else []
    decision_seed = investigation.get("design_decision_seed") if isinstance(investigation.get("design_decision_seed"), list) else []
    impact_matrix = readiness.get("impact_matrix") if isinstance(readiness.get("impact_matrix"), list) else []
    risk_register = readiness.get("risk_register") if isinstance(readiness.get("risk_register"), list) else []
    acceptance_criteria = readiness.get("acceptance_criteria") if isinstance(readiness.get("acceptance_criteria"), list) else []
    test_strategy = readiness.get("test_strategy") if isinstance(readiness.get("test_strategy"), dict) else {}
    repo_grade_workflow = repo_grade_workflow_from_request(request)
    problem_statement = problem_statement_from_evidence(request, survey)
    architecture_options = architecture_options_from_evidence(request, survey)
    architecture_decision_record = architecture_decision_record_from_evidence(request, survey)
    symbol_lines: list[str] = []
    for item in symbols[:20]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        functions = ", ".join(str(name) for name in item.get("functions", [])[:8]) if isinstance(item.get("functions"), list) else ""
        classes = ", ".join(str(name) for name in item.get("classes", [])[:8]) if isinstance(item.get("classes"), list) else ""
        skipped = str(item.get("skipped") or "")
        detail = skipped or f"functions=[{functions}] classes=[{classes}]"
        symbol_lines.append(f"- {path}: {detail}")
    ranked_lines: list[str] = []
    for item in ranked_files[:20]:
        if not isinstance(item, dict):
            continue
        reasons = ", ".join(str(reason) for reason in item.get("reasons", [])[:4]) if isinstance(item.get("reasons"), list) else ""
        ranked_lines.append(f"- {item.get('path')}: score={item.get('score')} reasons=[{reasons}]")
    link_lines: list[str] = []
    for item in test_source_links[:20]:
        if not isinstance(item, dict):
            continue
        sources = ", ".join(str(path) for path in item.get("source_paths", [])[:8]) if isinstance(item.get("source_paths"), list) else ""
        link_lines.append(f"- {item.get('test_path')} -> {sources}")
    read_order_lines: list[str] = []
    for item in read_order[:20]:
        if not isinstance(item, dict):
            continue
        read_order_lines.append(f"- {item.get('phase')}: {item.get('path')} ({item.get('reason')})")
    targeted_read_lines: list[str] = []
    for item in targeted_reads[:20]:
        if not isinstance(item, dict):
            continue
        targeted_read_lines.append(
            f"- {item.get('path')}: {item.get('question')} dependents={item.get('dependent_count', 0)}"
        )
    hypothesis_lines: list[str] = []
    for item in hypotheses[:12]:
        if not isinstance(item, dict):
            continue
        evidence = ", ".join(str(value) for value in item.get("evidence", [])[:4]) if isinstance(item.get("evidence"), list) else ""
        hypothesis_lines.append(f"- [{item.get('confidence')}] {item.get('hypothesis')} evidence=[{evidence}] risk={item.get('risk')}")
    impact_lines: list[str] = []
    for item in impact_matrix[:12]:
        if not isinstance(item, dict):
            continue
        tests_for_item = ", ".join(str(value) for value in item.get("linked_tests", [])[:5]) if isinstance(item.get("linked_tests"), list) else ""
        impact_lines.append(
            f"- {item.get('path')}: impact={item.get('impact_level')} dependents={item.get('dependent_count', 0)} tests=[{tests_for_item}]"
        )
    risk_lines: list[str] = []
    for item in risk_register[:12]:
        if not isinstance(item, dict):
            continue
        risk_lines.append(f"- [{item.get('severity')}] {item.get('risk')}: {item.get('mitigation')}")
    acceptance_lines: list[str] = []
    for item in acceptance_criteria[:12]:
        if not isinstance(item, dict):
            continue
        acceptance_lines.append(f"- {item.get('criterion')}: {item.get('verification')}")
    test_strategy_lines: list[str] = []
    if isinstance(test_strategy.get("primary_commands"), list):
        test_strategy_lines.extend(f"- primary: {item}" for item in test_strategy.get("primary_commands", [])[:8])
    if isinstance(test_strategy.get("linked_test_targets"), list):
        test_strategy_lines.extend(f"- linked: {item}" for item in test_strategy.get("linked_test_targets", [])[:8])
    if isinstance(test_strategy.get("fallback_checks"), list):
        test_strategy_lines.extend(f"- fallback: {item}" for item in test_strategy.get("fallback_checks", [])[:8])
    content = "\n".join(
        [
            "# Ceraxia Change Plan",
            "",
            f"Goal: {goal}",
            "",
            "## Scope",
            "- Inspect the named task and constrain edits to the smallest coherent module set.",
            "- Preserve user changes and expose blockers instead of guessing.",
            "",
            "## Candidate Files",
            *[f"- {item}" for item in candidates[:30]],
            "",
            "## Ranked Repo Map",
            *ranked_lines,
            "",
            "## Test Source Links",
            *link_lines,
            "",
            "## Recommended Read Order",
            *read_order_lines,
            "",
            "## Targeted Reading Plan",
            *targeted_read_lines,
            "",
            "## Hypothesis Log",
            *hypothesis_lines,
            "",
            "## Problem Statement",
            f"- status: {problem_statement.get('status')}",
            f"- observed_problem: {problem_statement.get('observed_problem')}",
            *[
                f"- success_criteria: {item}"
                for item in problem_statement.get("success_criteria", [])
                if isinstance(item, str)
            ],
            f"- ambiguity_policy: {problem_statement.get('ambiguity_policy')}",
            "",
            "## Design Decision Seed",
            *[f"- {item}" for item in decision_seed[:12]],
            "",
            "## Architecture Options",
            *[
                f"- {item.get('id')}: recommended={item.get('recommended')} summary={item.get('summary')}"
                for item in architecture_options.get("options", [])
                if isinstance(item, dict)
            ],
            f"- selection_rule: {architecture_options.get('selection_rule')}",
            "",
            "## Architecture Decision Record",
            f"- status: {architecture_decision_record.get('status')}",
            f"- decision: {architecture_decision_record.get('decision')}",
            *[
                f"- driver: {item}"
                for item in architecture_decision_record.get("drivers", [])
                if isinstance(item, str)
            ],
            *[
                f"- rejected: {item.get('option')} because {item.get('rejected_because')}"
                for item in architecture_decision_record.get("alternatives_considered", [])
                if isinstance(item, dict)
            ],
            f"- rollback: {architecture_decision_record.get('rollback')}",
            "",
            "## Repo-Grade Workflow",
            f"- mode: {repo_grade_workflow.get('mode')}",
            *[
                f"- required_pass: {item}"
                for item in repo_grade_workflow.get("required_passes", [])
                if isinstance(item, str)
            ],
            f"- requires_architecture_decision_record: {repo_grade_workflow.get('requires_architecture_decision_record')}",
            f"- requires_focused_and_broad_verification: {repo_grade_workflow.get('requires_focused_and_broad_verification')}",
            f"- requires_pr_summary: {repo_grade_workflow.get('requires_pr_summary')}",
            "",
            "## File Impact Matrix",
            *impact_lines,
            "",
            "## Risk Register",
            *risk_lines,
            "",
            "## Acceptance Criteria",
            *acceptance_lines,
            "",
            "## Test Strategy",
            *test_strategy_lines,
            "",
            "## Test Surface",
            *[f"- {item}" for item in tests[:30]],
            "",
            "## Python Symbol Surface",
            *symbol_lines,
            "",
            "## Suggested Verification",
            *[f"- {item}" for item in suggested_commands[:8]],
            "",
            "## Implementation Policy",
            "- Produce an auditable patch manifest before mutating source files.",
            "- Require verification commands or explicit blockers before final readiness.",
            "",
            "## Task Profile",
            f"- kinds: {', '.join(str(item) for item in task_profile.get('kinds', [])) if isinstance(task_profile.get('kinds'), list) else ''}",
            f"- complexity: {task_profile.get('complexity', '')}",
            *[
                f"- risk: {item}"
                for item in (task_profile.get("risk_flags") if isinstance(task_profile.get("risk_flags"), list) else [])
            ],
            "",
            "## Worker Brief",
            f"- brief: {worker_brief.get('brief', '')}",
            f"- handoff_question: {worker_brief.get('handoff_question', '')}",
            *[
                f"- must_produce: {item}"
                for item in (worker_brief.get("must_produce") if isinstance(worker_brief.get("must_produce"), list) else [])
            ],
            "",
            "## Role Policy",
            f"- role: {role_policy.get('role', '')}",
            f"- authority: {role_policy.get('authority', '')}",
            f"- may_mutate_source: {role_policy.get('may_mutate_source', False)}",
            *[
                f"- required_evidence: {item}"
                for item in (role_policy.get("required_evidence") if isinstance(role_policy.get("required_evidence"), list) else [])
            ],
        ]
    )
    write_text(workspace_root, output_path, content + "\n")
    write_json(workspace_root, sibling_artifact(output_path, "problem_statement.json"), problem_statement)
    write_json(workspace_root, sibling_artifact(output_path, "architecture_options.json"), architecture_options)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Code change plan written.",
        "artifacts": [
            output_path,
            sibling_artifact(output_path, "problem_statement.json"),
            sibling_artifact(output_path, "architecture_options.json"),
        ],
        "confidence": "medium",
    }
