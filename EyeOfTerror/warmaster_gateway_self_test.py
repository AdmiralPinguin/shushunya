#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import eye_of_terror.warmaster_gateway as warmaster_gateway
from eye_of_terror.warmaster_gateway import cancel_http_worker_tasks, make_handler, parse_limit, parse_nonnegative_int, resolve_run_child_path, valid_task_id, validate_service_host
from eye_of_terror.ledger import TaskLedger


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_dispatch_ports(run_dir: Path, port: int) -> None:
    for dispatch_path in sorted((run_dir / "dispatch").glob("*.json")):
        packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
        if isinstance(packet, dict):
            packet["port"] = port
            write_json(dispatch_path, packet)


def request_json(url: str, payload: dict | None = None, timeout: int = 5) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def request_options(url: str) -> int:
    req = urllib.request.Request(url, method="OPTIONS")
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status


def make_cancel_handler(calls: list[str]) -> type[BaseHTTPRequestHandler]:
    class CancelHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            calls.append(self.path)
            body = {"ok": True, "task": {"status": "cancelled"}}
            data = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return CancelHandler


def main() -> int:
    if parse_limit("999999", default=20) != 200 or parse_limit("bad", default=20) != 20:
        raise AssertionError("limit parser did not clamp values")
    if parse_nonnegative_int("42", default=0) != 42 or parse_nonnegative_int("bad", default=7) != 7:
        raise AssertionError("nonnegative integer parser returned an unexpected value")
    if not valid_task_id("valid-task_1.2") or valid_task_id("../escape") or valid_task_id("x" * 129):
        raise AssertionError("task id validator accepted an unsafe value")
    if validate_service_host("localhost") != "localhost":
        raise AssertionError("loopback host validator rejected localhost")
    try:
        validate_service_host("example.com")
    except ValueError:
        pass
    else:
        raise AssertionError("service host validator accepted non-loopback host")
    with tempfile.TemporaryDirectory() as temp_dir:
        run_root = Path(temp_dir) / "runs"
        try:
            resolve_run_child_path(run_root / "x", str(Path(temp_dir) / "escape"), "work")
        except ValueError:
            pass
        else:
            raise AssertionError("run child path resolver accepted path outside run_dir")
        original_planner = warmaster_gateway.plan_lore_reconstruction
        try:
            class BadContract:
                task_id = "bad-contract"

                def to_dict(self) -> dict:
                    return {
                        "version": 1,
                        "task_id": self.task_id,
                        "kind": "research",
                        "goal": "bad",
                        "assigned_governor": "IskandarKhayon",
                        "completion_criteria": ["done"],
                        "worker_plan": [],
                    }

            warmaster_gateway.plan_lore_reconstruction = lambda _message, task_id=None: type("BadPlan", (), {"contract": BadContract()})()
            bad_contract = warmaster_gateway.prepare_task("Собери все известное о событиях Скалатракса.", "bad-contract", run_root)
            if bad_contract.get("error_code") != "invalid_task_contract" or (run_root / "bad-contract").exists():
                raise AssertionError(f"Warmaster accepted an invalid task contract: {bad_contract}")
        finally:
            warmaster_gateway.plan_lore_reconstruction = original_planner
        bad_dispatch = Path(temp_dir) / "bad-dispatch" / "dispatch"
        bad_dispatch.mkdir(parents=True, exist_ok=True)
        (bad_dispatch / "broken.json").write_text("{", encoding="utf-8")
        bad_cancel = cancel_http_worker_tasks(bad_dispatch.parent)
        if not bad_cancel or bad_cancel[0].get("ok"):
            raise AssertionError(f"bad dispatch cancel fan-out should report failure: {bad_cancel}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            if request_options(base + "/task") != 204:
                raise AssertionError("OPTIONS did not return 204")
            health = request_json(base + "/health")
            if not health.get("ok"):
                raise AssertionError(f"bad health: {health}")
            capabilities = request_json(base + "/capabilities")
            required_capabilities = {"background_execution", "worker_registry", "worker_cancel_fanout"}
            if not required_capabilities.issubset(set(capabilities.get("capabilities", []))):
                raise AssertionError(f"bad gateway capabilities response: {capabilities}")
            doctor = request_json(base + "/doctor")
            if not doctor.get("ok") or "worker_manifests" not in doctor.get("checks", []) or doctor.get("counts", {}).get("worker_manifests", 0) < 1:
                raise AssertionError(f"bad doctor response: {doctor}")
            corrupt_dir = run_root / "corrupt-ledger-test"
            corrupt_dir.mkdir(parents=True, exist_ok=True)
            (corrupt_dir / "status.json").write_text(json.dumps({"task_id": "corrupt-ledger-test"}), encoding="utf-8")
            (corrupt_dir / "task_ledger.json").write_text("{", encoding="utf-8")
            corrupt_runs = request_json(base + "/runs")
            corrupt_item = next((item for item in corrupt_runs.get("runs", []) if item.get("task_id") == "corrupt-ledger-test"), None)
            if not corrupt_item or corrupt_item.get("status") != "corrupt" or not corrupt_item.get("ledger_error"):
                raise AssertionError(f"corrupt ledger was not represented safely: {corrupt_runs}")
            corrupt_status_dir = run_root / "corrupt-status-test"
            corrupt_status_dir.mkdir(parents=True, exist_ok=True)
            (corrupt_status_dir / "status.json").write_text("{", encoding="utf-8")
            TaskLedger.create(corrupt_status_dir / "task_ledger.json", "corrupt-status-test", "test corrupt status", "IskandarKhayon")
            corrupt_status = request_json(base + "/runs/corrupt-status-test")
            if not corrupt_status.get("ok") or "status_error" not in corrupt_status:
                raise AssertionError(f"corrupt status was not represented safely: {corrupt_status}")
            corrupt_status_summary = request_json(base + "/runs/corrupt-status-test/summary")
            if corrupt_status_summary.get("summary", {}).get("status") != "corrupt":
                raise AssertionError(f"corrupt status summary was not represented safely: {corrupt_status_summary}")
            corrupt_contract_dir = run_root / "corrupt-contract-test"
            corrupt_contract_dir.mkdir(parents=True, exist_ok=True)
            (corrupt_contract_dir / "contract.json").write_text("{", encoding="utf-8")
            try:
                request_json(base + "/runs/corrupt-contract-test/contract")
            except urllib.error.HTTPError as exc:
                if exc.code != 500:
                    raise
                corrupt_contract = json.loads(exc.read().decode("utf-8"))
                if corrupt_contract.get("error_code") != "corrupt_contract":
                    raise AssertionError(f"bad corrupt contract response: {corrupt_contract}")
            else:
                raise AssertionError("corrupt contract endpoint should return a diagnostic error")
            governors = request_json(base + "/governors")
            if not governors.get("ok") or not any(item.get("name") == "IskandarKhayon" for item in governors.get("governors", [])):
                raise AssertionError(f"bad governors response: {governors}")
            governor_health = request_json(base + "/governors?health=1")
            if not governor_health.get("health_checked") or not all("runtime" in item for item in governor_health.get("governors", [])):
                raise AssertionError(f"bad governor health response: {governor_health}")
            workers = request_json(base + "/workers")
            if not workers.get("ok") or not any(item.get("name") == "Lexmechanic" for item in workers.get("workers", [])):
                raise AssertionError(f"bad workers response: {workers}")
            lexmechanic = next(item for item in workers["workers"] if item.get("name") == "Lexmechanic")
            if not lexmechanic.get("metadata_available") or "web_search" not in lexmechanic.get("capabilities", []):
                raise AssertionError(f"workers response did not expose worker metadata: {workers}")
            shushunya = next(item for item in workers["workers"] if item.get("name") == "ShushunyaAgent")
            if not shushunya.get("metadata_available") or shushunya.get("status") != "active":
                raise AssertionError(f"general worker metadata is missing: {shushunya}")
            worker_health = request_json(base + "/workers?health=1")
            if not worker_health.get("health_checked") or not all("runtime" in item for item in worker_health.get("workers", [])):
                raise AssertionError(f"bad worker health response: {worker_health}")
            try:
                request_json(base + "/task", {"message": "почини python приложение", "task_id": "unsupported-code"})
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                rejected = json.loads(exc.read().decode("utf-8"))
                if rejected.get("kind") != "code":
                    raise AssertionError(f"bad unsupported route response: {rejected}")
            else:
                raise AssertionError("unsupported code task should be rejected until a code governor exists")
            try:
                request_json(
                    base + "/task",
                    {"message": "Собери все известное о событиях Скалатракса.", "task_id": "../escape"},
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                invalid_task = json.loads(exc.read().decode("utf-8"))
                if invalid_task.get("error_code") != "invalid_task_id":
                    raise AssertionError(f"bad invalid task_id response: {invalid_task}")
            else:
                raise AssertionError("unsafe task_id should be rejected")
            task = request_json(
                base + "/task",
                {"message": "Собери все известное о событиях Скалатракса.", "task_id": "warmaster-test"},
            )
            if not task.get("ok") or task.get("governor") != "IskandarKhayon":
                raise AssertionError(f"bad task response: {task}")
            try:
                request_json(
                    base + "/task",
                    {"message": "Собери все известное о событиях Скалатракса.", "task_id": "warmaster-test"},
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                duplicate = json.loads(exc.read().decode("utf-8"))
                if duplicate.get("error_code") != "task_exists":
                    raise AssertionError(f"bad duplicate task response: {duplicate}")
            else:
                raise AssertionError("duplicate task_id should not overwrite an existing run")
            state = request_json(base + "/state?run_limit=5")
            if (
                not state.get("ok")
                or not any(item.get("task_id") == "warmaster-test" for item in state.get("runs", []))
                or not any(item.get("name") == "Lexmechanic" for item in state.get("workers", []))
                or "state_snapshot" not in state.get("capabilities", {}).get("capabilities", [])
                or "process_active_run_snapshot" not in state.get("capabilities", {}).get("capabilities", [])
                or not isinstance(state.get("process_active_runs"), list)
                or state.get("run_summary", {}).get("total", 0) < 2
            ):
                raise AssertionError(f"bad gateway state: {state}")
            run_dir = Path(task["run_dir"])
            if not (run_dir / "dispatch" / "source_discovery.json").exists():
                raise AssertionError(f"gateway did not prepare run package: {task}")
            run_status = request_json(base + "/runs/warmaster-test")
            if not run_status.get("ok") or run_status.get("task_id") != "warmaster-test" or not run_status.get("ledger"):
                raise AssertionError(f"bad run status: {run_status}")
            run_summary = request_json(base + "/runs/warmaster-test/summary")
            if (
                not run_summary.get("ok")
                or run_summary.get("summary", {}).get("task_id") != "warmaster-test"
                or run_summary.get("summary", {}).get("revision_plan", {}).get("required")
            ):
                raise AssertionError(f"bad run summary: {run_summary}")
            snapshot = request_json(base + "/runs/warmaster-test/snapshot?events_after=0&event_limit=1")
            if (
                not snapshot.get("ok")
                or snapshot.get("summary", {}).get("task_id") != "warmaster-test"
                or snapshot.get("active")
                or snapshot.get("event_cursor", {}).get("next") != 1
                or snapshot.get("revision_plan", {}).get("required")
            ):
                raise AssertionError(f"bad run snapshot: {snapshot}")
            run_active = request_json(base + "/runs/warmaster-test/active")
            if not run_active.get("ok") or run_active.get("active"):
                raise AssertionError(f"bad run active response: {run_active}")
            contract = request_json(base + "/runs/warmaster-test/contract")
            if not contract.get("ok") or contract["contract"].get("task_id") != "warmaster-test":
                raise AssertionError(f"bad run contract: {contract}")
            dispatch = request_json(base + "/runs/warmaster-test/dispatch")
            if not dispatch.get("ok") or not any(item.get("packet", {}).get("worker") == "Lexmechanic" for item in dispatch.get("dispatch", [])):
                raise AssertionError(f"bad run dispatch: {dispatch}")
            worker_tasks = request_json(base + "/runs/warmaster-test/worker_tasks")
            if (
                not worker_tasks.get("ok")
                or not worker_tasks.get("worker_tasks")
                or worker_tasks["worker_tasks"][0].get("task_id") != "warmaster-test:source_discovery"
            ):
                raise AssertionError(f"bad worker task mapping: {worker_tasks}")
            try:
                request_json(base + "/runs/warmaster-test/worker_tasks?live=1&host=example.com")
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                bad_host = json.loads(exc.read().decode("utf-8"))
                if "loopback" not in bad_host.get("error", ""):
                    raise AssertionError(f"bad worker task host rejection: {bad_host}")
            else:
                raise AssertionError("worker task live lookup should reject non-loopback host")
            events = request_json(base + "/runs/warmaster-test/events?limit=1")
            if not events.get("ok") or len(events.get("events", [])) != 1 or events.get("cursor", {}).get("next") != events.get("cursor", {}).get("total"):
                raise AssertionError(f"bad run events: {events}")
            first_events = request_json(base + "/runs/warmaster-test/events?after=0&limit=1")
            if not first_events.get("ok") or len(first_events.get("events", [])) != 1 or first_events.get("cursor", {}).get("next") != 1:
                raise AssertionError(f"bad cursor run events: {first_events}")
            run_list = request_json(base + "/runs")
            if (
                not run_list.get("ok")
                or run_list.get("run_summary", {}).get("total", 0) < 1
                or not any(item.get("task_id") == "warmaster-test" and item.get("progress", {}).get("planned_steps") == 7 for item in run_list.get("runs", []))
            ):
                raise AssertionError(f"bad run list: {run_list}")
            limited_run_list = request_json(base + "/runs?limit=1")
            if not limited_run_list.get("ok") or len(limited_run_list.get("runs", [])) != 1 or limited_run_list.get("run_summary", {}).get("total", 0) < 2:
                raise AssertionError(f"bad limited run list: {limited_run_list}")
            executed = request_json(base + "/runs/warmaster-test/execute_local", {"timeout_sec": 30}, timeout=60)
            if not executed.get("ok"):
                raise AssertionError(f"bad local execution: {executed}")
            ledger = request_json(base + "/runs/warmaster-test/ledger")
            if not ledger.get("ok") or ledger["ledger"].get("status") != "completed":
                raise AssertionError(f"bad ledger after execution: {ledger}")
            artifacts = request_json(base + "/runs/warmaster-test/artifacts")
            if not artifacts.get("ok") or not artifacts.get("artifacts") or not artifacts["artifacts"][0].get("exists"):
                raise AssertionError(f"bad artifacts response: {artifacts}")
            completed_snapshot = request_json(base + "/runs/warmaster-test/snapshot?events_after=0&event_limit=3")
            if (
                not completed_snapshot.get("ok")
                or not completed_snapshot.get("artifacts")
                or completed_snapshot.get("summary", {}).get("status") != "completed"
                or completed_snapshot.get("revision_plan", {}).get("required")
            ):
                raise AssertionError(f"bad completed run snapshot: {completed_snapshot}")
            artifact_path = artifacts["artifacts"][0]["path"]
            text_artifact = request_json(base + f"/runs/warmaster-test/artifact_text?path={artifact_path}")
            if not text_artifact.get("ok") or "ready" not in text_artifact.get("text", ""):
                raise AssertionError(f"bad artifact text response: {text_artifact}")
            text_preview = request_json(base + f"/runs/warmaster-test/artifact_text?path={artifact_path}&max_bytes=8")
            if not text_preview.get("ok") or len(text_preview.get("text", "").encode("utf-8")) > 8:
                raise AssertionError(f"bad artifact preview response: {text_preview}")
            event_types = [event.get("type") for event in ledger["ledger"].get("events", [])]
            if event_types.count("task_created") != 1:
                raise AssertionError(f"ledger should preserve original task_created event: {ledger}")
            try:
                request_json(base + "/runs/warmaster-test/execute_local", {"timeout_sec": 30}, timeout=60)
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                blocked = json.loads(exc.read().decode("utf-8"))
                if "already completed" not in blocked.get("error", ""):
                    raise AssertionError(f"bad rerun block response: {blocked}")
            else:
                raise AssertionError("completed run should not execute again without force=true")
            forced = request_json(base + "/runs/warmaster-test/execute_local", {"timeout_sec": 30, "force": True}, timeout=60)
            if not forced.get("ok"):
                raise AssertionError(f"forced rerun failed: {forced}")
            unsafe_task = request_json(
                base + "/task",
                {"message": "Собери все известное о событиях Скалатракса.", "task_id": "warmaster-unsafe-workspace-test"},
            )
            if not unsafe_task.get("ok"):
                raise AssertionError(f"bad unsafe workspace task response: {unsafe_task}")
            try:
                request_json(
                    base + "/runs/warmaster-unsafe-workspace-test/execute_http",
                    {"timeout_sec": 30, "host": "example.com"},
                    timeout=60,
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                bad_host = json.loads(exc.read().decode("utf-8"))
                if "loopback" not in bad_host.get("error", ""):
                    raise AssertionError(f"bad execute_http host rejection: {bad_host}")
            else:
                raise AssertionError("execute_http should reject non-loopback host")
            try:
                request_json(
                    base + "/runs/warmaster-unsafe-workspace-test/execute_local",
                    {"timeout_sec": 30, "workspace_root": str(Path(temp_dir) / "outside-work")},
                    timeout=60,
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                unsafe_workspace = json.loads(exc.read().decode("utf-8"))
                if "path must stay inside run_dir" not in unsafe_workspace.get("error", ""):
                    raise AssertionError(f"bad unsafe workspace response: {unsafe_workspace}")
            else:
                raise AssertionError("execute_local should reject workspace_root outside run_dir")
            background_task = request_json(
                base + "/task",
                {"message": "Собери все известное о событиях Скалатракса.", "task_id": "warmaster-background-test"},
            )
            if not background_task.get("ok"):
                raise AssertionError(f"bad background task response: {background_task}")
            started = request_json(base + "/runs/warmaster-background-test/start_local", {"timeout_sec": 30})
            if started.get("status") != "started":
                raise AssertionError(f"background start failed: {started}")
            for _ in range(60):
                background_ledger = request_json(base + "/runs/warmaster-background-test/ledger")
                if background_ledger["ledger"].get("status") == "completed":
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(f"background run did not complete: {background_ledger}")
            background_events = [event.get("type") for event in background_ledger["ledger"].get("events", [])]
            if "background_start_requested" not in background_events:
                raise AssertionError(f"background start event missing: {background_ledger}")
            cancel_task = request_json(
                base + "/task",
                {"message": "Собери все известное о событиях Скалатракса.", "task_id": "warmaster-cancel-test"},
            )
            if not cancel_task.get("ok"):
                raise AssertionError(f"bad cancel task response: {cancel_task}")
            cancel_calls: list[str] = []
            worker_server = ThreadingHTTPServer(("127.0.0.1", 0), make_cancel_handler(cancel_calls))
            worker_thread = threading.Thread(target=worker_server.serve_forever, daemon=True)
            worker_thread.start()
            try:
                patch_dispatch_ports(Path(cancel_task["run_dir"]), worker_server.server_port)
                cancelled = request_json(base + "/runs/warmaster-cancel-test/cancel", {"reason": "test"})
            finally:
                worker_server.shutdown()
                worker_thread.join(timeout=5)
            if not cancelled.get("ok") or not cancelled["ledger"].get("cancel_requested"):
                raise AssertionError(f"bad cancel response: {cancelled}")
            if not cancel_calls or not any(item.get("ok") for item in cancelled.get("worker_cancellations", [])):
                raise AssertionError(f"cancel was not propagated to worker tasks: {cancelled}")
            try:
                request_json(base + "/runs/warmaster-cancel-test/execute_local", {"timeout_sec": 30}, timeout=60)
            except urllib.error.HTTPError as exc:
                if exc.code != 500:
                    raise
                cancelled_execution = json.loads(exc.read().decode("utf-8"))
                if not cancelled_execution.get("summary", {}).get("cancelled"):
                    raise AssertionError(f"bad cancelled execution response: {cancelled_execution}")
            else:
                raise AssertionError("cancelled run should not execute successfully")
            stale_dir = run_root / "stale-test"
            stale_dir.mkdir(parents=True, exist_ok=True)
            stale = TaskLedger.create(stale_dir / "task_ledger.json", "stale-test", "goal", "IskandarKhayon")
            stale.set_status("running")
            recovered = request_json(base + "/recover_stale", {}, timeout=10)
            if not recovered.get("ok") or recovered["recovered"][0].get("status") != "interrupted":
                raise AssertionError(f"bad stale recovery: {recovered}")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] Warmaster gateway")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
