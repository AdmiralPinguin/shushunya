#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "InnerCircle" / "Ceraxia" / "field_trials.json"
PROTOCOL = ROOT / "InnerCircle" / "Ceraxia" / "EVALUATION.md"


def main() -> int:
    data = json.loads(SPEC.read_text(encoding="utf-8"))
    protocol = PROTOCOL.read_text(encoding="utf-8")
    dimensions = data.get("dimensions", [])
    trials = data.get("trials", [])
    target = data.get("target", {})
    if target.get("minimum_representative_trials", 0) < 12:
        raise AssertionError(f"Ceraxia field trials target is too small: {target}")
    if len(trials) < target.get("minimum_representative_trials", 0):
        raise AssertionError(f"Ceraxia field trial suite is undersized: {len(trials)} {target}")
    if len(set(dimensions)) != len(dimensions) or len(dimensions) < 8:
        raise AssertionError(f"Ceraxia dimensions are missing or duplicated: {dimensions}")
    seen: set[str] = set()
    classes: set[str] = set()
    for trial in trials:
        trial_id = str(trial.get("id") or "")
        if not trial_id or trial_id in seen:
            raise AssertionError(f"bad or duplicate Ceraxia trial id: {trial}")
        seen.add(trial_id)
        classes.add(str(trial.get("class") or ""))
        if not trial.get("task") or not trial.get("required_evidence") or not trial.get("failure_modes_to_watch"):
            raise AssertionError(f"Ceraxia field trial lacks task/evidence/failure modes: {trial}")
    if len(classes) < 8:
        raise AssertionError(f"Ceraxia field trials are not diverse enough: {classes}")
    ledger_scores = data.get("ledger_template", {}).get("scores", {})
    if set(ledger_scores) != set(dimensions):
        raise AssertionError(f"Ceraxia ledger scores drift from dimensions: {ledger_scores} {dimensions}")
    required_phrases = [
        "A scripted self-test proves only that a known scenario still works.",
        "The real 7/10 target is met only when",
        "human-readable review notes",
    ]
    for phrase in required_phrases:
        if phrase not in protocol:
            raise AssertionError(f"Ceraxia evaluation protocol lost core warning: {phrase}")
    print("[ok] Ceraxia field trials specification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
