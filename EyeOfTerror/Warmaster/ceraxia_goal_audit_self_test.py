#!/usr/bin/env python3
from __future__ import annotations

from ceraxia_goal_audit import build_audit


def main() -> int:
    audit = build_audit()
    if audit.get("kind") != "ceraxia_goal_audit":
        raise AssertionError(f"bad audit kind: {audit}")
    if audit.get("status") != "proven":
        raise AssertionError(f"Ceraxia goal audit is not fully proven: {audit}")
    requirements = audit.get("requirements")
    if not isinstance(requirements, list) or len(requirements) != 10:
        raise AssertionError(f"Ceraxia goal audit must cover all 10 objective items: {audit}")
    seen = {str(item.get("id") or "") for item in requirements if isinstance(item, dict)}
    if seen != {str(index) for index in range(1, 11)}:
        raise AssertionError(f"Ceraxia goal audit requirement ids drifted: {seen}")
    for item in requirements:
        if not isinstance(item, dict):
            raise AssertionError(f"requirement row is not an object: {item}")
        if item.get("status") != "proven":
            raise AssertionError(f"requirement is not proven: {item}")
        checks = item.get("checks")
        if not isinstance(checks, list) or len(checks) < 3:
            raise AssertionError(f"requirement has too little evidence: {item}")
        for check in checks:
            if not isinstance(check, dict) or check.get("passed") is not True or not check.get("evidence"):
                raise AssertionError(f"bad audit evidence check: requirement={item.get('id')} check={check}")
    summary = audit.get("field_trial_report_summary") if isinstance(audit.get("field_trial_report_summary"), dict) else {}
    if summary.get("fresh_honest_trial_count", 0) < 12 or summary.get("fresh_honest_class_count", 0) < 8:
        raise AssertionError(f"fresh field trial summary no longer proves the target: {summary}")
    if summary.get("target_met") is not True or summary.get("expert_target_met") is not True:
        raise AssertionError(f"strict Ceraxia targets are no longer met: {summary}")
    print("[ok] Ceraxia goal audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
