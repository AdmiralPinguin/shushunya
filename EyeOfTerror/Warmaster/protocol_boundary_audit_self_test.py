#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BOUNDARY_FILES = [
    ROOT / "EyeOfTerror" / "Warmaster" / "eye_of_terror" / "inner_circle" / "iskandar_service.py",
    ROOT / "EyeOfTerror" / "Warmaster" / "eye_of_terror" / "inner_circle" / "ceraxia_service.py",
    ROOT / "EyeOfTerror" / "Pictorium" / "Moriana" / "moriana_governor.py",
    ROOT / "EyeOfTerror" / "Mechanicum" / "PlanningBrigade" / "role_service.py",
]

DOC_FILES = [
    ROOT / "EyeOfTerror" / "Warmaster" / "contracts" / "governor_api.md",
]

FORBIDDEN_BOUNDARY_SNIPPETS = [
    'payload.get("task"',
    'payload.get("goal"',
    'payload.get("message"',
    'payload.get("request"',
    'body": {"task"',
]

FORBIDDEN_DOC_SNIPPETS = [
    '"body": {"task"',
    '"task": "<task>"',
    '"task": "User task text"',
]


def assert_clean(path: Path, forbidden: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    hits = [snippet for snippet in forbidden if snippet in text]
    if hits:
        raise AssertionError(f"{path.relative_to(ROOT)} contains forbidden protocol boundary snippets: {hits}")


def main() -> int:
    for path in BOUNDARY_FILES:
        assert_clean(path, FORBIDDEN_BOUNDARY_SNIPPETS)
    for path in DOC_FILES:
        assert_clean(path, FORBIDDEN_DOC_SNIPPETS)
    print("[ok] Protocol boundary audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
