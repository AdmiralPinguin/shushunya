#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from eye_of_terror.contracts import build_lore_reconstruction_contract
from eye_of_terror.inner_circle.iskandar_service import make_handler, oversight_template, pipeline_summary, required_workers, resolve_run_dir


def request_json(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def request_options(url: str) -> int:
    req = urllib.request.Request(url, method="OPTIONS")
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status


def main() -> int:
    contract_workers = [
        step.worker
        for step in build_lore_reconstruction_contract("Собери события Скалатракса", task_id="iskandar-service-test").worker_plan
    ]
    if required_workers() != contract_workers:
        raise AssertionError(f"Iskandar required workers drifted from contract plan: {required_workers()}")
    pipeline = pipeline_summary()
    if (
        pipeline.get("step_count") != len(contract_workers)
        or pipeline.get("steps", [])[0].get("worker") != "CorpusIngestor"
        or pipeline.get("steps", [])[1].get("depends_on") != ["corpus_ingestion"]
        or pipeline.get("steps", [])[2].get("expected_artifacts") != ["/work/capabilities/source_snapshots.json"]
        or pipeline.get("steps", [])[3].get("worker") != "OcularisRenderium"
        or pipeline.get("steps", [])[3].get("expected_artifacts") != ["/work/capabilities/rendered_snapshots.json"]
        or pipeline.get("steps", [])[4].get("depends_on") != ["source_rendering"]
    ):
        raise AssertionError(f"bad Iskandar pipeline summary: {pipeline}")
    oversight = oversight_template()
    if (
        oversight.get("final_review", {}).get("critic_step") != "critic_review"
        or oversight.get("final_review", {}).get("requires_evidence_trace") is not True
        or not oversight.get("artifact_roles", {}).get("final", [])[0].endswith("/final_manifest.json")
        or not any(item.get("from_step") == "critic_review" and item.get("to_steps") == ["finalize"] for item in oversight.get("handoffs", []))
        or oversight.get("revision_policy", {}).get("final_steps") != ["critic_review", "finalize"]
        or oversight.get("iteration_policy", {}).get("recommended_endpoint") != "POST /runs/{task_id}/start_research_loop_http"
        or oversight.get("iteration_policy", {}).get("max_revision_cycles") != 3
        or len(oversight.get("step_quality_matrix", [])) != len(contract_workers)
    ):
        raise AssertionError(f"bad Iskandar oversight template: {oversight}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        if resolve_run_dir(root / "runs", "child", "task").resolve() != (root / "runs" / "child").resolve():
            raise AssertionError("relative run_dir did not resolve under default root")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(root / "runs"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            if request_options(base + "/plan") != 200:
                raise AssertionError("OPTIONS did not return 200")
            health = request_json(base + "/health")
            if not health.get("ok"):
                raise AssertionError(f"bad health: {health}")
            capabilities = request_json(base + "/capabilities")
            if "dispatch_packet_preparation" not in capabilities.get("capabilities", []):
                raise AssertionError(f"bad capabilities: {capabilities}")
            if capabilities.get("required_workers", [])[0] != "CorpusIngestor" or "FabricatorFinalis" not in capabilities.get("required_workers", []):
                raise AssertionError(f"capabilities did not expose required workers: {capabilities}")
            if (
                capabilities.get("worker_availability", {}).get("ok") is not True
                or capabilities.get("worker_availability", {}).get("missing_workers")
                or capabilities.get("worker_availability", {}).get("unavailable_workers")
                or capabilities.get("worker_availability", {}).get("resolved_workers", {}).get("Lexmechanic", {}).get("status") != "prototype"
            ):
                raise AssertionError(f"capabilities did not expose worker availability: {capabilities}")
            if (
                "oversight_plan" not in capabilities.get("capabilities", [])
                or capabilities.get("oversight", {}).get("final_review", {}).get("final_step") != "finalize"
                or capabilities.get("oversight", {}).get("revision_policy", {}).get("requires_focused_context") is not True
                or capabilities.get("oversight", {}).get("iteration_policy", {}).get("controller") != "WarmasterGateway"
            ):
                raise AssertionError(f"capabilities did not expose oversight plan: {capabilities}")
            if (
                capabilities.get("pipeline", {}).get("step_count") != len(contract_workers)
                or capabilities.get("pipeline", {}).get("steps", [])[0].get("step_id") != "corpus_ingestion"
                or capabilities.get("summary", {}).get("step_count") != len(contract_workers)
                or capabilities.get("summary", {}).get("step_quality_matrix_count") != len(contract_workers)
                or capabilities.get("display", {}).get("headline") != "Iskandar Khayon capabilities"
                or capabilities.get("client_action", {}).get("path") != "/plan"
                or "step_quality_matrix" not in capabilities.get("capabilities", [])
            ):
                raise AssertionError(f"capabilities did not expose pipeline summary: {capabilities}")
            plan = request_json(base + "/plan", {"task": "Собери события Скалатракса", "task_id": "iskandar-http-test"})
            if (
                not plan.get("ok")
                or plan["contract"]["assigned_governor"] != "IskandarKhayon"
                or plan.get("oversight", {}).get("artifact_roles", {}).get("critic") != ["/work/skalathrax/critic_report.json"]
                or len(plan.get("oversight", {}).get("step_quality_matrix", [])) != len(contract_workers)
                or plan.get("oversight", {}).get("final_review", {}).get("final_artifact") != "/work/skalathrax/final_manifest.json"
                or plan.get("oversight", {}).get("iteration_policy", {}).get("recommended_endpoint") != "POST /runs/{task_id}/start_research_loop_http"
                or plan.get("pipeline", {}).get("step_count") != len(contract_workers)
                or plan.get("pipeline", {}).get("steps", [])[0].get("worker") != "CorpusIngestor"
                or plan.get("actions", {}).get("can_prepare_run") is not True
                or plan.get("actions", {}).get("next_action", {}).get("kind") != "prepare_run"
                or plan.get("actions", {}).get("next_action", {}).get("body", {}).get("task") != "Собери события Скалатракса"
                or plan.get("actions", {}).get("next_action", {}).get("body", {}).get("task_id") != "iskandar-http-test"
                or plan.get("phase") != "plan_ready"
                or plan.get("decision", {}).get("can_prepare_run") is not True
                or plan.get("display", {}).get("headline") != "Plan is ready"
                or plan.get("client_action", {}).get("path") != "/prepare_run"
            ):
                raise AssertionError(f"bad plan: {plan}")
            run_dir = root / "runs" / "custom-run"
            prepared = request_json(
                base + "/prepare_run",
                {"task": "Собери события Скалатракса", "task_id": "iskandar-http-test", "run_dir": str(run_dir)},
            )
            if (
                not prepared.get("ok")
                or not (run_dir / "dispatch" / "source_discovery.json").exists()
                or not (run_dir / "oversight.json").exists()
                or not prepared.get("status", {}).get("oversight_path")
                or prepared.get("phase") != "run_prepared"
                or prepared.get("decision", {}).get("can_handoff_to_warmaster") is not True
                or prepared.get("display", {}).get("headline") != "Run package prepared"
                or prepared.get("client_action") != {}
            ):
                raise AssertionError(f"bad prepared run: {prepared}")
            prepared_oversight = json.loads((run_dir / "oversight.json").read_text(encoding="utf-8"))
            if prepared_oversight.get("final_review", {}).get("final_artifact") != "/work/skalathrax/final_manifest.json":
                raise AssertionError(f"prepare_run wrote bad oversight: {prepared_oversight}")
            if prepared_oversight.get("iteration_policy", {}).get("max_revision_cycles") != 3:
                raise AssertionError(f"prepare_run wrote bad iteration policy: {prepared_oversight}")
            prepared_fact_dispatch = json.loads((run_dir / "dispatch" / "fact_extraction.json").read_text(encoding="utf-8"))
            if (
                prepared_fact_dispatch.get("request", {}).get("quality_expectations", {}).get("step_quality", {}).get("step_id") != "fact_extraction"
                or prepared_fact_dispatch.get("request", {}).get("quality_expectations", {}).get("final_review", {}).get("final_step") != "finalize"
            ):
                raise AssertionError(f"prepare_run did not write dispatch quality expectations: {prepared_fact_dispatch}")
            try:
                request_json(
                    base + "/prepare_run",
                    {"task": "Собери события Скалатракса", "task_id": "iskandar-escape-test", "run_dir": str(root / "escape")},
            )
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                rejected = json.loads(exc.read().decode("utf-8"))
                if "run_dir must stay inside" not in rejected.get("error", ""):
                    raise AssertionError(f"bad run_dir rejection: {rejected}")
            else:
                raise AssertionError("prepare_run should reject run_dir outside default root")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] Iskandar service")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
