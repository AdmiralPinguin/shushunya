#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
WARMMASTER_ROOT = PROJECT_ROOT / "EyeOfTerror" / "Warmaster"
if str(WARMMASTER_ROOT) not in sys.path:
    sys.path.insert(0, str(WARMMASTER_ROOT))

from EyeOfTerror.Pictorium.Moriana.moriana_governor import create_or_execute_run


TRIALS = [
    {
        "id": "live_simple_image",
        "task": "нарисуй простую картинку древнего механикум-алтаря 512x512",
        "expected_kind": "image",
    },
    {
        "id": "live_character_environment",
        "task": "нарисуй техножреца в огромной кузне, единый стиль, 512x512",
        "expected_kind": "image",
    },
    {
        "id": "live_comic_4_panels",
        "task": "сделай комикс 4 панели про техножреца который запускает древнюю кузню",
        "expected_kind": "comic",
    },
]


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def visual_artifacts(registry: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = registry.get("artifacts") if isinstance(registry.get("artifacts"), list) else []
    return [
        item
        for item in artifacts
        if isinstance(item, dict)
        and item.get("type") in {"image", "comic_panel"}
        and item.get("status") == "accepted"
    ]


def run_trial(run_root: Path, trial: dict[str, Any], *, max_wait_sec: float, poll_interval_sec: float) -> dict[str, Any]:
    payload = {
        "task": trial["task"],
        "task_id": trial["id"],
        "execute": True,
        "submit": True,
        "wait_for_result": True,
        "run_inline_once": True,
        "max_wait_sec": max_wait_sec,
        "poll_interval_sec": poll_interval_sec,
    }
    result = create_or_execute_run(run_root, payload)
    run_dir = Path(str(result.get("run_dir") or ""))
    status = read_json(run_dir / "status.json")
    final = read_json(run_dir / "final" / "final_manifest.json")
    quality = read_json(run_dir / "final" / "quality_report.json")
    registry = read_json(run_dir / "artifact_registry.json")
    accepted_visuals = visual_artifacts(registry)
    return {
        "id": trial["id"],
        "task": trial["task"],
        "expected_kind": trial["expected_kind"],
        "ok": bool(result.get("ok")),
        "run_dir": str(run_dir),
        "task_kind": status.get("task_kind"),
        "run_status": status.get("status"),
        "final_status": final.get("status"),
        "final_kind": final.get("kind"),
        "quality_score": quality.get("score"),
        "quality_next_action": quality.get("next_action"),
        "delivery_ready": quality.get("delivery_ready"),
        "blocker_count": quality.get("blocker_count"),
        "accepted_visual_artifact_count": quality.get("accepted_visual_artifact_count"),
        "accepted_visual_artifacts": [
            {
                "artifact_id": item.get("artifact_id"),
                "type": item.get("type"),
                "path": item.get("path"),
                "step": item.get("step"),
            }
            for item in accepted_visuals
        ],
        "revision_decision": result.get("revision_decision"),
    }


def build_report(records: list[dict[str, Any]], *, run_root: Path) -> dict[str, Any]:
    weak_cases = [
        item
        for item in records
        if not item.get("delivery_ready")
        or item.get("quality_next_action") != "accept_final"
        or int(item.get("accepted_visual_artifact_count") or 0) == 0
    ]
    avg_score = round(sum(int(item.get("quality_score") or 0) for item in records) / max(1, len(records)), 2)
    return {
        "kind": "pictorium_moriana_live_quality_trial_report",
        "run_root": str(run_root),
        "trial_count": len(records),
        "ok": not weak_cases,
        "avg_quality_score": avg_score,
        "weak_case_count": len(weak_cases),
        "weak_cases": [
            {
                "id": item["id"],
                "task_kind": item.get("task_kind"),
                "final_status": item.get("final_status"),
                "quality_score": item.get("quality_score"),
                "next_action": item.get("quality_next_action"),
                "accepted_visual_artifact_count": item.get("accepted_visual_artifact_count"),
                "blocker_count": item.get("blocker_count"),
            }
            for item in weak_cases
        ],
        "records": records,
        "readiness_verdict": "live_visual_trials_passed" if not weak_cases else "live_visual_trials_need_repair",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live Moriana visual quality trials through Moriana execution.")
    parser.add_argument("--report-json", default="")
    parser.add_argument("--run-root", default="")
    parser.add_argument("--profile", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--max-wait-sec", type=float, default=0.0)
    parser.add_argument("--poll-interval-sec", type=float, default=0.5)
    args = parser.parse_args()

    started_at = utc_now()
    with tempfile.TemporaryDirectory(prefix="moriana-live-quality-") as tmp:
        run_root = Path(args.run_root) if args.run_root else Path(tmp) / "runtime" / "pictorium" / "runs"
        run_root.mkdir(parents=True, exist_ok=True)
        selected_trials = TRIALS if args.profile == "full" else TRIALS[:1]
        records = [
            run_trial(
                run_root,
                trial,
                max_wait_sec=args.max_wait_sec,
                poll_interval_sec=args.poll_interval_sec,
            )
            for trial in selected_trials
        ]
        report = build_report(records, run_root=run_root)
        report["started_at"] = started_at
        report["finished_at"] = utc_now()
        if args.report_json:
            path = Path(args.report_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "trial_count": report["trial_count"],
                "weak_case_count": report["weak_case_count"],
                "avg_quality_score": report["avg_quality_score"],
                "readiness_verdict": report["readiness_verdict"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
