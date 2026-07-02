#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ceraxia_evidence_contract import NEXT_STAGE_PACKAGE_KIND, next_stage_evidence_status


WARMASTER_ROOT = Path(__file__).resolve().parent
EYE_ROOT = WARMASTER_ROOT.parent
REPO_ROOT = EYE_ROOT.parent


def parse_artifact(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("artifacts must use key=path")
    key, path = value.split("=", 1)
    key = key.strip()
    path = path.strip()
    if not key or not path:
        raise argparse.ArgumentTypeError("artifact key and path must be non-empty")
    return key, path


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_package(args: argparse.Namespace) -> dict[str, Any]:
    artifacts = dict(args.artifact or [])
    return {
        "kind": NEXT_STAGE_PACKAGE_KIND,
        "contract_version": 1,
        "trial_id": args.trial_id,
        "run_id": args.run_id,
        "task_class": args.task_class,
        "status": args.status,
        "attempt_count": args.attempt_count,
        "real_repo_task": True,
        "fixture_only": False,
        "false_success": args.false_success,
        "multi_file_nonfixture": args.multi_file_nonfixture,
        "changed_files": args.changed_file or [],
        "verification_passed": args.verification_passed,
        "review_accepted": args.review_accepted,
        "postmortem": args.postmortem,
        "artifacts": artifacts,
    }


def build_entry(args: argparse.Namespace, package: dict[str, Any], package_path: Path | None) -> dict[str, Any]:
    next_stage: dict[str, Any] = {
        "status": args.status,
        "attempt_count": args.attempt_count,
        "class": args.task_class,
        "multi_file_nonfixture": args.multi_file_nonfixture,
        "false_success": args.false_success,
        "postmortem": args.postmortem,
        "evidence_package": str(package_path) if package_path else package,
    }
    return {
        "trial_id": args.trial_id,
        "run_id": args.run_id,
        "human_review_notes": args.postmortem,
        "next_stage": next_stage,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and validate a Ceraxia live next-stage evidence package.")
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-class", required=True)
    parser.add_argument(
        "--status",
        required=True,
        choices=[
            "fully_successful",
            "repaired_success",
            "honest_blocked",
            "failed",
            "broken",
            "reviewer_rejected",
        ],
    )
    parser.add_argument("--attempt-count", type=int, required=True)
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--artifact", action="append", type=parse_artifact, default=[])
    parser.add_argument("--multi-file-nonfixture", action="store_true")
    parser.add_argument("--false-success", action="store_true")
    parser.add_argument("--verification-passed", action="store_true")
    parser.add_argument("--review-accepted", action="store_true")
    parser.add_argument("--postmortem", default="")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    package = build_package(args)
    output_path = args.output.resolve() if args.output else None
    if output_path:
        write_json(output_path, package)
    entry = build_entry(args, package, output_path)
    status = next_stage_evidence_status(REPO_ROOT, entry, {"class": args.task_class})
    payload = {
        "package_path": str(output_path) if output_path else "",
        "entry": entry,
        "status": status,
        "package": package,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status.get("passed") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
