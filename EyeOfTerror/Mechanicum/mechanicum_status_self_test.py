#!/usr/bin/env python3
from __future__ import annotations

import mechanicum_status


def main() -> int:
    status = mechanicum_status.build_status()
    if not status["ok"]:
        raise AssertionError(f"Mechanicum status should be ready: {status}")
    if status["architecture_contract"]["governance_root"] != "EyeOfTerror/Mechanicum":
        raise AssertionError(f"status should expose the architecture contract: {status}")
    by_name = {item["name"]: item for item in status["components"]}
    code_maturity = by_name["CodeBrigade"]["maturity"]
    if "blocked_execution_adapter" not in code_maturity or "preflight" not in code_maturity or "allowlisted_verification" not in code_maturity:
        raise AssertionError(f"CodeBrigade should honestly report preflight and verification-adapter maturity: {by_name['CodeBrigade']}")
    if by_name["Ceraxia"]["maturity"] != "dry_run_controller_with_import_edges_evidence_matrix_and_verification":
        raise AssertionError(f"Ceraxia should honestly report dry-run survey and verification maturity: {by_name['Ceraxia']}")
    if "replace blocked CodeBrigade execution adapter" not in status["next_architecture_step"]:
        raise AssertionError(f"status should point to the next architecture gap: {status}")
    if status["roadmap"][0]["owner"] != "CodeBrigade":
        raise AssertionError(f"first roadmap item should target CodeBrigade: {status}")
    if [item["priority"] for item in status["roadmap"]] != sorted(item["priority"] for item in status["roadmap"]):
        raise AssertionError(f"roadmap priorities should be sorted: {status}")
    print("[ok] EyeOfTerror Mechanicum status report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
