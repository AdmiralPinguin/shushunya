from __future__ import annotations

from typing import Any

from .common import replace_project_file


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
