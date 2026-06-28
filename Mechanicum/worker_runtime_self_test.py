#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from worker_runtime import load_worker, make_handler


def request_json(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / "test" / "source_map.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps({"topic": "other", "sources": []}), encoding="utf-8")
        run_worker = load_worker(repo_root / "Mechanicum" / "NoosphericExtractor", "noospheric_extractor")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("NoosphericExtractor", root, run_worker))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            health = request_json(base + "/health")
            if not health.get("ok") or health.get("worker") != "NoosphericExtractor":
                raise AssertionError(f"bad health response: {health}")
            if "GET /tasks" not in health.get("endpoints", []):
                raise AssertionError(f"health did not advertise worker API endpoints: {health}")
            capabilities = request_json(base + "/capabilities")
            if capabilities.get("worker") != "NoosphericExtractor" or not isinstance(capabilities.get("capabilities"), list):
                raise AssertionError(f"bad capabilities response: {capabilities}")
            result = request_json(
                base + "/run",
                {
                    "request": {
                        "task_id": "runtime-test",
                        "input_artifacts": ["/work/test/source_map.json"],
                        "step": {"expected_artifacts": ["/work/test/direct_event_notes.json"]},
                    }
                },
            )
            if not result.get("ok"):
                raise AssertionError(f"bad run response: {result}")
            task = request_json(base + "/tasks/runtime-test")
            if not task.get("ok") or task["task"].get("status") != "completed" or not task["task"].get("created_at") or not task["task"].get("updated_at"):
                raise AssertionError(f"bad task status response: {task}")
            task_list = request_json(base + "/tasks")
            if not task_list.get("ok") or not any(item.get("task_id") == "runtime-test" for item in task_list.get("tasks", [])):
                raise AssertionError(f"bad task list response: {task_list}")
            try:
                request_json(
                    base + "/run",
                    {
                        "request": {
                            "task_id": "missing-input-test",
                            "input_artifacts": ["/work/test/missing.json"],
                            "step": {"expected_artifacts": ["/work/test/should_not_exist.json"]},
                        }
                    },
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                missing_input = json.loads(exc.read().decode("utf-8"))
                if missing_input.get("error") != "input artifact preflight failed":
                    raise AssertionError(f"bad missing input response: {missing_input}")
            else:
                raise AssertionError("worker runtime should reject missing input artifacts")
            missing_input_task = request_json(base + "/tasks/missing-input-test")
            if missing_input_task.get("task", {}).get("status") != "failed":
                raise AssertionError(f"missing input task did not fail durably: {missing_input_task}")
            cancelled = request_json(base + "/tasks/cancel-before-start/cancel", {})
            if not cancelled.get("ok") or not cancelled["task"].get("cancel_requested") or not cancelled["task"].get("cancel_reason"):
                raise AssertionError(f"bad task cancel response: {cancelled}")
            try:
                request_json(
                    base + "/run",
                    {
                        "request": {
                            "task_id": "cancel-before-start",
                            "step": {"expected_artifacts": ["/work/test/should_not_exist.json"]},
                        }
                    },
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                blocked = json.loads(exc.read().decode("utf-8"))
                if blocked.get("status") != "cancelled":
                    raise AssertionError(f"bad blocked run response: {blocked}")
            else:
                raise AssertionError("cancelled worker task should not start")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] worker runtime")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
