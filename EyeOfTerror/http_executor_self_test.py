#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from eye_of_terror.http_executor import execute_run, run_step, terminal_payload_allows_completion

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
MECHANICUM_ROOT = REPO_ROOT / "Mechanicum"
if str(MECHANICUM_ROOT) not in sys.path:
    sys.path.insert(0, str(MECHANICUM_ROOT))

from worker_runtime import load_worker, make_handler  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_dispatch_ports(run_dir: Path, ports_by_worker: dict[str, int]) -> None:
    for dispatch_path in sorted((run_dir / "dispatch").glob("*.json")):
        packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
        if isinstance(packet, dict) and packet.get("worker") in ports_by_worker:
            packet["port"] = ports_by_worker[str(packet["worker"])]
            write_json(dispatch_path, packet)


class CaptureRunHandler(BaseHTTPRequestHandler):
    captured_payload: dict | None = None

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length)
        type(self).captured_payload = json.loads(body.decode("utf-8"))
        response = {
            "ok": True,
            "worker": "CaptureWorker",
            "task_id": "capture-test",
            "status": "completed",
            "artifacts": [],
        }
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    if terminal_payload_allows_completion({"ok": True, "status": "blocked"}):
        raise AssertionError("blocked terminal payload should not complete a run")
    if terminal_payload_allows_completion({"ok": True}):
        raise AssertionError("terminal payload without status should not complete a run")
    if terminal_payload_allows_completion({"ok": True, "status": "mystery"}):
        raise AssertionError("unknown terminal payload status should not complete a run")
    if terminal_payload_allows_completion({"ok": True, "status": "ready", "revision_plan": {"required": True}}):
        raise AssertionError("required revision plan should not complete a run")
    if not terminal_payload_allows_completion({"ok": True, "status": "ready", "revision_plan": {"required": False}}):
        raise AssertionError("ready terminal payload should complete a run")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        work = root / "work"
        source = work / "test" / "source_map.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps({"topic": "other", "sources": []}), encoding="utf-8")
        run_worker = load_worker(REPO_ROOT / "Mechanicum" / "NoosphericExtractor", "noospheric_extractor")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("NoosphericExtractor", work, run_worker))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            run_dir = root / "run"
            dispatch_dir = run_dir / "dispatch"
            write_json(
                run_dir / "status.json",
                {
                    "steps": [
                        {
                            "step_id": "fact_extraction",
                            "worker": "NoosphericExtractor",
                            "port": server.server_port,
                        }
                    ],
                    "dispatch_dir": str(dispatch_dir),
                },
            )
            write_json(
                dispatch_dir / "fact_extraction.json",
                {
                    "step_id": "fact_extraction",
                    "worker": "NoosphericExtractor",
                    "port": server.server_port,
                    "request": {
                        "task_id": "http-test",
                        "step": {"expected_artifacts": ["/work/test/direct_event_notes.json"]},
                    },
                },
            )
            summary = execute_run(run_dir, timeout_sec=5)
            if not summary.get("ok"):
                raise AssertionError(summary)
            if not (work / "test" / "direct_event_notes.json").exists():
                raise AssertionError("HTTP executor did not write worker artifact")
            if not (run_dir / "task_ledger.json").exists():
                raise AssertionError("HTTP executor did not write task ledger")
            server.shutdown()
            thread.join(timeout=5)
            summary = execute_run(run_dir, timeout_sec=1)
            if summary.get("ok") or not summary.get("preflight_failures"):
                raise AssertionError(f"expected preflight failure: {summary}")
            corrupt_dispatch_run = root / "corrupt-dispatch-run"
            corrupt_dispatch_dir = corrupt_dispatch_run / "dispatch"
            write_json(
                corrupt_dispatch_run / "status.json",
                {
                    "steps": [
                        {
                            "step_id": "fact_extraction",
                            "worker": "NoosphericExtractor",
                            "port": server.server_port,
                        }
                    ],
                    "dispatch_dir": str(corrupt_dispatch_dir),
                },
            )
            corrupt_dispatch_dir.mkdir(parents=True, exist_ok=True)
            (corrupt_dispatch_dir / "fact_extraction.json").write_text("{", encoding="utf-8")
            corrupt_summary = execute_run(corrupt_dispatch_run, timeout_sec=1)
            if corrupt_summary.get("ok") or "dispatch unavailable" not in corrupt_summary.get("preflight_failures", [{}])[0].get("error", ""):
                raise AssertionError(f"HTTP executor did not record corrupt dispatch preflight failure: {corrupt_summary}")
            corrupt_ledger = json.loads((corrupt_dispatch_run / "task_ledger.json").read_text(encoding="utf-8"))
            if corrupt_ledger.get("status") != "failed" or corrupt_ledger.get("result", {}).get("status") != "preflight_failed":
                raise AssertionError(f"corrupt dispatch preflight failure was not recorded durably: {corrupt_ledger}")
            capture_dispatch = root / "capture.json"
            capture_server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureRunHandler)
            capture_thread = threading.Thread(target=capture_server.serve_forever, daemon=True)
            capture_thread.start()
            try:
                write_json(
                    capture_dispatch,
                    {
                        "step_id": "capture_step",
                        "worker": "CaptureWorker",
                        "port": capture_server.server_port,
                        "request": {"task_id": "capture-test"},
                    },
                )
                captured = run_step(
                    capture_dispatch,
                    "127.0.0.1",
                    5,
                    revision_context={"reasons": ["Needs focused rewrite"], "source_steps": ["critic_review"], "priority": "blocker"},
                )
                if not captured.ok:
                    raise AssertionError(captured)
                context = (CaptureRunHandler.captured_payload or {}).get("request", {}).get("revision_context")
                if context is None or context.get("reasons") != ["Needs focused rewrite"]:
                    raise AssertionError(f"HTTP executor did not forward revision_context: {CaptureRunHandler.captured_payload}")
            finally:
                capture_server.shutdown()
                capture_thread.join(timeout=5)
            wrong_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("WrongWorker", work, run_worker))
            wrong_thread = threading.Thread(target=wrong_server.serve_forever, daemon=True)
            wrong_thread.start()
            try:
                patch_dispatch_ports(run_dir, {"NoosphericExtractor": wrong_server.server_port})
                summary = execute_run(run_dir, timeout_sec=5)
                failures = summary.get("preflight_failures") or []
                if summary.get("ok") or not any("identity mismatch" in item.get("error", "") for item in failures):
                    raise AssertionError(f"expected identity mismatch preflight failure: {summary}")
            finally:
                wrong_server.shutdown()
                wrong_thread.join(timeout=5)
        finally:
            try:
                server.shutdown()
            except Exception:
                pass
            thread.join(timeout=5)
    print("[ok] http executor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
