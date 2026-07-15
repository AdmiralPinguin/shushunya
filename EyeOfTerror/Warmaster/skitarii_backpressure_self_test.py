"""Focused proof that a full Skitarii worker queue (HTTP 429) is retried with
backoff instead of collapsing into a dead ``blocked`` verdict.

Regression guard for the reasoning-on-failure principle: a transient, retryable
capacity signal must never become a silent terminal block.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WARMASTER_ROOT = Path(__file__).resolve().parent
REPO_ROOT = WARMASTER_ROOT.parent.parent
for _root in (REPO_ROOT, WARMASTER_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from eye_of_terror import skitarii_bridge as sb
from eye_of_terror.ledger import TaskLedger


class _PollSentinel(Exception):
    """Marks that control reached the post-create poll loop."""


def _creation_body() -> dict:
    return {
        "goal": "print the first five primes",
        "delegating_task_id": "primes-task",
        "max_wall_sec": 120,
    }


def _fresh_run_dir(temp: str) -> Path:
    run_dir = Path(temp) / "run"
    run_dir.mkdir(parents=True)
    TaskLedger.create(
        run_dir / "task_ledger.json",
        "primes-task",
        "print the first five primes",
        "Ceraxia",
    )
    return run_dir


class SkitariiBackpressureTest(unittest.TestCase):
    def test_queue_full_then_created_retries_with_backoff(self) -> None:
        body = json.dumps(_creation_body(), ensure_ascii=False).encode("utf-8")
        request_sha256 = sb._service_request_sha256(_creation_body())
        requested_id = sb._service_mission_id("primes-task", 1)

        post_calls = {"n": 0}

        def fake_request(method, path, *, body=None, timeout=30.0,
                         allowed_http_statuses=frozenset()):
            if method == "GET" and path == f"/missions/{requested_id}":
                # No prior attempt landed: report absence so we take the POST path.
                return {"_http_status": 404}
            if method == "POST" and path == "/missions":
                post_calls["n"] += 1
                if post_calls["n"] == 1:
                    # First attempt: the bounded worker queue is full.
                    return {
                        "error": "the bounded mission worker queue is full",
                        "retryable": True,
                        "_http_status": 429,
                    }
                # Second attempt: a slot freed up and the mission is created.
                return {"mission_id": requested_id, "request_sha256": request_sha256}
            if method == "GET" and path == f"/missions/{requested_id}":
                raise _PollSentinel()
            # Any later poll GET proves we got past create.
            raise _PollSentinel()

        with mock.patch.object(sb, "_skitarii_json_request", side_effect=fake_request), \
                mock.patch.object(sb.time, "sleep") as sleep_mock, \
                tempfile.TemporaryDirectory() as temp:
            run_dir = _fresh_run_dir(temp)
            ledger = TaskLedger.load(run_dir / "task_ledger.json")
            with self.assertRaises(_PollSentinel):
                sb._await_async_skitarii_mission(
                    body, run_dir, "primes-task", ledger, 120,
                )

        # The 429 was retried, not fatal: exactly two POSTs and one backoff sleep.
        self.assertEqual(post_calls["n"], 2)
        self.assertEqual(sleep_mock.call_count, 1)

    def test_persistent_queue_full_raises_retryable_backpressure(self) -> None:
        body = json.dumps(_creation_body(), ensure_ascii=False).encode("utf-8")
        requested_id = sb._service_mission_id("primes-task", 1)

        def fake_request(method, path, *, body=None, timeout=30.0,
                         allowed_http_statuses=frozenset()):
            if method == "GET" and path == f"/missions/{requested_id}":
                return {"_http_status": 404}
            if method == "POST" and path == "/missions":
                return {"error": "queue full", "retryable": True, "_http_status": 429}
            raise AssertionError(f"unexpected call {method} {path}")

        # Jump the clock so the retry budget is exhausted after one backoff.
        clock = {"t": 0.0}

        def fake_monotonic():
            clock["t"] += 1000.0
            return clock["t"]

        with mock.patch.object(sb, "_skitarii_json_request", side_effect=fake_request), \
                mock.patch.object(sb.time, "sleep"), \
                mock.patch.object(sb.time, "monotonic", side_effect=fake_monotonic), \
                tempfile.TemporaryDirectory() as temp:
            run_dir = _fresh_run_dir(temp)
            ledger = TaskLedger.load(run_dir / "task_ledger.json")
            with self.assertRaises(sb._SkitariiQueueBackpressure):
                sb._await_async_skitarii_mission(
                    body, run_dir, "primes-task", ledger, 120,
                )


if __name__ == "__main__":
    unittest.main()
