#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


def infer_acceptance_features(task: str) -> list[dict[str, Any]]:
    lowered = task.lower()
    features: list[dict[str, Any]] = []
    if any(word in lowered for word in ("calculator", "calculate", "калькулятор", "слож", "вычит", "умнож", "делен", "делить")):
        features.append(
            {
                "id": "calculator_operations",
                "kind": "functional_requirement",
                "description": "perform basic arithmetic operations through core logic and CLI entrypoint",
                "operations": ["add", "subtract", "multiply", "divide"],
            }
        )
    if any(word in lowered for word in ("todo", "to-do", "task list", "задачник", "список задач", "список дел", "тасклист")):
        features.append(
            {
                "id": "todo_list",
                "kind": "functional_requirement",
                "description": "provide a browser todo list with add, complete, delete, and persistent state behavior",
                "operations": ["add", "complete", "delete", "persist"],
            }
        )
    if any(word in lowered for word in ("kanban", "project board", "task board", "status board", "канбан", "доска задач", "проектная доска")):
        features.append(
            {
                "id": "kanban_board_frontend",
                "kind": "multi_workflow_requirement",
                "description": "provide a browser kanban board with card creation, status movement, filtering, metrics, rendering, and local persistence",
                "operations": ["create_card", "move_card", "filter_cards", "compute_metrics", "persist_board"],
            }
        )
    if any(word in lowered for word in ("notes", "note api", "замет", "заметки", "заметок")):
        features.append(
            {
                "id": "notes_api",
                "kind": "functional_requirement",
                "description": "provide note creation, listing, lookup, and deletion through service logic and HTTP routes",
                "operations": ["create", "list", "get", "delete"],
            }
        )
    if any(word in lowered for word in ("issue tracker", "ticket api", "bug tracker", "трекер задач", "трекер ошибок", "тикет")):
        features.append(
            {
                "id": "issue_tracker_api",
                "kind": "multi_workflow_requirement",
                "description": "provide issue creation, assignment, status transitions, filtering, and HTTP route wiring through separated domain, store, routes, and tests",
                "operations": ["create_issue", "assign_issue", "transition_issue", "filter_issues", "http_routes"],
            }
        )
    if any(word in lowered for word in ("operations dashboard", "service dashboard", "ops dashboard", "incident dashboard", "операционный дашборд", "дашборд операций", "дашборд сервисов")):
        features.append(
            {
                "id": "operations_dashboard_api",
                "kind": "long_form_multi_workflow_requirement",
                "description": "provide a multi-module operations dashboard API with service registration, incident tracking, health metrics, event timeline, and route adapters",
                "operations": ["register_service", "record_incident", "resolve_incident", "compute_health_metrics", "build_event_timeline", "http_routes"],
            }
        )
    if any(word in lowered for word in ("csv summary", "csv summarize", "summarize csv", "data summary", "сводк", "суммар", "csv отчет", "csv отчёт")):
        features.append(
            {
                "id": "csv_summary",
                "kind": "functional_requirement",
                "description": "summarize CSV rows, columns, numeric sums, and numeric averages through processor and CLI",
                "operations": ["count_rows", "list_columns", "sum_numeric_columns", "average_numeric_columns"],
            }
        )
    if any(word in lowered for word in ("analytics pipeline", "sales analytics", "multi workflow analytics", "аналитический пайплайн", "аналитика продаж", "воронка аналитики")):
        features.append(
            {
                "id": "sales_analytics_pipeline",
                "kind": "multi_workflow_requirement",
                "description": "load sales CSV records, filter them, group totals by region, render a markdown report, and expose CLI JSON output",
                "operations": ["load_records", "filter_records", "group_region_totals", "render_markdown_report", "cli_json_output"],
            }
        )
    if any(word in lowered for word in ("tool router", "command router", "agent tool", "local agent tool", "локальный агент", "инструмент агента", "роутер команд")):
        features.append(
            {
                "id": "local_agent_command_router",
                "kind": "functional_requirement",
                "description": "route named local agent tool actions through a registry, validate JSON payloads, and reject unknown actions",
                "operations": ["status", "echo", "summarize", "reject_unknown_action"],
            }
        )
    if any(word in lowered for word in ("telegram command", "telegram bot", "бот команд", "телеграм бот", "команды бота", "/start", "/help")):
        features.append(
            {
                "id": "telegram_command_bot",
                "kind": "functional_requirement",
                "description": "provide testable Telegram bot command handling for start, help, status, echo, and unknown commands",
                "operations": ["start", "help", "status", "echo", "unknown_command"],
            }
        )
    if any(word in lowered for word in ("react counter", "vite counter", "counter app", "счетчик", "счётчик")):
        features.append(
            {
                "id": "vite_counter_app",
                "kind": "functional_requirement",
                "description": "provide a Vite frontend counter app with increment, decrement, reset, and visible state",
                "operations": ["increment", "decrement", "reset", "render_count"],
            }
        )
    if any(word in lowered for word in ("text utils", "text utility", "text library", "slugify", "word count", "текстовая библиотека", "утилиты текста")):
        features.append(
            {
                "id": "python_text_utils_library",
                "kind": "functional_requirement",
                "description": "provide a reusable Python text utilities library with normalization, slug generation, word counting, and summaries",
                "operations": ["normalize_text", "slugify", "word_count", "summarize_text"],
            }
        )
    return features


def apply_task_feature_overrides(
    task: str,
    template_id: str,
    project_name: str,
    files: list[Any],
    module_contracts: list[Any],
) -> tuple[list[Any], list[Any], list[dict[str, Any]]]:
    features = infer_acceptance_features(task)
    if not features:
        return files, module_contracts, []
    if template_id == "python_cli_basic" and any(feature.get("id") == "calculator_operations" for feature in features):
        return apply_python_cli_calculator_feature(project_name, files), calculator_module_contracts(project_name), features
    if template_id == "static_site" and any(feature.get("id") == "kanban_board_frontend" for feature in features):
        return apply_static_site_kanban_board_feature(project_name, files), static_kanban_board_module_contracts(), features
    if template_id == "static_site" and any(feature.get("id") == "todo_list" for feature in features):
        return apply_static_site_todo_feature(project_name, files), static_todo_module_contracts(), features
    if template_id == "python_fastapi_service" and any(feature.get("id") == "issue_tracker_api" for feature in features):
        return apply_fastapi_issue_tracker_feature(project_name, files), fastapi_issue_tracker_module_contracts(), features
    if template_id == "python_fastapi_service" and any(feature.get("id") == "operations_dashboard_api" for feature in features):
        return apply_fastapi_operations_dashboard_feature(project_name, files), fastapi_operations_dashboard_module_contracts(), features
    if template_id == "python_fastapi_service" and any(feature.get("id") == "notes_api" for feature in features):
        return apply_fastapi_notes_feature(project_name, files), fastapi_notes_module_contracts(), features
    if template_id == "data_processing_tool" and any(feature.get("id") == "sales_analytics_pipeline" for feature in features):
        return apply_sales_analytics_pipeline_feature(project_name, files), sales_analytics_pipeline_module_contracts(project_name), features
    if template_id == "data_processing_tool" and any(feature.get("id") == "csv_summary" for feature in features):
        return apply_data_processing_csv_summary_feature(project_name, files), csv_summary_module_contracts(project_name), features
    if template_id == "local_agent_tool" and any(feature.get("id") == "local_agent_command_router" for feature in features):
        return apply_local_agent_command_router_feature(project_name, files), local_agent_command_router_module_contracts(project_name), features
    if template_id == "telegram_bot_python" and any(feature.get("id") == "telegram_command_bot" for feature in features):
        return apply_telegram_command_bot_feature(project_name, files), telegram_command_bot_module_contracts(project_name), features
    if template_id == "node_vite_app" and any(feature.get("id") == "vite_counter_app" for feature in features):
        return apply_vite_counter_app_feature(project_name, files), vite_counter_app_module_contracts(), features
    if template_id == "python_library" and any(feature.get("id") == "python_text_utils_library" for feature in features):
        return apply_python_text_utils_library_feature(project_name, files), python_text_utils_library_module_contracts(project_name), features
    return files, module_contracts, features


def replace_project_file(files: list[Any], rel_path: str, content: str) -> list[Any]:
    replaced = False
    rows: list[Any] = []
    for item in files:
        if isinstance(item, dict) and item.get("path") == rel_path:
            rows.append({"path": rel_path, "content": content})
            replaced = True
        else:
            rows.append(item)
    if not replaced:
        rows.append({"path": rel_path, "content": content})
    return rows


