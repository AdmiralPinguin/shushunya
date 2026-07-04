#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from greenfield_feature_worker import apply_task_feature_overrides
from greenfield_implementation_worker import build_implementation_trace, build_implementation_worker_plan
from greenfield_scenario_worker import build_greenfield_scenario_plan
from greenfield_templates import GREENFIELD_MARKER, PROJECT_TYPES, STACK_DEFAULTS, template_for, template_id_for_project_type

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.model_brain import request_model_decision  # noqa: E402


def greenfield_model_runtime_defaults(role: str, payload: dict[str, Any]) -> dict[str, str]:
    synthesis_payload = isinstance(payload.get("synthesis_contract"), dict) or isinstance(payload.get("module_synthesis_contract"), dict)
    if role in {"GreenfieldImplementationWorker", "GreenfieldRepairWorker"} or synthesis_payload:
        return {
            "EYE_MODEL_TIMEOUT_SEC": "120",
            "EYE_MODEL_MAX_TOKENS": "4096",
            "EYE_MODEL_MAX_CONTEXT_CHARS": "50000",
        }
    if role == "GreenfieldReviewer":
        return {
            "EYE_MODEL_TIMEOUT_SEC": "45",
            "EYE_MODEL_MAX_TOKENS": "1024",
            "EYE_MODEL_MAX_CONTEXT_CHARS": "30000",
        }
    return {
        "EYE_MODEL_TIMEOUT_SEC": "30",
        "EYE_MODEL_MAX_TOKENS": "1024",
        "EYE_MODEL_MAX_CONTEXT_CHARS": "30000",
    }


def request_greenfield_model_guidance(role: str, payload: dict[str, Any], instructions: str) -> dict[str, Any]:
    runtime_defaults = greenfield_model_runtime_defaults(role, payload)
    previous_values = {key: os.environ.get(key) for key in runtime_defaults}
    for key, value in runtime_defaults.items():
        if previous_values[key] is None:
            os.environ[key] = value
    try:
        return request_model_decision(
            "CodeBrigade",
            role,
            payload,
            layer="code_worker",
            instructions=instructions,
        )
    finally:
        for key, previous in previous_values.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def project_name_from_task(task: str) -> str:
    name_match = re.search(r"`([^`/\\]+)`", task)
    if name_match:
        return re.sub(r"[^A-Za-z0-9_-]+", "-", name_match.group(1)).strip("-") or "ceraxia_project"
    return "ceraxia_project"


def infer_project_type(task: str) -> str:
    lowered = task.lower()
    has_data_intent = any(word in lowered for word in ("data", "csv", "pipeline", "analytics", "данн", "пайплайн", "аналитик"))
    has_strong_api_intent = any(word in lowered for word in ("fastapi", "http", "server", "сервер", "endpoint"))
    if any(word in lowered for word in ("browser tool", "browser app", "web tool", "site", "website", "frontend", "html", "css", "javascript", "svg", "браузер", "сайт", "страниц")):
        return "web_app"
    if has_data_intent and not has_strong_api_intent:
        return "automation_tool"
    if has_strong_api_intent or any(word in lowered for word in ("api", "апи")):
        return "api_service"
    if any(word in lowered for word in ("library", "package", "sdk", "библиот")):
        return "library"
    if any(word in lowered for word in ("site", "website", "frontend", "html", "css", "vite", "react", "vue", "сайт", "страниц")):
        return "web_app"
    if any(word in lowered for word in ("bot", "telegram", "бот")):
        return "bot"
    if any(word in lowered for word in ("game", "игр")):
        return "game"
    return "cli_tool"


