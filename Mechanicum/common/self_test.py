#!/usr/bin/env python3
from __future__ import annotations

from swe_guardrails import build_repo_map, python_module_name, test_source_links


def main() -> int:
    symbols = [
        {"path": "sample.py", "module": "sample", "functions": ["value"], "classes": [], "imports": []},
        {"path": "test_sample.py", "module": "test_sample", "functions": ["test_value"], "classes": [], "imports": ["sample.value"]},
    ]
    if python_module_name("pkg/__init__.py") != "pkg" or python_module_name("pkg/core.py") != "pkg.core":
        raise AssertionError("bad Python module name conversion")
    links = test_source_links(symbols)
    if links != [{"test_path": "test_sample.py", "source_paths": ["sample.py"]}]:
        raise AssertionError(f"bad test-source links: {links}")
    repo_map = build_repo_map("почини value", [], ["test_sample.py"], symbols)
    ranked = repo_map.get("ranked_files", [])
    if not ranked or ranked[0].get("path") != "sample.py":
        raise AssertionError(f"imported source should outrank the test file: {repo_map}")
    print("[ok] Mechanicum common SWE guardrails")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
