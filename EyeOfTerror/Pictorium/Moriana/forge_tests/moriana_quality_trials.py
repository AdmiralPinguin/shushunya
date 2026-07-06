#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
WARMMASTER_ROOT = PROJECT_ROOT / "EyeOfTerror" / "Warmaster"
if str(WARMMASTER_ROOT) not in sys.path:
    sys.path.insert(0, str(WARMMASTER_ROOT))

from EyeOfTerror.Pictorium.Moriana.moriana_governor import create_or_execute_run


TRIALS = [
    {
        "id": "simple_image",
        "task": "нарисуй простую картинку древнего механикум-алтаря 512x512",
        "payload": {"execute": True, "test_artifact_mode": "good"},
        "expected_kind": "image",
    },
    {
        "id": "complex_character_environment",
        "task": "нарисуй сложную картинку техножреца в огромной кузне, много деталей окружения, единый стиль, 512x512",
        "payload": {"execute": True, "test_artifact_mode": "good"},
        "expected_kind": "image",
    },
    {
        "id": "image_series_3",
        "task": "сделай серию 3 изображения про один и тот же древний механикум-алтарь 512x512",
        "payload": {"execute": True, "test_artifact_mode": "series_good"},
        "expected_kind": "image_series",
    },
    {
        "id": "comic_4_panels",
        "task": "сделай комикс 4 панели про техножреца который запускает древнюю кузню",
        "payload": {"execute": True},
        "expected_kind": "comic",
    },
    {
        "id": "comic_8_panels",
        "task": "сделай комикс 8 панелей про один отряд сервиторов в мрачном цехе, сохрани персонажей и стиль",
        "payload": {"execute": True},
        "expected_kind": "comic",
    },
    {
        "id": "hard_style_character",
        "task": "нарисуй сложную картинку: один персонаж, строгая силуэтная узнаваемость, холодный индустриальный свет, без текста, 512x512",
        "payload": {"execute": True, "test_artifact_mode": "good"},
        "expected_kind": "image",
    },
    {
        "id": "existing_live_artifact",
        "task": "проверь уже готовую реальную картинку механикум-алтаря 512x512",
        "payload": {"execute": True, "external_artifact": True},
        "expected_kind": "image",
    },
]


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return payload