def ensure_readme_documents_commands(
    files: list[Any],
    project_name: str,
    run_commands: list[Any],
    verification_commands: list[Any],
) -> list[Any]:
    run_command_rows = [str(command) for command in run_commands if isinstance(command, str) and command.strip()]
    verification_command_rows = [str(command) for command in verification_commands if isinstance(command, str) and command.strip()]
    readme_index = next(
        (
            index
            for index, item in enumerate(files)
            if isinstance(item, dict) and item.get("path") == "README.md"
        ),
        -1,
    )
    if readme_index < 0:
        files.append({"path": "README.md", "content": f"# {project_name}\n"})
        readme_index = len(files) - 1
    readme = files[readme_index]
    text = str(readme.get("content") or "")
    if not text.strip():
        text = f"# {project_name}\n"
    additions: list[str] = []
    for heading, commands in (("Run", run_command_rows), ("Test", verification_command_rows)):
        missing = [command for command in commands if command not in text]
        if missing:
            additions.append("\n".join([f"## {heading}", *[f"```bash\n{command}\n```" for command in missing]]))
    if additions:
        text = text.rstrip() + "\n\n" + "\n\n".join(additions) + "\n"
    readme["content"] = text
    files[readme_index] = readme
    return files


def extract_guidance_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        return {}
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_guidance_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if isinstance(item, str) and item.strip()]


def merged_guidance_strings(parsed: dict[str, Any], guidance: dict[str, Any], key: str) -> list[str]:
    return list(dict.fromkeys([*normalize_guidance_strings(guidance.get(key)), *normalize_guidance_strings(parsed.get(key))]))


def extract_guidance_module_path(item: str) -> str:
    match = re.search(r"([A-Za-z0-9_./-]+\.(?:py|js|jsx|ts|tsx|html|css))", item)
    if not match:
        return ""
    return match.group(1).strip("./")


def module_name_from_path(path: str) -> str:
    clean = path.strip().strip("/")
    suffix = Path(clean).suffix
    stem = clean[: -len(suffix)] if suffix else clean
    return stem.replace("/", ".").replace("-", "_")


def apply_architecture_model_module_constraints(
    project_name: str,
    files: list[Any],
    module_contracts: list[Any],
    model_constraints: dict[str, Any],
) -> tuple[list[Any], list[Any]]:
    existing_files = {str(item.get("path") or "") for item in files if isinstance(item, dict)}
    existing_contracts = {str(item.get("path") or "") for item in module_contracts if isinstance(item, dict)}
    next_files = list(files)
    initial_contracts = [dict(item) if isinstance(item, dict) else item for item in module_contracts]
    added_source_contracts: list[dict[str, Any]] = []
    added_test_requirements: list[str] = []
    constraint_items = [
        *[item for item in model_constraints.get("missing_modules", []) if isinstance(item, str)],
        *[item for item in model_constraints.get("evidence_required", []) if isinstance(item, str)],
    ]
    def resolve_constraint_path(rel_path: str) -> str:
        if rel_path in existing_files:
            return rel_path
        suffix_matches = [path for path in existing_files if path.endswith(f"/{rel_path}")]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        return rel_path

    for item in constraint_items:
        if not isinstance(item, str):
            continue
        rel_path = extract_guidance_module_path(item)
        if not rel_path:
            continue
        rel_path = resolve_constraint_path(rel_path)
        if rel_path not in existing_files:
            next_files.append({"path": rel_path, "content": ""})
            existing_files.add(rel_path)
        if rel_path not in existing_contracts:
            added_source_contracts.append(
                {
                    "module": module_name_from_path(rel_path),
                    "path": rel_path,
                    "responsibility": f"GreenfieldArchitect requested module for {project_name}",
                    "requirements": [item],
                    "source": "greenfield_architect_model_constraints",
                }
            )
            existing_contracts.add(rel_path)
            if not rel_path.startswith("tests/") and "/tests/" not in f"/{rel_path}":
                added_test_requirements.append(f"prove GreenfieldArchitect requested module: {item}")
    next_contracts: list[Any] = []
    inserted_added_sources = False
    for contract in initial_contracts:
        if not inserted_added_sources and isinstance(contract, dict):
            rel_path = str(contract.get("path") or "")
            if rel_path.startswith("test") or rel_path.startswith("tests/") or "/tests/" in f"/{rel_path}":
                next_contracts.extend(added_source_contracts)
                inserted_added_sources = True
        next_contracts.append(contract)
    if not inserted_added_sources:
        next_contracts.extend(added_source_contracts)
    if added_test_requirements:
        for contract in next_contracts:
            if not isinstance(contract, dict):
                continue
            rel_path = str(contract.get("path") or "")
            if not (rel_path.startswith("test") or rel_path.startswith("tests/") or "/tests/" in f"/{rel_path}"):
                continue
            requirements = [str(row) for row in contract.get("requirements", []) if isinstance(row, str)]
            contract["requirements"] = list(dict.fromkeys([*requirements, *added_test_requirements]))
    return next_files, next_contracts


