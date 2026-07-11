#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]


COMPONENTS = {
    "CeraxiaLeadership": {
        "kind": "code_warband_leadership_governor",
        "root": "EyeOfTerror/Warmaster/eye_of_terror/inner_circle",
        "required_files": ["ceraxia.py", "ceraxia_service.py"],
        "maturity": "native_mission_leadership_boundary",
    },
    "NativeCodeRun": {
        "kind": "native_code_execution_boundary",
        "root": "EyeOfTerror/Warmaster/eye_of_terror",
        "required_files": [
            "native_code_run.py",
            "orchestrator.py",
            "skitarii_bridge.py",
        ],
        "maturity": "single_warband_delegation_with_persisted_directive",
    },
    "SkitariiWarband": {
        "kind": "autonomous_coding_warband",
        "root": "EyeOfTerror/Mechanicum/Skitarii",
        "required_files": [
            "service.py",
            "warband.py",
            "spec.py",
            "explorer.py",
            "planner.py",
            "executor.py",
            "reviewer.py",
            "acceptor.py",
            "mission_store.py",
        ],
        "maturity": "isolated_execution_with_private_acceptance_and_patch_bundle",
    },
}


ROADMAP = [
    {
        "priority": 1,
        "item": "keep a live gateway-to-verdict smoke in the required barrier",
        "owner": "Abaddon",
    },
    {
        "priority": 2,
        "item": "expand held-out behavioural evaluation across real repositories",
        "owner": "SkitariiWarband",
    },
    {
        "priority": 3,
        "item": "move Gateway and Ceraxia lifecycle under supervised services",
        "owner": "Abaddon",
    },
]


def component_status(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    component_root = REPO_ROOT / str(spec["root"])
    missing: list[str] = []
    present: list[str] = []
    for rel in spec["required_files"]:
        path = component_root / rel
        (present if path.is_file() else missing).append(rel)
    return {
        "name": name,
        "kind": spec["kind"],
        "root": str(component_root.relative_to(REPO_ROOT)),
        "maturity": spec["maturity"],
        "status": "ready" if not missing else "incomplete",
        "present_files": present,
        "missing_files": missing,
    }


def build_status() -> dict[str, Any]:
    components = [component_status(name, spec) for name, spec in COMPONENTS.items()]
    incomplete = [item["name"] for item in components if item["status"] != "ready"]
    architecture_contract = json.loads((ROOT / "architecture_contract.json").read_text(encoding="utf-8"))
    return {
        "ok": not incomplete,
        "kind": "eye_mechanicum_status",
        "root": str(ROOT),
        "architecture_contract": architecture_contract,
        "components": components,
        "incomplete_components": incomplete,
        "roadmap": ROADMAP,
        "next_architecture_step": ROADMAP[0]["item"] if not incomplete else "repair incomplete native components",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report the native Ceraxia/Skitarii architecture status.")
    parser.add_argument("--json", action="store_true", help="Print full JSON status.")
    args = parser.parse_args()
    status = build_status()
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        suffix = "ready" if status["ok"] else f"incomplete: {', '.join(status['incomplete_components'])}"
        prefix = "ok" if status["ok"] else "fail"
        print(f"[{prefix}] EyeOfTerror Mechanicum status: {suffix}")
    return 0 if status["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
