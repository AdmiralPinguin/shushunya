#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from eye_of_terror.campaigns import (
    FINAL_REPORT_FILE,
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
from eye_of_terror.ledger import TaskLedger
from eye_of_terror.warmaster_gateway import make_handler


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request_json(url: str, payload: dict | None = None) -> dict:
    if payload is None:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    else:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise AssertionError(f"response is not an object: {data}")
    return data


def make_completed_research_run(run_root: Path, task_id: str) -> None:
    run_dir = run_root / task_id
    workspace = run_dir / "work"
    write_json(
        run_dir / "status.json",
        {
            "task_id": task_id,
            "status": "completed",
            "governor": "IskandarKhayon",
            "steps": [],
        },
    )
    for name, payload in {
        "research_corpus.json": {"items": [{"source": "example"}]},
        "source_map.json": {"sources": []},
        "synthesis_plan.json": {"sections": []},
        "final_manifest.json": {
            "status": "ready",
            "approved": True,
            "files": [
                {"path": "/work/research/research_corpus.json"},
                {"path": "/work/research/source_map.json"},
                {"path": "/work/research/synthesis_plan.json"},
                {"path": "/work/research/reconstruction_ru.md"},
            ],
        },
    }.items():
        write_json(workspace / "research" / name, payload)
    (workspace / "research" / "reconstruction_ru.md").write_text("brief\n", encoding="utf-8")
    ledger = TaskLedger.create(run_dir / "task_ledger.json", task_id, "research goal", "IskandarKhayon")
    ledger.set_result(
        {
            "ok": True,
            "status": "completed",
            "summary": "done",
            "workspace_root": str(workspace),
            "artifacts": [
                "/work/research/research_corpus.json",
                "/work/research/source_map.json",
                "/work/research/synthesis_plan.json",
                "/work/research/reconstruction_ru.md",
                "/work/research/final_manifest.json",
            ],
        }
    )
    ledger.set_status("completed")


def make_completed_code_run(run_root: Path, task_id: str) -> None:
    run_dir = run_root / task_id
    workspace = run_dir / "work"
    write_json(run_dir / "status.json", {"task_id": task_id, "status": "completed", "governor": "Ceraxia", "steps": []})
    write_json(workspace / "ceraxia" / "final_manifest.json", {"status": "ready", "approved": True, "files": []})
    ledger = TaskLedger.create(run_dir / "task_ledger.json", task_id, "code goal", "Ceraxia")
    ledger.set_result(
        {
            "ok": True,
            "status": "completed",
            "summary": "done",
            "workspace_root": str(workspace),
            "artifacts": ["/work/ceraxia/final_manifest.json"],
        }
    )
    ledger.set_status("completed")


def main() -> int:
    message = "собери обзор источников по RISC-V и реализуй python демо код"
    plan = decompose_task(message, campaign_id="campaign-self-test")
    if validate_campaign_plan(plan):
        raise AssertionError(f"campaign plan should validate: {validate_campaign_plan(plan)}")
    if [item["id"] for item in plan["subruns"]] != ["research", "implementation"]:
        raise AssertionError(f"unexpected subrun order: {plan['subruns']}")
    if plan["subruns"][1]["depends_on"] != ["research"]:
        raise AssertionError(f"implementation must depend on research: {plan['subruns'][1]}")

    preflight = campaign_preflight(message, campaign_id="campaign-self-test")
    if not preflight.get("ok") or preflight.get("next_action", {}).get("endpoint") != "POST /campaign":
        raise AssertionError(f"bad campaign preflight: {preflight}")

    with tempfile.TemporaryDirectory() as temp_dir:
        run_root = Path(temp_dir)
        prepared = prepare_campaign(run_root, message, campaign_id="campaign-self-test")
        if not prepared.get("ok") or prepared.get("state", {}).get("status") != "planned":
            raise AssertionError(f"bad prepared campaign: {prepared}")
        campaigns = list_campaigns(run_root)
        if len(campaigns) != 1 or campaigns[0].get("campaign_id") != "campaign-self-test":
            raise AssertionError(f"campaign list failed: {campaigns}")

        research_created = create_subrun(run_root, "campaign-self-test", "research")
        if not research_created.get("ok") or research_created.get("task", {}).get("governor") != "IskandarKhayon":
            raise AssertionError(f"research subrun create failed: {research_created}")

        make_completed_research_run(run_root, "campaign-self-test-research")
        refreshed = campaign_state(run_root, "campaign-self-test")
        handoff = refreshed.get("state", {}).get("handoffs", {}).get("research_to_implementation", {})
        if handoff.get("status") != "ready" or not Path(str(handoff.get("path") or "")).exists():
            raise AssertionError(f"handoff was not created: {refreshed}")

        code_created = create_subrun(run_root, "campaign-self-test", "implementation")
        code_ledger = json.loads((run_root / "campaign-self-test-code" / "task_ledger.json").read_text(encoding="utf-8"))
        if not code_created.get("ok") or "research_to_implementation.json" not in code_ledger.get("goal", ""):
            raise AssertionError(f"implementation subrun did not receive handoff: {code_created}")
        make_completed_code_run(run_root, "campaign-self-test-code")
        final_state = campaign_state(run_root, "campaign-self-test")
        if final_state.get("state", {}).get("status") != "completed":
            raise AssertionError(f"campaign final review should complete: {final_state}")
        if not (Path(temp_dir) / "_campaigns" / "campaign-self-test" / FINAL_REPORT_FILE).exists():
            raise AssertionError("final report was not written")

        report = final_review(run_root, "campaign-self-test", final_state["plan"], final_state["state"])
        if report.get("status") != "completed":
            raise AssertionError(f"final review failed: {report}")
        explicit_handoff = create_handoff(run_root, "campaign-self-test", final_state["plan"], final_state["state"], "research_to_implementation")
        if explicit_handoff.get("status") != "ready":
            raise AssertionError(f"explicit handoff failed: {explicit_handoff}")

    with tempfile.TemporaryDirectory() as temp_dir:
        run_root = Path(temp_dir)
        old_base_url = os.environ.get("EYE_MODEL_BASE_URL")
        old_timeout = os.environ.get("EYE_MODEL_TIMEOUT_SEC")
        os.environ["EYE_MODEL_BASE_URL"] = "http://127.0.0.1:9/v1"
        os.environ["EYE_MODEL_TIMEOUT_SEC"] = "1"
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
            if old_base_url is None:
                os.environ.pop("EYE_MODEL_BASE_URL", None)
            else:
                os.environ["EYE_MODEL_BASE_URL"] = old_base_url
            if old_timeout is None:
                os.environ.pop("EYE_MODEL_TIMEOUT_SEC", None)
            else:
                os.environ["EYE_MODEL_TIMEOUT_SEC"] = old_timeout
    print("[ok] Warmaster campaign orchestration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
