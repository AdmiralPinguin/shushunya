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
    if any(word in lowered for word in ("notes", "note api", "замет", "заметки", "заметок")):
        features.append(
            {
                "id": "notes_api",
                "kind": "functional_requirement",
                "description": "provide note creation, listing, lookup, and deletion through service logic and HTTP routes",
                "operations": ["create", "list", "get", "delete"],
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
    if template_id == "static_site" and any(feature.get("id") == "todo_list" for feature in features):
        return apply_static_site_todo_feature(project_name, files), static_todo_module_contracts(), features
    if template_id == "python_fastapi_service" and any(feature.get("id") == "notes_api" for feature in features):
        return apply_fastapi_notes_feature(project_name, files), fastapi_notes_module_contracts(), features
    if template_id == "data_processing_tool" and any(feature.get("id") == "csv_summary" for feature in features):
        return apply_data_processing_csv_summary_feature(project_name, files), csv_summary_module_contracts(project_name), features
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
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
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