def apply_python_cli_calculator_feature(project_name: str, files: list[Any]) -> list[Any]:
    package = project_name.replace("-", "_")
    core = (
        "def calculate(left: float, operator: str, right: float) -> float:\n"
        "    if operator == 'add':\n"
        "        return left + right\n"
        "    if operator == 'subtract':\n"
        "        return left - right\n"
        "    if operator == 'multiply':\n"
        "        return left * right\n"
        "    if operator == 'divide':\n"
        "        if right == 0:\n"
        "            raise ValueError('division by zero')\n"
        "        return left / right\n"
        "    raise ValueError(f'unsupported operator: {operator}')\n\n\n"
        "def run() -> str:\n"
        "    return 'calculator ready'\n"
    )
    cli = (
        "import argparse\n\n"
        "from .core import calculate\n\n\n"
        "def build_parser() -> argparse.ArgumentParser:\n"
        "    parser = argparse.ArgumentParser(description='Run a basic calculator operation')\n"
        "    parser.add_argument('operator', choices=['add', 'subtract', 'multiply', 'divide'])\n"
        "    parser.add_argument('left', type=float)\n"
        "    parser.add_argument('right', type=float)\n"
        "    return parser\n\n\n"
        "def main(argv: list[str] | None = None) -> None:\n"
        "    args = build_parser().parse_args(argv)\n"
        "    print(calculate(args.left, args.operator, args.right))\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    tests = (
        f"import unittest\n\nfrom {package}.core import calculate, run\n\n\n"
        "class CalculatorTests(unittest.TestCase):\n"
        "    def test_operations(self):\n"
        "        self.assertEqual(calculate(2, 'add', 3), 5)\n"
        "        self.assertEqual(calculate(5, 'subtract', 2), 3)\n"
        "        self.assertEqual(calculate(4, 'multiply', 3), 12)\n"
        "        self.assertEqual(calculate(8, 'divide', 2), 4)\n\n"
        "    def test_division_by_zero_is_rejected(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            calculate(1, 'divide', 0)\n\n"
        "    def test_run_status(self):\n"
        "        self.assertEqual(run(), 'calculator ready')\n"
    )
    readme = (
        f"# {project_name}\n\n## Run\n\n```bash\npython -m {package}.cli add 2 3\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n\n"
        "```bash\npython -m py_compile "
        f"{package}/__init__.py {package}/registry.py {package}/schema.py {package}/session.py {package}/runner.py {package}/contract.py {package}/tool.py\n```\n"
    )
    rows = replace_project_file(files, f"{package}/core.py", core)
    rows = replace_project_file(rows, f"{package}/cli.py", cli)
    rows = replace_project_file(rows, "tests/test_core.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows


def calculator_module_contracts(project_name: str) -> list[dict[str, Any]]:
    package = project_name.replace("-", "_")
    return [
        {
            "module": f"{package}.core",
            "path": f"{package}/core.py",
            "responsibility": "calculator arithmetic behavior",
            "requirements": ["add numbers", "subtract numbers", "multiply numbers", "divide numbers", "reject division by zero"],
        },
        {
            "module": f"{package}.cli",
            "path": f"{package}/cli.py",
            "responsibility": "command-line calculator entrypoint",
            "requirements": ["parse operator and operands", "print calculated result"],
        },
        {
            "module": "tests.test_core",
            "path": "tests/test_core.py",
            "responsibility": "calculator behavior verification",
            "requirements": ["prove arithmetic operations", "prove division by zero rejection"],
        },
    ]


def apply_static_site_todo_feature(project_name: str, files: list[Any]) -> list[Any]:
    html = (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"  <title>{project_name} Todo</title>\n"
        "  <link rel=\"stylesheet\" href=\"styles.css\">\n"
        "</head>\n"
        "<body>\n"
        "  <main class=\"todo-shell\">\n"
        "    <header>\n"
        f"      <h1>{project_name} Todo</h1>\n"
        "      <p id=\"status\">ready</p>\n"
        "    </header>\n"
        "    <form id=\"todo-form\" class=\"todo-form\">\n"
        "      <label for=\"todo-input\">New task</label>\n"
        "      <div class=\"todo-entry\">\n"
        "        <input id=\"todo-input\" name=\"todo\" type=\"text\" autocomplete=\"off\" required>\n"
        "        <button id=\"todo-add\" type=\"submit\">Add</button>\n"
        "      </div>\n"
        "    </form>\n"
        "    <ul id=\"todo-list\" class=\"todo-list\" aria-live=\"polite\"></ul>\n"
        "  </main>\n"
        "  <script src=\"app.js\"></script>\n"
        "</body>\n"
        "</html>\n"
    )
    css = (
        ":root { color-scheme: light; font-family: Inter, system-ui, sans-serif; }\n"
        "body { margin: 0; background: #f3f5f8; color: #17191f; }\n"
        ".todo-shell { max-width: 720px; margin: 8vh auto; padding: 24px; }\n"
        "header { margin-bottom: 24px; }\n"
        "h1 { margin: 0 0 8px; font-size: 2rem; font-weight: 700; }\n"
        "#status { margin: 0; color: #526070; }\n"
        ".todo-form { display: grid; gap: 8px; margin-bottom: 20px; }\n"
        ".todo-entry { display: grid; grid-template-columns: 1fr auto; gap: 8px; }\n"
        "input, button { font: inherit; border-radius: 6px; border: 1px solid #c8d0da; padding: 10px 12px; }\n"
        "button { background: #243b55; color: white; cursor: pointer; }\n"
        ".todo-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }\n"
        ".todo-item { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 10px; background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 10px; }\n"
        ".todo-item.is-complete span { color: #657386; text-decoration: line-through; }\n"
    )
    js = (
        "const STORAGE_KEY = 'ceraxia.todo.items';\n"
        "let todos = loadTodos();\n\n"
        "function loadTodos() {\n"
        "  try {\n"
        "    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');\n"
        "  } catch (_error) {\n"
        "    return [];\n"
        "  }\n"
        "}\n\n"
        "function saveTodos() {\n"
        "  localStorage.setItem(STORAGE_KEY, JSON.stringify(todos));\n"
        "}\n\n"
        "function addTodo(title) {\n"
        "  const cleanTitle = title.trim();\n"
        "  if (!cleanTitle) return null;\n"
        "  const item = { id: Date.now().toString(36), title: cleanTitle, complete: false };\n"
        "  todos = [...todos, item];\n"
        "  saveTodos();\n"
        "  renderTodos();\n"
        "  return item;\n"
        "}\n\n"
        "function toggleTodo(id) {\n"
        "  todos = todos.map((item) => item.id === id ? { ...item, complete: !item.complete } : item);\n"
        "  saveTodos();\n"
        "  renderTodos();\n"
        "}\n\n"
        "function deleteTodo(id) {\n"
        "  todos = todos.filter((item) => item.id !== id);\n"
        "  saveTodos();\n"
        "  renderTodos();\n"
        "}\n\n"
        "function renderTodos() {\n"
        "  const list = document.querySelector('#todo-list');\n"
        "  list.innerHTML = '';\n"
        "  for (const item of todos) {\n"
        "    const row = document.createElement('li');\n"
        "    row.className = `todo-item${item.complete ? ' is-complete' : ''}`;\n"
        "    row.dataset.todoId = item.id;\n"
        "    row.innerHTML = `<input type=\"checkbox\" ${item.complete ? 'checked' : ''} aria-label=\"Complete task\"><span></span><button type=\"button\">Delete</button>`;\n"
        "    row.querySelector('span').textContent = item.title;\n"
        "    row.querySelector('input').addEventListener('change', () => toggleTodo(item.id));\n"
        "    row.querySelector('button').addEventListener('click', () => deleteTodo(item.id));\n"
        "    list.appendChild(row);\n"
        "  }\n"
        "  document.querySelector('#status').textContent = `${todos.length} task${todos.length === 1 ? '' : 's'}`;\n"
        "}\n\n"
        "document.querySelector('#todo-form').addEventListener('submit', (event) => {\n"
        "  event.preventDefault();\n"
        "  const input = document.querySelector('#todo-input');\n"
        "  addTodo(input.value);\n"
        "  input.value = '';\n"
        "  input.focus();\n"
        "});\n\n"
        "renderTodos();\n"
    )
    tests = (
        "from pathlib import Path\n"
        "import unittest\n\n\n"
        "class StaticTodoSiteTests(unittest.TestCase):\n"
        "    def test_entrypoint_contains_todo_ui(self):\n"
        "        html = Path('index.html').read_text(encoding='utf-8')\n"
        "        self.assertIn('todo-form', html)\n"
        "        self.assertIn('todo-input', html)\n"
        "        self.assertIn('todo-list', html)\n"
        "        self.assertIn('styles.css', html)\n"
        "        self.assertIn('app.js', html)\n\n"
        "    def test_script_implements_todo_behaviors(self):\n"
        "        script = Path('app.js').read_text(encoding='utf-8')\n"
        "        self.assertIn('function addTodo', script)\n"
        "        self.assertIn('function toggleTodo', script)\n"
        "        self.assertIn('function deleteTodo', script)\n"
        "        self.assertIn('function renderTodos', script)\n"
        "        self.assertIn('localStorage', script)\n"
    )
    readme = (
        f"# {project_name}\n\nA browser todo list with add, complete, delete, and local persistence behavior.\n\n"
        "## Run\n\n```bash\nopen index.html\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, "index.html", html)
    rows = replace_project_file(rows, "styles.css", css)
    rows = replace_project_file(rows, "app.js", js)
    rows = replace_project_file(rows, "tests/test_static_site.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows


def static_todo_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "static_page",
            "path": "index.html",
            "responsibility": "todo application document and accessible controls",
            "requirements": ["load stylesheet and script", "provide task input", "provide todo list region"],
        },
        {
            "module": "todo_script",
            "path": "app.js",
            "responsibility": "browser todo behavior",
            "requirements": ["add tasks", "toggle completed state", "delete tasks", "persist tasks in localStorage", "render current list"],
        },
        {
            "module": "tests.test_static_site",
            "path": "tests/test_static_site.py",
            "responsibility": "static todo structure and behavior-contract verification",
            "requirements": ["prove required HTML controls", "prove JavaScript exposes todo behaviors"],
        },
    ]


def apply_static_site_kanban_board_feature(project_name: str, files: list[Any]) -> list[Any]:
    html = (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"  <title>{project_name} Kanban</title>\n"
        "  <link rel=\"stylesheet\" href=\"styles.css\">\n"
        "</head>\n"
        "<body>\n"
        "  <main class=\"board-shell\">\n"
        "    <header class=\"board-header\">\n"
        f"      <h1>{project_name} Kanban</h1>\n"
        "      <p id=\"status\">ready</p>\n"
        "      <dl id=\"metrics\" class=\"metrics\" aria-live=\"polite\"></dl>\n"
        "    </header>\n"
        "    <form id=\"kanban-form\" class=\"card-form\">\n"
        "      <input id=\"card-title\" name=\"title\" type=\"text\" aria-label=\"Card title\" autocomplete=\"off\" required>\n"
        "      <input id=\"card-owner\" name=\"owner\" type=\"text\" aria-label=\"Owner\" autocomplete=\"off\">\n"
        "      <select id=\"card-priority\" name=\"priority\">\n"
        "        <option value=\"normal\">Normal</option>\n"
        "        <option value=\"high\">High</option>\n"
        "      </select>\n"
        "      <button type=\"submit\">Add card</button>\n"
        "    </form>\n"
        "    <nav id=\"filter-bar\" class=\"filter-bar\" aria-label=\"Board filters\">\n"
        "      <button type=\"button\" data-filter=\"all\">All</button>\n"
        "      <button type=\"button\" data-filter=\"high\">High priority</button>\n"
        "      <button type=\"button\" data-filter=\"mine\">Owned</button>\n"
        "    </nav>\n"
        "    <section id=\"board-columns\" class=\"board-columns\" aria-label=\"Kanban board\">\n"
        "      <section class=\"board-column\" data-status=\"backlog\"><h2>Backlog</h2><ol></ol></section>\n"
        "      <section class=\"board-column\" data-status=\"doing\"><h2>Doing</h2><ol></ol></section>\n"
        "      <section class=\"board-column\" data-status=\"done\"><h2>Done</h2><ol></ol></section>\n"
        "    </section>\n"
        "  </main>\n"
        "  <script src=\"state.js\"></script>\n"
        "  <script src=\"board.js\"></script>\n"
        "  <script src=\"app.js\"></script>\n"
        "</body>\n"
        "</html>\n"
    )
    css = (
        ":root { color-scheme: light; font-family: Inter, system-ui, sans-serif; background: #f4f6f9; color: #171a21; }\n"
        "body { margin: 0; }\n"
        ".board-shell { width: min(1120px, calc(100% - 32px)); margin: 32px auto; display: grid; gap: 18px; }\n"
        ".board-header { display: grid; gap: 8px; }\n"
        "h1, h2, p { margin: 0; }\n"
        ".metrics { display: flex; flex-wrap: wrap; gap: 10px; margin: 0; }\n"
        ".metrics div { display: grid; gap: 2px; min-width: 92px; background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 8px 10px; }\n"
        ".metrics dt { font-size: .78rem; color: #5d6878; }\n"
        ".metrics dd { margin: 0; font-weight: 700; }\n"
        ".card-form { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(140px, 220px) 130px auto; gap: 8px; }\n"
        "input, select, button { min-height: 40px; border: 1px solid #bfc9d8; border-radius: 6px; background: white; color: inherit; font: inherit; padding: 8px 10px; }\n"
        "button { cursor: pointer; font-weight: 650; }\n"
        ".filter-bar { display: flex; flex-wrap: wrap; gap: 8px; }\n"
        ".filter-bar button.is-active { background: #1f3553; color: white; border-color: #1f3553; }\n"
        ".board-columns { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }\n"
        ".board-column { min-height: 280px; background: #ffffff; border: 1px solid #d6dde8; border-radius: 8px; padding: 12px; }\n"
        ".board-column ol { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 10px; }\n"
        ".kanban-card { display: grid; gap: 8px; border: 1px solid #d4dae5; border-left: 4px solid #6d7c91; border-radius: 8px; padding: 10px; background: #fbfcfe; }\n"
        ".kanban-card[data-priority=\"high\"] { border-left-color: #b23b3b; }\n"
        ".card-meta { display: flex; flex-wrap: wrap; gap: 6px; color: #5b6573; font-size: .85rem; }\n"
        ".card-actions { display: flex; gap: 6px; }\n"
        ".card-actions button { min-height: 32px; padding: 5px 8px; }\n"
        "@media (max-width: 760px) { .card-form, .board-columns { grid-template-columns: 1fr; } }\n"
    )
    state_js = (
        "const KANBAN_STORAGE_KEY = 'ceraxia.kanban.board';\n"
        "const KANBAN_STATUSES = ['backlog', 'doing', 'done'];\n\n"
        "function defaultBoard() {\n"
        "  return { activeFilter: 'all', cards: [] };\n"
        "}\n\n"
        "function loadBoard() {\n"
        "  try {\n"
        "    const parsed = JSON.parse(localStorage.getItem(KANBAN_STORAGE_KEY) || 'null');\n"
        "    return parsed && Array.isArray(parsed.cards) ? parsed : defaultBoard();\n"
        "  } catch (_error) {\n"
        "    return defaultBoard();\n"
        "  }\n"
        "}\n\n"
        "function saveBoard(board) {\n"
        "  localStorage.setItem(KANBAN_STORAGE_KEY, JSON.stringify(board));\n"
        "  return board;\n"
        "}\n\n"
        "function createCard(board, input) {\n"
        "  const title = String(input.title || '').trim();\n"
        "  if (!title) return board;\n"
        "  const card = {\n"
        "    id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,\n"
        "    title,\n"
        "    owner: String(input.owner || '').trim() || 'unassigned',\n"
        "    priority: input.priority === 'high' ? 'high' : 'normal',\n"
        "    status: 'backlog'\n"
        "  };\n"
        "  return { ...board, cards: [...board.cards, card] };\n"
        "}\n\n"
        "function moveCard(board, cardId, nextStatus) {\n"
        "  if (!KANBAN_STATUSES.includes(nextStatus)) return board;\n"
        "  return { ...board, cards: board.cards.map((card) => card.id === cardId ? { ...card, status: nextStatus } : card) };\n"
        "}\n\n"
        "function filterCards(board, filterName) {\n"
        "  const filter = filterName || board.activeFilter || 'all';\n"
        "  if (filter === 'high') return board.cards.filter((card) => card.priority === 'high');\n"
        "  if (filter === 'mine') return board.cards.filter((card) => card.owner !== 'unassigned');\n"
        "  return board.cards;\n"
        "}\n\n"
        "function boardMetrics(board) {\n"
        "  const counts = Object.fromEntries(KANBAN_STATUSES.map((status) => [status, 0]));\n"
        "  for (const card of board.cards) counts[card.status] = (counts[card.status] || 0) + 1;\n"
        "  return { total: board.cards.length, high: board.cards.filter((card) => card.priority === 'high').length, ...counts };\n"
        "}\n"
    )
    board_js = (
        "function renderMetrics(board) {\n"
        "  const metrics = boardMetrics(board);\n"
        "  const node = document.querySelector('#metrics');\n"
        "  node.innerHTML = Object.entries(metrics).map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join('');\n"
        "}\n\n"
        "function renderFilters(board, onFilterChange) {\n"
        "  document.querySelectorAll('#filter-bar [data-filter]').forEach((button) => {\n"
        "    button.classList.toggle('is-active', button.dataset.filter === board.activeFilter);\n"
        "    button.onclick = () => onFilterChange(button.dataset.filter);\n"
        "  });\n"
        "}\n\n"
        "function renderCard(card, onMove) {\n"
        "  const item = document.createElement('li');\n"
        "  item.className = 'kanban-card';\n"
        "  item.dataset.cardId = card.id;\n"
        "  item.dataset.priority = card.priority;\n"
        "  item.innerHTML = `<strong></strong><div class=\"card-meta\"><span>${card.owner}</span><span>${card.priority}</span></div><div class=\"card-actions\"></div>`;\n"
        "  item.querySelector('strong').textContent = card.title;\n"
        "  const actions = item.querySelector('.card-actions');\n"
        "  for (const status of KANBAN_STATUSES) {\n"
        "    if (status === card.status) continue;\n"
        "    const button = document.createElement('button');\n"
        "    button.type = 'button';\n"
        "    button.textContent = status;\n"
        "    button.addEventListener('click', () => onMove(card.id, status));\n"
        "    actions.appendChild(button);\n"
        "  }\n"
        "  return item;\n"
        "}\n\n"
        "function renderBoard(board, onMove, onFilterChange) {\n"
        "  const visibleCards = filterCards(board, board.activeFilter);\n"
        "  document.querySelectorAll('[data-status]').forEach((column) => {\n"
        "    const status = column.dataset.status;\n"
        "    const list = column.querySelector('ol');\n"
        "    list.innerHTML = '';\n"
        "    for (const card of visibleCards.filter((item) => item.status === status)) list.appendChild(renderCard(card, onMove));\n"
        "  });\n"
        "  document.querySelector('#status').textContent = `${visibleCards.length} visible card${visibleCards.length === 1 ? '' : 's'}`;\n"
        "  renderMetrics(board);\n"
        "  renderFilters(board, onFilterChange);\n"
        "}\n"
    )
    app_js = (
        "let boardState = loadBoard();\n\n"
        "function commitBoard(nextBoard) {\n"
        "  boardState = saveBoard(nextBoard);\n"
        "  renderBoard(boardState, handleMoveCard, handleFilterChange);\n"
        "}\n\n"
        "function handleMoveCard(cardId, nextStatus) {\n"
        "  commitBoard(moveCard(boardState, cardId, nextStatus));\n"
        "}\n\n"
        "function handleFilterChange(filterName) {\n"
        "  commitBoard({ ...boardState, activeFilter: filterName });\n"
        "}\n\n"
        "document.querySelector('#kanban-form').addEventListener('submit', (event) => {\n"
        "  event.preventDefault();\n"
        "  const form = event.currentTarget;\n"
        "  commitBoard(createCard(boardState, {\n"
        "    title: form.elements.title.value,\n"
        "    owner: form.elements.owner.value,\n"
        "    priority: form.elements.priority.value\n"
        "  }));\n"
        "  form.reset();\n"
        "  form.elements.title.focus();\n"
        "});\n\n"
        "renderBoard(boardState, handleMoveCard, handleFilterChange);\n"
    )
    tests = (
        "from pathlib import Path\n"
        "import unittest\n\n\n"
        "class KanbanBoardContractTests(unittest.TestCase):\n"
        "    def test_entrypoint_wires_multi_file_frontend(self):\n"
        "        html = Path('index.html').read_text(encoding='utf-8')\n"
        "        self.assertIn('kanban-form', html)\n"
        "        self.assertIn('board-columns', html)\n"
        "        self.assertIn('data-status=\"backlog\"', html)\n"
        "        self.assertIn('data-status=\"doing\"', html)\n"
        "        self.assertIn('data-status=\"done\"', html)\n"
        "        self.assertLess(html.index('state.js'), html.index('board.js'))\n"
        "        self.assertLess(html.index('board.js'), html.index('app.js'))\n\n"
        "    def test_state_module_owns_board_workflows(self):\n"
        "        source = Path('state.js').read_text(encoding='utf-8')\n"
        "        for marker in ('function createCard', 'function moveCard', 'function filterCards', 'function boardMetrics', 'localStorage'):\n"
        "            self.assertIn(marker, source)\n\n"
        "    def test_render_and_app_modules_wire_interactions(self):\n"
        "        board = Path('board.js').read_text(encoding='utf-8')\n"
        "        app = Path('app.js').read_text(encoding='utf-8')\n"
        "        for marker in ('function renderBoard', 'function renderMetrics', 'data-status', 'onMove'):\n"
        "            self.assertIn(marker, board)\n"
        "        for marker in ('addEventListener', 'saveBoard', 'renderBoard', 'filterCards', 'createCard'):\n"
        "            self.assertIn(marker, app + board)\n"
    )
    readme = (
        f"# {project_name}\n\nA static kanban board with card creation, status movement, filtering, metrics, and local persistence.\n\n"
        "## Run\n\n```bash\nopen index.html\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, "index.html", html)
    rows = replace_project_file(rows, "styles.css", css)
    rows = replace_project_file(rows, "state.js", state_js)
    rows = replace_project_file(rows, "board.js", board_js)
    rows = replace_project_file(rows, "app.js", app_js)
    rows = replace_project_file(rows, "tests/test_kanban_board.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows


def static_kanban_board_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "static_page",
            "path": "index.html",
            "responsibility": "kanban application document and workflow regions",
            "requirements": ["provide card form", "provide status columns", "provide metrics region", "load state board app scripts in order"],
        },
        {
            "module": "kanban_state",
            "path": "state.js",
            "responsibility": "kanban board state transitions and persistence",
            "requirements": ["create cards", "move cards between statuses", "filter cards", "compute board metrics", "persist board in localStorage"],
        },
        {
            "module": "kanban_rendering",
            "path": "board.js",
            "responsibility": "kanban board rendering and interaction controls",
            "requirements": ["render status columns", "render card movement controls", "render metrics", "wire movement actions"],
        },
        {
            "module": "kanban_app_wiring",
            "path": "app.js",
            "responsibility": "kanban form, filter, and persistence event wiring",
            "requirements": ["handle card form submit", "handle filter changes", "save board after mutations", "rerender board state"],
        },
        {
            "module": "tests.test_kanban_board",
            "path": "tests/test_kanban_board.py",
            "responsibility": "kanban frontend workflow contract verification",
            "requirements": ["prove HTML contract", "prove state workflow", "prove rendering and app wiring"],
        },
    ]


def apply_fastapi_notes_feature(project_name: str, files: list[Any]) -> list[Any]:
    main = (
        "try:\n"
        "    from fastapi import FastAPI, HTTPException\n"
        "    from pydantic import BaseModel\n"
        "except ModuleNotFoundError:\n"
        "    FastAPI = None\n"
        "    HTTPException = None\n"
        "    BaseModel = object\n\n\n"
        "class NoteCreate(BaseModel):\n"
        "    title: str\n"
        "    body: str = ''\n\n\n"
        "_notes: dict[int, dict[str, object]] = {}\n"
        "_next_id = 1\n\n\n"
        "def reset_notes() -> None:\n"
        "    global _notes, _next_id\n"
        "    _notes = {}\n"
        "    _next_id = 1\n\n\n"
        "def create_note(title: str, body: str = '') -> dict[str, object]:\n"
        "    global _next_id\n"
        "    clean_title = title.strip()\n"
        "    if not clean_title:\n"
        "        raise ValueError('note title is required')\n"
        "    note = {'id': _next_id, 'title': clean_title, 'body': body.strip()}\n"
        "    _notes[_next_id] = note\n"
        "    _next_id += 1\n"
        "    return dict(note)\n\n\n"
        "def list_notes() -> list[dict[str, object]]:\n"
        "    return [dict(note) for note in _notes.values()]\n\n\n"
        "def get_note(note_id: int) -> dict[str, object]:\n"
        "    if note_id not in _notes:\n"
        "        raise KeyError(note_id)\n"
        "    return dict(_notes[note_id])\n\n\n"
        "def delete_note(note_id: int) -> dict[str, object]:\n"
        "    if note_id not in _notes:\n"
        "        raise KeyError(note_id)\n"
        "    return dict(_notes.pop(note_id))\n\n\n"
        "def health() -> dict[str, bool]:\n"
        "    return {'ok': True}\n\n\n"
        "if FastAPI is not None:\n"
        f"    app = FastAPI(title='{project_name} Notes API')\n\n"
        "    @app.get('/health')\n"
        "    def health_endpoint() -> dict[str, bool]:\n"
        "        return health()\n\n"
        "    @app.post('/notes')\n"
        "    def create_note_endpoint(payload: NoteCreate) -> dict[str, object]:\n"
        "        try:\n"
        "            return create_note(payload.title, payload.body)\n"
        "        except ValueError as exc:\n"
        "            raise HTTPException(status_code=400, detail=str(exc)) from exc\n\n"
        "    @app.get('/notes')\n"
        "    def list_notes_endpoint() -> list[dict[str, object]]:\n"
        "        return list_notes()\n\n"
        "    @app.get('/notes/{note_id}')\n"
        "    def get_note_endpoint(note_id: int) -> dict[str, object]:\n"
        "        try:\n"
        "            return get_note(note_id)\n"
        "        except KeyError as exc:\n"
        "            raise HTTPException(status_code=404, detail='note not found') from exc\n\n"
        "    @app.delete('/notes/{note_id}')\n"
        "    def delete_note_endpoint(note_id: int) -> dict[str, object]:\n"
        "        try:\n"
        "            return delete_note(note_id)\n"
        "        except KeyError as exc:\n"
        "            raise HTTPException(status_code=404, detail='note not found') from exc\n"
        "else:\n"
        "    app = None\n"
    )
    tests = (
        "import unittest\n\n"
        "from app.main import create_note, delete_note, get_note, health, list_notes, reset_notes\n\n\n"
        "class NotesApiTests(unittest.TestCase):\n"
        "    def setUp(self):\n"
        "        reset_notes()\n\n"
        "    def test_health(self):\n"
        "        self.assertEqual(health(), {'ok': True})\n\n"
        "    def test_create_list_and_get_note(self):\n"
        "        note = create_note(' First note ', ' body ')\n"
        "        self.assertEqual(note['id'], 1)\n"
        "        self.assertEqual(note['title'], 'First note')\n"
        "        self.assertEqual(note['body'], 'body')\n"
        "        self.assertEqual(list_notes(), [note])\n"
        "        self.assertEqual(get_note(1), note)\n\n"
        "    def test_rejects_empty_title(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            create_note('   ')\n\n"
        "    def test_delete_note(self):\n"
        "        note = create_note('remove me')\n"
        "        self.assertEqual(delete_note(note['id']), note)\n"
        "        self.assertEqual(list_notes(), [])\n"
        "        with self.assertRaises(KeyError):\n"
        "            get_note(note['id'])\n"
    )
    readme = (
        f"# {project_name}\n\nA FastAPI-compatible notes service with pure note logic tested without a live server.\n\n"
        "## Run\n\n```bash\nuvicorn app.main:app --reload\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n\n"
        "```bash\npython -m py_compile app/main.py\n```\n"
    )
    rows = replace_project_file(files, "app/main.py", main)
    rows = replace_project_file(rows, "tests/test_health.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows


def fastapi_notes_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "app.main",
            "path": "app/main.py",
            "responsibility": "notes service domain logic and optional FastAPI routes",
            "requirements": ["health returns ok true", "create notes", "list notes", "get notes by id", "delete notes", "reject empty note titles"],
        },
        {
            "module": "tests.test_health",
            "path": "tests/test_health.py",
            "responsibility": "notes service behavior verification",
            "requirements": ["prove note lifecycle", "prove invalid title rejection", "prove deletion behavior without requiring live server"],
        },
    ]


