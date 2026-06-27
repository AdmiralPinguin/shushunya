#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from eye_of_terror.http_executor import execute_run

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
MECHANICUM_ROOT = REPO_ROOT / "Mechanicum"
if str(MECHANICUM_ROOT) not in sys.path:
    sys.path.insert(0, str(MECHANICUM_ROOT))

from worker_runtime import load_worker, make_handler  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
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
