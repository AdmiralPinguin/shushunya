from __future__ import annotations

"""Greenfield project template registry for Ceraxia CodeBrigade."""

from typing import Any


GREENFIELD_MARKER = ".ceraxia_greenfield_workspace"
PROJECT_TYPES = {"web_app", "api_service", "cli_tool", "library", "bot", "android_app", "game", "automation_tool"}
STACK_DEFAULTS = {
    "python_cli_basic": {"language": "python", "framework": "stdlib", "package_manager": "none", "runtime": "python"},
    "python_fastapi_service": {"language": "python", "framework": "fastapi", "package_manager": "pip", "runtime": "uvicorn"},
    "python_library": {"language": "python", "framework": "stdlib", "package_manager": "none", "runtime": "python"},
    "node_vite_app": {"language": "javascript", "framework": "vite", "package_manager": "npm", "runtime": "browser"},
    "static_site": {"language": "html_css_js", "framework": "none", "package_manager": "none", "runtime": "browser"},
    "telegram_bot_python": {"language": "python", "framework": "python-telegram-bot", "package_manager": "pip", "runtime": "python"},
    "data_processing_tool": {"language": "python", "framework": "stdlib", "package_manager": "none", "runtime": "python"},
    "local_agent_tool": {"language": "python", "framework": "stdlib", "package_manager": "none", "runtime": "python"},
}


def module_name(project_name: str) -> str:
    return project_name.replace("-", "_")


def template_id_for_project_type(project_type: str, task: str) -> str:
    lowered = task.lower()
    if project_type == "api_service":
        return "python_fastapi_service"
    if project_type == "web_app":
        if "vite" in lowered or "react" in lowered or "vue" in lowered:
            return "node_vite_app"
        return "static_site"
    if project_type == "library":
        return "python_library"
    if project_type == "bot":
        return "telegram_bot_python"
    if "data" in lowered or "csv" in lowered or "данн" in lowered:
        return "data_processing_tool"
    if "agent" in lowered or "агент" in lowered:
        return "local_agent_tool"
    return "python_cli_basic"


