#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eye_of_terror.contracts import build_code_task_contract
from eye_of_terror.inner_circle.ceraxia import plan_code_task
from eye_of_terror.inner_circle.ceraxia_service import make_handler, oversight_template, pipeline_summary, required_workers, resolve_run_dir, task_from_payload
from EyeOfTerror.common_protocol import commander_order, validate_protocol_payload


def request_json(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def ceraxia_command(task: str, task_id: str) -> dict:
    order = commander_order(
        f"mission-{task_id}",
        to="Ceraxia",
        user_request=task,
        commander_intent="Проверить протокольный вход Цераксии через приказ Вармастера.",
        primary_goal=task,
        success_conditions=[
            "governor_plan preserves the commander mission_id",
            "worker_order packets preserve the commander mission_id",
        ],
        constraints=["Do not answer the user directly from the governor layer."],
    )
    validate_protocol_payload(order, expected_type="commander_order")
    return order


def protocol_only_order(task_id: str) -> dict:
    order = commander_order(
        f"mission-{task_id}",
        to="Ceraxia",
        user_request="ПРИКАЗ ВАРМАСТЕРА\nСырой запрос пользователя не должен стать task.",
        commander_intent="Передать Цераксии нормализованную задачу по коду.",
        primary_goal="почини python приложение",
        success_conditions=["governor receives primary_goal as task compatibility text"],
        constraints=["Do not use raw user_request as the transport task."],
    )
    validate_protocol_payload(order, expected_type="commander_order")
    return order


def main() -> int:
    direct_order = protocol_only_order("ceraxia-protocol-direct")
    direct_task, direct_command = task_from_payload({"commander_order": direct_order})
    if (
        not direct_task.startswith(str(direct_order["primary_goal"]))
        or direct_task.startswith("ПРИКАЗ ВАРМАСТЕРА")
        or "Do not use raw user_request as the transport task." not in direct_task
        or direct_command != direct_order
    ):
        raise AssertionError(f"Ceraxia task_from_payload did not stay protocol-first: task={direct_task!r} command={direct_command}")
    try:
        task_from_payload({"task": "сырой обход бригадира"})
    except ValueError as exc:
        if "commander_order is required" not in str(exc):
            raise AssertionError(f"bad direct task rejection: {exc}") from exc
    else:
        raise AssertionError("Ceraxia accepted direct task input without commander_order")
    contract_workers = [
        step.worker
        for step in build_code_task_contract("почини python приложение", task_id="ceraxia-service-test").worker_plan
    ]
    expected_workers = [
        "LogisRepository",
        "MagosStrategos",
        "FerrumPatchwright",
        "OrdinatusVerifier",
        "JudicatorCodicis",
        "SealwrightFinalis",
    ]
    if required_workers() != expected_workers or contract_workers != expected_workers:
        raise AssertionError(f"Ceraxia required workers drifted from contract plan: {required_workers()} {contract_workers}")
    pipeline = pipeline_summary()
    if (
        pipeline.get("kind") != "code_task"
        or pipeline.get("step_count") != 6
        or pipeline.get("steps", [])[0].get("step_id") != "repository_survey"
        or pipeline.get("steps", [])[2].get("expected_artifacts") != ["/work/capabilities/patch_manifest.json"]
        or pipeline.get("steps", [])[3].get("expected_artifacts") != ["/work/capabilities/verification_report.json", "/work/capabilities/repair_loop_state.json"]
        or pipeline.get("steps", [])[5].get("depends_on") != ["code_review"]
    ):
        raise AssertionError(f"bad Ceraxia pipeline summary: {pipeline}")
    oversight = oversight_template()
    if (
        oversight.get("kind") != "code_task_oversight"
        or oversight.get("final_review", {}).get("critic_step") != "code_review"
        or oversight.get("revision_policy", {}).get("final_steps") != ["code_review", "finalize"]
        or len(oversight.get("step_quality_matrix", [])) != 6
        or oversight.get("task_profile", {}).get("complexity") not in {"low", "medium", "high"}
        or len(oversight.get("worker_specialization_briefs", [])) != 6
        or "multi_file_json_marker" not in oversight.get("patch_contract", {}).get("synthesis_modes", [])
    ):
        raise AssertionError(f"bad Ceraxia oversight template: {oversight}")
    role_by_step = {
        item.get("step_id"): item.get("role_policy", {})
        for item in oversight.get("step_quality_matrix", [])
        if isinstance(item, dict)
    }
    if (
        role_by_step.get("implementation", {}).get("authority") != "scoped_source_mutation_from_patch_contract_or_safe_inference"
        or role_by_step.get("implementation", {}).get("may_mutate_source") is not True
        or role_by_step.get("code_review", {}).get("may_mutate_source") is not False
    ):
        raise AssertionError(f"bad Ceraxia role policies: {oversight}")
    local_plan = plan_code_task("почини python приложение", task_id="ceraxia-local-plan-test").to_dict()
    if (
        local_plan.get("task_profile", {}).get("kinds") != ["bugfix"]
        or len(local_plan.get("worker_specialization_briefs", [])) != 6
        or local_plan.get("worker_specialization_briefs", [])[2].get("worker") != "FerrumPatchwright"
    ):
        raise AssertionError(f"Ceraxia plan should expose task profile and worker briefs: {local_plan}")
    repo_grade_plan = plan_code_task(
        "repo-grade architecture refactor migration compatibility 8-15 files with focused and broad verification",
        task_id="ceraxia-repo-grade-plan-test",
    ).to_dict()
    repo_grade_profile = repo_grade_plan.get("task_profile", {})
    repo_grade_briefs = repo_grade_plan.get("worker_specialization_briefs", [])
    if (
        repo_grade_profile.get("workflow_mode") != "repo_grade"
        or "architecture decision record with alternatives and tradeoffs"
        not in repo_grade_profile.get("repo_grade_required_evidence", [])
        or "architecture decision record" not in repo_grade_briefs[1].get("must_produce", [])
        or "broad verification or blocker" not in repo_grade_briefs[3].get("must_produce", [])
        or "pr_summary" not in repo_grade_briefs[5].get("must_produce", [])
    ):
        raise AssertionError(f"Ceraxia repo-grade plan should expose architecture workflow evidence: {repo_grade_plan}")
    patch_contract = local_plan.get("patch_contract", {})
    if (
        "CERAXIA_FILES" not in patch_contract.get("input_markers", [])
        or "operation batches are atomic and roll back earlier mutations on failure" not in patch_contract.get("safety_gates", [])
        or "natural language replace inference requires explicit backtick-delimited path, old text, and new text" not in patch_contract.get("safety_gates", [])
        or "natural language add-function inference requires explicit backtick-delimited path, function name, and safe return literal" not in patch_contract.get("safety_gates", [])
        or "natural language add-function inference blocks duplicate Python function definitions" not in patch_contract.get("safety_gates", [])
        or "test-inferred return mismatch mode requires exactly one import/assertEqual literal candidate and one simple source return literal" not in patch_contract.get("safety_gates", [])
        or "test-inferred missing function mode requires exactly one import/assertEqual literal candidate" not in patch_contract.get("safety_gates", [])
        or "test-inferred arithmetic return mode requires exactly one two-argument assertEqual arithmetic candidate" not in patch_contract.get("safety_gates", [])
        or "python -m unittest" not in patch_contract.get("verification_allowlist", [])
        or "python_symbol_extraction" not in patch_contract.get("repository_intelligence", [])
        or "append" not in patch_contract.get("operation_types", [])
    ):
        raise AssertionError(f"bad Ceraxia local patch contract: {patch_contract}")
    ferrum_contract = local_plan.get("resolved_workers", {}).get("FerrumPatchwright", {}).get("role_contract", {})
    if (
        ferrum_contract.get("owned_step") != "implementation"
        or ferrum_contract.get("authority") != "scoped_source_mutation_from_patch_contract_or_safe_inference"
    ):
        raise AssertionError(f"Ceraxia plan should expose worker role contracts: {local_plan}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        if resolve_run_dir(root / "runs", "child", "task").resolve() != (root / "runs" / "child").resolve():
            raise AssertionError("relative run_dir did not resolve under default root")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(root / "runs"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            health = request_json(base + "/health")
            if not health.get("ok") or health.get("governor") != "Ceraxia":
                raise AssertionError(f"bad health: {health}")
            capabilities = request_json(base + "/capabilities")
            if (
                capabilities.get("governor") != "Ceraxia"
                or capabilities.get("summary", {}).get("step_count") != 6
                or capabilities.get("worker_availability", {}).get("ok") is not True
                or len(capabilities.get("worker_specialization_briefs", [])) != 6
                or "write_file" not in capabilities.get("patch_contract", {}).get("operation_types", [])
                or "model_backed_governor_planning" not in capabilities.get("capabilities", [])
                or capabilities.get("model_brain", {}).get("kind") != "eye_of_terror_model_brain"
            ):
                raise AssertionError(f"bad capabilities: {capabilities}")
            try:
                request_json(base + "/plan", {"task": "почини python приложение", "task_id": "ceraxia-raw-reject"})
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                rejected = json.loads(exc.read().decode("utf-8"))
                if "commander_order is required" not in rejected.get("error", ""):
                    raise AssertionError(f"bad raw task rejection: {rejected}")
            else:
                raise AssertionError("Ceraxia /plan accepted raw task without commander_order")
            plan = request_json(
                base + "/plan",
                {
                    "task": "почини python приложение",
                    "task_id": "ceraxia-http-test",
                    "commander_order": ceraxia_command("почини python приложение", "ceraxia-http-test"),
                },
            )
            if (
                not plan.get("ok")
                or plan.get("contract", {}).get("assigned_governor") != "Ceraxia"
                or plan.get("pipeline", {}).get("step_count") != 6
                or plan.get("phase") != "plan_ready"
                or plan.get("task_profile", {}).get("kinds") != ["bugfix"]
                or plan.get("worker_specialization_briefs", [])[5].get("worker") != "SealwrightFinalis"
                or plan.get("actions", {}).get("next_action", {}).get("kind") != "prepare_run"
                or "explicit_json_patch" not in plan.get("patch_contract", {}).get("synthesis_modes", [])
                or "natural_language_simple_replace" not in plan.get("patch_contract", {}).get("synthesis_modes", [])
                or "natural_language_add_function" not in plan.get("patch_contract", {}).get("synthesis_modes", [])
                or "test_inferred_return_mismatch" not in plan.get("patch_contract", {}).get("synthesis_modes", [])
                or "test_inferred_missing_function" not in plan.get("patch_contract", {}).get("synthesis_modes", [])
                or "test_inferred_arithmetic_return" not in plan.get("patch_contract", {}).get("synthesis_modes", [])
                or plan.get("resolved_workers", {}).get("FerrumPatchwright", {}).get("role_contract", {}).get("owned_step") != "implementation"
                or plan.get("model_brain", {}).get("status") != "answered"
            ):
                raise AssertionError(f"bad plan: {plan}")
            protocol_only_plan = request_json(
                base + "/plan",
                {"task_id": "ceraxia-protocol-only-plan", "commander_order": protocol_only_order("ceraxia-protocol-only-plan")},
            )
            if (
                not protocol_only_plan.get("ok")
                or protocol_only_plan.get("governor_plan", {}).get("understanding") != "почини python приложение"
                or "task" in protocol_only_plan.get("actions", {}).get("next_action", {}).get("body", {})
            ):
                raise AssertionError(f"Ceraxia /plan did not use commander_order as authority: {protocol_only_plan}")
            callable_contract = request_json(
                base + "/callable_contract",
                {
                    "task": "почини python приложение",
                    "task_id": "ceraxia-callable-test",
                    "commander_order": ceraxia_command("почини python приложение", "ceraxia-callable-test"),
                    "repo_path": str(root / "sample-repo"),
                    "constraints": {"allowed_commands": ["python -m unittest discover"]},
                },
            )
            required_final_fields = callable_contract.get("final_package_schema", {}).get("required_fields", [])
            if (
                not callable_contract.get("ok")
                or callable_contract.get("callable_kind") != "specialized_code_brigade"
                or "CERAXIA_TARGET_REPO:" not in callable_contract.get("normalized_task", "")
                or "patch_package" not in required_final_fields
                or "pr_summary" not in required_final_fields
                or callable_contract.get("next_action", {}).get("endpoint") != "POST /prepare_run"
                or callable_contract.get("model_brain", {}).get("status") != "answered"
            ):
                raise AssertionError(f"bad callable contract: {callable_contract}")
            run_dir = root / "runs" / "custom-run"
            prepared = request_json(
                base + "/prepare_run",
                {
                    "task": "почини python приложение",
                    "task_id": "ceraxia-http-test",
                    "run_dir": str(run_dir),
                    "commander_order": ceraxia_command("почини python приложение", "ceraxia-http-test"),
                },
            )
            if (
                not prepared.get("ok")
                or prepared.get("governor") != "Ceraxia"
                or not (run_dir / "dispatch" / "repository_survey.json").exists()
                or not (run_dir / "oversight.json").exists()
                or prepared.get("model_brain", {}).get("status") != "answered"
            ):
                raise AssertionError(f"bad prepared run: {prepared}")
            implementation_dispatch = json.loads((run_dir / "dispatch" / "implementation.json").read_text(encoding="utf-8"))
            expectations = implementation_dispatch.get("request", {}).get("quality_expectations", {})
            if (
                expectations.get("task_profile", {}).get("kinds") != ["bugfix"]
                or expectations.get("worker_brief", {}).get("worker") != "FerrumPatchwright"
                or not expectations.get("worker_brief", {}).get("must_produce")
            ):
                raise AssertionError(f"Ceraxia dispatch should carry task profile and worker brief: {implementation_dispatch}")
            protocol_run_dir = root / "runs" / "protocol-run"
            protocol_task = "почини python приложение"
            protocol_prepared = request_json(
                base + "/prepare_run",
                {
                    "task": protocol_task,
                    "task_id": "ceraxia-protocol-test",
                    "run_dir": str(protocol_run_dir),
                    "commander_order": ceraxia_command(protocol_task, "ceraxia-protocol-test"),
                },
            )
            protocol_plan = json.loads((protocol_run_dir / "governor_plan.json").read_text(encoding="utf-8"))
            protocol_dispatch = json.loads((protocol_run_dir / "dispatch" / "implementation.json").read_text(encoding="utf-8"))
            if (
                not protocol_prepared.get("ok")
                or protocol_plan.get("mission_id") != "mission-ceraxia-protocol-test"
                or protocol_dispatch.get("worker_order", {}).get("mission_id") != "mission-ceraxia-protocol-test"
                or protocol_dispatch.get("request", {}).get("worker_order", {}).get("mission_id") != "mission-ceraxia-protocol-test"
            ):
                raise AssertionError(
                    "Ceraxia /prepare_run did not preserve commander_order mission_id: "
                    f"prepared={protocol_prepared} plan={protocol_plan} dispatch={protocol_dispatch}"
                )
            try:
                request_json(
                    base + "/prepare_run",
                    {
                        "task": "почини python приложение",
                        "task_id": "ceraxia-escape-test",
                        "run_dir": str(root / "escape"),
                        "commander_order": ceraxia_command("почини python приложение", "ceraxia-escape-test"),
                    },
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
            else:
                raise AssertionError("prepare_run should reject run_dir outside default root")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] Ceraxia service")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
