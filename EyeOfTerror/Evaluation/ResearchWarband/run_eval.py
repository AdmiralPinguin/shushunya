#!/usr/bin/env python3
"""Run the public synthetic smoke against the deterministic fake subject."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_eval.manifest import require_object, strict_json_load
from research_eval.results import (
    ResultWriteError,
    publication_safe_result,
    write_result_atomic,
)
from research_eval.runner import run_suite
from research_eval.subjects import FakeSubjectAdapter


ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default=str(ROOT / "suites/public_smoke_v1/manifest.json"))
    parser.add_argument("--fake-results", default=str(ROOT / "replays/public_smoke_v1/results.json"))
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    try:
        replay, _ = strict_json_load(Path(args.fake_results).resolve())
        subject = FakeSubjectAdapter(require_object(replay, "fake results"))
        result = publication_safe_result(
            run_suite(args.suite, subject, allowed_root=ROOT)
        )
    except Exception as exc:  # every current failure must replace a stale pass
        result = {
            "schema_version": 1,
            "run_valid": False,
            "run_passed": False,
            "infrastructure_error": (
                "evaluation failed before a complete run result: " + type(exc).__name__
            ),
        }
    if args.out:
        try:
            result = write_result_atomic(result, args.out)
        except ResultWriteError as exc:
            parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["run_valid"]:
        return 2
    return 0 if result["run_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