def apply_fastapi_operations_dashboard_feature(project_name: str, files: list[Any]) -> list[Any]:
    domain = (
        "from dataclasses import dataclass, replace\n"
        "from typing import Literal\n\n\n"
        "ServiceTier = Literal['critical', 'standard']\n"
        "IncidentStatus = Literal['open', 'resolved']\n\n\n"
        "@dataclass(frozen=True)\n"
        "class ServiceRecord:\n"
        "    id: int\n"
        "    name: str\n"
        "    owner: str\n"
        "    tier: ServiceTier = 'standard'\n"
        "    uptime: float = 100.0\n\n\n"
        "@dataclass(frozen=True)\n"
        "class Incident:\n"
        "    id: int\n"
        "    service_id: int\n"
        "    title: str\n"
        "    severity: int\n"
        "    status: IncidentStatus = 'open'\n\n\n"
        "def register_service(service_id: int, name: str, owner: str, tier: ServiceTier = 'standard', uptime: float = 100.0) -> ServiceRecord:\n"
        "    clean_name = name.strip()\n"
        "    clean_owner = owner.strip()\n"
        "    if not clean_name:\n"
        "        raise ValueError('service name is required')\n"
        "    if not clean_owner:\n"
        "        raise ValueError('service owner is required')\n"
        "    if tier not in {'critical', 'standard'}:\n"
        "        raise ValueError(f'unsupported service tier: {tier}')\n"
        "    if uptime < 0 or uptime > 100:\n"
        "        raise ValueError('uptime must be between 0 and 100')\n"
        "    return ServiceRecord(id=service_id, name=clean_name, owner=clean_owner, tier=tier, uptime=uptime)\n\n\n"
        "def record_incident(incident_id: int, service_id: int, title: str, severity: int) -> Incident:\n"
        "    clean_title = title.strip()\n"
        "    if not clean_title:\n"
        "        raise ValueError('incident title is required')\n"
        "    if severity < 1 or severity > 5:\n"
        "        raise ValueError('incident severity must be between 1 and 5')\n"
        "    return Incident(id=incident_id, service_id=service_id, title=clean_title, severity=severity)\n\n\n"
        "def resolve_incident(incident: Incident) -> Incident:\n"
        "    return replace(incident, status='resolved')\n"
    )
    store = (
        "from .domain import Incident, ServiceRecord, ServiceTier, register_service, record_incident, resolve_incident\n\n\n"
        "class OperationsStore:\n"
        "    def __init__(self) -> None:\n"
        "        self._services: dict[int, ServiceRecord] = {}\n"
        "        self._incidents: dict[int, Incident] = {}\n"
        "        self._next_service_id = 1\n"
        "        self._next_incident_id = 1\n\n"
        "    def reset(self) -> None:\n"
        "        self._services = {}\n"
        "        self._incidents = {}\n"
        "        self._next_service_id = 1\n"
        "        self._next_incident_id = 1\n\n"
        "    def register(self, name: str, owner: str, tier: ServiceTier = 'standard', uptime: float = 100.0) -> ServiceRecord:\n"
        "        service = register_service(self._next_service_id, name, owner, tier, uptime)\n"
        "        self._services[service.id] = service\n"
        "        self._next_service_id += 1\n"
        "        return service\n\n"
        "    def services(self, *, owner: str | None = None, tier: ServiceTier | None = None) -> list[ServiceRecord]:\n"
        "        rows = list(self._services.values())\n"
        "        if owner:\n"
        "            rows = [service for service in rows if service.owner == owner]\n"
        "        if tier:\n"
        "            rows = [service for service in rows if service.tier == tier]\n"
        "        return rows\n\n"
        "    def get_service(self, service_id: int) -> ServiceRecord:\n"
        "        if service_id not in self._services:\n"
        "            raise KeyError(service_id)\n"
        "        return self._services[service_id]\n\n"
        "    def open_incident(self, service_id: int, title: str, severity: int) -> Incident:\n"
        "        self.get_service(service_id)\n"
        "        incident = record_incident(self._next_incident_id, service_id, title, severity)\n"
        "        self._incidents[incident.id] = incident\n"
        "        self._next_incident_id += 1\n"
        "        return incident\n\n"
        "    def resolve(self, incident_id: int) -> Incident:\n"
        "        if incident_id not in self._incidents:\n"
        "            raise KeyError(incident_id)\n"
        "        incident = resolve_incident(self._incidents[incident_id])\n"
        "        self._incidents[incident.id] = incident\n"
        "        return incident\n\n"
        "    def incidents(self, *, status: str | None = None, service_id: int | None = None) -> list[Incident]:\n"
        "        rows = list(self._incidents.values())\n"
        "        if status:\n"
        "            rows = [incident for incident in rows if incident.status == status]\n"
        "        if service_id:\n"
        "            rows = [incident for incident in rows if incident.service_id == service_id]\n"
        "        return rows\n\n\n"
        "STORE = OperationsStore()\n"
    )
    metrics = (
        "from .domain import Incident, ServiceRecord\n\n\n"
        "def service_health_score(service: ServiceRecord, incidents: list[Incident]) -> float:\n"
        "    open_penalty = sum(incident.severity * 4 for incident in incidents if incident.service_id == service.id and incident.status == 'open')\n"
        "    tier_penalty = 5 if service.tier == 'critical' and service.uptime < 99.0 else 0\n"
        "    return max(0.0, min(100.0, service.uptime - open_penalty - tier_penalty))\n\n\n"
        "def build_dashboard_metrics(services: list[ServiceRecord], incidents: list[Incident]) -> dict[str, object]:\n"
        "    scores = {service.name: service_health_score(service, incidents) for service in services}\n"
        "    open_incidents = [incident for incident in incidents if incident.status == 'open']\n"
        "    critical_services = [service for service in services if service.tier == 'critical']\n"
        "    average_health = sum(scores.values()) / len(scores) if scores else 100.0\n"
        "    return {\n"
        "        'service_count': len(services),\n"
        "        'critical_service_count': len(critical_services),\n"
        "        'open_incident_count': len(open_incidents),\n"
        "        'average_health': round(average_health, 2),\n"
        "        'health_by_service': scores,\n"
        "    }\n"
    )
    events = (
        "from .domain import Incident, ServiceRecord\n\n\n"
        "def service_event(service: ServiceRecord) -> dict[str, object]:\n"
        "    return {'type': 'service_registered', 'service_id': service.id, 'label': service.name, 'owner': service.owner}\n\n\n"
        "def incident_event(incident: Incident) -> dict[str, object]:\n"
        "    return {'type': f'incident_{incident.status}', 'incident_id': incident.id, 'service_id': incident.service_id, 'severity': incident.severity, 'label': incident.title}\n\n\n"
        "def build_event_timeline(services: list[ServiceRecord], incidents: list[Incident]) -> list[dict[str, object]]:\n"
        "    service_rows = [service_event(service) for service in sorted(services, key=lambda item: item.id)]\n"
        "    incident_rows = [incident_event(incident) for incident in sorted(incidents, key=lambda item: item.id)]\n"
        "    return service_rows + incident_rows\n"
    )
    routes = (
        "try:\n"
        "    from fastapi import APIRouter, HTTPException\n"
        "    from pydantic import BaseModel\n"
        "except ModuleNotFoundError:\n"
        "    APIRouter = None\n"
        "    HTTPException = None\n"
        "    BaseModel = object\n\n"
        "from .domain import Incident, ServiceRecord, ServiceTier\n"
        "from .events import build_event_timeline\n"
        "from .metrics import build_dashboard_metrics\n"
        "from .store import STORE, OperationsStore\n\n\n"
        "class ServiceCreate(BaseModel):\n"
        "    name: str\n"
        "    owner: str\n"
        "    tier: ServiceTier = 'standard'\n"
        "    uptime: float = 100.0\n\n\n"
        "class IncidentCreate(BaseModel):\n"
        "    service_id: int\n"
        "    title: str\n"
        "    severity: int\n\n\n"
        "def service_to_dict(service: ServiceRecord) -> dict[str, object]:\n"
        "    return {'id': service.id, 'name': service.name, 'owner': service.owner, 'tier': service.tier, 'uptime': service.uptime}\n\n\n"
        "def incident_to_dict(incident: Incident) -> dict[str, object]:\n"
        "    return {'id': incident.id, 'service_id': incident.service_id, 'title': incident.title, 'severity': incident.severity, 'status': incident.status}\n\n\n"
        "def register_service_response(payload: dict[str, object], store: OperationsStore = STORE) -> dict[str, object]:\n"
        "    return service_to_dict(store.register(str(payload.get('name', '')), str(payload.get('owner', '')), payload.get('tier', 'standard'), float(payload.get('uptime', 100.0))))\n\n\n"
        "def record_incident_response(payload: dict[str, object], store: OperationsStore = STORE) -> dict[str, object]:\n"
        "    return incident_to_dict(store.open_incident(int(payload.get('service_id', 0)), str(payload.get('title', '')), int(payload.get('severity', 1))))\n\n\n"
        "def resolve_incident_response(incident_id: int, store: OperationsStore = STORE) -> dict[str, object]:\n"
        "    return incident_to_dict(store.resolve(incident_id))\n\n\n"
        "def dashboard_response(store: OperationsStore = STORE) -> dict[str, object]:\n"
        "    services = store.services()\n"
        "    incidents = store.incidents()\n"
        "    return {'metrics': build_dashboard_metrics(services, incidents), 'events': build_event_timeline(services, incidents)}\n\n\n"
        "router = APIRouter(prefix='/operations') if APIRouter is not None else None\n"
        "if router is not None:\n"
        "    @router.post('/services')\n"
        "    def register_service_endpoint(payload: ServiceCreate) -> dict[str, object]:\n"
        "        try:\n"
        "            return register_service_response({'name': payload.name, 'owner': payload.owner, 'tier': payload.tier, 'uptime': payload.uptime})\n"
        "        except ValueError as exc:\n"
        "            raise HTTPException(status_code=400, detail=str(exc)) from exc\n\n"
        "    @router.post('/incidents')\n"
        "    def record_incident_endpoint(payload: IncidentCreate) -> dict[str, object]:\n"
        "        try:\n"
        "            return record_incident_response({'service_id': payload.service_id, 'title': payload.title, 'severity': payload.severity})\n"
        "        except (KeyError, ValueError) as exc:\n"
        "            raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 400, detail=str(exc)) from exc\n\n"
        "    @router.post('/incidents/{incident_id}/resolve')\n"
        "    def resolve_incident_endpoint(incident_id: int) -> dict[str, object]:\n"
        "        try:\n"
        "            return resolve_incident_response(incident_id)\n"
        "        except KeyError as exc:\n"
        "            raise HTTPException(status_code=404, detail='incident not found') from exc\n\n"
        "    @router.get('/dashboard')\n"
        "    def dashboard_endpoint() -> dict[str, object]:\n"
        "        return dashboard_response()\n"
    )
    main = (
        "try:\n"
        "    from fastapi import FastAPI\n"
        "except ModuleNotFoundError:\n"
        "    FastAPI = None\n\n"
        "from .routes import router\n\n\n"
        "def health() -> dict[str, bool]:\n"
        "    return {'ok': True}\n\n\n"
        "if FastAPI is not None:\n"
        f"    app = FastAPI(title='{project_name} Operations Dashboard API')\n\n"
        "    @app.get('/health')\n"
        "    def health_endpoint() -> dict[str, bool]:\n"
        "        return health()\n\n"
        "    if router is not None:\n"
        "        app.include_router(router)\n"
        "else:\n"
        "    app = None\n"
    )
    tests = (
        "import unittest\n\n"
        "from app.domain import record_incident, register_service, resolve_incident\n"
        "from app.events import build_event_timeline\n"
        "from app.metrics import build_dashboard_metrics, service_health_score\n"
        "from app.routes import dashboard_response, record_incident_response, register_service_response, resolve_incident_response\n"
        "from app.store import OperationsStore\n\n\n"
        "class OperationsDashboardWorkflowTests(unittest.TestCase):\n"
        "    def test_domain_service_incident_lifecycle(self):\n"
        "        service = register_service(1, ' API ', 'Ops', 'critical', 98.5)\n"
        "        incident = record_incident(1, service.id, ' Latency spike ', 3)\n"
        "        resolved = resolve_incident(incident)\n"
        "        self.assertEqual(service.name, 'API')\n"
        "        self.assertEqual(incident.status, 'open')\n"
        "        self.assertEqual(resolved.status, 'resolved')\n\n"
        "    def test_store_metrics_and_filters_workflow(self):\n"
        "        store = OperationsStore()\n"
        "        api = store.register('API', 'Ops', 'critical', 98.5)\n"
        "        web = store.register('Web', 'Product', 'standard', 99.9)\n"
        "        incident = store.open_incident(api.id, 'Latency spike', 3)\n"
        "        metrics = build_dashboard_metrics(store.services(), store.incidents())\n"
        "        self.assertEqual([service.name for service in store.services(owner='Ops')], ['API'])\n"
        "        self.assertEqual(store.incidents(status='open'), [incident])\n"
        "        self.assertLess(service_health_score(api, store.incidents()), service_health_score(web, store.incidents()))\n"
        "        self.assertEqual(metrics['open_incident_count'], 1)\n\n"
        "    def test_events_and_route_adapters_workflow(self):\n"
        "        store = OperationsStore()\n"
        "        service = register_service_response({'name': 'Search', 'owner': 'Platform', 'tier': 'critical', 'uptime': 99.0}, store)\n"
        "        incident = record_incident_response({'service_id': service['id'], 'title': 'Error budget burn', 'severity': 4}, store)\n"
        "        resolved = resolve_incident_response(incident['id'], store)\n"
        "        dashboard = dashboard_response(store)\n"
        "        timeline = build_event_timeline(store.services(), store.incidents())\n"
        "        self.assertEqual(resolved['status'], 'resolved')\n"
        "        self.assertEqual(dashboard['metrics']['service_count'], 1)\n"
        "        self.assertEqual(timeline[0]['type'], 'service_registered')\n"
        "        self.assertEqual(timeline[-1]['type'], 'incident_resolved')\n"
    )
    readme = (
        f"# {project_name}\n\nA long-form FastAPI operations dashboard with service registry, incident workflow, metrics, timeline events, route adapters, and workflow tests.\n\n"
        "## Run\n\n```bash\nuvicorn app.main:app --reload\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n\n"
        "```bash\npython -m py_compile app/main.py app/domain.py app/store.py app/metrics.py app/events.py app/routes.py\n```\n"
    )
    rows = replace_project_file(files, "app/domain.py", domain)
    rows = replace_project_file(rows, "app/store.py", store)
    rows = replace_project_file(rows, "app/metrics.py", metrics)
    rows = replace_project_file(rows, "app/events.py", events)
    rows = replace_project_file(rows, "app/routes.py", routes)
    rows = replace_project_file(rows, "app/main.py", main)
    rows = replace_project_file(rows, "tests/test_operations_dashboard.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows


def fastapi_operations_dashboard_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "app.domain",
            "path": "app/domain.py",
            "responsibility": "operations dashboard service and incident domain model",
            "requirements": ["register services", "record incidents", "resolve incidents", "validate service and incident inputs"],
        },
        {
            "module": "app.store",
            "path": "app/store.py",
            "responsibility": "operations dashboard in-memory repository",
            "requirements": ["store services", "filter services", "store incidents", "filter incidents", "resolve stored incidents"],
        },
        {
            "module": "app.metrics",
            "path": "app/metrics.py",
            "responsibility": "operations dashboard health metrics",
            "requirements": ["compute service health score", "count open incidents", "count critical services", "build health by service"],
        },
        {
            "module": "app.events",
            "path": "app/events.py",
            "responsibility": "operations dashboard event timeline",
            "requirements": ["build service events", "build incident events", "order event timeline"],
        },
        {
            "module": "app.routes",
            "path": "app/routes.py",
            "responsibility": "operations dashboard FastAPI route adapters",
            "requirements": ["register service route response", "record incident route response", "resolve incident route response", "dashboard route response"],
        },
        {
            "module": "app.main",
            "path": "app/main.py",
            "responsibility": "operations dashboard FastAPI app assembly",
            "requirements": ["expose health", "include operations router when FastAPI is installed"],
        },
        {
            "module": "tests.test_operations_dashboard",
            "path": "tests/test_operations_dashboard.py",
            "responsibility": "operations dashboard long-form workflow verification",
            "requirements": ["prove domain lifecycle", "prove store metrics workflow", "prove route and event workflow"],
        },
    ]


