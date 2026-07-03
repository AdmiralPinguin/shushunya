#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from greenfield_feature_worker import apply_task_feature_overrides
from greenfield_templates import GREENFIELD_MARKER, PROJECT_TYPES, STACK_DEFAULTS, template_for, template_id_for_project_type

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.model_brain import request_model_decision  # noqa: E402


def request_greenfield_model_guidance(role: str, payload: dict[str, Any], instructions: str) -> dict[str, Any]:
    previous_timeout = os.environ.get("EYE_MODEL_TIMEOUT_SEC")
    if previous_timeout is None:
        os.environ["EYE_MODEL_TIMEOUT_SEC"] = "3"
    try:
        return request_model_decision(
            "CodeBrigade",
            role,
            payload,
            layer="code_worker",
            instructions=instructions,
        )
    finally:
        if previous_timeout is None:
            os.environ.pop("EYE_MODEL_TIMEOUT_SEC", None)
        else:
            os.environ["EYE_MODEL_TIMEOUT_SEC"] = previous_timeout


def project_name_from_task(task: str) -> str:
    name_match = re.search(r"`([^`/\\]+)`", task)
    if name_match:
        return re.sub(r"[^A-Za-z0-9_-]+", "-", name_match.group(1)).strip("-") or "ceraxia_project"
    return "ceraxia_project"


def infer_project_type(task: str) -> str:
    lowered = task.lower()
    if any(word in lowered for word in ("fastapi", "api", "http", "server", "сервер", "апи", "endpoint")):
        return "api_service"
    if any(word in lowered for word in ("site", "website", "frontend", "html", "css", "vite", "react", "vue", "сайт", "страниц")):
        return "web_app"
    if any(word in lowered for word in ("library", "package", "sdk", "библиот")):
        return "library"
    if any(word in lowered for word in ("bot", "telegram", "бот")):
        return "bot"
    if any(word in lowered for word in ("game", "игр")):
        return "game"
    return "cli_tool"


