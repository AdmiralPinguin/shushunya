#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from execution_contract import build_blocked_execution_result, build_implemented_execution_result, build_patch_manifest
from execution_preflight import is_repo_relative_path
from verification_adapter import run_verification_commands

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.model_brain import request_model_decision  # noqa: E402


GREENFIELD_MARKER = ".ceraxia_greenfield_workspace"
PROJECT_TYPES = {"web_app", "api_service", "cli_tool", "library", "bot", "android_app", "game", "automation_tool"}
STACK_DEFAULTS = {
    "python_cli_basic": {"language": "python", "framework": "stdlib", "package_manager": "none", "runtime": "python"},
    "python_fastapi_service": {"language": "python", "framework": "fastapi", "package_manager": "pip", "runtime": "uvicorn"},
    "static_site": {"language": "html_css_js", "framework": "none", "package_manager": "none", "runtime": "browser"},
}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_entries(repo: Path) -> list[Path]:
    if not repo.exists() or not repo.is_dir():
        return []
    return [path for path in repo.iterdir() if path.name not in {".git", "__pycache__"}]


def greenfield_workspace_status(repo: Path) -> dict[str, Any]:
    entries = repo_entries(repo)
    marker = repo / GREENFIELD_MARKER
    owned = marker.exists() and marker.is_file()
    return {
        "kind": "code_brigade_greenfield_workspace_status",
        "repo_path": str(repo),
        "repo_exists": repo.exists(),
        "repo_is_dir": repo.is_dir(),
        "marker": GREENFIELD_MARKER,
        "owned_by_ceraxia": owned,
        "top_level_entry_count": len(entries),
        "top_level_entries": sorted(path.name for path in entries)[:40],
        "greenfield_allowed": repo.exists() and repo.is_dir() and (owned or len(entries) == 0),
    }


def project_name_from_task(task: str) -> str:
    name_match = re.search(r"`([^`/\\]+)`", task)
    if name_match:
        return re.sub(r"[^A-Za-z0-9_-]+", "-", name_match.group(1)).strip("-") or "ceraxia_project"
    return "ceraxia_project"


def infer_project_type(task: str) -> str:
    lowered = task.lower()
    if any(word in lowered for word in ("fastapi", "api", "http", "server", "сервер", "апи", "endpoint")):
        return "api_service"
    if any(word in lowered for word in ("site", "website", "frontend", "html", "css", "сайт", "страниц")):
        return "web_app"
    if any(word in lowered for word in ("library", "package", "sdk", "библиот")):
        return "library"
    if any(word in lowered for word in ("bot", "telegram", "бот")):
        return "bot"
    if any(word in lowered for word in ("game", "игр")):
        return "game"
    return "cli_tool"


def template_id_for_project_type(project_type: str, task: str) -> str:
    lowered = task.lower()
    if project_type == "api_service":
        return "python_fastapi_service"
    if project_type == "web_app":
        return "static_site"
    if "data" in lowered or "данн" in lowered:
        return "python_cli_basic"
    return "python_cli_basic"


