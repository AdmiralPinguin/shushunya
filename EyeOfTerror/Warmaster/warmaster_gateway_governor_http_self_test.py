#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import eye_of_terror.brigade as brigade
import eye_of_terror.warmaster_gateway as warmaster_gateway
from eye_of_terror.inner_circle.iskandar import plan_lore_reconstruction
from eye_of_terror.inner_circle.iskandar_service import make_handler as make_iskandar_handler
from eye_of_terror.pipeline import write_pipeline_run
from EyeOfTerror.common_protocol import commander_order, validate_protocol_payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_bad_prepare_handler(run_root: Path) -> type[BaseHTTPRequestHandler]:
    class BadPrepareHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            body = {"ok": True, "required_workers": ["Lexmechanic", "FabricatorFinalis"]} if self.path == "/capabilities" else {"ok": True, "governor": "IskandarKhayon"}
            data = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
            plan = plan_lore_reconstruction(str(payload.get("task") or ""), task_id=str(payload.get("task_id") or "") or None)
            if self.path == "/plan":
                body = plan.to_dict()
            elif self.path == "/prepare_run":
                run_dir = Path(str(payload.get("run_dir") or run_root / plan.contract.task_id))
                status = write_pipeline_run(plan.contract, run_dir, oversight=plan.to_dict()["oversight"])
                if "bad-dispatch" in str(payload.get("task_id") or ""):
                    (run_dir / "dispatch" / "source_discovery.json").write_text("{", encoding="utf-8")
                elif "bad-worker" in str(payload.get("task_id") or ""):
                    dispatch_path = run_dir / "dispatch" / "source_discovery.json"
                    packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
                    packet["worker"] = "Chronologis"
                    write_json(dispatch_path, packet)
                else:
                    (run_dir / "oversight.json").unlink()
                body = {"ok": True, "status": status}
            else:
                body = {"ok": False, "error": "not found"}
            data = json.dumps(body).encode("utf-8")
            self.send_response(200 if body.get("ok") else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return BadPrepareHandler


def service_governor(port: int) -> Any:
    class ServiceGovernor:
        name = "IskandarKhayon"
        status = "active"

        def __init__(self, service_port: int) -> None:
            self.port = service_port

        def active(self) -> bool:
            return True

        def to_dict(self) -> dict[str, Any]:
            return {
                "name": self.name,
                "status": self.status,
                "port": self.port,
                "task_kinds": ["research", "research_writing", "lore_reconstruction"],
                "route_terms": ["скалатракс"],
                "service": "eye_of_terror.inner_circle.iskandar_service",
            }

    return ServiceGovernor(port)


def iskandar_command(task: str, task_id: str) -> dict[str, Any]:
    order = commander_order(
        f"mission-{task_id}",
        to="IskandarKhayon",
        user_request=task,
        commander_intent="Проверить HTTP-подготовку бригадира через приказ Вармастера.",
        primary_goal=task,
        success_conditions=[
            "governor HTTP service receives commander_order",
            "prepared run package preserves mission protocol artifacts",
        ],
        constraints=["Do not use direct raw governor task input."],
    )
    validate_protocol_payload(order, expected_type="commander_order")
    return order


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        run_root = Path(temp_dir)
        iskandar_server = ThreadingHTTPServer(("127.0.0.1", 0), make_iskandar_handler(run_root))
        iskandar_thread = threading.Thread(target=iskandar_server.serve_forever, daemon=True)
        iskandar_thread.start()
        try:
            prepared = warmaster_gateway.prepare_task_via_governor_service(
                "Собери все известное о событиях Скалатракса.",
                "warmaster-governor-http-focused-test",
                run_root,
                service_governor(iskandar_server.server_port),
                commander_order=iskandar_command("Собери все известное о событиях Скалатракса.", "warmaster-governor-http-focused-test"),
            )
            run_dir = Path(str(prepared.get("run_dir") or ""))
            if (
                not prepared.get("ok")
                or prepared.get("governor_transport") != "http"
                or not (run_dir / "dispatch" / "source_discovery.json").exists()
                or not (run_dir / "task_ledger.json").exists()
                or prepared.get("actions", {}).get("next_action", {}).get("kind") != "preflight_run"
            ):
                raise AssertionError(f"bad http governor preparation: {prepared}")

            bad_prepare_server = ThreadingHTTPServer(("127.0.0.1", 0), make_bad_prepare_handler(run_root))
            bad_prepare_thread = threading.Thread(target=bad_prepare_server.serve_forever, daemon=True)
            bad_prepare_thread.start()
            try:
                bad_governor = service_governor(bad_prepare_server.server_port)
                for suffix, expected_fragment in (
                    ("bad-prepare", "oversight not found"),
                    ("bad-dispatch", "source_discovery.json"),
                    ("bad-worker", "dispatch worker mismatch"),
                ):
                    bad_prepared = warmaster_gateway.prepare_task_via_governor_service(
                        "Собери все известное о событиях Скалатракса.",
                        f"warmaster-governor-{suffix}-focused-test",
                        run_root,
                        bad_governor,
                        commander_order=iskandar_command(
                            "Собери все известное о событиях Скалатракса.",
                            f"warmaster-governor-{suffix}-focused-test",
                        ),
                    )
                    if (
                        bad_prepared.get("error_code") != "governor_prepare_invalid_run"
                        or not any(expected_fragment in error for error in bad_prepared.get("validation", {}).get("errors", []))
                        or not bad_prepared.get("cleanup", {}).get("removed")
                    ):
                        raise AssertionError(f"invalid governor-prepared package was not rejected: {bad_prepared}")
            finally:
                bad_prepare_server.shutdown()
                bad_prepare_thread.join(timeout=120)

            original_worker_refs = brigade.worker_refs
            brigade.worker_refs = lambda: []
            try:
                missing_workers = warmaster_gateway.prepare_task_via_governor_service(
                    "Собери все известное о событиях Скалатракса.",
                    "warmaster-governor-missing-workers-focused-test",
                    run_root,
                    service_governor(iskandar_server.server_port),
                    commander_order=iskandar_command(
                        "Собери все известное о событиях Скалатракса.",
                        "warmaster-governor-missing-workers-focused-test",
                    ),
                )
                if (
                    missing_workers.get("error_code") != "governor_workers_missing"
                    or "Lexmechanic" not in missing_workers.get("missing_workers", [])
                    or missing_workers.get("actions", {}).get("next_action", {}).get("kind") != "inspect_brigade"
                ):
                    raise AssertionError(f"missing governor workers were not rejected: {missing_workers}")
            finally:
                brigade.worker_refs = original_worker_refs
        finally:
            iskandar_server.shutdown()
            iskandar_thread.join(timeout=120)
    print("[ok] Warmaster gateway HTTP governor preparation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
