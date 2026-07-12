"""Run the long-wall real-model suite through the HTTP subject on port 7202."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def _find_eval_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        for relative in (
            Path("EyeOfTerror/Evaluation/ResearchWarband"),
            Path("evaluation/EyeOfTerror/Evaluation/ResearchWarband"),
        ):
            candidate = parent / relative
            if (candidate / "research_eval/runner.py").is_file():
                return candidate
    raise RuntimeError("ResearchWarband external evaluator tree was not found")


def main(argv: list[str] | None = None) -> int:
    eval_root = _find_eval_root()
    if str(eval_root) not in sys.path:
        sys.path.insert(0, str(eval_root))
    from research_eval.results import ResultWriteError, write_result_atomic
    from research_eval.runner import run_suite
    from .external_eval_subject import HTTPExternalEvalSubject

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        default=str(eval_root / "suites/public_smoke_long_v1/manifest.json"),
        help=(
            "suite manifest; default is the long-wall real-model gate. "
            "public_smoke_v1 is reserved for the fast evaluator self-test"
        ),
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:7202")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    subject = HTTPExternalEvalSubject(args.base_url)
    result = run_suite(args.suite, subject, allowed_root=eval_root)
    if args.out:
        try:
            write_result_atomic(result, args.out)
        except ResultWriteError as exc:
            parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["run_valid"]:
        return 2
    return 0 if result["run_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
