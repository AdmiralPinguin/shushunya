#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eye_of_terror.task_prepare import preflight_task, prepare_task
from eye_of_terror.views import payload_with_task_view


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        run_root = Path(temp_dir)
        code_preflight = preflight_task(
            "почини python bug в приложении и прогони тесты",
            "warmaster-code-preflight",
            run_root,
            governor_transport="local",
        )
        if (
            code_preflight.get("ok")
            or code_preflight.get("governor") != "Ceraxia"
            or code_preflight.get("route", {}).get("kind") != "code"
            or code_preflight.get("error_code") != "ceraxia_leader_service_required"
            or code_preflight.get("actions", {}).get("next_action", {}).get("body", {}).get("governor_transport") != "http"
        ):
            raise AssertionError(f"code preflight must route to the live Ceraxia leader: {code_preflight}")

        comic_preflight = preflight_task(
            "сделай комикс 4 панели про техножреца",
            "warmaster-comic-preflight",
            run_root,
            governor_transport="local",
        )
        if (
            not comic_preflight.get("ok")
            or comic_preflight.get("governor") != "Moriana"
            or comic_preflight.get("route", {}).get("kind") != "comic_generation"
            or comic_preflight.get("contract_summary", {}).get("kind") != "comic_generation"
        ):
            raise AssertionError(f"comic preflight must route to Moriana comic contract: {comic_preflight}")

        series_preflight = preflight_task(
            "сделай серию 3 изображения про одну кузню",
            "warmaster-series-preflight",
            run_root,
            governor_transport="local",
        )
        if (
            not series_preflight.get("ok")
            or series_preflight.get("governor") != "Moriana"
            or series_preflight.get("route", {}).get("kind") != "image_series_generation"
            or series_preflight.get("contract_summary", {}).get("kind") != "image_series_generation"
        ):
            raise AssertionError(f"series preflight must route to Moriana series contract: {series_preflight}")

        mixed_task = "собери обзор источников по RISC-V и реализуй python демо код"
        mixed_preflight = payload_with_task_view(
            preflight_task(mixed_task, "warmaster-mixed-preflight", run_root, governor_transport="local"),
            fallback_task_id="warmaster-mixed-preflight",
        )
        if (
            mixed_preflight.get("ok")
            or mixed_preflight.get("error_code") != "multi_governor_decomposition_required"
            or mixed_preflight.get("phase") != "decomposition_required"
            or mixed_preflight.get("route", {}).get("requires_decomposition") is not True
            or {item.get("name") for item in mixed_preflight.get("route", {}).get("matched_governors", []) if item.get("active")} != {"IskandarKhayon", "Ceraxia"}
            or not mixed_preflight.get("route", {}).get("supporting_governors")
        ):
            raise AssertionError(f"mixed task should require explicit decomposition: {mixed_preflight}")

        mixed_prepare = prepare_task(mixed_task, "warmaster-mixed-prepare", run_root, governor_transport="local")
        if mixed_prepare.get("ok") or mixed_prepare.get("error_code") != "multi_governor_decomposition_required":
            raise AssertionError(f"prepare_task must not silently create a single-governor mixed run: {mixed_prepare}")
    print("[ok] Warmaster strategy routing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
