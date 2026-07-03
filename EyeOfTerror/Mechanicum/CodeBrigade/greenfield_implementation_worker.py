#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

GuidanceFn = Callable[[str, dict[str, Any], str], dict[str, Any]]


def build_implementation_worker_plan(
    task: str,
    template_id: str,
    module_contracts: list[Any],
    expected_files: list[str],
    request_guidance: GuidanceFn | None = None,
) -> dict[str, Any]:
    source_files = [
        path
        for path in expected_files
        if path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css"))
        and "/tests/" not in f"/{path}"
        and not Path(path).name.startswith("test_")
    ]
    test_files = [path for path in expected_files if "test" in Path(path).name.lower() or "/tests/" in f"/{path}"]
    rows: list[dict[str, Any]] = []
    for index, contract in enumerate(module_contracts, start=1):
        if not isinstance(contract, dict):
            continue
        path = str(contract.get("path") or "")
        requirements = [str(item) for item in contract.get("requirements", []) if isinstance(item, str)]
        row = {
            "sequence": index,
            "module": str(contract.get("module") or ""),
            "path": path,
            "responsibility": str(contract.get("responsibility") or ""),
            "requirements": requirements,
            "requirement_trace": [
                {
                    "requirement": requirement,
                    "file": path,
                    "function_or_component": infer_symbol_name(path, requirement),
                    "verification_files": test_files,
                }
                for requirement in requirements
            ],
            "paired_tests": [test for test in test_files if paired_test_matches(path, test)] or test_files[:1],
            "status": "planned_for_implementation",
        }
        rows.append(row)
    if request_guidance is None:
        implementation_guidance: dict[str, Any] = {"status": "not_requested", "reason": "no guidance callback supplied"}
    else:
        implementation_guidance = request_guidance(
            "GreenfieldImplementationWorker",
            {
                "task": task,
                "template_id": template_id,
                "module_contracts": module_contracts,
                "source_files": source_files,
                "test_files": test_files,
            },
            "Plan module-by-module implementation from contracts. Preserve requirement to file/function/test trace and reject empty placeholder work.",
        )
    return {
        "kind": "code_brigade_greenfield_implementation_plan",
        "contract_version": "eye-mechanicum.v1",
        "role": "GreenfieldImplementationWorker",
        "template_id": template_id,
        "module_sequence": rows,
        "milestones": [
            {"name": "scaffold", "exit_gate": "workspace marker, manifests, README, entrypoints, and test folders exist"},
            {"name": "module_implementation", "exit_gate": "each module contract has source code and requirement trace"},
            {"name": "verification", "exit_gate": "allowlisted tests/build/smoke commands pass or return a clear blocker"},
        ],
        "anti_stub_policy": {
            "forbidden_markers": ["TODO", "pass #", "NotImplementedError", "placeholder"],
            "minimum_nonempty_source_files": len(source_files),
            "minimum_test_files": len(test_files),
        },
        "source_files": source_files,
        "test_files": test_files,
        "model_guidance": implementation_guidance,
    }


def build_implementation_trace(implementation_plan: dict[str, Any]) -> dict[str, Any]:
    module_sequence = implementation_plan.get("module_sequence") if isinstance(implementation_plan.get("module_sequence"), list) else []
    rows: list[dict[str, Any]] = []
    for module in module_sequence:
        if not isinstance(module, dict):
            continue
        trace_rows = module.get("requirement_trace") if isinstance(module.get("requirement_trace"), list) else []
        paired_tests = [str(path) for path in module.get("paired_tests", []) if isinstance(path, str)]
        for trace in trace_rows:
            if not isinstance(trace, dict):
                continue
            rows.append(
                {
                    "module": str(module.get("module") or ""),
                    "requirement": str(trace.get("requirement") or ""),
                    "file": str(trace.get("file") or module.get("path") or ""),
                    "function_or_component": str(trace.get("function_or_component") or ""),
                    "verification_files": [str(path) for path in trace.get("verification_files", []) if isinstance(path, str)],
                    "paired_tests": paired_tests,
                    "status": "planned",
                }
            )
    return {
        "kind": "code_brigade_greenfield_implementation_trace",
        "contract_version": "eye-mechanicum.v1",
        "status": "complete" if rows else "empty",
        "requirement_trace_count": len(rows),
        "module_count": len([row for row in module_sequence if isinstance(row, dict)]),
        "rows": rows,
    }


def infer_symbol_name(path: str, requirement: str) -> str:
    name = Path(path).stem
    lowered = f"{path} {requirement}".lower()
    if path.endswith((".html", ".css")):
        return name
    if path.endswith((".js", ".jsx", ".ts", ".tsx")):
        if "component" in lowered or "render" in lowered:
            return "component"
        return name
    if "cli" in lowered:
        return "main"
    if "health" in lowered:
        return "health"
    if "reply" in lowered:
        return "build_reply"
    if "summary" in lowered or "csv" in lowered:
        return "summarize_rows"
    if "structured" in lowered:
        return "build_tool_result"
    if "describe" in lowered:
        return "describe"
    if "ready" in lowered or "result" in lowered:
        return "run"
    return name


def paired_test_matches(source_path: str, test_path: str) -> bool:
    source_name = Path(source_path).stem.lower()
    test_name = Path(test_path).stem.lower()
    return source_name in test_name or source_name in test_path.lower()
