#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LAYOUT = ROOT / "runtime" / "live" / "layout.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the last real multi-monitor placement")
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    parser.add_argument("--expect", type=int, default=0, help="required display count")
    parser.add_argument("--max-age", type=int, default=180, help="maximum capture age in seconds")
    args = parser.parse_args()

    try:
        payload = json.loads(args.layout.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FAIL: cannot read {args.layout}: {exc}")
        return 2

    failures: list[str] = []
    captured_text = payload.get("captured_at")
    try:
        captured_at = datetime.fromisoformat(str(captured_text))
        age = max(0.0, (datetime.now().astimezone() - captured_at).total_seconds())
    except ValueError:
        age = float("inf")
        failures.append("capture timestamp is missing or invalid")
    if age > args.max_age:
        failures.append(f"capture is stale ({age:.0f}s > {args.max_age}s)")

    screens = payload.get("screens")
    if not isinstance(screens, list):
        screens = []
        failures.append("screen list is missing")
    if args.expect and len(screens) != args.expect:
        failures.append(f"expected {args.expect} screens, captured {len(screens)}")

    for item in screens:
        if not isinstance(item, dict):
            failures.append("invalid screen record")
            continue
        role = item.get("role", "unknown")
        expected = item.get("expected_screen_name", "unknown")
        actual = item.get("screen_name", "unknown")
        ok = item.get("placement_ok") is True
        state = "OK" if ok else "FAIL"
        print(f"{state}: {role}: {expected} -> {actual}; {item.get('visibility', 'unknown')}")
        if not ok:
            failures.append(f"{role} is not fullscreen on {expected}")

    if payload.get("placement_ok") is not True:
        failures.append("global placement gate is not OK")

    if failures:
        for failure in dict.fromkeys(failures):
            print(f"FAIL: {failure}")
        return 1

    print(f"PASS: {len(screens)} displays verified on {payload.get('platform', 'unknown')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
