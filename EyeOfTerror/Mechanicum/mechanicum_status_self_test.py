#!/usr/bin/env python3
from __future__ import annotations

import mechanicum_status


def main() -> int:
    status = mechanicum_status.build_status()
    if not status["ok"]:
        raise AssertionError(f"Mechanicum status should be ready: {status}")
    by_name = {item["name"]: item for item in status["components"]}
    if by_name["CodeBrigade"]["maturity"] != "contract_only":
        raise AssertionError(f"CodeBrigade should honestly report contract-only maturity: {by_name['CodeBrigade']}")
    if by_name["Ceraxia"]["maturity"] != "dry_run_controller":
        raise AssertionError(f"Ceraxia should honestly report dry-run maturity: {by_name['Ceraxia']}")
    if "wire CodeBrigade real execution adapter" not in status["next_architecture_step"]:
        raise AssertionError(f"status should point to the next architecture gap: {status}")
    print("[ok] EyeOfTerror Mechanicum status report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