def python_cli_template(project_name: str) -> dict[str, Any]:
    package = module_name(project_name)
    return {
        "template_id": "python_cli_basic",
        "files": [
            {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
            {"path": "README.md", "content": f"# {project_name}\n\n## Run\n\n```bash\npython -m {package}.cli\n```\n\n## Test\n\n```bash\npython -m unittest discover tests\n```\n"},
            {"path": f"{package}/__init__.py", "content": f"__all__ = [\"run\"]\n\nfrom .core import run\n"},
            {"path": f"{package}/core.py", "content": "def run() -> str:\n    return \"ready\"\n"},
            {"path": f"{package}/cli.py", "content": "from .core import run\n\n\ndef main() -> None:\n    print(run())\n\n\nif __name__ == \"__main__\":\n    main()\n"},
            {"path": "tests/test_core.py", "content": f"import unittest\n\nfrom {package}.core import run\n\n\nclass CoreTests(unittest.TestCase):\n    def test_run(self):\n        self.assertEqual(run(), \"ready\")\n"},
            {"path": "pyproject.toml", "content": f"[project]\nname = \"{project_name}\"\nversion = \"0.1.0\"\ndescription = \"Generated Ceraxia Python CLI project\"\nrequires-python = \">=3.10\"\n"},
        ],
        "entrypoints": [{"name": "cli", "command": f"python -m {package}.cli", "path": f"{package}/cli.py"}],
        "run_commands": [f"python -m {package}.cli"],
        "verification_commands": ["python -m unittest discover tests", f"python -m py_compile {package}/core.py {package}/cli.py"],
        "module_contracts": [
            {"module": f"{package}.core", "path": f"{package}/core.py", "responsibility": "domain behavior", "requirements": ["return stable ready result"]},
            {"module": f"{package}.cli", "path": f"{package}/cli.py", "responsibility": "command-line entrypoint", "requirements": ["print core result"]},
            {"module": "tests.test_core", "path": "tests/test_core.py", "responsibility": "CLI behavior verification", "requirements": ["prove core result matches requested CLI behavior"]},
        ],
        "common_failure_fixes": [
            {"failure": "ModuleNotFoundError", "fix": "ensure package directory has __init__.py and tests run from workspace root"},
            {"failure": "no tests discovered", "fix": "keep tests under tests/ with test_*.py names"},
        ],
    }


def fastapi_service_template(project_name: str) -> dict[str, Any]:
    return {
        "template_id": "python_fastapi_service",
        "files": [
            {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
            {"path": "README.md", "content": "# " + project_name + "\n\n## Run\n\n```bash\nuvicorn app.main:app --reload\n```\n\n## Test\n\n```bash\npython -m unittest discover tests\n```\n\n```bash\npython -m py_compile app/main.py\n```\n"},
            {"path": "requirements.txt", "content": "fastapi\nuvicorn\n"},
            {"path": "app/__init__.py", "content": ""},
            {"path": "app/main.py", "content": "try:\n    from fastapi import FastAPI\nexcept ModuleNotFoundError:\n    FastAPI = None\n\n\ndef health() -> dict[str, bool]:\n    return {\"ok\": True}\n\n\nif FastAPI is not None:\n    app = FastAPI(title=\"Ceraxia Service\")\n\n    @app.get(\"/health\")\n    def health_endpoint() -> dict[str, bool]:\n        return health()\nelse:\n    app = None\n"},
            {"path": "tests/test_health.py", "content": "import unittest\n\nfrom app.main import health\n\n\nclass HealthTests(unittest.TestCase):\n    def test_health(self):\n        self.assertEqual(health(), {\"ok\": True})\n"},
        ],
        "entrypoints": [{"name": "http", "command": "uvicorn app.main:app --reload", "path": "app/main.py"}],
        "run_commands": ["uvicorn app.main:app --reload"],
        "verification_commands": ["python -m unittest discover tests", "python -m py_compile app/main.py"],
        "module_contracts": [
            {"module": "app.main", "path": "app/main.py", "responsibility": "HTTP app and health behavior", "requirements": ["health returns ok true", "FastAPI app is exposed when dependency is installed"]},
            {"module": "tests.test_health", "path": "tests/test_health.py", "responsibility": "service behavior verification", "requirements": ["prove health contract without requiring a live server"]},
        ],
        "common_failure_fixes": [
            {"failure": "ModuleNotFoundError: fastapi", "fix": "install requirements.txt or keep tests focused on import-safe pure functions"},
            {"failure": "entrypoint missing", "fix": "keep app/main.py as the uvicorn module path"},
        ],
    }


def python_library_template(project_name: str) -> dict[str, Any]:
    package = module_name(project_name)
    return {
        "template_id": "python_library",
        "files": [
            {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
            {"path": "README.md", "content": f"# {project_name}\n\n## Run\n\n```bash\npython -m unittest discover tests\n```\n\n## Test\n\n```bash\npython -m unittest discover tests\n```\n"},
            {"path": f"{package}/__init__.py", "content": "from .core import describe\n\n__all__ = [\"describe\"]\n"},
            {"path": f"{package}/core.py", "content": "def describe(value: str) -> str:\n    return value.strip().lower()\n"},
            {"path": "tests/test_library.py", "content": f"import unittest\n\nfrom {package}.core import describe\n\n\nclass LibraryTests(unittest.TestCase):\n    def test_describe_normalizes_text(self):\n        self.assertEqual(describe(' Ready '), 'ready')\n"},
            {"path": "pyproject.toml", "content": f"[project]\nname = \"{project_name}\"\nversion = \"0.1.0\"\ndescription = \"Generated Ceraxia Python library\"\nrequires-python = \">=3.10\"\n"},
        ],
        "entrypoints": [{"name": "test", "command": "python -m unittest discover tests", "path": "tests/test_library.py"}],
        "run_commands": ["python -m unittest discover tests"],
        "verification_commands": ["python -m unittest discover tests", f"python -m py_compile {package}/core.py"],
        "module_contracts": [
            {"module": f"{package}.core", "path": f"{package}/core.py", "responsibility": "public library behavior", "requirements": ["expose deterministic pure function"]},
            {"module": "tests.test_library", "path": "tests/test_library.py", "responsibility": "library contract verification", "requirements": ["prove public behavior"]},
        ],
        "common_failure_fixes": [{"failure": "import path mismatch", "fix": "align package directory with pyproject project name normalization"}],
    }


def node_vite_app_template(project_name: str) -> dict[str, Any]:
    return {
        "template_id": "node_vite_app",
        "files": [
            {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
            {"path": "README.md", "content": f"# {project_name}\n\n## Install\n\n```bash\nnpm install\n```\n\n## Run\n\n```bash\nnpm run dev\n```\n\n## Test\n\n```bash\npython -m unittest discover tests\n```\n"},
            {"path": "package.json", "content": "{\n  \"scripts\": {\n    \"dev\": \"vite\",\n    \"build\": \"vite build\"\n  },\n  \"dependencies\": {\n    \"@vitejs/plugin-react\": \"latest\",\n    \"vite\": \"latest\",\n    \"react\": \"latest\",\n    \"react-dom\": \"latest\"\n  },\n  \"devDependencies\": {}\n}\n"},
            {"path": "index.html", "content": "<!doctype html>\n<html>\n  <head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"><title>Ceraxia Vite App</title></head>\n  <body><div id=\"root\"></div><script type=\"module\" src=\"/src/main.jsx\"></script></body>\n</html>\n"},
            {"path": "src/main.jsx", "content": "import React from 'react';\nimport { createRoot } from 'react-dom/client';\nimport './styles.css';\n\nfunction App() {\n  return <main><h1>Ceraxia App</h1><p>ready</p></main>;\n}\n\ncreateRoot(document.getElementById('root')).render(<App />);\n"},
            {"path": "src/styles.css", "content": "body { margin: 0; font-family: system-ui, sans-serif; color: #17191f; background: #f6f7f9; }\nmain { max-width: 760px; margin: 10vh auto; padding: 24px; }\n"},
            {"path": "tests/test_vite_contract.py", "content": "from pathlib import Path\nimport json\nimport unittest\n\n\nclass ViteContractTests(unittest.TestCase):\n    def test_manifest_and_entrypoint(self):\n        manifest = json.loads(Path('package.json').read_text(encoding='utf-8'))\n        self.assertIn('dev', manifest['scripts'])\n        self.assertIn('/src/main.jsx', Path('index.html').read_text(encoding='utf-8'))\n        self.assertIn('ready', Path('src/main.jsx').read_text(encoding='utf-8'))\n"},
        ],
        "entrypoints": [{"name": "vite-dev", "command": "npm run dev", "path": "src/main.jsx"}],
        "run_commands": ["npm run dev"],
        "verification_commands": ["python -m unittest discover tests"],
        "module_contracts": [
            {"module": "src/main.jsx", "path": "src/main.jsx", "responsibility": "frontend application entrypoint", "requirements": ["render ready first screen"]},
            {"module": "package.json", "path": "package.json", "responsibility": "Node/Vite package contract", "requirements": ["define dev and build scripts"]},
            {"module": "tests.test_vite_contract", "path": "tests/test_vite_contract.py", "responsibility": "frontend contract verification", "requirements": ["prove manifest and browser entrypoint are wired"]},
        ],
        "common_failure_fixes": [{"failure": "npm missing", "fix": "block dependency install with an explicit package-manager blocker"}],
    }


def static_site_template(project_name: str) -> dict[str, Any]:
    return {
        "template_id": "static_site",
        "files": [
            {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
            {"path": "README.md", "content": f"# {project_name}\n\n## Run\n\n```bash\nopen index.html\n```\n\n## Test\n\n```bash\npython -m unittest discover tests\n```\n"},
            {"path": "index.html", "content": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>Ceraxia Site</title>\n  <link rel=\"stylesheet\" href=\"styles.css\">\n</head>\n<body>\n  <main>\n    <h1>Ceraxia Site</h1>\n    <p id=\"status\">ready</p>\n  </main>\n  <script src=\"app.js\"></script>\n</body>\n</html>\n"},
            {"path": "styles.css", "content": "body { margin: 0; font-family: system-ui, sans-serif; background: #f6f7f9; color: #17191f; }\nmain { max-width: 760px; margin: 10vh auto; padding: 24px; }\n"},
            {"path": "app.js", "content": "document.documentElement.dataset.ceraxia = 'ready';\n"},
            {"path": "tests/test_static_site.py", "content": "from pathlib import Path\nimport unittest\n\n\nclass StaticSiteTests(unittest.TestCase):\n    def test_entrypoint_references_assets(self):\n        html = Path('index.html').read_text(encoding='utf-8')\n        self.assertIn('styles.css', html)\n        self.assertIn('app.js', html)\n        self.assertIn('ready', html)\n"},
        ],
        "entrypoints": [{"name": "browser", "command": "open index.html", "path": "index.html"}],
        "run_commands": ["open index.html"],
        "verification_commands": ["python -m unittest discover tests"],
        "module_contracts": [
            {"module": "static_page", "path": "index.html", "responsibility": "first screen content", "requirements": ["loads stylesheet and script", "shows ready state"]},
            {"module": "tests.test_static_site", "path": "tests/test_static_site.py", "responsibility": "static site contract verification", "requirements": ["prove entrypoint references required assets and visible state"]},
        ],
        "common_failure_fixes": [{"failure": "asset not referenced", "fix": "ensure index.html links styles.css and app.js"}],
    }


def telegram_bot_template(project_name: str) -> dict[str, Any]:
    package = module_name(project_name)
    return {
        "template_id": "telegram_bot_python",
        "files": [
            {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
            {"path": "README.md", "content": f"# {project_name}\n\n## Run\n\n```bash\npython -m {package}.bot\n```\n\n## Test\n\n```bash\npython -m unittest discover tests\n```\n"},
            {"path": "requirements.txt", "content": "python-telegram-bot\n"},
            {"path": f"{package}/__init__.py", "content": ""},
            {"path": f"{package}/bot.py", "content": "import os\n\n\ndef build_reply(text: str) -> str:\n    return 'ready' if not text.strip() else text.strip()\n\n\ndef main() -> None:\n    token = os.environ.get('TELEGRAM_BOT_TOKEN')\n    if not token:\n        raise SystemExit('TELEGRAM_BOT_TOKEN is required')\n    print('bot configured')\n\n\nif __name__ == '__main__':\n    main()\n"},
            {"path": "tests/test_bot.py", "content": f"import unittest\n\nfrom {package}.bot import build_reply\n\n\nclass BotTests(unittest.TestCase):\n    def test_reply(self):\n        self.assertEqual(build_reply(' ping '), 'ping')\n"},
        ],
        "entrypoints": [{"name": "telegram-bot", "command": f"python -m {package}.bot", "path": f"{package}/bot.py"}],
        "run_commands": [f"python -m {package}.bot"],
        "verification_commands": ["python -m unittest discover tests", f"python -m py_compile {package}/bot.py"],
        "module_contracts": [
            {"module": f"{package}.bot", "path": f"{package}/bot.py", "responsibility": "Telegram bot runtime and pure reply logic", "requirements": ["requires token for live run", "reply logic testable without network"]},
            {"module": "tests.test_bot", "path": "tests/test_bot.py", "responsibility": "bot logic verification", "requirements": ["prove pure reply behavior"]},
        ],
        "common_failure_fixes": [{"failure": "missing TELEGRAM_BOT_TOKEN", "fix": "treat live bot startup as blocked unless token is supplied"}],
    }


def data_processing_template(project_name: str) -> dict[str, Any]:
    package = module_name(project_name)
    return {
        "template_id": "data_processing_tool",
        "files": [
            {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
            {"path": "README.md", "content": f"# {project_name}\n\n## Run\n\n```bash\npython -m {package}.cli input.csv\n```\n\n## Test\n\n```bash\npython -m unittest discover tests\n```\n"},
            {"path": f"{package}/__init__.py", "content": "from .processor import summarize_rows\n\n__all__ = [\"summarize_rows\"]\n"},
            {"path": f"{package}/processor.py", "content": "import csv\nfrom io import StringIO\n\n\ndef summarize_rows(csv_text: str) -> dict[str, int]:\n    rows = list(csv.DictReader(StringIO(csv_text)))\n    return {'rows': len(rows)}\n"},
            {"path": f"{package}/cli.py", "content": "from pathlib import Path\nimport sys\n\nfrom .processor import summarize_rows\n\n\ndef main(argv: list[str] | None = None) -> None:\n    args = list(sys.argv[1:] if argv is None else argv)\n    if not args:\n        raise SystemExit('input csv path required')\n    print(summarize_rows(Path(args[0]).read_text(encoding='utf-8')))\n\n\nif __name__ == '__main__':\n    main()\n"},
            {"path": "tests/test_processor.py", "content": f"import unittest\n\nfrom {package}.processor import summarize_rows\n\n\nclass ProcessorTests(unittest.TestCase):\n    def test_counts_rows(self):\n        self.assertEqual(summarize_rows('name\\na\\nb\\n'), {{'rows': 2}})\n"},
            {"path": "pyproject.toml", "content": f"[project]\nname = \"{project_name}\"\nversion = \"0.1.0\"\nrequires-python = \">=3.10\"\n"},
        ],
        "entrypoints": [{"name": "data-cli", "command": f"python -m {package}.cli input.csv", "path": f"{package}/cli.py"}],
        "run_commands": [f"python -m {package}.cli input.csv"],
        "verification_commands": ["python -m unittest discover tests", f"python -m py_compile {package}/processor.py {package}/cli.py"],
        "module_contracts": [
            {"module": f"{package}.processor", "path": f"{package}/processor.py", "responsibility": "data transformation logic", "requirements": ["parse CSV text", "return row summary"]},
            {"module": f"{package}.cli", "path": f"{package}/cli.py", "responsibility": "file-based CLI", "requirements": ["read input path and print summary"]},
            {"module": "tests.test_processor", "path": "tests/test_processor.py", "responsibility": "data processing verification", "requirements": ["prove CSV processor behavior"]},
        ],
        "common_failure_fixes": [{"failure": "input file missing", "fix": "keep CLI argument validation explicit and test processor separately"}],
    }


def local_agent_tool_template(project_name: str) -> dict[str, Any]:
    package = module_name(project_name)
    return {
        "template_id": "local_agent_tool",
        "files": [
            {"path": GREENFIELD_MARKER, "content": "created-by=ceraxia-code-brigade\n"},
            {"path": "README.md", "content": f"# {project_name}\n\n## Run\n\n```bash\npython -m {package}.tool\n```\n\n## Test\n\n```bash\npython -m unittest discover tests\n```\n"},
            {"path": f"{package}/__init__.py", "content": ""},
            {"path": f"{package}/registry.py", "content": "def available_actions() -> list[str]:\n    return ['status']\n"},
            {"path": f"{package}/schema.py", "content": "def validate_payload(payload: dict | None = None) -> dict:\n    return {} if payload is None else payload\n"},
            {"path": f"{package}/session.py", "content": "class AgentSession:\n    def __init__(self) -> None:\n        self.history = []\n"},
            {"path": f"{package}/runner.py", "content": "def run_action(action: str = 'status') -> dict[str, str]:\n    return {'status': 'ready', 'action': action.strip()}\n"},
            {"path": f"{package}/contract.py", "content": "def build_tool_result(task: str) -> dict[str, str]:\n    return {'status': 'ready', 'task': task.strip()}\n"},
            {"path": f"{package}/tool.py", "content": "from .contract import build_tool_result\n\n\ndef main() -> None:\n    print(build_tool_result('default'))\n\n\nif __name__ == '__main__':\n    main()\n"},
            {"path": "tests/test_contract.py", "content": f"import unittest\n\nfrom {package}.contract import build_tool_result\n\n\nclass ContractTests(unittest.TestCase):\n    def test_result(self):\n        self.assertEqual(build_tool_result(' task ')['task'], 'task')\n"},
            {"path": "pyproject.toml", "content": f"[project]\nname = \"{project_name}\"\nversion = \"0.1.0\"\nrequires-python = \">=3.10\"\n"},
        ],
        "entrypoints": [{"name": "local-agent-tool", "command": f"python -m {package}.tool", "path": f"{package}/tool.py"}],
        "run_commands": [f"python -m {package}.tool"],
        "verification_commands": [
            "python -m unittest discover tests",
            f"python -m py_compile {package}/__init__.py {package}/registry.py {package}/schema.py {package}/session.py {package}/runner.py {package}/contract.py {package}/tool.py",
        ],
        "module_contracts": [
            {"module": f"{package}.contract", "path": f"{package}/contract.py", "responsibility": "tool input/output contract", "requirements": ["return structured result"]},
            {"module": f"{package}.tool", "path": f"{package}/tool.py", "responsibility": "local command entrypoint", "requirements": ["print structured result"]},
            {"module": "tests.test_contract", "path": "tests/test_contract.py", "responsibility": "local agent tool verification", "requirements": ["prove structured contract behavior"]},
        ],
        "common_failure_fixes": [{"failure": "unstructured output", "fix": "keep contract module returning serializable dictionaries"}],
    }


TEMPLATES = {
    "python_cli_basic": python_cli_template,
    "python_fastapi_service": fastapi_service_template,
    "python_library": python_library_template,
    "node_vite_app": node_vite_app_template,
    "static_site": static_site_template,
    "telegram_bot_python": telegram_bot_template,
    "data_processing_tool": data_processing_template,
    "local_agent_tool": local_agent_tool_template,
}


def template_for(template_id: str, project_name: str) -> dict[str, Any]:
    return TEMPLATES.get(template_id, python_cli_template)(project_name)


def available_templates() -> list[str]:
    return sorted(TEMPLATES)
