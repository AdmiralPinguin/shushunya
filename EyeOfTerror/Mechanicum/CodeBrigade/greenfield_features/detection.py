from __future__ import annotations

from typing import Any

from .common import calculator_requested


def infer_acceptance_features(task: str) -> list[dict[str, Any]]:
    lowered = task.lower()
    features: list[dict[str, Any]] = []
    kanban_requested = any(word in lowered for word in ("kanban", "project board", "task board", "status board", "канбан", "доска задач", "проектная доска"))
    if calculator_requested(task):
        features.append(
            {
                "id": "calculator_operations",
                "kind": "functional_requirement",
                "description": "perform basic arithmetic operations through core logic and CLI entrypoint",
                "operations": ["add", "subtract", "multiply", "divide"],
            }
        )
    task_dashboard = (
        any(word in lowered for word in ("task dashboard", "tasks dashboard", "dashboard", "дашборд", "карточк", "cards"))
        and any(word in lowered for word in ("task", "tasks", "todo", "done", "active", "задач", "дел"))
        and any(word in lowered for word in ("filter", "filters", "active/done", "toggle", "localstorage", "фильтр", "переключ", "сохран"))
    )
    if not kanban_requested and (task_dashboard or any(word in lowered for word in ("todo", "to-do", "task list", "задачник", "список задач", "список дел", "тасклист"))):
        features.append(
            {
                "id": "todo_list",
                "kind": "functional_requirement",
                "description": "provide a browser todo list with add, complete, delete, and persistent state behavior",
                "operations": ["add", "complete", "delete", "persist"],
            }
        )
    if kanban_requested:
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
    if (
        any(word in lowered for word in ("maintenance", "service request", "work order", "equipment", "technician", "обслужив", "оборудован", "техник", "заявк"))
        and any(word in lowered for word in ("assign", "status", "summary", "filter", "назнач", "статус", "свод", "фильтр"))
    ):
        features.append(
            {
                "id": "maintenance_work_orders_api",
                "kind": "multi_workflow_requirement",
                "description": "provide maintenance work-order creation, technician assignment, status transitions, filtering, summary counts, and HTTP route adapters",
                "operations": ["create_work_order", "assign_technician", "transition_status", "filter_work_orders", "summary_by_status", "http_routes"],
            }
        )
    if any(word in lowered for word in ("inventory", "stock adjustment", "stock ledger", "low-stock", "low stock", "sku", "склад", "остатк", "инвентар")):
        features.append(
            {
                "id": "inventory_ops_api",
                "kind": "multi_workflow_requirement",
                "description": "provide inventory CRUD, stock adjustment ledger, low-stock reporting, SKU/category/status filtering, JSON error payloads, and HTTP route wiring",
                "operations": ["create_item", "update_item", "adjust_stock", "low_stock_report", "filter_inventory", "json_errors", "http_routes"],
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
    has_todo_feature = any(feature.get("id") == "todo_list" for feature in features)
    explicit_counter_app = any(word in lowered for word in ("react counter", "vite counter", "counter app", "counter application", "приложение счетчик", "приложение счётчик"))
    generic_counter = any(word in lowered for word in ("счетчик", "счётчик")) and not has_todo_feature and not kanban_requested
    if explicit_counter_app or generic_counter:
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