def api_library_adapter_requested(task: str, template_id: str) -> bool:
    if template_id != "python_fastapi_service":
        return False
    lowered = task.lower()
    api_markers = ("fastapi", "api", "http", "server", "endpoint", "route", "adapter", "апи", "сервер")
    library_markers = ("library", "package", "sdk", "hybrid", "pure python", "adapter", "библиот")
    return any(marker in lowered for marker in api_markers) and any(marker in lowered for marker in library_markers)


def static_browser_tool_requested(task: str, template_id: str) -> bool:
    if template_id != "static_site":
        return False
    lowered = task.lower()
    browser_markers = ("browser tool", "web tool", "html", "css", "javascript", "svg", "браузер")
    tool_markers = ("textarea", "json", "render", "preview", "validation", "editor", "редакт", "узл", "связ")
    return any(marker in lowered for marker in browser_markers) and any(marker in lowered for marker in tool_markers)


def apply_static_browser_tool_contract(
    task: str,
    files: list[Any],
    module_contracts: list[Any],
    template_id: str,
) -> tuple[list[Any], list[Any]]:
    if not static_browser_tool_requested(task, template_id):
        return files, module_contracts
    next_contracts = [dict(item) if isinstance(item, dict) else item for item in module_contracts]
    required_by_path = {
        "index.html": [
            "render a textarea JSON editor",
            "render a button or control that triggers preview rendering",
            "render an SVG preview area for nodes and links",
            "render a validation error panel",
        ],
        "app.js": [
            "provide sample JSON map data",
            "parse JSON from the textarea",
            "render SVG nodes and links",
            "show validation errors without a backend",
        ],
        "styles.css": [
            "style the editor, render control, error panel, and SVG preview",
        ],
        "tests/test_static_site.py": [
            "prove HTML asset wiring",
            "prove textarea, render control, SVG preview, sample data, and validation markers exist",
        ],
    }
    existing_contract_paths = {str(item.get("path") or "") for item in next_contracts if isinstance(item, dict)}
    for contract in next_contracts:
        if not isinstance(contract, dict):
            continue
        rel_path = str(contract.get("path") or "")
        additions = required_by_path.get(rel_path)
        if not additions:
            continue
        requirements = [str(item) for item in contract.get("requirements", []) if isinstance(item, str)]
        contract["requirements"] = list(dict.fromkeys([*requirements, *additions]))
    for rel_path, additions in required_by_path.items():
        if rel_path in existing_contract_paths:
            continue
        module_name = Path(rel_path).stem.replace("-", "_")
        next_contracts.append(
            {
                "module": module_name,
                "path": rel_path,
                "responsibility": "Task-derived browser tool behavior",
                "requirements": additions,
                "source": "static_browser_tool_contract",
            }
        )
    return files, next_contracts


