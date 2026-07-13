#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from eye_of_terror import campaigns as campaigns_module
from eye_of_terror import mission_control, task_prepare, warmaster_gateway
from eye_of_terror.campaigns import (
    FINAL_REPORT_FILE,
    NATIVE_RESEARCH_RESULT_CONTRACT,
    campaign_preflight,
    campaign_state,
    create_handoff,
    create_subrun,
    decompose_task,
    final_review,
    list_campaigns,
    prepare_campaign,
    validate_campaign_plan,
)
from eye_of_terror.inner_circle import ceraxia_service, iskandar_service
from eye_of_terror.ledger import TaskLedger
from eye_of_terror.mission_control import record_warmaster_acceptance
from eye_of_terror.native_code_run import (
    NATIVE_EXECUTION,
    is_native_code_run,
    validate_native_code_run_package,
)
from eye_of_terror.native_research_run import (
    NATIVE_RESEARCH_EXECUTION,
    is_native_research_run,
    validate_native_research_run_package,
)
from eye_of_terror.routing import RouteDecision
from eye_of_terror.warmaster_gateway import make_handler


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request_json(url: str, payload: dict | None = None) -> dict:
    if payload is None:
        with urllib.request.urlopen(url, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    else:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise AssertionError(f"response is not an object: {data}")
    return data


def route_for_self_test(message: str) -> RouteDecision:
    if "evidence-grounded" in message:
        governor = "IskandarKhayon"
        kind = "research"
        supporting: list[dict] = []
        decomposition = False
    elif "senior engineer" in message or "handoff" in message:
        governor = "Ceraxia"
        kind = "code"
        supporting = []
        decomposition = False
    else:
        governor = "Ceraxia"
        kind = "code"
        supporting = [{"name": "IskandarKhayon", "active": True, "kind": "research"}]
        decomposition = True
    matched = [{"name": governor, "active": True, "kind": kind}]
    return RouteDecision(
        True,
        governor,
        kind,
        "self-test deterministic route",
        matched_governors=matched,
        supporting_governors=supporting,
        requires_decomposition=decomposition,
        model_brain={"ok": True, "status": "self_test"},
        llm_route={"ok": True, "governor": governor, "kind": kind},
    )


def command_model_answer(owner: str, _role: str, payload: dict, **_kwargs: object) -> dict:
    if owner == "WarmasterAcceptance":
        content = {
            "accepted": True,
            "reason": "Self-test evidence satisfies the commander order.",
            "required_revision": {},
            "escalate_to_user": False,
        }
    else:
        goal = str(payload.get("user_request") or payload.get("mission_request") or "self-test goal")
        content = {
            "commander_intent": "Complete the bounded campaign subrun.",
            "primary_goal": goal,
            "success_conditions": ["Executable evidence is accepted."],
            "constraints": ["Preserve unrelated changes."],
            "escalate_to_user_if": ["A product decision is required."],
        }
    return {"ok": True, "content": json.dumps(content, ensure_ascii=False)}


def ceraxia_model_answer() -> dict:
    return {
        "ok": True,
        "content": json.dumps(
            {
                "decision": "delegate",
                "mission_intent": "Implement the campaign code subrun without scope drift.",
                "priorities": ["Correct behavior", "Executable verification"],
                "constraints": ["Consume the campaign handoff."],
                "success_conditions": ["The requested behavior is verified."],
                "tradeoffs": ["Prefer a bounded change."],
                "escalation_conditions": ["A product decision is required."],
            },
            ensure_ascii=False,
        ),
    }


def iskandar_model_answer() -> dict:
    return {
        "ok": True,
        "status": "answered",
        "content": {
            "decision": "delegate",
            "research_objective": "Build the source-backed implementation basis.",
            "depth": "standard",
            "source_policy": "balanced",
            "error_tolerance": "strict",
            "answer_mode": "direct_answer",
            "priorities": ["Prefer direct evidence."],
            "allowed_source_classes": ["primary_source", "official_documentation"],
            "prohibited_source_classes": ["machine_generated_summary"],
            "constraints": [],
            "success_conditions": [],
            "output_requirements": ["Return an evidence-bound implementation brief."],
            "escalation_conditions": [],
            "clarification_question": "",
        },
    }


def healthy_skitarii_backend() -> dict:
    return {
        "name": "SkitariiWarband",
        "kind": "vm_isolated_code_warband",
        "endpoint": "http://127.0.0.1:7200",
        "healthy": True,
        "status": "healthy",
        "lifecycle": "active",
        "health": {"status": "ok", "vm_alive": True, "process_boundary_ready": True},
        "error": "",
    }


def healthy_research_backend() -> dict:
    return {
        "name": "ResearchWarband",
        "kind": "native_research_warband",
        "endpoint": "http://127.0.0.1:7201",
        "health_endpoint": "http://127.0.0.1:7201/health",
        "healthy": True,
        "status": "healthy",
        "health": {"ok": True},
        "error": "",
        "dispatch_owner": "native_research_backend_router",
        "contract_relation": "executes one native Iskandar-delegated research mission",
    }


def open_test_mission(run_root: Path):
    protocol_root = run_root / "_mission_protocol"

    def opener(_warmaster_root: Path, message: str, task_id: str | None, source_channel: str = "main_chat") -> dict:
        return mission_control.open_mission(protocol_root, message, task_id, source_channel=source_channel)

    return opener


def prepare_with_test_governors(ceraxia_port: int, iskandar_port: int):
    original_prepare = campaigns_module.prepare_task
    ceraxia = SimpleNamespace(name="Ceraxia", port=ceraxia_port)
    iskandar = SimpleNamespace(name="IskandarKhayon", port=iskandar_port)

    def prepare(
        message: str,
        task_id: str | None,
        run_root: Path,
        governor_transport: str = "local",
        governor_host: str = "127.0.0.1",
        forced_governor: str | None = None,
        commander_order: dict | None = None,
        require_commander_order: bool = False,
    ) -> dict:
        if forced_governor == "Ceraxia":
            if governor_transport != "http":
                raise AssertionError("campaign bypassed the live Ceraxia leadership service")
            return task_prepare.prepare_native_ceraxia_via_service(
                message,
                task_id,
                run_root,
                ceraxia,
                host=governor_host,
                port=ceraxia_port,
                commander_order=commander_order,
                require_commander_order=require_commander_order,
            )
        if forced_governor == "IskandarKhayon":
            if governor_transport != "http":
                raise AssertionError("campaign bypassed the live Iskandar leadership service")
            return task_prepare.prepare_native_iskandar_via_service(
                message,
                task_id,
                run_root,
                iskandar,
                host=governor_host,
                port=iskandar_port,
                commander_order=commander_order,
                require_commander_order=require_commander_order,
            )
        return original_prepare(
            message,
            task_id,
            run_root,
            governor_transport=governor_transport,
            governor_host=governor_host,
            forced_governor=forced_governor,
            commander_order=commander_order,
            require_commander_order=require_commander_order,
        )

    return prepare


def portable_campaign_artifact_status(original):
    """Keep this campaign test runnable on Windows where dir_fd is absent.

    The hardened artifact reader has its own Linux barriers.  Here we only need
    to prove that the campaign consumes Skitarii's dynamic logical paths.
    """

    def status(ledger: dict) -> dict:
        result = ledger.get("result") if isinstance(ledger.get("result"), dict) else {}
        if result.get("final_step") != "skitarii":
            return original(ledger)
        root = Path(str(result.get("artifact_root") or ""))
        items = []
        for raw_path in result.get("artifacts") if isinstance(result.get("artifacts"), list) else []:
            logical = str(raw_path)
            target = root.joinpath(*Path(logical).parts)
            items.append(
                {
                    "path": logical,
                    "exists": target.is_file(),
                    "bytes": target.stat().st_size if target.is_file() else 0,
                    "errors": [] if target.is_file() else ["artifact is missing"],
                    "source": "result",
                }
            )
        return {"artifacts": items}

    return status


def make_completed_research_run(run_root: Path, task_id: str) -> None:
    run_dir = run_root / task_id
    if not is_native_research_run(run_dir) or validate_native_research_run_package(run_dir):
        raise AssertionError(
            "research campaign fixture is not a valid native Iskandar-to-ResearchWarband run",
        )
    contract = json.loads((run_dir / "contract.json").read_text(encoding="utf-8"))
    mission_id = str(contract["mission_id"])
    evidence_ledger = {
        "sources": [
            {
                "id": "source-1",
                "uri": "https://example.invalid/risc-v",
                "title": "RISC-V primary source",
            }
        ],
        "spans": [{"id": "span-1", "source_id": "source-1"}],
        "claims": [{"id": "claim-1", "text": "The implementation basis is supported."}],
        "evidence_edges": [{"claim_id": "claim-1", "span_id": "span-1"}],
        "derivations": [],
        "conflicts": [],
        "gaps": [],
        "final_claim_refs": ["claim-1"],
    }
    raw_result = {
        "runner_contract_version": "research-warband-runner/v1",
        "outcome": "accepted",
        "reason": "accepted",
        "external_evaluator_result": {
            "contract_version": "research-result/v1",
            "mission_id": mission_id,
            "status": "accepted",
            "accepted": True,
            "final_text": "Verified source-backed implementation brief.",
            "question": "",
            "ledger": evidence_ledger,
            "search_log": [
                {"query": "RISC-V official specification"},
                {"acquired_uri": "https://example.invalid/risc-v"},
            ],
        },
        "pipeline_audit": {
            "searched_queries": ["RISC-V official specification"],
            "acquired_uris": ["https://example.invalid/risc-v"],
            "semantic_reviews": [],
            "verification_report": {
                "accepted": True,
                "integrity_ok": True,
                "issues": [],
            },
            "rounds_used": 1,
            "model_calls": 4,
            "diagnostics": {},
            "persistent_graph_written": False,
            "runtime_attestation_sha256": "a" * 64,
        },
    }
    ledger = TaskLedger.load(run_dir / "task_ledger.json")
    ledger.set_result(
        {
            "ok": True,
            "task_id": task_id,
            "phase": "completed",
            "status": "completed",
            "final_step": "research_warband",
            "summary": "Verified source-backed implementation brief.",
            "artifacts": [],
            "artifact_root": str(run_dir.resolve()),
            "needs_user": False,
            "question": "",
            "next_action": {},
            "research_warband_mission_id": mission_id,
            "research_result": raw_result,
            "via": "research_warband",
        }
    )
    ledger.data["research_warband_mission"] = {
        "id": mission_id,
        "request_sha256": "b" * 64,
        "status": "done",
        "service": "http://127.0.0.1:7201",
        "attempt": 1,
        "inflight": False,
        "cleanup_complete": True,
    }
    ledger.save()
    ledger.set_status("completed")


def make_completed_code_run(run_root: Path, task_id: str) -> None:
    run_dir = run_root / task_id
    if not is_native_code_run(run_dir) or validate_native_code_run_package(run_dir):
        raise AssertionError("code campaign fixture is not a valid native Ceraxia-to-Skitarii run")
    patch_path = run_dir / "work" / "skitarii.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text("diff --git a/demo.py b/demo.py\n", encoding="utf-8")
    ledger = TaskLedger.load(run_dir / "task_ledger.json")
    ledger.set_result(
        {
            "ok": True,
            "status": "completed",
            "phase": "completed",
            "summary": "Verified Skitarii patch applied successfully.",
            "final_step": "skitarii",
            "artifact_root": str(run_dir.resolve()),
            "artifacts": ["work/skitarii.patch"],
            "patch_stage": {
                "applies_to_live": True,
                "tests_pass_in_worktree": True,
                "applied_to_live": True,
                "post_apply_tests_passed": True,
                "rolled_back": False,
            },
            "ready_to_apply": False,
            "next_action": {},
        }
    )
    ledger.set_status("completed")


def main() -> int:
    os.environ["RESEARCH_WARBAND_BEARER_TOKEN"] = (
        "campaign-research-warband-test-token-0123456789abcdef"
    )
    message = "собери обзор источников по RISC-V и реализуй python демо код"
    campaigns_module.route_message = route_for_self_test
    mission_control.route_message = route_for_self_test
    mission_control.request_model_decision = command_model_answer
    warmaster_gateway.request_model_decision = (
        lambda *_args, **_kwargs: {"ok": True, "content": "{}", "status": "self_test"}
    )
    campaigns_module.artifact_status = portable_campaign_artifact_status(
        campaigns_module.artifact_status,
    )
    plan = decompose_task(message, campaign_id="campaign-self-test")
    if validate_campaign_plan(plan):
        raise AssertionError(f"campaign plan should validate: {validate_campaign_plan(plan)}")
    if [item["id"] for item in plan["subruns"]] != ["research", "implementation"]:
        raise AssertionError(f"unexpected subrun order: {plan['subruns']}")
    if plan["subruns"][1]["depends_on"] != ["research"]:
        raise AssertionError(f"implementation must depend on research: {plan['subruns'][1]}")
    research_plan = plan["subruns"][0]
    if (
        research_plan.get("execution") != NATIVE_RESEARCH_EXECUTION
        or research_plan.get("expected_artifacts") != []
        or research_plan.get("result_contract") != NATIVE_RESEARCH_RESULT_CONTRACT
        or "/work/research/" in json.dumps(research_plan)
    ):
        raise AssertionError(
            f"research is not a native ResearchWarband result boundary: {research_plan}",
        )
    implementation_plan = plan["subruns"][1]
    if (
        implementation_plan.get("execution") != NATIVE_EXECUTION
        or implementation_plan.get("expected_artifacts") != []
        or implementation_plan.get("result_contract", {}).get("kind") != "skitarii_bridge_result"
        or "/work/ceraxia/" in json.dumps(implementation_plan)
    ):
        raise AssertionError(f"implementation is not a native Skitarii result boundary: {implementation_plan}")

    preflight = campaign_preflight(message, campaign_id="campaign-self-test")
    if not preflight.get("ok") or preflight.get("next_action", {}).get("endpoint") != "POST /campaign":
        raise AssertionError(f"bad campaign preflight: {preflight}")

    with tempfile.TemporaryDirectory() as temp_dir:
        run_root = Path(temp_dir)
        campaigns_module.open_mission = open_test_mission(run_root)
        ceraxia_handler = ceraxia_service.make_handler(run_root)
        ceraxia_server = ThreadingHTTPServer(("127.0.0.1", 0), ceraxia_handler)
        ceraxia_thread = threading.Thread(target=ceraxia_server.serve_forever, daemon=True)
        ceraxia_service.skitarii_backend_health = lambda *_args, **_kwargs: healthy_skitarii_backend()
        ceraxia_service.request_model_decision = lambda *_args, **_kwargs: ceraxia_model_answer()
        ceraxia_thread.start()
        iskandar_service.research_warband_backend_health = (
            lambda *_args, **_kwargs: healthy_research_backend()
        )
        iskandar_service.request_model_decision = (
            lambda *_args, **_kwargs: iskandar_model_answer()
        )
        iskandar_handler = iskandar_service.make_handler(run_root)
        iskandar_server = ThreadingHTTPServer(("127.0.0.1", 0), iskandar_handler)
        iskandar_thread = threading.Thread(
            target=iskandar_server.serve_forever,
            daemon=True,
        )
        iskandar_thread.start()
        campaigns_module.prepare_task = prepare_with_test_governors(
            ceraxia_server.server_port,
            iskandar_server.server_port,
        )
        prepared = prepare_campaign(run_root, message, campaign_id="campaign-self-test")
        if not prepared.get("ok") or prepared.get("state", {}).get("status") != "planned":
            raise AssertionError(f"bad prepared campaign: {prepared}")
        campaign_ref = json.loads((run_root / "_campaigns" / "campaign-self-test" / "mission_ref.json").read_text(encoding="utf-8"))
        if (
            not campaign_ref.get("mission_id")
            or campaign_ref.get("mission_id") != prepared.get("mission", {}).get("mission_id")
            or campaign_ref.get("mission_id") != prepared.get("state", {}).get("mission_id")
            or not Path(str(campaign_ref.get("mission_dir") or "")).joinpath("commander_order.json").exists()
        ):
            raise AssertionError(f"campaign mission_ref missing: {prepared}")
        campaigns = list_campaigns(run_root)
        if len(campaigns) != 1 or campaigns[0].get("campaign_id") != "campaign-self-test":
            raise AssertionError(f"campaign list failed: {campaigns}")

        research_created = create_subrun(run_root, "campaign-self-test", "research")
        if (
            not research_created.get("ok")
            or research_created.get("task", {}).get("governor") != "IskandarKhayon"
            or research_created.get("governor_transport") != "http"
            or not is_native_research_run(run_root / "campaign-self-test-research")
            or validate_native_research_run_package(
                run_root / "campaign-self-test-research"
            )
            or (run_root / "campaign-self-test-research" / "dispatch").exists()
        ):
            raise AssertionError(f"research subrun create failed: {research_created}")
        research_ref = json.loads((run_root / "campaign-self-test-research" / "mission_ref.json").read_text(encoding="utf-8"))
        if research_ref.get("mission_id") != research_created.get("mission", {}).get("mission_id"):
            raise AssertionError(f"research subrun mission_ref missing: {research_created}")

        make_completed_research_run(run_root, "campaign-self-test-research")
        pre_acceptance_state = campaign_state(run_root, "campaign-self-test")
        pre_acceptance_handoff = pre_acceptance_state.get("state", {}).get("handoffs", {}).get("research_to_implementation", {})
        if pre_acceptance_handoff.get("status") == "ready":
            raise AssertionError(f"handoff became ready before Warmaster acceptance: {pre_acceptance_state}")
        pre_acceptance_subrun = pre_acceptance_state.get("state", {}).get("subruns", {}).get("research", {})
        if pre_acceptance_subrun.get("protocol_completion", {}).get("ok") is True:
            raise AssertionError(f"subrun protocol completed before Warmaster acceptance: {pre_acceptance_subrun}")
        research_acceptance = record_warmaster_acceptance(run_root / "campaign-self-test-research")
        if not research_acceptance.get("accepted"):
            raise AssertionError(f"research acceptance failed: {research_acceptance}")
        refreshed = campaign_state(run_root, "campaign-self-test")
        handoff = refreshed.get("state", {}).get("handoffs", {}).get("research_to_implementation", {})
        if handoff.get("status") != "ready" or not Path(str(handoff.get("path") or "")).exists():
            raise AssertionError(f"handoff was not created: {refreshed}")
        if handoff.get("checks", [{}])[1].get("name") != "source_protocol_completed":
            raise AssertionError(f"handoff did not record source protocol check: {handoff}")
        handoff_payload = json.loads(
            Path(str(handoff["path"])).read_text(encoding="utf-8"),
        )
        if (
            handoff_payload.get("source_native_completion", {}).get("kind")
            != "research_warband_bridge_result"
            or not handoff_payload.get("source_evidence_ledger", {}).get("sources")
            or not handoff_payload.get("source_manifest", {}).get("search_log")
            or handoff_payload.get("required_artifacts") != []
            or "/work/research/" in json.dumps(handoff_payload)
        ):
            raise AssertionError(
                f"handoff did not consume the native evidence result: {handoff_payload}",
            )

        code_created = create_subrun(run_root, "campaign-self-test", "implementation")
        code_ledger = json.loads((run_root / "campaign-self-test-code" / "task_ledger.json").read_text(encoding="utf-8"))
        code_run_dir = run_root / "campaign-self-test-code"
        if (
            not code_created.get("ok")
            or code_created.get("governor_transport") != "http"
            or "research_to_implementation.json" not in code_ledger.get("goal", "")
            or not is_native_code_run(code_run_dir)
            or validate_native_code_run_package(code_run_dir)
            or (code_run_dir / "dispatch").exists()
        ):
            raise AssertionError(f"implementation subrun did not receive handoff: {code_created}")
        code_ref = json.loads((run_root / "campaign-self-test-code" / "mission_ref.json").read_text(encoding="utf-8"))
        if code_ref.get("mission_id") != code_created.get("mission", {}).get("mission_id"):
            raise AssertionError(f"implementation subrun mission_ref missing: {code_created}")
        make_completed_code_run(run_root, "campaign-self-test-code")
        before_code_acceptance = campaign_state(run_root, "campaign-self-test")
        if before_code_acceptance.get("state", {}).get("status") == "completed":
            raise AssertionError(f"campaign completed before implementation Warmaster acceptance: {before_code_acceptance}")
        code_acceptance = record_warmaster_acceptance(run_root / "campaign-self-test-code")
        if not code_acceptance.get("accepted"):
            raise AssertionError(f"implementation acceptance failed: {code_acceptance}")
        final_state = campaign_state(run_root, "campaign-self-test")
        if final_state.get("state", {}).get("status") != "completed":
            raise AssertionError(f"campaign final review should complete: {final_state}")
        if not (Path(temp_dir) / "_campaigns" / "campaign-self-test" / FINAL_REPORT_FILE).exists():
            raise AssertionError("final report was not written")

        report = final_review(run_root, "campaign-self-test", final_state["plan"], final_state["state"])
        if report.get("status") != "completed":
            raise AssertionError(f"final review failed: {report}")
        research_deliverable = report.get("deliverables", {}).get("research", {})
        if (
            research_deliverable.get("kind") != "research_warband_bridge_result"
            or research_deliverable.get("result", {}).get("final_step")
            != "research_warband"
            or not research_deliverable.get("evidence_ledger", {}).get("sources")
            or not research_deliverable.get("source_manifest", {}).get("search_log")
            or research_deliverable.get("service_mission", {}).get("cleanup_complete")
            is not True
        ):
            raise AssertionError(
                f"campaign final report did not consume native research evidence: {research_deliverable}",
            )
        native_deliverable = report.get("deliverables", {}).get("implementation", {})
        if (
            native_deliverable.get("kind") != "skitarii_bridge_result"
            or native_deliverable.get("result", {}).get("final_step") != "skitarii"
            or native_deliverable.get("artifact_status", [{}])[0].get("exists") is not True
            or "/work/ceraxia/" in json.dumps(native_deliverable)
        ):
            raise AssertionError(f"campaign final report did not consume native artifacts: {native_deliverable}")
        explicit_handoff = create_handoff(run_root, "campaign-self-test", final_state["plan"], final_state["state"], "research_to_implementation")
        if explicit_handoff.get("status") != "ready":
            raise AssertionError(f"explicit handoff failed: {explicit_handoff}")
        ceraxia_server.shutdown()
        ceraxia_server.server_close()
        ceraxia_thread.join(timeout=5)
        iskandar_server.shutdown()
        iskandar_server.server_close()
        iskandar_thread.join(timeout=5)

    with tempfile.TemporaryDirectory() as temp_dir:
        run_root = Path(temp_dir)
        campaigns_module.open_mission = open_test_mission(run_root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            capabilities = request_json(base + "/capabilities")
            if "POST /campaign_preflight" not in capabilities.get("endpoints", []):
                raise AssertionError(f"campaign endpoints missing from capabilities: {capabilities}")
            http_preflight = request_json(base + "/campaign_preflight", {"message": message, "campaign_id": "campaign-http-test"})
            if not http_preflight.get("ok") or http_preflight.get("next_action", {}).get("endpoint") != "POST /campaign":
                raise AssertionError(f"bad HTTP campaign preflight: {http_preflight}")
            http_campaign = request_json(base + "/campaign", {"message": message, "campaign_id": "campaign-http-test"})
            if not http_campaign.get("ok"):
                raise AssertionError(f"bad HTTP campaign create: {http_campaign}")
            http_list = request_json(base + "/campaigns")
            if not any(item.get("campaign_id") == "campaign-http-test" for item in http_list.get("campaigns", [])):
                raise AssertionError(f"HTTP campaign list missing created campaign: {http_list}")
            http_state = request_json(base + "/campaigns/campaign-http-test")
            if http_state.get("state", {}).get("status") != "planned":
                raise AssertionError(f"bad HTTP campaign state: {http_state}")
            http_runs = request_json(base + "/runs")
            if any(item.get("task_id") == "_campaigns" for item in http_runs.get("runs", [])):
                raise AssertionError(f"service campaign dir leaked into runs: {http_runs}")
            http_cancel = request_json(base + "/campaigns/campaign-http-test/cancel", {"reason": "self test"})
            if not http_cancel.get("ok") or http_cancel.get("state", {}).get("status") != "cancelled":
                raise AssertionError(f"bad HTTP campaign cancel: {http_cancel}")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] Warmaster campaign orchestration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
