#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.common_protocol import worker_order
from EyeOfTerror.Scriptorium.Brigade.worker_protocol import strict_worker_request_from_payload


def main() -> int:
    try:
        strict_worker_request_from_payload({"task": "raw research task"}, "CorpusIngestor")
    except ValueError as exc:
        if "worker_order is required" not in str(exc):
            raise
    else:
        raise AssertionError("Scriptorium worker CLI accepted raw payload without worker_order")
    order = worker_order(
        "mission-scriptorium-worker",
        step_id="corpus_ingestion",
        sender="IskandarKhayon",
        to="CorpusIngestor",
        task="inspect corpus",
        expected_output="/work/research/research_corpus.json",
    )
    normalized = strict_worker_request_from_payload({"worker_order": order, "request": {"worker_order": order}}, "CorpusIngestor")
    if normalized.get("task") != "inspect corpus" or normalized.get("worker_order") != order:
        raise AssertionError(f"Scriptorium worker_order normalization failed: {normalized}")
    try:
        strict_worker_request_from_payload({"worker_order": order, "request": {"worker_order": {**order, "to": "Lexmechanic"}}}, "CorpusIngestor")
    except ValueError as exc:
        if "request.worker_order must match" not in str(exc):
            raise
    else:
        raise AssertionError("Scriptorium worker CLI accepted mismatched request.worker_order")
    print("[ok] Scriptorium worker protocol")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