def apply_api_library_adapter_contract(
    task: str,
    files: list[Any],
    module_contracts: list[Any],
    verification_commands: list[Any],
    template_id: str,
) -> tuple[list[Any], list[Any], list[Any]]:
    if not api_library_adapter_requested(task, template_id):
        return files, module_contracts, verification_commands
    existing_files = {str(item.get("path") or "") for item in files if isinstance(item, dict)}
    existing_contracts = {str(item.get("path") or "") for item in module_contracts if isinstance(item, dict)}
    next_files = list(files)
    next_contracts = [dict(item) if isinstance(item, dict) else item for item in module_contracts]
    required_files = [
        ("app/service.py", "pure Python service/domain library for the API task"),
        ("app/routes.py", "HTTP route adapter helpers and optional FastAPI router wiring"),
        ("tests/test_api_contract.py", "API/library hybrid contract tests"),
    ]
    for rel_path, _description in required_files:
        if rel_path not in existing_files:
            next_files.append({"path": rel_path, "content": ""})
            existing_files.add(rel_path)
    required_contracts = [
        {
            "module": "app.service",
            "path": "app/service.py",
            "responsibility": "pure Python service/library behavior behind the HTTP API",
            "requirements": ["implement requested domain behavior without requiring a live server"],
            "source": "api_library_adapter_contract",
        },
        {
            "module": "app.routes",
            "path": "app/routes.py",
            "responsibility": "HTTP adapter helpers and FastAPI router wiring for the service layer",
            "requirements": [
                "expose route adapter functions",
                "import safely when FastAPI is not installed by wrapping FastAPI imports in try/except ModuleNotFoundError",
                "wire APIRouter when FastAPI is installed",
                "delegate to app.service",
            ],
            "source": "api_library_adapter_contract",
        },
        {
            "module": "tests.test_api_contract",
            "path": "tests/test_api_contract.py",
            "responsibility": "API/library hybrid verification",
            "requirements": ["prove pure service behavior", "prove route adapter behavior without live server"],
            "source": "api_library_adapter_contract",
        },
    ]
    for contract in required_contracts:
        if contract["path"] not in existing_contracts:
            next_contracts.append(contract)
            existing_contracts.add(contract["path"])
    for contract in next_contracts:
        if not isinstance(contract, dict) or contract.get("path") != "app/main.py":
            continue
        requirements = [str(item) for item in contract.get("requirements", []) if isinstance(item, str)]
        requirements.extend(
            [
                "include app.routes.router when FastAPI and router are available",
                "keep main import-safe when FastAPI is not installed",
            ]
        )
        contract["requirements"] = list(dict.fromkeys(requirements))
    rows = [str(item) for item in verification_commands if isinstance(item, str)]
    if "python -m unittest discover tests" not in rows:
        rows.insert(0, "python -m unittest discover tests")
    return next_files, next_contracts, rows


def sync_python_source_compile_commands(verification_commands: list[Any], expected_files: list[str]) -> list[Any]:
    if not any(isinstance(item, str) and item.startswith("python -m py_compile ") for item in verification_commands):
        return verification_commands
    py_sources = [
        path
        for path in expected_files
        if path.endswith(".py")
        and not path.startswith("tests/")
        and "/tests/" not in f"/{path}"
        and not Path(path).name.startswith("test_")
        and not path.endswith("/__init__.py")
    ]
    if not py_sources:
        return verification_commands
    command = "python -m py_compile " + " ".join(py_sources)
    rows = [str(item) for item in verification_commands if isinstance(item, str)]
    rows = [row for row in rows if not row.startswith("python -m py_compile ")]
    rows.append(command)
    return rows


def architecture_model_constraints(model_guidance: dict[str, Any]) -> dict[str, Any]:
    content = str(model_guidance.get("content") or "") if isinstance(model_guidance, dict) else ""
    parsed = extract_guidance_json(content)
    guidance = parsed.get("guidance") if isinstance(parsed.get("guidance"), dict) else {}
    missing_modules = merged_guidance_strings(parsed, guidance, "missing_modules")
    verification_gaps = merged_guidance_strings(parsed, guidance, "verification_gaps")
    scaffold_risks = merged_guidance_strings(parsed, guidance, "scaffold_risks")
    next_steps = merged_guidance_strings(parsed, guidance, "next_steps")
    evidence_required = merged_guidance_strings(parsed, guidance, "evidence_required")
    return {
        "kind": "code_brigade_greenfield_architecture_model_constraints",
        "status": "parsed" if parsed else "unparsed" if content.strip() else "not_requested",
        "missing_modules": missing_modules,
        "verification_gaps": verification_gaps,
        "scaffold_risks": scaffold_risks,
        "next_steps": next_steps,
        "evidence_required": evidence_required,
        "constraint_count": sum(
            len(row)
            for row in (
                missing_modules,
                verification_gaps,
                scaffold_risks,
                evidence_required,
            )
        ),
    }