def build_greenfield_project_brief(task: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    project_name = str(payload.get("project_name") or project_name_from_task(task))
    project_type = str(payload.get("project_type") or infer_project_type(task))
    if project_type not in PROJECT_TYPES:
        project_type = "automation_tool"
    template_id = str(payload.get("template_id") or template_id_for_project_type(project_type, task))
    template = template_for(template_id, project_name)
    files = list(payload.get("files") if isinstance(payload.get("files"), list) and payload.get("files") else template["files"])
    verification_commands = payload.get("verification_commands") if isinstance(payload.get("verification_commands"), list) else template["verification_commands"]
    file_paths = [str(item.get("path") or "") for item in files if isinstance(item, dict) and item.get("path")]
    default_entrypoints = template["entrypoints"]
    default_run_commands = template["run_commands"]
    if isinstance(payload.get("files"), list) and "app.py" in file_paths:
        default_entrypoints = [{"name": "app", "command": "python app.py", "path": "app.py"}]
        default_run_commands = ["python app.py"]
    run_commands = payload.get("run_commands") if isinstance(payload.get("run_commands"), list) else default_run_commands
    entrypoints = payload.get("entrypoints") if isinstance(payload.get("entrypoints"), list) else default_entrypoints
    stack = payload.get("stack") if isinstance(payload.get("stack"), dict) else STACK_DEFAULTS.get(template_id, STACK_DEFAULTS["python_cli_basic"])
    if "README.md" not in file_paths:
        files.append(
            {
                "path": "README.md",
                "content": (
                    f"# {project_name}\n\n## Run\n\n"
                    + "\n".join(f"```bash\n{command}\n```" for command in run_commands)
                    + "\n\n## Test\n\n"
                    + "\n".join(f"```bash\n{command}\n```" for command in verification_commands)
                    + "\n"
                ),
            }
        )
        file_paths.append("README.md")
    expected_files = [str(item.get("path") or "") for item in files if isinstance(item, dict) and item.get("path")]
    if isinstance(payload.get("module_contracts"), list):
        module_contracts = payload["module_contracts"]
    elif isinstance(payload.get("files"), list) and "app.py" in file_paths:
        module_contracts = [
            {"module": "app", "path": "app.py", "responsibility": "application entrypoint and behavior", "requirements": ["run without syntax errors"]},
            {"module": "test_app", "path": "test_app.py", "responsibility": "behavior verification", "requirements": ["prove app behavior"]},
        ]
    else:
        module_contracts = template["module_contracts"]
    base_file_paths = [str(item.get("path") or "") for item in files if isinstance(item, dict) and item.get("path")]
    base_contract_paths = [str(item.get("path") or "") for item in module_contracts if isinstance(item, dict) and item.get("path")]
    files, module_contracts, acceptance_features = apply_task_feature_overrides(task, template_id, project_name, files, module_contracts)
    if any(feature.get("id") == "calculator_operations" for feature in acceptance_features):
        package = project_name.replace("-", "_")
        run_commands = [f"python -m {package}.cli add 2 3"]
        entrypoints = [{"name": "cli", "command": run_commands[0], "path": f"{package}/cli.py"}]
    expected_files = [str(item.get("path") or "") for item in files if isinstance(item, dict) and item.get("path")]
    definition_of_done = payload.get("definition_of_done") if isinstance(payload.get("definition_of_done"), list) else [
        "expected files are created inside the assigned workspace",
        "entrypoints named in the project brief exist",
        "allowlisted verification commands pass or blockers are explicit",
        "README documents real run and verification commands",
    ]
    model_guidance = request_greenfield_model_guidance(
        "GreenfieldArchitect",
        {
            "task": task,
            "project_type": project_type,
            "template_id": template_id,
            "expected_files": expected_files,
            "module_contracts": module_contracts,
            "acceptance_features": acceptance_features,
            "definition_of_done": definition_of_done,
        },
        "Review the greenfield architecture plan, identify missing modules, verification gaps, and scaffold risks. Return concise guidance.",
    )
    implementation_plan = build_implementation_worker_plan(task, template_id, module_contracts, expected_files)
    implementation_trace = build_implementation_trace(implementation_plan)
    implementation_feature_report = build_implementation_feature_report(
        task,
        template_id,
        acceptance_features,
        base_file_paths,
        files,
        base_contract_paths,
        module_contracts,
    )
    brief = {
        "kind": "code_brigade_greenfield_project_brief",
        "contract_version": "eye-mechanicum.v1",
        "project_name": project_name,
        "project_type": project_type,
        "template_id": template_id,
        "template_contract": {
            "template_id": template_id,
            "required_files": expected_files,
            "optional_files": template.get("optional_files", []),
            "install_commands": payload.get("install_commands", []),
            "run_commands": run_commands,
            "verification_commands": verification_commands,
            "expected_tree": expected_files,
            "common_failure_fixes": template.get("common_failure_fixes", []),
        },
        "stack": stack,
        "entrypoints": entrypoints,
        "expected_files": expected_files,
        "files": files,
        "verification_commands": verification_commands,
        "run_commands": run_commands,
        "artifact_contract": {
            "required": ["README.md", GREENFIELD_MARKER],
            "source_files": [path for path in expected_files if path.endswith((".py", ".js", ".ts", ".html", ".css"))],
            "test_files": [path for path in expected_files if "test" in Path(path).name.lower() or "/tests/" in f"/{path}"],
            "manifest_files": [path for path in expected_files if Path(path).name in {"pyproject.toml", "requirements.txt", "package.json", "build.gradle"}],
        },
        "workspace_policy": {
            "marker": GREENFIELD_MARKER,
            "allowed_when": ["target directory is empty", "target directory already contains the greenfield marker"],
            "forbidden_when": ["target directory has user files and no greenfield marker"],
        },
        "definition_of_done": definition_of_done,
        "acceptance_features": acceptance_features,
        "architecture_plan": {
            "summary": str(payload.get("summary") or template.get("summary") or f"{template_id} scaffold"),
            "selected_template": template_id,
            "selection_reason": "deterministic GreenfieldArchitect selected the smallest template matching task type",
            "mvp_boundaries": ["working entrypoint", "focused tests", "README with real commands", "task-derived acceptance features implemented when detected"],
            "anti_stub_policy": "non-trivial projects must expose separate entrypoint, implementation module, and tests",
            "model_guidance": model_guidance,
        },
        "file_tree_plan": [{"path": path, "role": "planned_project_file"} for path in expected_files],
        "module_contracts": module_contracts,
        "implementation_plan": implementation_plan,
        "implementation_trace": implementation_trace,
        "implementation_feature_report": implementation_feature_report,
        "verification_plan": {
            "commands": verification_commands,
            "run_commands": run_commands,
            "smoke_checks": ["entrypoint paths exist", "README mentions run/test commands"],
            "loop_stop_conditions": [
                "same verification failure repeats",
                "dependency is unavailable",
                "task requires external secrets",
                "workspace policy is violated",
            ],
        },
        "dependency_plan": {
            "package_manager": stack.get("package_manager", "none"),
            "install_commands": payload.get("install_commands", []),
            "manifest_files": [path for path in expected_files if Path(path).name in {"pyproject.toml", "requirements.txt", "package.json", "build.gradle", "settings.gradle", "gradle.properties"}],
            "lockfile_policy": "preserve lock files only when package manager generates them inside workspace",
            "dependency_strategy": {
                "package_manager_required": stack.get("package_manager", "none") != "none",
                "install_default": "record_manifest_only",
                "install_execution": "only explicit allowlisted install_commands inside greenfield workspace",
                "junk_dependency_policy": "only template-declared dependencies or explicit payload install commands are allowed",
            },
        },
    }
    attach_greenfield_plan_artifacts(brief)
    return brief


def build_implementation_feature_report(
    task: str,
    template_id: str,
    acceptance_features: list[dict[str, Any]],
    base_file_paths: list[str],
    files: list[Any],
    base_contract_paths: list[str],
    module_contracts: list[Any],
) -> dict[str, Any]:
    generated_paths = [str(item.get("path") or "") for item in files if isinstance(item, dict) and item.get("path")]
    contract_paths = [str(item.get("path") or "") for item in module_contracts if isinstance(item, dict) and item.get("path")]
    feature_ids = [str(feature.get("id") or "") for feature in acceptance_features if isinstance(feature, dict) and feature.get("id")]
    changed_contract_paths = sorted(set(contract_paths) - set(base_contract_paths)) or sorted(set(contract_paths) if feature_ids else [])
    changed_file_paths = sorted(set(generated_paths) - set(base_file_paths))
    if feature_ids and not changed_file_paths:
        changed_file_paths = sorted(set(generated_paths))
    guidance = request_greenfield_model_guidance(
        "GreenfieldImplementationWorker",
        {
            "task": task,
            "template_id": template_id,
            "recognized_feature_ids": feature_ids,
            "generated_file_paths": generated_paths,
            "changed_file_paths": changed_file_paths,
            "module_contract_paths": contract_paths,
        },
        "Review task-derived implementation features. Identify missing behavior, generated files, paired tests, and risks before verification.",
    )
    return {
        "kind": "code_brigade_greenfield_implementation_feature_report",
        "contract_version": "eye-mechanicum.v1",
        "template_id": template_id,
        "recognized_feature_ids": feature_ids,
        "feature_count": len(feature_ids),
        "features": acceptance_features,
        "changed_file_paths": changed_file_paths,
        "changed_module_contract_paths": changed_contract_paths,
        "implementation_strategy": "task-derived feature override" if feature_ids else "template scaffold without task-derived override",
        "model_guidance": guidance,
    }


def attach_greenfield_plan_artifacts(brief: dict[str, Any]) -> None:
    artifact_specs = [
        ("architecture_plan.json", "architecture_plan"),
        ("file_tree_plan.json", "file_tree_plan"),
        ("module_contracts.json", "module_contracts"),
        ("implementation_trace.json", "implementation_trace"),
        ("verification_plan.json", "verification_plan"),
    ]
    files = brief.get("files") if isinstance(brief.get("files"), list) else []
    existing_paths = {str(item.get("path") or "") for item in files if isinstance(item, dict)}
    plan_paths: list[str] = []
    for rel_path, _key in artifact_specs:
        plan_paths.append(rel_path)
    expected_files = [str(path) for path in brief.get("expected_files", []) if isinstance(path, str)]
    for rel_path in plan_paths:
        if rel_path not in expected_files:
            expected_files.append(rel_path)
    brief["expected_files"] = expected_files
    file_tree_plan = brief.get("file_tree_plan") if isinstance(brief.get("file_tree_plan"), list) else []
    planned_paths = {str(row.get("path") or "") for row in file_tree_plan if isinstance(row, dict)}
    for rel_path in plan_paths:
        if rel_path not in planned_paths:
            file_tree_plan.append({"path": rel_path, "role": "greenfield_plan_artifact"})
    brief["file_tree_plan"] = file_tree_plan
    template_contract = brief.get("template_contract") if isinstance(brief.get("template_contract"), dict) else {}
    template_contract["required_files"] = expected_files
    template_contract["expected_tree"] = expected_files
    brief["template_contract"] = template_contract
    artifact_contract = brief.get("artifact_contract") if isinstance(brief.get("artifact_contract"), dict) else {}
    required = [str(path) for path in artifact_contract.get("required", []) if isinstance(path, str)]
    for rel_path in plan_paths:
        if rel_path not in required:
            required.append(rel_path)
    artifact_contract["required"] = required
    brief["artifact_contract"] = artifact_contract
    for rel_path, key in artifact_specs:
        if rel_path not in existing_paths:
            files.append({"path": rel_path, "content": json.dumps(brief.get(key, {}), ensure_ascii=False, indent=2, sort_keys=True) + "\n"})
    brief["files"] = files


def build_implementation_worker_plan(
    task: str,
    template_id: str,
    module_contracts: list[Any],
    expected_files: list[str],
) -> dict[str, Any]:
    source_files = [path for path in expected_files if path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css")) and "/tests/" not in f"/{path}" and not Path(path).name.startswith("test_")]
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
    implementation_guidance = request_greenfield_model_guidance(
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