def apply_fastapi_issue_tracker_feature(project_name: str, files: list[Any]) -> list[Any]:
    domain = (
        "from dataclasses import dataclass, replace\n"
        "from typing import Literal\n\n\n"
        "IssueStatus = Literal['open', 'in_progress', 'resolved']\n\n\n"
        "@dataclass(frozen=True)\n"
        "class Issue:\n"
        "    id: int\n"
        "    title: str\n"
        "    description: str = ''\n"
        "    assignee: str = ''\n"
        "    status: IssueStatus = 'open'\n\n\n"
        "def create_issue(issue_id: int, title: str, description: str = '') -> Issue:\n"
        "    clean_title = title.strip()\n"
        "    if not clean_title:\n"
        "        raise ValueError('issue title is required')\n"
        "    return Issue(id=issue_id, title=clean_title, description=description.strip())\n\n\n"
        "def assign_issue(issue: Issue, assignee: str) -> Issue:\n"
        "    clean_assignee = assignee.strip()\n"
        "    if not clean_assignee:\n"
        "        raise ValueError('assignee is required')\n"
        "    return replace(issue, assignee=clean_assignee)\n\n\n"
        "def transition_issue(issue: Issue, status: IssueStatus) -> Issue:\n"
        "    if status not in {'open', 'in_progress', 'resolved'}:\n"
        "        raise ValueError(f'unsupported issue status: {status}')\n"
        "    return replace(issue, status=status)\n"
    )
    store = (
        "from .domain import Issue, IssueStatus, assign_issue, create_issue, transition_issue\n\n\n"
        "class IssueStore:\n"
        "    def __init__(self) -> None:\n"
        "        self._issues: dict[int, Issue] = {}\n"
        "        self._next_id = 1\n\n"
        "    def reset(self) -> None:\n"
        "        self._issues = {}\n"
        "        self._next_id = 1\n\n"
        "    def create(self, title: str, description: str = '') -> Issue:\n"
        "        issue = create_issue(self._next_id, title, description)\n"
        "        self._issues[issue.id] = issue\n"
        "        self._next_id += 1\n"
        "        return issue\n\n"
        "    def get(self, issue_id: int) -> Issue:\n"
        "        if issue_id not in self._issues:\n"
        "            raise KeyError(issue_id)\n"
        "        return self._issues[issue_id]\n\n"
        "    def list(self, *, status: IssueStatus | None = None, assignee: str | None = None) -> list[Issue]:\n"
        "        issues = list(self._issues.values())\n"
        "        if status:\n"
        "            issues = [issue for issue in issues if issue.status == status]\n"
        "        if assignee:\n"
        "            issues = [issue for issue in issues if issue.assignee == assignee]\n"
        "        return issues\n\n"
        "    def assign(self, issue_id: int, assignee: str) -> Issue:\n"
        "        issue = assign_issue(self.get(issue_id), assignee)\n"
        "        self._issues[issue.id] = issue\n"
        "        return issue\n\n"
        "    def transition(self, issue_id: int, status: IssueStatus) -> Issue:\n"
        "        issue = transition_issue(self.get(issue_id), status)\n"
        "        self._issues[issue.id] = issue\n"
        "        return issue\n\n\n"
        "STORE = IssueStore()\n"
    )
    routes = (
        "try:\n"
        "    from fastapi import APIRouter, HTTPException\n"
        "    from pydantic import BaseModel\n"
        "except ModuleNotFoundError:\n"
        "    APIRouter = None\n"
        "    HTTPException = None\n"
        "    BaseModel = object\n\n"
        "from .domain import Issue, IssueStatus\n"
        "from .store import STORE, IssueStore\n\n\n"
        "class IssueCreate(BaseModel):\n"
        "    title: str\n"
        "    description: str = ''\n\n\n"
        "class IssueAssignment(BaseModel):\n"
        "    assignee: str\n\n\n"
        "class IssueTransition(BaseModel):\n"
        "    status: IssueStatus\n\n\n"
        "def issue_to_dict(issue: Issue) -> dict[str, object]:\n"
        "    return {'id': issue.id, 'title': issue.title, 'description': issue.description, 'assignee': issue.assignee, 'status': issue.status}\n\n\n"
        "def create_issue_response(payload: dict[str, str], store: IssueStore = STORE) -> dict[str, object]:\n"
        "    return issue_to_dict(store.create(payload.get('title', ''), payload.get('description', '')))\n\n\n"
        "def list_issue_response(status: IssueStatus | None = None, assignee: str | None = None, store: IssueStore = STORE) -> list[dict[str, object]]:\n"
        "    return [issue_to_dict(issue) for issue in store.list(status=status, assignee=assignee)]\n\n\n"
        "def assign_issue_response(issue_id: int, payload: dict[str, str], store: IssueStore = STORE) -> dict[str, object]:\n"
        "    return issue_to_dict(store.assign(issue_id, payload.get('assignee', '')))\n\n\n"
        "def transition_issue_response(issue_id: int, payload: dict[str, str], store: IssueStore = STORE) -> dict[str, object]:\n"
        "    return issue_to_dict(store.transition(issue_id, payload.get('status', 'open')))\n\n\n"
        "router = APIRouter(prefix='/issues') if APIRouter is not None else None\n"
        "if router is not None:\n"
        "    @router.post('')\n"
        "    def create_issue_endpoint(payload: IssueCreate) -> dict[str, object]:\n"
        "        try:\n"
        "            return create_issue_response({'title': payload.title, 'description': payload.description})\n"
        "        except ValueError as exc:\n"
        "            raise HTTPException(status_code=400, detail=str(exc)) from exc\n\n"
        "    @router.get('')\n"
        "    def list_issues_endpoint(status: IssueStatus | None = None, assignee: str | None = None) -> list[dict[str, object]]:\n"
        "        return list_issue_response(status=status, assignee=assignee)\n\n"
        "    @router.post('/{issue_id}/assign')\n"
        "    def assign_issue_endpoint(issue_id: int, payload: IssueAssignment) -> dict[str, object]:\n"
        "        try:\n"
        "            return assign_issue_response(issue_id, {'assignee': payload.assignee})\n"
        "        except (KeyError, ValueError) as exc:\n"
        "            raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 400, detail=str(exc)) from exc\n\n"
        "    @router.post('/{issue_id}/transition')\n"
        "    def transition_issue_endpoint(issue_id: int, payload: IssueTransition) -> dict[str, object]:\n"
        "        try:\n"
        "            return transition_issue_response(issue_id, {'status': payload.status})\n"
        "        except (KeyError, ValueError) as exc:\n"
        "            raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 400, detail=str(exc)) from exc\n"
    )
    main = (
        "try:\n"
        "    from fastapi import FastAPI\n"
        "except ModuleNotFoundError:\n"
        "    FastAPI = None\n\n"
        "from .routes import router\n\n\n"
        "def health() -> dict[str, bool]:\n"
        "    return {'ok': True}\n\n\n"
        "if FastAPI is not None:\n"
        f"    app = FastAPI(title='{project_name} Issue Tracker API')\n\n"
        "    @app.get('/health')\n"
        "    def health_endpoint() -> dict[str, bool]:\n"
        "        return health()\n\n"
        "    if router is not None:\n"
        "        app.include_router(router)\n"
        "else:\n"
        "    app = None\n"
    )
    tests = (
        "import unittest\n\n"
        "from app.domain import assign_issue, create_issue, transition_issue\n"
        "from app.routes import assign_issue_response, create_issue_response, list_issue_response, transition_issue_response\n"
        "from app.store import IssueStore\n\n\n"
        "class IssueTrackerWorkflowTests(unittest.TestCase):\n"
        "    def test_domain_create_assign_transition_workflow(self):\n"
        "        issue = create_issue(1, ' Login bug ', ' fails on mobile ')\n"
        "        self.assertEqual(issue.title, 'Login bug')\n"
        "        assigned = assign_issue(issue, 'Ahriman')\n"
        "        self.assertEqual(assigned.assignee, 'Ahriman')\n"
        "        resolved = transition_issue(assigned, 'resolved')\n"
        "        self.assertEqual(resolved.status, 'resolved')\n\n"
        "    def test_store_filtering_workflow(self):\n"
        "        store = IssueStore()\n"
        "        first = store.create('First')\n"
        "        second = store.create('Second')\n"
        "        store.assign(first.id, 'Khayon')\n"
        "        store.assign(second.id, 'Tezek')\n"
        "        store.transition(second.id, 'in_progress')\n"
        "        self.assertEqual([issue.title for issue in store.list(assignee='Khayon')], ['First'])\n"
        "        self.assertEqual([issue.title for issue in store.list(status='in_progress')], ['Second'])\n\n"
        "    def test_route_adapter_workflow(self):\n"
        "        store = IssueStore()\n"
        "        created = create_issue_response({'title': 'Route bug', 'description': 'bad status'}, store)\n"
        "        assigned = assign_issue_response(created['id'], {'assignee': 'Lheor'}, store)\n"
        "        self.assertEqual(assigned['assignee'], 'Lheor')\n"
        "        transitioned = transition_issue_response(created['id'], {'status': 'resolved'}, store)\n"
        "        self.assertEqual(transitioned['status'], 'resolved')\n"
        "        self.assertEqual(list_issue_response(status='resolved', store=store), [transitioned])\n"
    )
    readme = (
        f"# {project_name}\n\nA multi-module FastAPI issue tracker service with domain logic, in-memory store, route adapters, and workflow tests.\n\n"
        "## Run\n\n```bash\nuvicorn app.main:app --reload\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n\n"
        "```bash\npython -m py_compile app/main.py app/domain.py app/store.py app/routes.py\n```\n"
    )
    rows = replace_project_file(files, "app/domain.py", domain)
    rows = replace_project_file(rows, "app/store.py", store)
    rows = replace_project_file(rows, "app/routes.py", routes)
    rows = replace_project_file(rows, "app/main.py", main)
    rows = replace_project_file(rows, "tests/test_issue_tracker.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows


def fastapi_issue_tracker_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "app.domain",
            "path": "app/domain.py",
            "responsibility": "issue entity and pure transition behavior",
            "requirements": ["create issues", "assign issues", "transition issue statuses", "reject invalid titles and statuses"],
        },
        {
            "module": "app.store",
            "path": "app/store.py",
            "responsibility": "in-memory issue repository and filters",
            "requirements": ["create stored issues", "get issues by id", "filter by status", "filter by assignee", "update assignment and status"],
        },
        {
            "module": "app.routes",
            "path": "app/routes.py",
            "responsibility": "FastAPI route adapter and pure response helpers",
            "requirements": ["create issue route response", "list issue route response", "assign issue route response", "transition issue route response"],
        },
        {
            "module": "app.main",
            "path": "app/main.py",
            "responsibility": "FastAPI application assembly and health endpoint",
            "requirements": ["expose health", "include issue router when FastAPI is installed"],
        },
        {
            "module": "tests.test_issue_tracker",
            "path": "tests/test_issue_tracker.py",
            "responsibility": "issue tracker multi-workflow verification",
            "requirements": ["prove domain workflow", "prove store filtering workflow", "prove route adapter workflow"],
        },
    ]


