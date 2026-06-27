#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lexmechanic import run


def main() -> int:
    request = {
        "task_id": "test-skalathrax:source_discovery",
        "contract": {
            "goal": "Собери все известное о событиях Скалатракса и сделай реконструкцию.",
        },
        "step": {
            "expected_artifacts": ["/work/skalathrax/source_map.json"],
        },
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        result = run(request, Path(temp_dir))
        if not result.get("ok"):
            raise AssertionError(f"Lexmechanic failed: {result}")
        output = Path(temp_dir) / "skalathrax" / "source_map.json"
        if not output.exists():
            raise AssertionError(f"source map was not written: {output}")
        data = json.loads(output.read_text(encoding="utf-8"))
        titles = {item.get("title") for item in data.get("sources", [])}
        required = {"Lexicanum: Battle of Skalathrax", "Kharn: Eater of Worlds"}
        if not required.issubset(titles):
            raise AssertionError(f"source map lacks required source candidates: {titles}")
        if data.get("matched_playbooks") != ["skalathrax_sources"]:
            raise AssertionError(f"wrong matched playbooks: {data.get('matched_playbooks')}")
        if data.get("discovery_status") != "playbook_matched":
            raise AssertionError(f"wrong discovery status: {data.get('discovery_status')}")
        if not data.get("coverage_gaps"):
            raise AssertionError("source map must include coverage gaps")
        generic_request = {
            "task_id": "test-generic:source_discovery",
            "contract": {"goal": "Собери историю неизвестной битвы."},
            "step": {"expected_artifacts": ["/work/generic/source_map.json"]},
        }
        generic_result = run(generic_request, Path(temp_dir))
        if not generic_result.get("ok"):
            raise AssertionError(f"Lexmechanic generic fallback failed: {generic_result}")
        generic = json.loads((Path(temp_dir) / "generic" / "source_map.json").read_text(encoding="utf-8"))
        if generic.get("sources") != []:
            raise AssertionError(f"generic fallback should not invent sources: {generic['sources']}")
        if generic.get("discovery_status") != "needs_live_discovery":
            raise AssertionError(f"generic fallback should request live discovery: {generic}")
        if not any("live source discovery" in gap for gap in generic.get("coverage_gaps", [])):
            raise AssertionError(f"generic fallback should demand live discovery: {generic}")
    print("[ok] Lexmechanic source map")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
