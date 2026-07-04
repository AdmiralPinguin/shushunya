from __future__ import annotations

from typing import Any

from .common import replace_project_file


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

def apply_vite_kanban_board_feature(project_name: str, files: list[Any]) -> list[Any]:
    main = (
        "import React, { useMemo, useState } from 'react';\n"
        "import { createRoot } from 'react-dom/client';\n"
        "import './styles.css';\n\n"
        "const STORAGE_KEY = 'ceraxia.vite.kanban.board';\n"
        "const STATUSES = ['backlog', 'doing', 'done'];\n"
        "const STATUS_LABELS = { backlog: 'Todo', doing: 'Doing', done: 'Done' };\n\n"
        "function defaultBoard() {\n"
        "  return { activeFilter: '', cards: [] };\n"
        "}\n\n"
        "function loadBoard() {\n"
        "  try {\n"
        "    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');\n"
        "    return parsed && Array.isArray(parsed.cards) ? parsed : defaultBoard();\n"
        "  } catch (_error) {\n"
        "    return defaultBoard();\n"
        "  }\n"
        "}\n\n"
        "function saveBoard(board) {\n"
        "  localStorage.setItem(STORAGE_KEY, JSON.stringify(board));\n"
        "  return board;\n"
        "}\n\n"
        "function createCard(board, title) {\n"
        "  const cleanTitle = title.trim();\n"
        "  if (!cleanTitle) return board;\n"
        "  const card = { id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`, title: cleanTitle, status: 'backlog' };\n"
        "  return { ...board, cards: [...board.cards, card] };\n"
        "}\n\n"
        "function moveCard(board, cardId, nextStatus) {\n"
        "  if (!STATUSES.includes(nextStatus)) return board;\n"
        "  return { ...board, cards: board.cards.map((card) => card.id === cardId ? { ...card, status: nextStatus } : card) };\n"
        "}\n\n"
        "function filterCards(board, query) {\n"
        "  const needle = query.trim().toLowerCase();\n"
        "  if (!needle) return board.cards;\n"
        "  return board.cards.filter((card) => card.title.toLowerCase().includes(needle));\n"
        "}\n\n"
        "function boardMetrics(board) {\n"
        "  const counts = Object.fromEntries(STATUSES.map((status) => [status, 0]));\n"
        "  for (const card of board.cards) counts[card.status] = (counts[card.status] || 0) + 1;\n"
        "  return { total: board.cards.length, ...counts };\n"
        "}\n\n"
        "function renderMetrics(board) {\n"
        "  const metrics = boardMetrics(board);\n"
        "  return <dl className=\"metrics\">{Object.entries(metrics).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;\n"
        "}\n\n"
        "function renderBoard(board, visibleCards, onMove) {\n"
        "  return <section className=\"board\" aria-label=\"Kanban board\">{STATUSES.map((status) => (\n"
        "    <section className=\"column\" data-status={status} key={status}>\n"
        "      <h2>{STATUS_LABELS[status]}</h2>\n"
        "      <ol>{visibleCards.filter((card) => card.status === status).map((card) => (\n"
        "        <li className=\"kanban-card\" key={card.id} data-card-id={card.id}>\n"
        "          <strong>{card.title}</strong>\n"
        "          <div className=\"card-actions\">{STATUSES.filter((target) => target !== card.status).map((target) => <button type=\"button\" key={target} onClick={() => onMove(card.id, target)}>Move to {STATUS_LABELS[target]}</button>)}</div>\n"
        "        </li>\n"
        "      ))}</ol>\n"
        "    </section>\n"
        "  ))}</section>;\n"
        "}\n\n"
        "function App() {\n"
        "  const [board, setBoard] = useState(() => loadBoard());\n"
        "  const [title, setTitle] = useState('');\n"
        "  const [query, setQuery] = useState(board.activeFilter || '');\n"
        "  const visibleCards = useMemo(() => filterCards({ ...board, activeFilter: query }, query), [board, query]);\n"
        "  function commit(nextBoard) {\n"
        "    const saved = saveBoard({ ...nextBoard, activeFilter: query });\n"
        "    setBoard(saved);\n"
        "  }\n"
        "  function addCard(event) {\n"
        "    event.preventDefault();\n"
        "    commit(createCard(board, title));\n"
        "    setTitle('');\n"
        "  }\n"
        "  function handleMoveCard(cardId, nextStatus) {\n"
        "    commit(moveCard(board, cardId, nextStatus));\n"
        "  }\n"
        "  React.useEffect(() => {\n"
        "    const listener = () => setBoard(loadBoard());\n"
        "    window.addEventListener('storage', listener);\n"
        "    return () => window.removeEventListener('storage', listener);\n"
        "  }, []);\n"
        "  return <main className=\"app-shell\">\n"
        "    <header><h1>{PROJECT_TITLE}</h1><p>{visibleCards.length} visible cards</p>{renderMetrics(board)}</header>\n"
        "    <form id=\"kanban-form\" onSubmit={addCard}>\n"
        "      <input id=\"card-title\" value={title} onChange={(event) => setTitle(event.target.value)} aria-label=\"Card title\" />\n"
        "      <button type=\"submit\">Add card</button>\n"
        "    </form>\n"
        "    <label className=\"filter-label\">Filter<input id=\"card-filter\" value={query} onChange={(event) => setQuery(event.target.value)} /></label>\n"
        "    {renderBoard(board, visibleCards, handleMoveCard)}\n"
        "  </main>;\n"
        "}\n\n"
        f"const PROJECT_TITLE = '{project_name} Kanban';\n"
        "createRoot(document.getElementById('root')).render(<App />);\n"
    )
    styles = (
        "body { margin: 0; background: #f4f6f8; color: #17191f; font-family: Inter, system-ui, sans-serif; }\n"
        ".app-shell { width: min(1120px, calc(100% - 32px)); margin: 32px auto; display: grid; gap: 18px; }\n"
        "header { display: grid; gap: 10px; }\n"
        "h1, h2, p { margin: 0; }\n"
        ".metrics { display: flex; flex-wrap: wrap; gap: 10px; margin: 0; }\n"
        ".metrics div { background: white; border: 1px solid #d8dee8; border-radius: 8px; min-width: 88px; padding: 8px 10px; }\n"
        ".metrics dt { color: #5f6b7a; font-size: .8rem; }\n"
        ".metrics dd { margin: 0; font-weight: 700; }\n"
        "#kanban-form { display: grid; grid-template-columns: 1fr auto; gap: 8px; }\n"
        "input, button { min-height: 40px; border: 1px solid #bfc9d6; border-radius: 6px; font: inherit; padding: 8px 10px; }\n"
        "button { cursor: pointer; background: #233a58; color: white; }\n"
        ".filter-label { display: grid; gap: 6px; max-width: 420px; }\n"
        ".board { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }\n"
        ".column { background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 12px; min-height: 280px; }\n"
        ".column ol { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 10px; }\n"
        ".kanban-card { display: grid; gap: 8px; border: 1px solid #d4dbe7; border-left: 4px solid #526a8a; border-radius: 8px; padding: 10px; }\n"
        ".card-actions { display: flex; flex-wrap: wrap; gap: 6px; }\n"
        ".card-actions button { min-height: 32px; padding: 5px 8px; }\n"
        "@media (max-width: 760px) { .board, #kanban-form { grid-template-columns: 1fr; } }\n"
    )
    tests = (
        "from pathlib import Path\n"
        "import json\n"
        "import unittest\n\n\n"
        "class ViteKanbanContractTests(unittest.TestCase):\n"
        "    def test_manifest_and_entrypoint(self):\n"
        "        manifest = json.loads(Path('package.json').read_text(encoding='utf-8'))\n"
        "        self.assertIn('dev', manifest['scripts'])\n"
        "        html = Path('index.html').read_text(encoding='utf-8')\n"
        "        self.assertIn('/src/main.jsx', html)\n\n"
        "    def test_kanban_workflow_markers(self):\n"
        "        source = Path('src/main.jsx').read_text(encoding='utf-8')\n"
        "        for marker in ('function createCard', 'function moveCard', 'function filterCards', 'function boardMetrics', 'function renderMetrics', 'function renderBoard'):\n"
        "            self.assertIn(marker, source)\n"
        "        for marker in ('localStorage', 'loadBoard', 'saveBoard', 'addEventListener', 'backlog', 'doing', 'done'):\n"
        "            self.assertIn(marker, source)\n"
        "        self.assertIn('card-filter', source)\n"
        "        self.assertIn('Move to', source)\n\n"
        "    def test_styles_define_board_columns(self):\n"
        "        styles = Path('src/styles.css').read_text(encoding='utf-8')\n"
        "        self.assertIn('.board', styles)\n"
        "        self.assertIn('.column', styles)\n"
    )
    readme = (
        f"# {project_name}\n\nA Vite kanban board with card creation, movement, filtering, column counters, and localStorage persistence.\n\n"
        "## Install\n\n```bash\nnpm install\n```\n\n"
        "## Run\n\n```bash\nnpm run dev\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, "src/main.jsx", main)
    rows = replace_project_file(rows, "src/styles.css", styles)
    rows = replace_project_file(rows, "tests/test_vite_contract.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def vite_kanban_board_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "src.main",
            "path": "src/main.jsx",
            "responsibility": "Vite React kanban board state, rendering, interactions, filtering, metrics, and persistence",
            "requirements": ["createCard", "moveCard", "filterCards", "boardMetrics", "renderMetrics", "renderBoard", "loadBoard", "saveBoard", "addEventListener"],
        },
        {
            "module": "src.styles",
            "path": "src/styles.css",
            "responsibility": "responsive kanban board layout and card styling",
            "requirements": ["style board columns", "style cards", "support narrow screens"],
        },
        {
            "module": "tests.test_vite_contract",
            "path": "tests/test_vite_contract.py",
            "responsibility": "Vite kanban workflow contract verification",
            "requirements": ["prove manifest and entrypoint", "prove kanban behavior markers", "prove board styling markers"],
        },
    ]