def apply_data_processing_csv_summary_feature(project_name: str, files: list[Any]) -> list[Any]:
    package = project_name.replace("-", "_")
    processor = (
        "import csv\n"
        "from io import StringIO\n\n\n"
        "def _to_number(value: str) -> float | None:\n"
        "    try:\n"
        "        return float(value)\n"
        "    except (TypeError, ValueError):\n"
        "        return None\n\n\n"
        "def summarize_rows(csv_text: str) -> dict[str, object]:\n"
        "    reader = csv.DictReader(StringIO(csv_text))\n"
        "    rows = list(reader)\n"
        "    columns = list(reader.fieldnames or [])\n"
        "    numeric_values: dict[str, list[float]] = {column: [] for column in columns}\n"
        "    for row in rows:\n"
        "        for column in columns:\n"
        "            number = _to_number(row.get(column, ''))\n"
        "            if number is not None:\n"
        "                numeric_values[column].append(number)\n"
        "    numeric_columns = {column: values for column, values in numeric_values.items() if values}\n"
        "    sums = {column: sum(values) for column, values in numeric_columns.items()}\n"
        "    averages = {column: sums[column] / len(values) for column, values in numeric_columns.items()}\n"
        "    return {\n"
        "        'rows': len(rows),\n"
        "        'columns': columns,\n"
        "        'numeric_sums': sums,\n"
        "        'numeric_averages': averages,\n"
        "    }\n"
    )
    cli = (
        "from pathlib import Path\n"
        "import json\n"
        "import sys\n\n"
        "from .processor import summarize_rows\n\n\n"
        "def main(argv: list[str] | None = None) -> None:\n"
        "    args = list(sys.argv[1:] if argv is None else argv)\n"
        "    if not args:\n"
        "        raise SystemExit('input csv path required')\n"
        "    summary = summarize_rows(Path(args[0]).read_text(encoding='utf-8'))\n"
        "    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    tests = (
        f"import unittest\n\nfrom {package}.processor import summarize_rows\n\n\n"
        "class CsvSummaryTests(unittest.TestCase):\n"
        "    def test_counts_rows_columns_sums_and_averages(self):\n"
        "        summary = summarize_rows('name,score,cost\\na,10,2.5\\nb,20,3.5\\n')\n"
        "        self.assertEqual(summary['rows'], 2)\n"
        "        self.assertEqual(summary['columns'], ['name', 'score', 'cost'])\n"
        "        self.assertEqual(summary['numeric_sums'], {'score': 30.0, 'cost': 6.0})\n"
        "        self.assertEqual(summary['numeric_averages'], {'score': 15.0, 'cost': 3.0})\n\n"
        "    def test_ignores_non_numeric_values(self):\n"
        "        summary = summarize_rows('name,score\\na,10\\nb,nope\\n')\n"
        "        self.assertEqual(summary['numeric_sums'], {'score': 10.0})\n"
    )
    readme = (
        f"# {project_name}\n\nA CSV summary tool that reports row count, columns, numeric sums, and numeric averages.\n\n"
        "## Run\n\n```bash\npython -m "
        f"{package}.cli input.csv\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, f"{package}/processor.py", processor)
    rows = replace_project_file(rows, f"{package}/cli.py", cli)
    rows = replace_project_file(rows, "tests/test_processor.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows


def csv_summary_module_contracts(project_name: str) -> list[dict[str, Any]]:
    package = project_name.replace("-", "_")
    return [
        {
            "module": f"{package}.processor",
            "path": f"{package}/processor.py",
            "responsibility": "CSV summary domain logic",
            "requirements": ["count rows", "list columns", "sum numeric columns", "average numeric columns", "ignore non-numeric values"],
        },
        {
            "module": f"{package}.cli",
            "path": f"{package}/cli.py",
            "responsibility": "file-based CSV summary CLI",
            "requirements": ["read input path", "print JSON summary"],
        },
        {
            "module": "tests.test_processor",
            "path": "tests/test_processor.py",
            "responsibility": "CSV summary behavior verification",
            "requirements": ["prove row and column summary", "prove numeric sums and averages", "prove non-numeric values are ignored"],
        },
    ]


def apply_sales_analytics_pipeline_feature(project_name: str, files: list[Any]) -> list[Any]:
    package = project_name.replace("-", "_")
    loader = (
        "import csv\n"
        "from dataclasses import dataclass\n"
        "from io import StringIO\n\n\n"
        "@dataclass(frozen=True)\n"
        "class SaleRecord:\n"
        "    region: str\n"
        "    product: str\n"
        "    amount: float\n"
        "    channel: str\n\n\n"
        "def load_records(csv_text: str) -> list[SaleRecord]:\n"
        "    reader = csv.DictReader(StringIO(csv_text))\n"
        "    records: list[SaleRecord] = []\n"
        "    for row in reader:\n"
        "        region = (row.get('region') or '').strip()\n"
        "        product = (row.get('product') or '').strip()\n"
        "        channel = (row.get('channel') or '').strip() or 'unknown'\n"
        "        if not region or not product:\n"
        "            continue\n"
        "        records.append(SaleRecord(region=region, product=product, amount=float(row.get('amount') or 0), channel=channel))\n"
        "    return records\n"
    )
    analyzer = (
        "from .loader import SaleRecord\n\n\n"
        "def filter_records(records: list[SaleRecord], *, min_amount: float = 0, channel: str | None = None) -> list[SaleRecord]:\n"
        "    selected = [record for record in records if record.amount >= min_amount]\n"
        "    if channel:\n"
        "        selected = [record for record in selected if record.channel == channel]\n"
        "    return selected\n\n\n"
        "def group_region_totals(records: list[SaleRecord]) -> dict[str, float]:\n"
        "    totals: dict[str, float] = {}\n"
        "    for record in records:\n"
        "        totals[record.region] = totals.get(record.region, 0.0) + record.amount\n"
        "    return dict(sorted(totals.items()))\n\n\n"
        "def top_region(records: list[SaleRecord]) -> str:\n"
        "    totals = group_region_totals(records)\n"
        "    if not totals:\n"
        "        return ''\n"
        "    return max(totals, key=lambda region: totals[region])\n"
    )
    report = (
        "from .analyzer import group_region_totals, top_region\n"
        "from .loader import SaleRecord\n\n\n"
        "def build_summary(records: list[SaleRecord]) -> dict[str, object]:\n"
        "    totals = group_region_totals(records)\n"
        "    return {\n"
        "        'record_count': len(records),\n"
        "        'region_totals': totals,\n"
        "        'top_region': top_region(records),\n"
        "    }\n\n\n"
        "def render_markdown_report(records: list[SaleRecord]) -> str:\n"
        "    summary = build_summary(records)\n"
        "    lines = ['# Sales Analytics Report', '', f\"Records: {summary['record_count']}\", '', '## Region totals']\n"
        "    for region, total in summary['region_totals'].items():\n"
        "        lines.append(f'- {region}: {total:.2f}')\n"
        "    lines.append('')\n"
        "    lines.append(f\"Top region: {summary['top_region'] or 'n/a'}\")\n"
        "    return '\\n'.join(lines)\n"
    )
    cli = (
        "import argparse\n"
        "import json\n"
        "from pathlib import Path\n\n"
        "from .analyzer import filter_records\n"
        "from .loader import load_records\n"
        "from .report import build_summary, render_markdown_report\n\n\n"
        "def build_parser() -> argparse.ArgumentParser:\n"
        "    parser = argparse.ArgumentParser(description='Run the sales analytics pipeline')\n"
        "    parser.add_argument('csv_path')\n"
        "    parser.add_argument('--min-amount', type=float, default=0)\n"
        "    parser.add_argument('--channel')\n"
        "    parser.add_argument('--format', choices=['json', 'markdown'], default='json')\n"
        "    return parser\n\n\n"
        "def run_pipeline(csv_text: str, *, min_amount: float = 0, channel: str | None = None) -> list[object]:\n"
        "    return filter_records(load_records(csv_text), min_amount=min_amount, channel=channel)\n\n\n"
        "def main(argv: list[str] | None = None) -> None:\n"
        "    args = build_parser().parse_args(argv)\n"
        "    records = run_pipeline(Path(args.csv_path).read_text(encoding='utf-8'), min_amount=args.min_amount, channel=args.channel)\n"
        "    if args.format == 'markdown':\n"
        "        print(render_markdown_report(records))\n"
        "    else:\n"
        "        print(json.dumps(build_summary(records), ensure_ascii=False, sort_keys=True))\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    tests = (
        f"import json\nimport tempfile\nimport unittest\nfrom io import StringIO\nfrom pathlib import Path\nimport contextlib\n\n"
        f"from {package}.analyzer import filter_records, group_region_totals, top_region\n"
        f"from {package}.cli import main, run_pipeline\n"
        f"from {package}.loader import load_records\n"
        f"from {package}.report import build_summary, render_markdown_report\n\n\n"
        "SAMPLE = 'region,product,amount,channel\\nNorth,Widget,10,web\\nSouth,Gadget,25,retail\\nNorth,Gadget,15,web\\n'\n\n\n"
        "class SalesAnalyticsPipelineTests(unittest.TestCase):\n"
        "    def test_load_filter_and_group_workflow(self):\n"
        "        records = load_records(SAMPLE)\n"
        "        self.assertEqual(len(records), 3)\n"
        "        selected = filter_records(records, min_amount=12, channel='web')\n"
        "        self.assertEqual([record.product for record in selected], ['Gadget'])\n"
        "        self.assertEqual(group_region_totals(records), {'North': 25.0, 'South': 25.0})\n"
        "        self.assertEqual(top_region(records), 'North')\n\n"
        "    def test_summary_and_markdown_report_workflow(self):\n"
        "        records = load_records(SAMPLE)\n"
        "        summary = build_summary(records)\n"
        "        self.assertEqual(summary['record_count'], 3)\n"
        "        self.assertEqual(summary['region_totals']['North'], 25.0)\n"
        "        markdown = render_markdown_report(records)\n"
        "        self.assertIn('# Sales Analytics Report', markdown)\n"
        "        self.assertIn('Top region: North', markdown)\n\n"
        "    def test_cli_json_output_workflow(self):\n"
        "        with tempfile.TemporaryDirectory() as tmp:\n"
        "            csv_path = Path(tmp) / 'sales.csv'\n"
        "            csv_path.write_text(SAMPLE, encoding='utf-8')\n"
        "            output = StringIO()\n"
        "            with contextlib.redirect_stdout(output):\n"
        "                main([str(csv_path), '--min-amount', '12'])\n"
        "            payload = json.loads(output.getvalue())\n"
        "            self.assertEqual(payload['record_count'], 2)\n"
        "            self.assertEqual(run_pipeline(SAMPLE, min_amount=12)[0].product, 'Gadget')\n"
    )
    readme = (
        f"# {project_name}\n\nA multi-module sales analytics pipeline that loads CSV records, filters them, groups region totals, renders markdown, and prints CLI JSON.\n\n"
        "## Run\n\n```bash\npython -m "
        f"{package}.cli input.csv\n```\n\n"
        "```bash\npython -m "
        f"{package}.cli sales.csv --min-amount 10 --format json\n```\n\n"
        "```bash\npython -m "
        f"{package}.cli sales.csv --format markdown\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n\n"
        "```bash\npython -m py_compile "
        f"{package}/processor.py {package}/cli.py\n```\n"
    )
    rows = replace_project_file(files, f"{package}/loader.py", loader)
    rows = replace_project_file(rows, f"{package}/analyzer.py", analyzer)
    rows = replace_project_file(rows, f"{package}/report.py", report)
    rows = replace_project_file(rows, f"{package}/cli.py", cli)
    rows = replace_project_file(rows, "tests/test_sales_pipeline.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows


def sales_analytics_pipeline_module_contracts(project_name: str) -> list[dict[str, Any]]:
    package = project_name.replace("-", "_")
    return [
        {
            "module": f"{package}.loader",
            "path": f"{package}/loader.py",
            "responsibility": "load typed sales records from CSV text",
            "requirements": ["parse CSV rows", "skip incomplete rows", "coerce amounts to floats", "preserve region product and channel"],
        },
        {
            "module": f"{package}.analyzer",
            "path": f"{package}/analyzer.py",
            "responsibility": "filter and aggregate sales records",
            "requirements": ["filter by minimum amount", "filter by channel", "group totals by region", "select top region"],
        },
        {
            "module": f"{package}.report",
            "path": f"{package}/report.py",
            "responsibility": "build structured and markdown sales reports",
            "requirements": ["build summary dictionary", "render markdown report", "include top region and region totals"],
        },
        {
            "module": f"{package}.cli",
            "path": f"{package}/cli.py",
            "responsibility": "command-line analytics pipeline entrypoint",
            "requirements": ["read CSV path", "support min amount filter", "support channel filter", "print JSON or markdown"],
        },
        {
            "module": "tests.test_sales_pipeline",
            "path": "tests/test_sales_pipeline.py",
            "responsibility": "multi-workflow sales analytics verification",
            "requirements": ["prove load filter group workflow", "prove report workflow", "prove CLI JSON workflow"],
        },
    ]


def apply_local_agent_command_router_feature(project_name: str, files: list[Any]) -> list[Any]:
    package = project_name.replace("-", "_")
    registry = (
        "from collections.abc import Callable\n"
        "from typing import Any\n\n\n"
        "ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]\n\n\n"
        "def _status(_payload: dict[str, Any]) -> dict[str, Any]:\n"
        "    return {'capabilities': sorted(ACTION_REGISTRY)}\n\n\n"
        "def _echo(payload: dict[str, Any]) -> dict[str, Any]:\n"
        "    message = str(payload.get('message', '')).strip()\n"
        "    if not message:\n"
        "        raise ValueError('message is required')\n"
        "    return {'message': message}\n\n\n"
        "def _summarize(payload: dict[str, Any]) -> dict[str, Any]:\n"
        "    text = str(payload.get('text', '')).strip()\n"
        "    if not text:\n"
        "        raise ValueError('text is required')\n"
        "    words = [word for word in text.split() if word]\n"
        "    return {'characters': len(text), 'words': len(words), 'preview': text[:80]}\n\n\n"
        "def _history(payload: dict[str, Any]) -> dict[str, Any]:\n"
        "    rows = payload.get('history', [])\n"
        "    if not isinstance(rows, list):\n"
        "        raise TypeError('history must be a list')\n"
        "    return {'events': len(rows), 'actions': [str(row.get('action', '')) for row in rows if isinstance(row, dict)]}\n\n\n"
        "ACTION_REGISTRY: dict[str, ToolHandler] = {\n"
        "    'status': _status,\n"
        "    'echo': _echo,\n"
        "    'summarize': _summarize,\n"
        "    'history': _history,\n"
        "}\n\n\n"
        "def available_actions() -> list[str]:\n"
        "    return sorted(ACTION_REGISTRY)\n\n\n"
        "def get_action_handler(action: str) -> ToolHandler:\n"
        "    clean_action = action.strip().lower()\n"
        "    if clean_action not in ACTION_REGISTRY:\n"
        "        raise ValueError(f'unsupported action: {action}')\n"
        "    return ACTION_REGISTRY[clean_action]\n"
    )
    schema = (
        "from typing import Any\n\n"
        "from .registry import available_actions\n\n\n"
        "REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {\n"
        "    'echo': ('message',),\n"
        "    'summarize': ('text',),\n"
        "}\n\n\n"
        "def normalize_action(action: str) -> str:\n"
        "    clean_action = str(action).strip().lower()\n"
        "    if clean_action not in available_actions():\n"
        "        raise ValueError(f'unsupported action: {action}')\n"
        "    return clean_action\n\n\n"
        "def validate_payload(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:\n"
        "    if payload is None:\n"
        "        clean_payload: dict[str, Any] = {}\n"
        "    else:\n"
        "        clean_payload = payload\n"
        "    if not isinstance(clean_payload, dict):\n"
        "        raise TypeError('payload must be a JSON object')\n"
        "    clean_action = normalize_action(action)\n"
        "    for field in REQUIRED_FIELDS.get(clean_action, ()):\n"
        "        if not str(clean_payload.get(field, '')).strip():\n"
        "            raise ValueError(f'{field} is required')\n"
        "    return clean_payload\n"
    )
    session = (
        "from dataclasses import dataclass, field\n"
        "from typing import Any\n\n\n"
        "@dataclass\n"
        "class AgentSession:\n"
        "    history: list[dict[str, Any]] = field(default_factory=list)\n\n"
        "    def record_action(self, action: str, payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:\n"
        "        event = {'action': action, 'payload': payload, 'result': result}\n"
        "        self.history.append(event)\n"
        "        return event\n\n"
        "    def summarize_history(self) -> dict[str, Any]:\n"
        "        return {'events': len(self.history), 'actions': [event['action'] for event in self.history]}\n"
    )
    runner = (
        "from typing import Any\n\n"
        "from .registry import get_action_handler\n"
        "from .schema import normalize_action, validate_payload\n"
        "from .session import AgentSession\n\n\n"
        "def run_action(action: str, payload: dict[str, Any] | None = None, session: AgentSession | None = None) -> dict[str, Any]:\n"
        "    clean_action = normalize_action(action)\n"
        "    clean_payload = validate_payload(clean_action, payload)\n"
        "    if clean_action == 'history' and session is not None:\n"
        "        clean_payload = {**clean_payload, 'history': session.history}\n"
        "    result = get_action_handler(clean_action)(clean_payload)\n"
        "    envelope = {'status': 'ok', 'action': clean_action, 'result': result}\n"
        "    if session is not None and clean_action != 'history':\n"
        "        session.record_action(clean_action, clean_payload, result)\n"
        "        envelope['session'] = session.summarize_history()\n"
        "    return envelope\n\n\n"
        "def run_sequence(commands: list[dict[str, Any]]) -> dict[str, Any]:\n"
        "    session = AgentSession()\n"
        "    outputs = []\n"
        "    for command in commands:\n"
        "        outputs.append(run_action(str(command.get('action', 'status')), command.get('payload', {}), session=session))\n"
        "    return {'status': 'ok', 'events': session.summarize_history(), 'outputs': outputs}\n"
    )
    contract = (
        "from typing import Any\n\n"
        "from .registry import ACTION_REGISTRY, available_actions\n"
        "from .runner import run_action, run_sequence\n\n"
        "__all__ = ['ACTION_REGISTRY', 'available_actions', 'build_tool_result', 'run_action', 'run_sequence']\n\n\n"
        "def build_tool_result(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:\n"
        "    envelope = run_action(action, payload)\n"
        "    result = dict(envelope['result'])\n"
        "    result.setdefault('status', envelope['status'])\n"
        "    return result\n"
    )
    tool = (
        "import argparse\n"
        "import json\n"
        "import sys\n\n"
        "from .registry import available_actions\n"
        "from .runner import run_action, run_sequence\n\n\n"
        "def _load_payload(raw_payload: str) -> dict[str, object]:\n"
        "    try:\n"
        "        payload = json.loads(raw_payload)\n"
        "    except json.JSONDecodeError as exc:\n"
        "        raise SystemExit(f'invalid JSON payload: {exc}') from exc\n"
        "    if not isinstance(payload, dict):\n"
        "        raise SystemExit('payload must be a JSON object')\n"
        "    return payload\n\n\n"
        "def _load_sequence(raw_sequence: str) -> list[dict[str, object]]:\n"
        "    try:\n"
        "        sequence = json.loads(raw_sequence)\n"
        "    except json.JSONDecodeError as exc:\n"
        "        raise SystemExit(f'invalid JSON sequence: {exc}') from exc\n"
        "    if not isinstance(sequence, list) or not all(isinstance(row, dict) for row in sequence):\n"
        "        raise SystemExit('sequence must be a JSON array of command objects')\n"
        "    return sequence\n\n\n"
        "def build_parser() -> argparse.ArgumentParser:\n"
        "    parser = argparse.ArgumentParser(description='Run a local agent tool action')\n"
        "    parser.add_argument('action', nargs='?', default='status', choices=available_actions())\n"
        "    parser.add_argument('--payload', default='{}', help='JSON object payload for the selected action')\n"
        "    parser.add_argument('--sequence', default='', help='JSON array of command objects to run in one session')\n"
        "    return parser\n\n\n"
        "def main(argv: list[str] | None = None) -> None:\n"
        "    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)\n"
        "    if args.sequence:\n"
        "        result = run_sequence(_load_sequence(args.sequence))\n"
        "    else:\n"
        "        result = run_action(args.action, _load_payload(args.payload))\n"
        "    print(json.dumps(result, ensure_ascii=False, sort_keys=True))\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    tests = (
        f"import json\nimport unittest\n\nfrom {package}.contract import available_actions, build_tool_result, run_action, run_sequence\nfrom {package}.session import AgentSession\nfrom {package}.tool import main\n\n\n"
        "class LocalAgentToolTests(unittest.TestCase):\n"
        "    def test_status_reports_capabilities(self):\n"
        "        result = build_tool_result('status')\n"
        "        self.assertEqual(result['status'], 'ok')\n"
        "        self.assertEqual(result['capabilities'], ['echo', 'history', 'status', 'summarize'])\n"
        "        self.assertEqual(available_actions(), result['capabilities'])\n\n"
        "    def test_echo_validates_payload(self):\n"
        "        self.assertEqual(build_tool_result('echo', {'message': ' ping '})['message'], 'ping')\n"
        "        with self.assertRaises(ValueError):\n"
        "            build_tool_result('echo', {})\n\n"
        "    def test_summarize_counts_text(self):\n"
        "        result = build_tool_result('summarize', {'text': 'alpha beta gamma'})\n"
        "        self.assertEqual(result['words'], 3)\n"
        "        self.assertEqual(result['characters'], len('alpha beta gamma'))\n\n"
        "    def test_unknown_action_is_rejected(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            build_tool_result('delete_everything', {})\n\n"
        "    def test_session_records_cross_command_workflow(self):\n"
        "        session = AgentSession()\n"
        "        first = run_action('echo', {'message': 'alpha'}, session=session)\n"
        "        second = run_action('summarize', {'text': 'alpha beta'}, session=session)\n"
        "        history = run_action('history', {}, session=session)\n"
        "        self.assertEqual(first['session']['events'], 1)\n"
        "        self.assertEqual(second['session']['actions'], ['echo', 'summarize'])\n"
        "        self.assertEqual(history['result']['actions'], ['echo', 'summarize'])\n\n"
        "    def test_sequence_runner_preserves_session_order(self):\n"
        "        result = run_sequence([\n"
        "            {'action': 'echo', 'payload': {'message': 'one'}},\n"
        "            {'action': 'summarize', 'payload': {'text': 'one two three'}},\n"
        "        ])\n"
        "        self.assertEqual(result['events']['actions'], ['echo', 'summarize'])\n"
        "        self.assertEqual(result['outputs'][1]['result']['words'], 3)\n\n"
        "    def test_cli_prints_json(self):\n"
        "        from io import StringIO\n"
        "        import contextlib\n\n"
        "        output = StringIO()\n"
        "        with contextlib.redirect_stdout(output):\n"
        "            main(['echo', '--payload', json.dumps({'message': 'hello'})])\n"
        "        self.assertEqual(json.loads(output.getvalue())['result']['message'], 'hello')\n\n"
        "    def test_cli_runs_sequence_json(self):\n"
        "        from io import StringIO\n"
        "        import contextlib\n\n"
        "        output = StringIO()\n"
        "        sequence = json.dumps([{'action': 'echo', 'payload': {'message': 'hello'}}, {'action': 'summarize', 'payload': {'text': 'hello world'}}])\n"
        "        with contextlib.redirect_stdout(output):\n"
        "            main(['--sequence', sequence])\n"
        "        self.assertEqual(json.loads(output.getvalue())['events']['actions'], ['echo', 'summarize'])\n"
    )
    readme = (
        f"# {project_name}\n\nA local agent tool with a registry, payload schema validation, session journal, command runner, and structured JSON output.\n\n"
        "## Run\n\n```bash\npython -m "
        f"{package}.tool status\n```\n\n"
        "```bash\npython -m "
        f"{package}.tool echo --payload '{{\"message\":\"hello\"}}'\n```\n\n"
        "```bash\npython -m "
        f"{package}.tool --sequence '[{{\"action\":\"echo\",\"payload\":{{\"message\":\"hello\"}}}},{{\"action\":\"summarize\",\"payload\":{{\"text\":\"hello world\"}}}}]'\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, f"{package}/registry.py", registry)
    rows = replace_project_file(rows, f"{package}/schema.py", schema)
    rows = replace_project_file(rows, f"{package}/session.py", session)
    rows = replace_project_file(rows, f"{package}/runner.py", runner)
    rows = replace_project_file(rows, f"{package}/contract.py", contract)
    rows = replace_project_file(rows, f"{package}/tool.py", tool)
    rows = replace_project_file(rows, "tests/test_contract.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows


def local_agent_command_router_module_contracts(project_name: str) -> list[dict[str, Any]]:
    package = project_name.replace("-", "_")
    return [
        {
            "module": f"{package}.registry",
            "path": f"{package}/registry.py",
            "responsibility": "local agent action registry and action handlers",
            "requirements": ["list available actions", "route status action", "route echo action", "route summarize action", "route history action", "reject unknown actions"],
        },
        {
            "module": f"{package}.schema",
            "path": f"{package}/schema.py",
            "responsibility": "local agent action and payload schema validation",
            "requirements": ["normalize actions", "reject unsupported actions", "reject invalid payloads", "require action fields"],
        },
        {
            "module": f"{package}.session",
            "path": f"{package}/session.py",
            "responsibility": "local agent session journal",
            "requirements": ["record action events", "summarize session history"],
        },
        {
            "module": f"{package}.runner",
            "path": f"{package}/runner.py",
            "responsibility": "local agent command execution workflow",
            "requirements": ["run single action", "run command sequence", "preserve session order", "return structured envelopes"],
        },
        {
            "module": f"{package}.contract",
            "path": f"{package}/contract.py",
            "responsibility": "compatibility facade for local agent tool APIs",
            "requirements": ["export registry actions", "export action runner", "preserve build_tool_result compatibility"],
        },
        {
            "module": f"{package}.tool",
            "path": f"{package}/tool.py",
            "responsibility": "JSON command-line interface for local agent tool actions",
            "requirements": ["parse action", "parse JSON payload", "parse JSON command sequence", "print structured JSON result"],
        },
        {
            "module": "tests.test_contract",
            "path": "tests/test_contract.py",
            "responsibility": "local agent tool behavior and workflow verification",
            "requirements": ["prove routed actions", "prove validation failures", "prove session workflow", "prove sequence CLI JSON output"],
        },
    ]


def apply_telegram_command_bot_feature(project_name: str, files: list[Any]) -> list[Any]:
    package = project_name.replace("-", "_")
    bot = (
        "import os\n"
        "from typing import Final\n\n\n"
        "COMMANDS: Final[dict[str, str]] = {\n"
        "    '/start': 'Start the bot and show the welcome message.',\n"
        "    '/help': 'Show available commands.',\n"
        "    '/status': 'Report bot readiness.',\n"
        "    '/echo': 'Echo the text after the command.',\n"
        "}\n\n\n"
        "def command_list() -> list[str]:\n"
        "    return sorted(COMMANDS)\n\n\n"
        "def _help_text() -> str:\n"
        "    return 'Available commands: ' + ', '.join(command_list())\n\n\n"
        "def build_reply(text: str) -> str:\n"
        "    message = text.strip()\n"
        "    if not message or message == '/start':\n"
        f"        return 'Hello from {project_name}. ' + _help_text()\n"
        "    if message == '/help':\n"
        "        return _help_text()\n"
        "    if message == '/status':\n"
        "        return 'Bot is ready.'\n"
        "    if message.startswith('/echo'):\n"
        "        payload = message.removeprefix('/echo').strip()\n"
        "        if not payload:\n"
        "            return 'Usage: /echo <text>'\n"
        "        return payload\n"
        "    if message.startswith('/'):\n"
        "        return 'Unknown command. ' + _help_text()\n"
        "    return message\n\n\n"
        "def build_runtime_config() -> dict[str, str]:\n"
        "    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()\n"
        "    if not token:\n"
        "        raise RuntimeError('TELEGRAM_BOT_TOKEN is required for live Telegram runtime')\n"
        "    return {'token': token}\n\n\n"
        "def main() -> None:\n"
        "    build_runtime_config()\n"
        "    print('bot configured')\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    tests = (
        f"import os\nimport unittest\nfrom unittest.mock import patch\n\nfrom {package}.bot import build_reply, build_runtime_config, command_list\n\n\n"
        "class TelegramCommandBotTests(unittest.TestCase):\n"
        "    def test_command_list(self):\n"
        "        self.assertEqual(command_list(), ['/echo', '/help', '/start', '/status'])\n\n"
        "    def test_start_help_and_status(self):\n"
        "        self.assertIn('/echo', build_reply('/start'))\n"
        "        self.assertIn('/status', build_reply('/help'))\n"
        "        self.assertEqual(build_reply('/status'), 'Bot is ready.')\n\n"
        "    def test_echo_command(self):\n"
        "        self.assertEqual(build_reply('/echo hello bot'), 'hello bot')\n"
        "        self.assertEqual(build_reply('/echo'), 'Usage: /echo <text>')\n\n"
        "    def test_unknown_command_and_plain_text(self):\n"
        "        self.assertIn('Unknown command', build_reply('/missing'))\n"
        "        self.assertEqual(build_reply(' plain text '), 'plain text')\n\n"
        "    def test_runtime_requires_token(self):\n"
        "        with patch.dict(os.environ, {}, clear=True):\n"
        "            with self.assertRaises(RuntimeError):\n"
        "                build_runtime_config()\n"
        "        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'token'}, clear=True):\n"
        "            self.assertEqual(build_runtime_config(), {'token': 'token'})\n"
    )
    readme = (
        f"# {project_name}\n\nA Telegram bot scaffold with testable command handling for /start, /help, /status, and /echo.\n\n"
        "## Run\n\n```bash\nTELEGRAM_BOT_TOKEN=your-token python -m "
        f"{package}.bot\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, f"{package}/bot.py", bot)
    rows = replace_project_file(rows, "tests/test_bot.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows


def telegram_command_bot_module_contracts(project_name: str) -> list[dict[str, Any]]:
    package = project_name.replace("-", "_")
    return [
        {
            "module": f"{package}.bot",
            "path": f"{package}/bot.py",
            "responsibility": "Telegram command parsing and live runtime configuration",
            "requirements": ["handle start command", "handle help command", "handle status command", "handle echo command", "reject unknown commands clearly", "require token only for live runtime"],
        },
        {
            "module": "tests.test_bot",
            "path": "tests/test_bot.py",
            "responsibility": "Telegram command behavior verification",
            "requirements": ["prove command routing", "prove unknown command fallback", "prove token requirement without network"],
        },
    ]


def apply_vite_counter_app_feature(project_name: str, files: list[Any]) -> list[Any]:
    main = (
        "import React, { useState } from 'react';\n"
        "import { createRoot } from 'react-dom/client';\n"
        "import './styles.css';\n\n\n"
        "export function CounterApp() {\n"
        "  const [count, setCount] = useState(0);\n"
        "  const increment = () => setCount((value) => value + 1);\n"
        "  const decrement = () => setCount((value) => value - 1);\n"
        "  const reset = () => setCount(0);\n\n"
        "  return (\n"
        "    <main className=\"counter-shell\">\n"
        f"      <h1>{project_name} Counter</h1>\n"
        "      <output aria-live=\"polite\" className=\"counter-value\">{count}</output>\n"
        "      <div className=\"counter-actions\">\n"
        "        <button type=\"button\" onClick={decrement}>Decrement</button>\n"
        "        <button type=\"button\" onClick={reset}>Reset</button>\n"
        "        <button type=\"button\" onClick={increment}>Increment</button>\n"
        "      </div>\n"
        "    </main>\n"
        "  );\n"
        "}\n\n\n"
        "createRoot(document.getElementById('root')).render(<CounterApp />);\n"
    )
    css = (
        ":root { font-family: Inter, system-ui, sans-serif; color: #191b21; background: #f5f7fb; }\n"
        "body { margin: 0; }\n"
        ".counter-shell { max-width: 680px; margin: 10vh auto; padding: 24px; display: grid; gap: 18px; }\n"
        "h1 { margin: 0; font-size: 2rem; }\n"
        ".counter-value { font-size: 4rem; font-weight: 700; line-height: 1; }\n"
        ".counter-actions { display: flex; flex-wrap: wrap; gap: 8px; }\n"
        "button { min-width: 112px; border: 1px solid #b9c3d3; border-radius: 6px; background: #ffffff; padding: 10px 14px; font: inherit; cursor: pointer; }\n"
        "button:hover { background: #e9eef7; }\n"
    )
    tests = (
        "from pathlib import Path\n"
        "import json\n"
        "import unittest\n\n\n"
        "class ViteCounterContractTests(unittest.TestCase):\n"
        "    def test_manifest_and_entrypoint(self):\n"
        "        manifest = json.loads(Path('package.json').read_text(encoding='utf-8'))\n"
        "        self.assertEqual(manifest['scripts']['dev'], 'vite')\n"
        "        self.assertIn('/src/main.jsx', Path('index.html').read_text(encoding='utf-8'))\n\n"
        "    def test_counter_behaviors_are_implemented(self):\n"
        "        source = Path('src/main.jsx').read_text(encoding='utf-8')\n"
        "        self.assertIn('useState(0)', source)\n"
        "        self.assertIn('const increment', source)\n"
        "        self.assertIn('const decrement', source)\n"
        "        self.assertIn('const reset', source)\n"
        "        self.assertIn('aria-live', source)\n"
        "        self.assertIn('Increment', source)\n"
        "        self.assertIn('Decrement', source)\n"
        "        self.assertIn('Reset', source)\n"
    )
    readme = (
        f"# {project_name}\n\nA Vite React counter app with increment, decrement, reset, and visible count state.\n\n"
        "## Install\n\n```bash\nnpm install\n```\n\n"
        "## Run\n\n```bash\nnpm run dev\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, "src/main.jsx", main)
    rows = replace_project_file(rows, "src/styles.css", css)
    rows = replace_project_file(rows, "tests/test_vite_contract.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows


def vite_counter_app_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "src.main",
            "path": "src/main.jsx",
            "responsibility": "React counter application entrypoint",
            "requirements": ["render count state", "increment count", "decrement count", "reset count", "expose accessible live output"],
        },
        {
            "module": "src.styles",
            "path": "src/styles.css",
            "responsibility": "counter app layout and control styling",
            "requirements": ["style counter shell", "style action buttons"],
        },
        {
            "module": "tests.test_vite_contract",
            "path": "tests/test_vite_contract.py",
            "responsibility": "Vite counter behavior-contract verification",
            "requirements": ["prove manifest entrypoint", "prove counter behaviors are present"],
        },
    ]


def apply_python_text_utils_library_feature(project_name: str, files: list[Any]) -> list[Any]:
    package = project_name.replace("-", "_")
    init = (
        "from .core import normalize_text, slugify, summarize_text, word_count\n\n"
        "__all__ = ['normalize_text', 'slugify', 'summarize_text', 'word_count']\n"
    )
    core = (
        "import re\n"
        "import unicodedata\n\n\n"
        "def normalize_text(value: str) -> str:\n"
        "    return ' '.join(value.strip().split())\n\n\n"
        "def slugify(value: str) -> str:\n"
        "    normalized = unicodedata.normalize('NFKD', normalize_text(value))\n"
        "    ascii_text = normalized.encode('ascii', 'ignore').decode('ascii').lower()\n"
        "    slug = re.sub(r'[^a-z0-9]+', '-', ascii_text).strip('-')\n"
        "    return slug or 'text'\n\n\n"
        "def word_count(value: str) -> int:\n"
        "    return len(re.findall(r'\\b\\w+\\b', normalize_text(value), flags=re.UNICODE))\n\n\n"
        "def summarize_text(value: str, max_words: int = 12) -> str:\n"
        "    if max_words < 1:\n"
        "        raise ValueError('max_words must be positive')\n"
        "    words = normalize_text(value).split()\n"
        "    if len(words) <= max_words:\n"
        "        return ' '.join(words)\n"
        "    return ' '.join(words[:max_words]) + '...'\n"
    )
    tests = (
        f"import unittest\n\nfrom {package} import normalize_text, slugify, summarize_text, word_count\n\n\n"
        "class TextUtilsLibraryTests(unittest.TestCase):\n"
        "    def test_normalize_text_collapses_spacing(self):\n"
        "        self.assertEqual(normalize_text('  alpha\\n\\tbeta   gamma  '), 'alpha beta gamma')\n\n"
        "    def test_slugify_generates_ascii_slug(self):\n"
        "        self.assertEqual(slugify('Hello, World! 2026'), 'hello-world-2026')\n"
        "        self.assertEqual(slugify('   '), 'text')\n\n"
        "    def test_word_count(self):\n"
        "        self.assertEqual(word_count('one two, three'), 3)\n"
        "        self.assertEqual(word_count('   '), 0)\n\n"
        "    def test_summarize_text(self):\n"
        "        self.assertEqual(summarize_text('one two three four', max_words=3), 'one two three...')\n"
        "        self.assertEqual(summarize_text('one two', max_words=3), 'one two')\n"
        "        with self.assertRaises(ValueError):\n"
        "            summarize_text('one two', max_words=0)\n"
    )
    readme = (
        f"# {project_name}\n\nA Python text utilities library with normalization, slug generation, word counting, and short summaries.\n\n"
        "## Use\n\n```python\nfrom "
        f"{package} import normalize_text, slugify, summarize_text, word_count\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, f"{package}/__init__.py", init)
    rows = replace_project_file(rows, f"{package}/core.py", core)
    rows = replace_project_file(rows, "tests/test_library.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows


def python_text_utils_library_module_contracts(project_name: str) -> list[dict[str, Any]]:
    package = project_name.replace("-", "_")
    return [
        {
            "module": package,
            "path": f"{package}/__init__.py",
            "responsibility": "public text utilities package exports",
            "requirements": ["export normalize_text", "export slugify", "export word_count", "export summarize_text"],
        },
        {
            "module": f"{package}.core",
            "path": f"{package}/core.py",
            "responsibility": "text utility domain behavior",
            "requirements": ["normalize whitespace", "generate ascii slugs", "count words", "summarize text with explicit word limits", "reject invalid summary limits"],
        },
        {
            "module": "tests.test_library",
            "path": "tests/test_library.py",
            "responsibility": "text utility library verification",
            "requirements": ["prove normalization", "prove slug generation", "prove word counting", "prove summary limits"],
        },
    ]