def build_greenfield_project_brief(
    task: str,
    payload: dict[str, Any] | None = None,
    request_guidance=request_greenfield_model_guidance,
) -> dict[str, Any]:
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
    files, module_contracts = apply_static_browser_tool_contract(task, files, module_contracts, template_id)
    files, module_contracts, verification_commands = apply_api_library_adapter_contract(task, files, module_contracts, verification_commands, template_id)
    if any(feature.get("id") == "calculator_operations" for feature in acceptance_features):
        package = project_name.replace("-", "_")
        run_commands = [f"python -m {package}.cli add 2 3"]
        entrypoints = [{"name": "cli", "command": run_commands[0], "path": f"{package}/cli.py"}]
    files = ensure_readme_documents_commands(files, project_name, run_commands, verification_commands)
    expected_files = [str(item.get("path") or "") for item in files if isinstance(item, dict) and item.get("path")]
    definition_of_done = payload.get("definition_of_done") if isinstance(payload.get("definition_of_done"), list) else [
        "expected files are created inside the assigned workspace",
        "entrypoints named in the project brief exist",
        "allowlisted verification commands pass or blockers are explicit",
        "README documents real run and verification commands",
    ]
    model_guidance = request_guidance(
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
    model_constraints = architecture_model_constraints(model_guidance)
    files, module_contracts = apply_architecture_model_module_constraints(project_name, files, module_contracts, model_constraints)
    expected_files = [str(item.get("path") or "") for item in files if isinstance(item, dict) and item.get("path")]
    verification_commands = sync_python_source_compile_commands(verification_commands, expected_files)
    files = ensure_readme_documents_commands(files, project_name, run_commands, verification_commands)
    implementation_plan = build_implementation_worker_plan(task, template_id, module_contracts, expected_files, request_guidance)
    implementation_trace = build_implementation_trace(implementation_plan)
    implementation_feature_report = build_implementation_feature_report(
        task,
        template_id,
        acceptance_features,
        base_file_paths,
        files,
        base_contract_paths,
        module_contracts,
        request_guidance,
    )
    scenario_plan = build_greenfield_scenario_plan(project_type, template_id, acceptance_features, expected_files, model_constraints, task)
    brief = {
        "kind": "code_brigade_greenfield_project_brief",
        "contract_version": "eye-mechanicum.v1",
        "task": task,
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
        "scenario_plan": scenario_plan,
        "architecture_plan": {
            "summary": str(payload.get("summary") or template.get("summary") or f"{template_id} scaffold"),
            "selected_template": template_id,
            "selection_reason": "deterministic GreenfieldArchitect selected the smallest template matching task type",
            "mvp_boundaries": ["working entrypoint", "focused tests", "README with real commands", "task-derived acceptance features implemented when detected"],
            "anti_stub_policy": "non-trivial projects must expose separate entrypoint, implementation module, and tests",
            "model_guidance": model_guidance,
            "model_constraints": model_constraints,
        },
        "file_tree_plan": [{"path": path, "role": "planned_project_file"} for path in expected_files],
        "module_contracts": module_contracts,
        "implementation_plan": implementation_plan,
        "implementation_trace": implementation_trace,
        "implementation_feature_report": implementation_feature_report,
        "verification_plan": {
            "commands": verification_commands,
            "run_commands": run_commands,
            "smoke_checks": ["entrypoint paths exist", "README mentions run/test commands", "scenario_plan evidence markers are present"],
            "scenario_plan": scenario_plan,
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
    request_guidance=request_greenfield_model_guidance,
) -> dict[str, Any]:
    generated_paths = [str(item.get("path") or "") for item in files if isinstance(item, dict) and item.get("path")]
    contract_paths = [str(item.get("path") or "") for item in module_contracts if isinstance(item, dict) and item.get("path")]
    feature_ids = [str(feature.get("id") or "") for feature in acceptance_features if isinstance(feature, dict) and feature.get("id")]
    changed_contract_paths = sorted(set(contract_paths) - set(base_contract_paths)) or sorted(set(contract_paths) if feature_ids else [])
    changed_file_paths = sorted(set(generated_paths) - set(base_file_paths))
    if feature_ids and not changed_file_paths:
        changed_file_paths = sorted(set(generated_paths))
    guidance = request_guidance(
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
        ("scenario_plan.json", "scenario_plan"),
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