def python_cli_template(project_name: str) -> dict[str, Any]:
    module_name = project_name.replace("-", "_")
    files = [
        {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
        {"path": "README.md", "content": f"# {project_name}\n\n## Run\n\n```bash\npython -m {module_name}.cli\n```\n\n## Test\n\n```bash\npython -m unittest discover tests\n```\n"},
        {"path": f"{module_name}/__init__.py", "content": f"__all__ = [\"run\"]\n\nfrom .core import run\n"},
        {"path": f"{module_name}/core.py", "content": "def run() -> str:\n    return \"ready\"\n"},
        {"path": f"{module_name}/cli.py", "content": "from .core import run\n\n\ndef main() -> None:\n    print(run())\n\n\nif __name__ == \"__main__\":\n    main()\n"},
        {"path": "tests/test_core.py", "content": f"import unittest\n\nfrom {module_name}.core import run\n\n\nclass CoreTests(unittest.TestCase):\n    def test_run(self):\n        self.assertEqual(run(), \"ready\")\n"},
        {"path": "pyproject.toml", "content": f"[project]\nname = \"{project_name}\"\nversion = \"0.1.0\"\ndescription = \"Generated Ceraxia Python CLI project\"\nrequires-python = \">=3.10\"\n"},
    ]
    return {
        "template_id": "python_cli_basic",
        "files": files,
        "entrypoints": [{"name": "cli", "command": f"python -m {module_name}.cli", "path": f"{module_name}/cli.py"}],
        "run_commands": [f"python -m {module_name}.cli"],
        "verification_commands": ["python -m unittest discover tests", f"python -m py_compile {module_name}/core.py {module_name}/cli.py"],
        "module_contracts": [
            {"module": f"{module_name}.core", "path": f"{module_name}/core.py", "responsibility": "domain behavior", "requirements": ["return stable ready result"]},
            {"module": f"{module_name}.cli", "path": f"{module_name}/cli.py", "responsibility": "command-line entrypoint", "requirements": ["print core result"]},
        ],
    }


def fastapi_service_template(project_name: str) -> dict[str, Any]:
    module_name = project_name.replace("-", "_")
    files = [
        {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
        {"path": "README.md", "content": "# " + project_name + "\n\n## Run\n\n```bash\nuvicorn app.main:app --reload\n```\n\n## Test\n\n```bash\npython -m unittest discover tests\n```\n"},
        {"path": "requirements.txt", "content": "fastapi\nuvicorn\n"},
        {"path": "app/__init__.py", "content": ""},
        {"path": "app/main.py", "content": "try:\n    from fastapi import FastAPI\nexcept ModuleNotFoundError:\n    FastAPI = None\n\n\ndef health() -> dict[str, bool]:\n    return {\"ok\": True}\n\n\nif FastAPI is not None:\n    app = FastAPI(title=\"Ceraxia Service\")\n\n    @app.get(\"/health\")\n    def health_endpoint() -> dict[str, bool]:\n        return health()\nelse:\n    app = None\n"},
        {"path": "tests/test_health.py", "content": "import unittest\n\nfrom app.main import health\n\n\nclass HealthTests(unittest.TestCase):\n    def test_health(self):\n        self.assertEqual(health(), {\"ok\": True})\n"},
    ]
    return {
        "template_id": "python_fastapi_service",
        "files": files,
        "entrypoints": [{"name": "http", "command": "uvicorn app.main:app --reload", "path": "app/main.py"}],
        "run_commands": ["uvicorn app.main:app --reload"],
        "verification_commands": ["python -m unittest discover tests", "python -m py_compile app/main.py"],
        "module_contracts": [
            {"module": "app.main", "path": "app/main.py", "responsibility": "HTTP app and health behavior", "requirements": ["health returns ok true", "FastAPI app is exposed when dependency is installed"]},
        ],
    }


def static_site_template(project_name: str) -> dict[str, Any]:
    files = [
        {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
        {"path": "README.md", "content": f"# {project_name}\n\nOpen `index.html` in a browser or serve the folder with any static server.\n"},
        {"path": "index.html", "content": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>Ceraxia Site</title>\n  <link rel=\"stylesheet\" href=\"styles.css\">\n</head>\n<body>\n  <main>\n    <h1>Ceraxia Site</h1>\n    <p id=\"status\">ready</p>\n  </main>\n  <script src=\"app.js\"></script>\n</body>\n</html>\n"},
        {"path": "styles.css", "content": "body { margin: 0; font-family: system-ui, sans-serif; background: #f6f7f9; color: #17191f; }\nmain { max-width: 760px; margin: 10vh auto; padding: 24px; }\n"},
        {"path": "app.js", "content": "document.documentElement.dataset.ceraxia = 'ready';\n"},
        {"path": "tests/test_static_site.py", "content": "from pathlib import Path\nimport unittest\n\n\nclass StaticSiteTests(unittest.TestCase):\n    def test_entrypoint_references_assets(self):\n        html = Path('index.html').read_text(encoding='utf-8')\n        self.assertIn('styles.css', html)\n        self.assertIn('app.js', html)\n        self.assertIn('ready', html)\n"},
    ]
    return {
        "template_id": "static_site",
        "files": files,
        "entrypoints": [{"name": "browser", "command": "open index.html", "path": "index.html"}],
        "run_commands": ["open index.html"],
        "verification_commands": ["python -m unittest discover tests"],
        "module_contracts": [
            {"module": "static_page", "path": "index.html", "responsibility": "first screen content", "requirements": ["loads stylesheet and script", "shows ready state"]},
        ],
    }


def template_for(template_id: str, project_name: str) -> dict[str, Any]:
    if template_id == "python_fastapi_service":
        return fastapi_service_template(project_name)
    if template_id == "static_site":
        return static_site_template(project_name)
    return python_cli_template(project_name)


def build_greenfield_project_brief(task: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    project_name = str(payload.get("project_name") or project_name_from_task(task))
    project_type = str(payload.get("project_type") or infer_project_type(task))
    if project_type not in PROJECT_TYPES:
        project_type = "automation_tool"
    template_id = str(payload.get("template_id") or template_id_for_project_type(project_type, task))
    template = template_for(template_id, project_name)
    files = payload.get("files") if isinstance(payload.get("files"), list) and payload.get("files") else template["files"]
    verification_commands = payload.get("verification_commands") if isinstance(payload.get("verification_commands"), list) else template["verification_commands"]
    run_commands = payload.get("run_commands") if isinstance(payload.get("run_commands"), list) else template["run_commands"]
    entrypoints = payload.get("entrypoints") if isinstance(payload.get("entrypoints"), list) else template["entrypoints"]
    stack = payload.get("stack") if isinstance(payload.get("stack"), dict) else STACK_DEFAULTS.get(template_id, STACK_DEFAULTS["python_cli_basic"])
    expected_files = [str(item.get("path") or "") for item in files if isinstance(item, dict) and item.get("path")]
    module_contracts = payload.get("module_contracts") if isinstance(payload.get("module_contracts"), list) else template["module_contracts"]
    definition_of_done = payload.get("definition_of_done") if isinstance(payload.get("definition_of_done"), list) else [
        "expected files are created inside the assigned workspace",
        "entrypoints named in the project brief exist",
        "allowlisted verification commands pass or blockers are explicit",
        "README documents real run and verification commands",
    ]
    previous_timeout = os.environ.get("EYE_MODEL_TIMEOUT_SEC")
    if previous_timeout is None:
        os.environ["EYE_MODEL_TIMEOUT_SEC"] = "3"
    try:
        model_guidance = request_model_decision(
            "CodeBrigade",
            "GreenfieldArchitect",
            {
                "task": task,
                "project_type": project_type,
                "template_id": template_id,
                "expected_files": expected_files,
                "definition_of_done": definition_of_done,
            },
            layer="code_worker",
            instructions="Review the greenfield architecture plan, identify missing modules, verification gaps, and scaffold risks. Return concise guidance.",
        )
    finally:
        if previous_timeout is None:
            os.environ.pop("EYE_MODEL_TIMEOUT_SEC", None)
        else:
            os.environ["EYE_MODEL_TIMEOUT_SEC"] = previous_timeout
    return {
        "kind": "code_brigade_greenfield_project_brief",
        "contract_version": "eye-mechanicum.v1",
        "project_name": project_name,
        "project_type": project_type,
        "template_id": template_id,
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
        "architecture_plan": {
            "summary": str(payload.get("summary") or template.get("summary") or f"{template_id} scaffold"),
            "selected_template": template_id,
            "selection_reason": "deterministic GreenfieldArchitect selected the smallest template matching task type",
            "mvp_boundaries": ["working entrypoint", "focused tests", "README with real commands"],
            "anti_stub_policy": "non-trivial projects must expose separate entrypoint, implementation module, and tests",
            "model_guidance": model_guidance,
        },
        "file_tree_plan": [{"path": path, "role": "planned_project_file"} for path in expected_files],
        "module_contracts": module_contracts,
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
            "manifest_files": [path for path in expected_files if Path(path).name in {"pyproject.toml", "requirements.txt", "package.json"}],
            "lockfile_policy": "preserve lock files only when package manager generates them inside workspace",
        },
    }


def extract_project_spec(task: str) -> dict[str, Any]:
    marker = "CERAXIA_PROJECT:"
    if marker not in task:
        brief = build_greenfield_project_brief(task)
        return {
            "source": "greenfield_project_brief",
            "files": brief["files"],
            "verification_commands": brief["verification_commands"],
            "summary": brief["architecture_plan"]["summary"],
            "greenfield_project_brief": brief,
        }
    raw = task.split(marker, 1)[1].strip()
    payload, _ = json.JSONDecoder().raw_decode(raw)
    if not isinstance(payload, dict):
        raise ValueError("CERAXIA_PROJECT payload must be a JSON object")
    brief = build_greenfield_project_brief(task, payload)
    return {
        "source": "explicit_ceraxia_project",
        "files": brief["files"],
        "verification_commands": brief["verification_commands"],
        "summary": brief["architecture_plan"]["summary"],
        "greenfield_project_brief": brief,
    }


def infer_minimal_project_spec(task: str) -> dict[str, Any]:
    lowered = task.lower()
    project_name = "ceraxia_project"
    name_match = re.search(r"`([^`/\\]+)`", task)
    if name_match:
        project_name = re.sub(r"[^A-Za-z0-9_-]+", "-", name_match.group(1)).strip("-") or project_name
    if any(word in lowered for word in ("api", "http", "fastapi", "server", "сервер", "апи")):
        app_content = (
            "def health():\n"
            "    return {\"ok\": True}\n\n"
            "if __name__ == \"__main__\":\n"
            "    print(health())\n"
        )
        test_content = (
            "import unittest\n"
            "import app\n\n"
            "class AppTests(unittest.TestCase):\n"
            "    def test_health(self):\n"
            "        self.assertEqual(app.health(), {\"ok\": True})\n"
        )
        summary = "minimal Python service scaffold"
    else:
        app_content = (
            "def main():\n"
            "    return \"ready\"\n\n"
            "if __name__ == \"__main__\":\n"
            "    print(main())\n"
        )
        test_content = (
            "import unittest\n"
            "import app\n\n"
            "class AppTests(unittest.TestCase):\n"
            "    def test_main(self):\n"
            "        self.assertEqual(app.main(), \"ready\")\n"
        )
        summary = "minimal Python application scaffold"
    return {
        "source": "inferred_minimal_python_project",
        "summary": summary,
        "files": [
            {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
            {"path": "README.md", "content": f"# {project_name}\n\nGenerated by Ceraxia CodeBrigade greenfield project mode.\n"},
            {"path": "app.py", "content": app_content},
            {"path": "test_app.py", "content": test_content},
        ],
        "verification_commands": ["python -m unittest test_app.py", "python -m py_compile app.py"],
    }


def normalize_project_file_rows(files: Any) -> list[dict[str, str]]:
    if not isinstance(files, list):
        raise ValueError("project files must be a list")
    rows: list[dict[str, str]] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise ValueError(f"project file {index} must be an object")
        rel_path = str(item.get("path") or "").strip()
        content = item.get("content")
        if not is_repo_relative_path(rel_path):
            raise ValueError(f"project file path must be repo-relative: {rel_path}")
        if Path(rel_path).parts and Path(rel_path).parts[0] in {".git", "__pycache__"}:
            raise ValueError(f"project file path targets forbidden workspace metadata: {rel_path}")
        if not isinstance(content, str):
            raise ValueError(f"project file content must be a string: {rel_path}")
        if rel_path.endswith(".py"):
            ast.parse(content)
        rows.append({"path": rel_path, "content": content})
    if not rows:
        raise ValueError("project file list is empty")
    return rows


def validate_greenfield_project_brief(brief: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if brief.get("kind") != "code_brigade_greenfield_project_brief":
        problems.append("greenfield_project_brief kind is required")
    if brief.get("contract_version") != "eye-mechanicum.v1":
        problems.append("greenfield_project_brief contract_version is unsupported")
    if brief.get("project_type") not in PROJECT_TYPES:
        problems.append("greenfield_project_brief project_type is unsupported")
    if not isinstance(brief.get("stack"), dict) or not brief.get("stack"):
        problems.append("greenfield_project_brief stack is required")
    if not isinstance(brief.get("entrypoints"), list) or not brief.get("entrypoints"):
        problems.append("greenfield_project_brief entrypoints are required")
    if not isinstance(brief.get("expected_files"), list) or not brief.get("expected_files"):
        problems.append("greenfield_project_brief expected_files are required")
    if not isinstance(brief.get("verification_commands"), list):
        problems.append("greenfield_project_brief verification_commands must be a list")
    if not isinstance(brief.get("run_commands"), list) or not brief.get("run_commands"):
        problems.append("greenfield_project_brief run_commands are required")
    if not isinstance(brief.get("artifact_contract"), dict) or not brief.get("artifact_contract"):
        problems.append("greenfield_project_brief artifact_contract is required")
    if not isinstance(brief.get("workspace_policy"), dict) or brief.get("workspace_policy", {}).get("marker") != GREENFIELD_MARKER:
        problems.append("greenfield_project_brief workspace_policy marker is required")
    if not isinstance(brief.get("definition_of_done"), list) or not brief.get("definition_of_done"):
        problems.append("greenfield_project_brief definition_of_done is required")
    if not isinstance(brief.get("architecture_plan"), dict) or not brief.get("architecture_plan"):
        problems.append("greenfield_project_brief architecture_plan is required")
    if not isinstance(brief.get("file_tree_plan"), list) or not brief.get("file_tree_plan"):
        problems.append("greenfield_project_brief file_tree_plan is required")
    if not isinstance(brief.get("module_contracts"), list) or not brief.get("module_contracts"):
        problems.append("greenfield_project_brief module_contracts are required")
    if not isinstance(brief.get("verification_plan"), dict) or not brief.get("verification_plan"):
        problems.append("greenfield_project_brief verification_plan is required")
    return problems


def execute_greenfield_project_brief(brief: dict[str, Any]) -> dict[str, Any]:
    repo = Path(str(brief.get("repo_path") or ""))
    workspace = greenfield_workspace_status(repo)
    if not workspace["repo_exists"] or not workspace["repo_is_dir"]:
        return build_blocked_execution_result(["greenfield repo_path must be an existing directory"], workspace)
    if not workspace["greenfield_allowed"]:
        return build_blocked_execution_result(
            ["greenfield project creation requires an empty directory or .ceraxia_greenfield_workspace marker"],
            workspace,
        )
    try:
        spec = extract_project_spec(str(brief.get("task") or ""))
        project_brief = spec.get("greenfield_project_brief") if isinstance(spec.get("greenfield_project_brief"), dict) else {}
        contract_problems = validate_greenfield_project_brief(project_brief)
        if contract_problems:
            return build_blocked_execution_result(contract_problems, workspace)
        files = list(spec.get("files") if isinstance(spec.get("files"), list) else [])
        if project_brief and not any(isinstance(item, dict) and item.get("path") == "greenfield_project_brief.json" for item in files):
            files.append({"path": "greenfield_project_brief.json", "content": json.dumps(project_brief, ensure_ascii=False, indent=2, sort_keys=True) + "\n"})
        rows = normalize_project_file_rows(files)
    except (ValueError, SyntaxError, json.JSONDecodeError) as exc:
        return build_blocked_execution_result([str(exc)], workspace)
    operation_results: list[dict[str, Any]] = []
    changed_files: list[str] = []
    originals: dict[Path, str | None] = {}
    try:
        for index, row in enumerate(rows):
            rel_path = row["path"]
            path = repo / rel_path
            before_hash = file_sha256(path) if path.exists() and path.is_file() and not path.is_symlink() else ""
            if path.exists() and not path.is_file():
                raise ValueError(f"project file target exists and is not a file: {rel_path}")
            if path.exists() and not workspace["owned_by_ceraxia"] and rel_path != GREENFIELD_MARKER:
                raise ValueError(f"project file target already exists in unowned greenfield workspace: {rel_path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            originals[path] = path.read_text(encoding="utf-8") if path.exists() else None
            path.write_text(row["content"], encoding="utf-8")
            after_hash = file_sha256(path)
            changed_files.append(rel_path)
            operation_results.append(
                {
                    "index": index,
                    "operation": "greenfield_create_or_update_file",
                    "path": rel_path,
                    "status": "applied",
                    "before_sha256": before_hash,
                    "after_sha256": after_hash,
                }
            )
    except Exception as exc:
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(original, encoding="utf-8")
        return build_blocked_execution_result(
            [str(exc)],
            workspace,
            f"rolled back {len(originals)} greenfield files",
            operation_results,
            build_patch_manifest([], operation_results, f"rolled back {len(originals)} greenfield files"),
        )
    commands = spec.get("verification_commands") if isinstance(spec.get("verification_commands"), list) else []
    verification = run_verification_commands([str(command) for command in commands if isinstance(command, str)], str(repo), execute=True)
    result = build_implemented_execution_result(
        changed_files,
        f"created {len(changed_files)} greenfield project files from {spec.get('source')}",
        workspace,
        operation_results,
        build_patch_manifest(changed_files, operation_results, ""),
    )
    result["greenfield_project"] = {
        "kind": "code_brigade_greenfield_project_result",
        "spec_source": spec.get("source", ""),
        "summary": spec.get("summary", ""),
        "workspace": workspace,
        "greenfield_project_brief": project_brief,
        "architecture_plan": project_brief.get("architecture_plan", {}) if isinstance(project_brief, dict) else {},
        "file_tree_plan": project_brief.get("file_tree_plan", []) if isinstance(project_brief, dict) else [],
        "module_contracts": project_brief.get("module_contracts", []) if isinstance(project_brief, dict) else [],
        "dependency_plan": project_brief.get("dependency_plan", {}) if isinstance(project_brief, dict) else {},
        "verification_plan": project_brief.get("verification_plan", {}) if isinstance(project_brief, dict) else {},
        "verification": verification,
    }
    result["verification_commands_executed"] = [
        str(item.get("command") or "")
        for item in verification.get("results", [])
        if isinstance(item, dict) and item.get("status") in {"passed", "failed", "blocked", "skipped"}
    ]
    if verification.get("status") not in {"passed", "planned"}:
        result["status"] = "blocked"
        result["blockers"] = ["greenfield project verification failed"]
    return result
