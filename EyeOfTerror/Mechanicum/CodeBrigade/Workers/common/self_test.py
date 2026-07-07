#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

COMMON_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[5]
for path in (COMMON_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from EyeOfTerror.common_protocol import worker_order
from worker_protocol import strict_worker_request_from_payload
from swe_guardrails import build_repo_map, python_module_name, source_candidates_from_traceback_text, test_source_links


def main() -> int:
    symbols = [
        {"path": "sample.py", "module": "sample", "functions": ["value"], "classes": [], "imports": []},
        {"path": "test_sample.py", "module": "test_sample", "functions": ["test_value"], "classes": [], "imports": ["sample.value"]},
    ]
    if python_module_name("pkg/__init__.py") != "pkg" or python_module_name("pkg/core.py") != "pkg.core":
        raise AssertionError("bad Python module name conversion")
    links = test_source_links(symbols)
    if links != [{"test_path": "test_sample.py", "source_paths": ["sample.py"]}]:
        raise AssertionError(f"bad test-source links: {links}")
    repo_map = build_repo_map("почини value", [], ["test_sample.py"], symbols)
    ranked = repo_map.get("ranked_files", [])
    if not ranked or ranked[0].get("path") != "sample.py":
        raise AssertionError(f"imported source should outrank the test file: {repo_map}")
    if repo_map.get("recommended_read_order", [])[0].get("path") != "sample.py":
        raise AssertionError(f"repo map should expose recommended read order: {repo_map}")
    traceback = (
        'Traceback (most recent call last):\n'
        '  File "/tmp/repo/pkg/core.py", line 2, in run\n'
        '  File "/tmp/other/outside.py", line 1, in nope\n'
    )
    candidates = source_candidates_from_traceback_text(traceback, "/tmp/repo")
    if candidates != ["pkg/core.py"]:
        raise AssertionError(f"bad traceback source candidates: {candidates}")
    try:
        strict_worker_request_from_payload({"task": "raw code task"}, "LogisRepository")
    except ValueError as exc:
        if "worker_order is required" not in str(exc):
            raise
    else:
        raise AssertionError("CodeBrigade worker CLI accepted raw payload without worker_order")
    order = worker_order(
        "mission-code-worker",
        step_id="repository_survey",
        sender="Ceraxia",
        to="LogisRepository",
        task="survey repo",
        expected_output="/work/code/repo_survey.json",
    )
    normalized = strict_worker_request_from_payload({"worker_order": order, "request": {"worker_order": order}}, "LogisRepository")
    if normalized.get("task") != "survey repo" or normalized.get("worker_order") != order:
        raise AssertionError(f"CodeBrigade worker_order normalization failed: {normalized}")
    try:
        strict_worker_request_from_payload({"worker_order": order, "request": {"worker_order": {**order, "to": "Other"}}}, "LogisRepository")
    except ValueError as exc:
        if "request.worker_order must match" not in str(exc):
            raise
    else:
        raise AssertionError("CodeBrigade worker CLI accepted mismatched request.worker_order")
    print("[ok] Mechanicum common SWE guardrails")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
