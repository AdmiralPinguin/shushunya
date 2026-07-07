#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PRODUCTION_FILES = [
    ROOT / "EyeOfTerror" / "Warmaster" / "eye_of_terror" / "warmaster_gateway.py",
    ROOT / "EyeOfTerror" / "Warmaster" / "eye_of_terror" / "orchestrator.py",
    ROOT / "EyeOfTerror" / "Warmaster" / "eye_of_terror" / "campaigns.py",
]

STRICT_CALLS = {
    "prepare_task",
    "preflight_task",
    "orchestrate_prepare_task",
}


def call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def keyword_value(node: ast.Call, name: str) -> ast.AST | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def is_strict_value(node: ast.AST | None) -> bool:
    return (isinstance(node, ast.Constant) and node.value is True) or (isinstance(node, ast.Name) and node.id == "require_commander_order")


def assert_strict_callsite(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        if name not in STRICT_CALLS:
            continue
        if not is_strict_value(keyword_value(node, "require_commander_order")):
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno} {name} missing require_commander_order=True")
        if keyword_value(node, "commander_order") is None:
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno} {name} missing commander_order=...")
    if violations:
        raise AssertionError("\n".join(violations))


def main() -> int:
    for path in PRODUCTION_FILES:
        assert_strict_callsite(path)
    print("[ok] Strict commander callsites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