def apply_vite_todo_dashboard_feature(project_name: str, files: list[Any]) -> list[Any]:
    main = (
        "import React, { useEffect, useMemo, useState } from 'react';\n"
        "import { createRoot } from 'react-dom/client';\n"
        "import './styles.css';\n\n"
        "const STORAGE_KEY = 'ceraxia.vite.todo.dashboard';\n"
        "const INITIAL_TASKS = [\n"
        "  { id: 'plan', title: 'Plan release checklist', done: false },\n"
        "  { id: 'review', title: 'Review generated contract tests', done: true },\n"
        "  { id: 'ship', title: 'Ship verified dashboard', done: false },\n"
        "];\n\n"
        "function loadTodos() {\n"
        "  try {\n"
        "    const raw = localStorage.getItem(STORAGE_KEY);\n"
        "    return raw ? JSON.parse(raw) : INITIAL_TASKS;\n"
        "  } catch (_error) {\n"
        "    return INITIAL_TASKS;\n"
        "  }\n"
        "}\n\n"
        "function saveTodos(tasks) {\n"
        "  localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));\n"
        "}\n\n"
        "function addTodo(tasks, title) {\n"
        "  const cleanTitle = title.trim();\n"
        "  if (!cleanTitle) return tasks;\n"
        "  return [...tasks, { id: `task-${Date.now()}`, title: cleanTitle, done: false }];\n"
        "}\n\n"
        "function deleteTodo(tasks, taskId) {\n"
        "  return tasks.filter((task) => task.id !== taskId);\n"
        "}\n\n"
        "export function filterTasks(tasks, filter) {\n"
        "  if (filter === 'active') return tasks.filter((task) => !task.done);\n"
        "  if (filter === 'done') return tasks.filter((task) => task.done);\n"
        "  return tasks;\n"
        "}\n\n"
        "export function remainingTasks(tasks) {\n"
        "  return tasks.filter((task) => !task.done).length;\n"
        "}\n\n"
        "export function TodoDashboard() {\n"
        "  const [tasks, setTasks] = useState(loadTodos);\n"
        "  const [draftTitle, setDraftTitle] = useState('');\n"
        "  const [activeFilter, setActiveFilter] = useState('all');\n\n"
        "  useEffect(() => {\n"
        "    saveTodos(tasks);\n"
        "  }, [tasks]);\n\n"
        "  const visibleTasks = useMemo(() => filterTasks(tasks, activeFilter), [tasks, activeFilter]);\n"
        "  const remaining = remainingTasks(tasks);\n"
        "  const toggleDone = (taskId) => setTasks((rows) => rows.map((task) => task.id === taskId ? { ...task, done: !task.done } : task));\n\n"
        "  const completeTodo = (taskId) => toggleDone(taskId);\n"
        "  const removeTask = (taskId) => setTasks((rows) => deleteTodo(rows, taskId));\n"
        "  const renderTodos = () => visibleTasks;\n"
        "  const submitTask = (event) => {\n"
        "    event.preventDefault();\n"
        "    setTasks((rows) => addTodo(rows, draftTitle));\n"
        "    setDraftTitle('');\n"
        "  };\n\n"
        "  return (\n"
        "    <main className=\"dashboard-shell\">\n"
        f"      <h1>{project_name} Task Dashboard</h1>\n"
        "      <p className=\"remaining-counter\" aria-live=\"polite\">{remaining} tasks remaining</p>\n"
        "      <form className=\"task-form\" onSubmit={submitTask}>\n"
        "        <label htmlFor=\"task-title\">New task</label>\n"
        "        <input id=\"task-title\" value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} aria-label=\"Add task\" />\n"
        "        <button type=\"submit\">Add task</button>\n"
        "      </form>\n"
        "      <nav className=\"filter-bar\" aria-label=\"Task filters\">\n"
        "        {['all', 'active', 'done'].map((filter) => (\n"
        "          <button key={filter} type=\"button\" className={filter === activeFilter ? 'is-active' : ''} onClick={() => setActiveFilter(filter)}>{filter}</button>\n"
        "        ))}\n"
        "      </nav>\n"
        "      <section className=\"task-grid\" aria-label=\"Task cards\">\n"
        "        {renderTodos().map((task) => (\n"
        "          <article key={task.id} className={`task-card${task.done ? ' is-done' : ''}`}>\n"
        "            <h2>{task.title}</h2>\n"
        "            <div className=\"task-actions\">\n"
        "              <button type=\"button\" onClick={() => completeTodo(task.id)}>{task.done ? 'Mark active' : 'Toggle done'}</button>\n"
        "              <button type=\"button\" onClick={() => removeTask(task.id)}>Delete</button>\n"
        "            </div>\n"
        "          </article>\n"
        "        ))}\n"
        "      </section>\n"
        "    </main>\n"
        "  );\n"
        "}\n\n"
        "createRoot(document.getElementById('root')).render(<TodoDashboard />);\n"
    )
    css = (
        ":root { font-family: Inter, system-ui, sans-serif; color: #17191f; background: #f6f8fb; }\n"
        "body { margin: 0; }\n"
        ".dashboard-shell { width: min(960px, calc(100% - 32px)); margin: 8vh auto; display: grid; gap: 18px; }\n"
        "h1 { margin: 0; font-size: 2rem; }\n"
        ".remaining-counter { margin: 0; color: #48556a; font-weight: 600; }\n"
        ".task-form { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: end; }\n"
        ".task-form label { grid-column: 1 / -1; font-weight: 700; }\n"
        ".task-form input { min-width: 0; border: 1px solid #bdc6d4; border-radius: 6px; padding: 10px 12px; font: inherit; }\n"
        ".filter-bar { display: flex; flex-wrap: wrap; gap: 8px; }\n"
        "button { border: 1px solid #bdc6d4; border-radius: 6px; background: #ffffff; padding: 10px 14px; font: inherit; cursor: pointer; }\n"
        "button.is-active { background: #253858; color: #ffffff; border-color: #253858; }\n"
        ".task-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }\n"
        ".task-card { display: grid; gap: 12px; min-height: 132px; border: 1px solid #d6dce8; border-radius: 8px; background: #ffffff; padding: 16px; }\n"
        ".task-card.is-done { background: #eef7f0; color: #53605a; }\n"
        ".task-card h2 { margin: 0; font-size: 1rem; line-height: 1.35; }\n"
        ".task-actions { display: flex; flex-wrap: wrap; gap: 8px; }\n"
    )
    tests = (
        "from pathlib import Path\n"
        "import json\n"
        "import unittest\n\n\n"
        "class ViteTodoDashboardContractTests(unittest.TestCase):\n"
        "    def test_manifest_and_entrypoint(self):\n"
        "        manifest = json.loads(Path('package.json').read_text(encoding='utf-8'))\n"
        "        self.assertEqual(manifest['scripts']['dev'], 'vite')\n"
        "        self.assertIn('/src/main.jsx', Path('index.html').read_text(encoding='utf-8'))\n\n"
        "    def test_task_dashboard_behaviors_are_implemented(self):\n"
        "        source = Path('src/main.jsx').read_text(encoding='utf-8')\n"
        "        for marker in ('TodoDashboard', 'addTodo', 'deleteTodo', 'renderTodos', 'saveTodos', 'loadTodos', 'filterTasks', 'remainingTasks', 'toggleDone', 'completeTodo', 'localStorage', \"'all'\", \"'active'\", \"'done'\", 'task-card'):\n"
        "            self.assertIn(marker, source)\n"
        "        self.assertIn('tasks remaining', source)\n"
        "        self.assertNotIn('CounterApp', source)\n"
        "        self.assertNotIn('Increment', source)\n"
    )
    readme = (
        f"# {project_name}\n\nA Vite React task dashboard with cards, all/active/done filters, toggle-done controls, remaining-task counter, and localStorage persistence.\n\n"
        "## Install\n\n```bash\nnpm install\n```\n\n"
        "## Run\n\n```bash\nnpm run dev\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, "src/main.jsx", main)
    rows = replace_project_file(rows, "src/styles.css", css)
    rows = replace_project_file(rows, "tests/test_vite_contract.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def vite_todo_dashboard_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "src.main",
            "path": "src/main.jsx",
            "responsibility": "React task dashboard application entrypoint",
            "requirements": ["add tasks", "render task cards", "filter all active done tasks", "toggle done state", "delete tasks", "count remaining tasks", "persist tasks in localStorage"],
        },
        {
            "module": "src.styles",
            "path": "src/styles.css",
            "responsibility": "task dashboard layout, filter, and card styling",
            "requirements": ["style dashboard shell", "style filter controls", "style task cards"],
        },
        {
            "module": "tests.test_vite_contract",
            "path": "tests/test_vite_contract.py",
            "responsibility": "Vite task dashboard behavior-contract verification",
            "requirements": ["prove manifest entrypoint", "prove task dashboard behaviors are present", "reject counter-app substitution"],
        },
    ]
