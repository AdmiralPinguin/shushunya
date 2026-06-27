#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
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
            result = request_json(
                base + "/run",
                {
                    "request": {
                        "task_id": "runtime-test",
                        "step": {"expected_artifacts": ["/work/test/direct_event_notes.json"]},
                    }
                },
            )
            if not result.get("ok"):
                raise AssertionError(f"bad run response: {result}")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] worker runtime")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
