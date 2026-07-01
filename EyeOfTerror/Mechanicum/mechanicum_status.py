#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


COMPONENTS = {
    "Ceraxia": {
        "kind": "governor_controller",
        "required_files": [
            "README.md",
            "ceraxia.py",
            "repo_survey.py",
            "self_test.py",
            "contracts/implementation_brief.schema.json",
            "contracts/evidence_matrix.schema.json",
            "contracts/run_artifacts.schema.json",
        ],
        "maturity": "dry_run_controller_with_import_edges_evidence_matrix_and_verification",
    },
    "PlanningBrigade": {
        "kind": "advisory_planning_brigade",
        "required_files": [
            "README.md",
            "planning_brigade.py",
            "planning_contract.schema.json",
            "self_test.py",
            "TaskTriage/README.md",
            "RepoSurveyor/README.md",
            "DesignStrategos/README.md",
            "VerificationArchitect/README.md",
            "RiskScribe/README.md",
        ],
        "maturity": "contracted_advisory_layer",
    },
    "CodeBrigade": {
        "kind": "implementation_brigade_contract",
        "required_files": [
            "README.md",
            "code_brigade_contract.schema.json",
            "code_brigade_adapter.py",
            "execution_result.schema.json",
            "execution_policy.json",
            "verification_adapter.py",
            "self_test.py",
        ],
        "maturity": "dry_run_handoff_with_implementation_plan_and_allowlisted_verification",
    },
}


ROADMAP = [
    {
        "priority": 1,
        "item": "wire CodeBrigade real execution adapter",
        "reason": "Ceraxia can produce a validated handoff, but source mutation is still intentionally blocked.",
        "owner": "CodeBrigade",
    },
    {
        "priority": 2,
        "item": "deepen repository survey beyond Python import edges",
        "reason": "Ceraxia now maps Python symbols and local imports; multi-language call/dependency evidence is still shallow.",
        "owner": "Ceraxia",
    },
    {
        "priority": 3,
        "item": "promote verification execution evidence into review-specific gates",
        "reason": "Ceraxia can execute allowlisted verification and summarize evidence; review should next reason about which commands are sufficient for each risk.",
        "owner": "Ceraxia",
    },
    {
        "priority": 4,
        "item": "split PlanningBrigade roles into callable services only after packet stability",
        "reason": "The advisory packet is now useful; process splitting should wait until contracts stop moving.",
        "owner": "PlanningBrigade",
    },
]


def component_status(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    present: list[str] = []
    for rel in spec["required_files"]:
        path = ROOT / name / rel
        if path.is_file():
            present.append(rel)
        else:
            missing.append(rel)
    return {
        "name": name,
        "kind": spec["kind"],
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
        "next_architecture_step": ROADMAP[0]["item"] if not incomplete else "repair incomplete component contracts",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report EyeOfTerror/Mechanicum component status.")
    parser.add_argument("--json", action="store_true", help="Print full JSON status.")
    args = parser.parse_args()
    status = build_status()
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        suffix = "ready" if status["ok"] else f"incomplete: {', '.join(status['incomplete_components'])}"
        print(f"[ok] EyeOfTerror Mechanicum status: {suffix}" if status["ok"] else f"[fail] EyeOfTerror Mechanicum status: {suffix}")
    return 0 if status["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