def trial_record(run_root: Path, trial: dict[str, Any], asset_root: Path) -> dict[str, Any]:
    payload = dict(trial["payload"])
    payload["task"] = trial["task"]
    payload["task_id"] = f"quality-{trial['id']}"
    if payload.pop("external_artifact", False):
        artifact_path = asset_root / f"{trial['id']}.png"
        Image.new("RGB", (512, 512), (40, 50, 70)).save(artifact_path)
        payload["artifact_path"] = str(artifact_path)
        payload["artifact_source"] = "quality_trial_existing_artifact"
        payload["job_spec"] = {"prompt": trial["task"], "width": 512, "height": 512}
    result = create_or_execute_run(run_root, payload)
    run_dir = Path(str(result["run_dir"]))
    quality = load_json(run_dir / "final" / "quality_report.json")
    final = load_json(run_dir / "final" / "final_manifest.json")
    status = load_json(run_dir / "status.json")
    artifacts = load_json(run_dir / "artifact_registry.json").get("artifacts", [])
    if not isinstance(artifacts, list):
        artifacts = []
    return {
        "id": trial["id"],
        "task": trial["task"],
        "expected_kind": trial["expected_kind"],
        "run_id": result["run_id"],
        "run_dir": str(run_dir),
        "ok": bool(result.get("ok")),
        "task_kind": status.get("task_kind"),
        "run_status": status.get("status"),
        "final_kind": final.get("kind"),
        "final_status": final.get("status"),
        "quality_score": quality.get("score"),
        "quality_next_action": quality.get("next_action"),
        "delivery_ready": quality.get("delivery_ready"),
        "blocker_count": quality.get("blocker_count"),
        "accepted_image_count": quality.get("accepted_image_count"),
        "revision_target_count": len(quality.get("revision_targets") if isinstance(quality.get("revision_targets"), list) else []),
        "artifact_count": len(artifacts),
        "artifact_status_counts": quality.get("artifact_counts", {}).get("by_status", {}),
        "artifact_type_counts": quality.get("artifact_counts", {}).get("by_type", {}),
        "limitations": quality.get("audit_limits", []),
        "coverage_mode": "existing_live_artifact" if payload.get("artifact_path") else ("synthetic_image" if trial["payload"].get("test_artifact_mode") else "manifest_orchestration"),
    }


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    weak_cases = [
        item
        for item in records
        if not item.get("delivery_ready") or item.get("quality_next_action") != "accept_final"
    ]
    task_kinds = Counter(str(item.get("task_kind") or "unknown") for item in records)
    final_statuses = Counter(str(item.get("final_status") or "unknown") for item in records)
    coverage_gaps = []
    for item in records:
        if item.get("coverage_mode") == "synthetic_image":
            coverage_gaps.append(
                {
                    "id": item["id"],
                    "gap": "synthetic_image_artifact",
                    "impact": "orchestration and deterministic verification are covered, live model visual quality is not",
                }
            )
        if item.get("coverage_mode") == "existing_live_artifact":
            continue
        if item.get("task_kind") == "comic" and int(item.get("accepted_image_count") or 0) == 0:
            coverage_gaps.append(
                {
                    "id": item["id"],
                    "gap": "comic_panel_art_not_generated",
                    "impact": "comic planning, panel packages, and layout are covered, but actual generated panel art is not",
                }
            )
    min_score = min((int(item.get("quality_score") or 0) for item in records), default=0)
    avg_score = round(sum(int(item.get("quality_score") or 0) for item in records) / max(1, len(records)), 2)
    return {
        "kind": "pictorium_quality_trial_report",
        "trial_count": len(records),
        "task_kinds": dict(sorted(task_kinds.items())),
        "final_statuses": dict(sorted(final_statuses.items())),
        "min_quality_score": min_score,
        "avg_quality_score": avg_score,
        "weak_case_count": len(weak_cases),
        "coverage_gap_count": len(coverage_gaps),
        "coverage_gaps": coverage_gaps,
        "weak_cases": [
            {
                "id": item["id"],
                "task_kind": item.get("task_kind"),
                "final_status": item.get("final_status"),
                "quality_score": item.get("quality_score"),
                "next_action": item.get("quality_next_action"),
                "blocker_count": item.get("blocker_count"),
                "revision_target_count": item.get("revision_target_count"),
            }
            for item in weak_cases
        ],
        "records": records,
        "limits": [
            "This trial runner uses synthetic accepted image artifacts for image cases unless a live Forge worker is explicitly used elsewhere.",
            "Comic trials validate orchestration, continuity packets, manifests, and quality reporting, not semantic panel art quality.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Moriana visual quality trial suite.")
    parser.add_argument("--report-json", default="")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="moriana-quality-trials-") as tmp:
        run_root = Path(tmp) / "runtime" / "pictorium" / "runs"
        asset_root = Path(tmp) / "assets"
        asset_root.mkdir(parents=True, exist_ok=True)
        records = [trial_record(run_root, trial, asset_root) for trial in TRIALS]
        report = aggregate(records)
    if args.report_json:
        path = Path(args.report_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    expected_kinds = {trial["id"]: trial["expected_kind"] for trial in TRIALS}
    errors = []
    for record in report["records"]:
        if record.get("task_kind") != expected_kinds[record["id"]]:
            errors.append(f"{record['id']} expected {expected_kinds[record['id']]}, got {record.get('task_kind')}")
        if not Path(str(record.get("run_dir") or "")).name:
            errors.append(f"{record['id']} did not return a run_dir")
        if not isinstance(record.get("quality_score"), int):
            errors.append(f"{record['id']} missing integer quality_score")
    if errors:
        raise AssertionError("; ".join(errors))
    print(
        json.dumps(
            {
                "ok": True,
                "trial_count": report["trial_count"],
                "weak_case_count": report["weak_case_count"],
                "coverage_gap_count": report["coverage_gap_count"],
                "avg_quality_score": report["avg_quality_score"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
