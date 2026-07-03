#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any


def build_greenfield_scenario_plan(
    project_type: str,
    template_id: str,
    acceptance_features: list[dict[str, Any]],
    expected_files: list[str],
) -> dict[str, Any]:
    feature_rows = [
        row
        for feature in acceptance_features
        if isinstance(feature, dict)
        for row in _feature_scenarios(feature)
    ]
    rows = feature_rows or _template_scenarios(project_type, template_id)
    existing_files = set(expected_files)
    default_evidence = _default_evidence_files(expected_files)
    for row in rows:
        evidence_files = [path for path in row.get("evidence_files", []) if path in existing_files]
        row["evidence_files"] = list(dict.fromkeys([*evidence_files, *default_evidence]))
    return {
        "kind": "code_brigade_greenfield_scenario_plan",
        "contract_version": "eye-mechanicum.v1",
        "status": "planned" if rows else "empty",
        "scenario_count": len(rows),
        "rows": rows,
    }


def review_greenfield_scenarios(repo: Path, project_brief: dict[str, Any]) -> dict[str, Any]:
    plan = project_brief.get("scenario_plan") if isinstance(project_brief.get("scenario_plan"), dict) else {}
    rows = plan.get("rows") if isinstance(plan.get("rows"), list) else []
    blockers: list[str] = []
    warnings: list[str] = []
    review_rows: list[dict[str, Any]] = []
    for index, scenario in enumerate(rows, start=1):
        if not isinstance(scenario, dict):
            blockers.append(f"scenario row {index} is not an object")
            continue
        scenario_id = str(scenario.get("id") or f"scenario_{index}")
        evidence_files = [str(path) for path in scenario.get("evidence_files", []) if isinstance(path, str)]
        markers = [str(marker) for marker in scenario.get("required_markers", []) if isinstance(marker, str) and marker.strip()]
        evidence_texts: dict[str, str] = {}
        missing_files: list[str] = []
        for rel_path in evidence_files:
            path = repo / rel_path
            if path.exists() and path.is_file():
                evidence_texts[rel_path] = path.read_text(encoding="utf-8")
            else:
                missing_files.append(rel_path)
        combined = "\n".join(evidence_texts.values()).lower()
        missing_markers = [marker for marker in markers if marker.lower() not in combined]
        status = "passed"
        if missing_files or missing_markers:
            status = "blocked"
            if missing_files:
                blockers.append(f"scenario {scenario_id} is missing evidence files: {', '.join(missing_files)}")
            if missing_markers:
                blockers.append(f"scenario {scenario_id} is missing behavior markers: {', '.join(missing_markers)}")
        elif len(evidence_texts) < 2:
            status = "weak"
            warnings.append(f"scenario {scenario_id} has narrow evidence coverage")
        review_rows.append(
            {
                "id": scenario_id,
                "description": str(scenario.get("description") or ""),
                "status": status,
                "evidence_files": sorted(evidence_texts),
                "missing_files": missing_files,
                "required_markers": markers,
                "missing_markers": missing_markers,
            }
        )
    if not rows:
        blockers.append("greenfield scenario plan has no scenario rows")
    return {
        "kind": "code_brigade_greenfield_scenario_review",
        "contract_version": "eye-mechanicum.v1",
        "status": "blocked" if blockers else "passed",
        "scenario_count": len(rows),
        "passed_count": sum(1 for row in review_rows if row["status"] == "passed"),
        "weak_count": sum(1 for row in review_rows if row["status"] == "weak"),
        "blocked_count": sum(1 for row in review_rows if row["status"] == "blocked"),
        "rows": review_rows,
        "blockers": blockers,
        "warnings": warnings,
    }


def _feature_scenarios(feature: dict[str, Any]) -> list[dict[str, Any]]:
    feature_id = str(feature.get("id") or "")
    operations = [str(item) for item in feature.get("operations", []) if isinstance(item, str)]
    table: dict[str, list[dict[str, Any]]] = {
        "calculator_operations": [
            _scenario("calculator_arithmetic", "CLI calculator performs arithmetic operations", ["core logic calculates add/subtract/multiply/divide", "CLI exposes the operations"], ["calculate", "add", "subtract", "multiply", "divide"], ["tests/test_core.py"]),
            _scenario("calculator_error_handling", "CLI calculator rejects division by zero", ["core raises a useful error", "tests prove the rejection"], ["division by zero", "test_division_by_zero"], ["tests/test_core.py"]),
        ],
        "todo_list": [
            _scenario("todo_lifecycle", "Todo UI adds, completes, deletes, and renders tasks", ["create a todo", "mark it complete", "delete it", "render visible state"], ["addTodo", "renderTodos", "complete", "delete"], ["index.html", "app.js", "tests/test_static_site.py"]),
            _scenario("todo_persistence", "Todo UI persists state locally", ["write state", "load state on next render"], ["localStorage", "saveTodos", "loadTodos"], ["app.js", "tests/test_static_site.py"]),
        ],
        "notes_api": [
            _scenario("notes_crud", "Notes API creates, lists, reads, and deletes notes", ["create note", "list notes", "get note", "delete note"], ["create_note", "list_notes", "get_note", "delete_note"], ["app/main.py", "tests/test_health.py"]),
            _scenario("notes_validation", "Notes API rejects invalid input and missing notes", ["reject empty title", "return not-found behavior"], ["note title is required", "note not found", "KeyError"], ["app/main.py", "tests/test_health.py"]),
        ],
        "issue_tracker_api": [
            _scenario("issue_domain_workflow", "Issue tracker creates, assigns, and transitions issues", ["create issue", "assign issue", "transition issue"], ["create_issue", "assign_issue", "transition_issue", "resolved"], ["app/domain.py", "tests/test_issue_tracker.py"]),
            _scenario("issue_store_filtering", "Issue tracker stores and filters issues by assignee and status", ["store issues", "filter by assignee", "filter by status"], ["IssueStore", "assignee", "status", "in_progress"], ["app/store.py", "tests/test_issue_tracker.py"]),
            _scenario("issue_route_adapters", "Issue tracker exposes route adapter helpers and FastAPI router wiring", ["create route response", "assign route response", "transition route response", "include router"], ["create_issue_response", "assign_issue_response", "transition_issue_response", "include_router"], ["app/routes.py", "app/main.py", "tests/test_issue_tracker.py"]),
        ],
        "csv_summary": [
            _scenario("csv_summary_metrics", "CSV tool reports rows, columns, sums, and averages", ["parse CSV", "count rows", "summarize numeric columns"], ["summarize_rows", "numeric_sums", "numeric_averages", "columns"], ["tests/test_processor.py"]),
            _scenario("csv_cli", "CSV tool exposes file-based CLI output", ["read input file", "print JSON summary"], ["Path", "json.dumps", "input csv path required"], []),
        ],
        "sales_analytics_pipeline": [
            _scenario("sales_pipeline_load_analyze", "Sales analytics pipeline loads, filters, groups, and selects top region", ["load CSV records", "filter by amount and channel", "group totals by region", "select top region"], ["load_records", "filter_records", "group_region_totals", "top_region"], ["tests/test_sales_pipeline.py"]),
            _scenario("sales_pipeline_report", "Sales analytics pipeline builds structured and markdown reports", ["build summary dictionary", "render markdown report", "include region totals and top region"], ["build_summary", "render_markdown_report", "Sales Analytics Report", "Top region"], ["tests/test_sales_pipeline.py"]),
            _scenario("sales_pipeline_cli", "Sales analytics pipeline exposes CLI JSON and markdown output", ["read CSV path", "apply filters", "print JSON", "print markdown"], ["build_parser", "run_pipeline", "json.dumps", "--format"], ["tests/test_sales_pipeline.py"]),
        ],
        "local_agent_command_router": [
            _scenario("agent_router_actions", "Local agent tool routes status, echo, and summarize actions", ["validate action", "call registry handler", "return structured result"], ["ACTION_REGISTRY", "status", "echo", "summarize"], ["tests/test_contract.py"]),
            _scenario("agent_router_rejection", "Local agent tool rejects unknown actions and bad payloads", ["reject unknown action", "reject non-object payload"], ["unsupported action", "payload must be", "json.loads"], ["tests/test_contract.py"]),
        ],
        "telegram_command_bot": [
            _scenario("telegram_commands", "Telegram bot handles start/help/status/echo commands", ["build command list", "return command replies"], ["COMMANDS", "/start", "/help", "/status", "/echo"], ["tests/test_bot.py"]),
            _scenario("telegram_runtime_boundary", "Telegram bot keeps live token requirement explicit", ["require token only for live runtime", "test pure command handling without network"], ["TELEGRAM_BOT_TOKEN", "build_runtime_config"], ["tests/test_bot.py"]),
        ],
        "vite_counter_app": [
            _scenario("counter_interactions", "Vite counter renders count controls", ["render count", "increment", "decrement", "reset"], ["useState(0)", "increment", "decrement", "reset"], ["src/main.jsx", "tests/test_vite_contract.py"]),
            _scenario("counter_entrypoint", "Vite counter is wired through package and browser entrypoint", ["package has dev script", "HTML loads main module"], ["vite", "/src/main.jsx", "createRoot"], ["package.json", "index.html", "src/main.jsx"]),
        ],
        "python_text_utils_library": [
            _scenario("text_utils_public_api", "Text utilities expose normalize, slugify, word count, and summary behavior", ["normalize text", "slugify", "count words", "summarize"], ["normalize_text", "slugify", "word_count", "summarize_text"], ["tests/test_library.py"]),
            _scenario("text_utils_edge_cases", "Text utilities handle spacing, empty slugs, and summary bounds", ["collapse whitespace", "empty slug fallback", "reject invalid summary size"], ["hello-world-2026", "max_words must be positive", "text"], ["tests/test_library.py"]),
        ],
    }
    rows = table.get(feature_id, [])
    if rows:
        return rows
    if operations:
        return [
            _scenario(
                f"{feature_id}_operations",
                str(feature.get("description") or f"{feature_id} operations are implemented"),
                [f"prove {operation}" for operation in operations],
                operations,
                [],
            )
        ]
    return []


def _template_scenarios(project_type: str, template_id: str) -> list[dict[str, Any]]:
    table = {
        "python_cli_basic": [_scenario("cli_ready", "CLI project runs and exposes tested core behavior", ["run core logic", "print CLI result"], ["run", "ready", "main"], [])],
        "python_fastapi_service": [_scenario("service_health", "API service exposes a health contract", ["call pure health logic", "wire HTTP health endpoint"], ["health", "ok", "FastAPI"], ["app/main.py", "tests/test_health.py"])],
        "python_library": [_scenario("library_public_behavior", "Library exposes tested public behavior", ["call public function", "verify deterministic result"], ["describe", "test_describe"], [])],
        "node_vite_app": [_scenario("vite_entrypoint", "Vite frontend has package, HTML, and render entrypoint", ["run dev script", "load root script", "render ready UI"], ["vite", "root", "ready"], ["package.json", "index.html", "src/main.jsx"])],
        "static_site": [_scenario("static_site_ready", "Static site loads content and assets", ["open HTML", "load stylesheet", "load script"], ["styles.css", "app.js", "ready"], ["index.html", "tests/test_static_site.py"])],
        "telegram_bot_python": [_scenario("telegram_bot_reply", "Telegram bot has pure reply logic and token boundary", ["build reply without network", "require token for live run"], ["build_reply", "TELEGRAM_BOT_TOKEN"], [])],
        "data_processing_tool": [_scenario("data_tool_summary", "Data tool parses input and returns a summary", ["parse rows", "print summary"], ["summarize_rows", "rows", "input csv path required"], [])],
        "local_agent_tool": [_scenario("local_agent_tool_contract", "Local agent tool returns structured results", ["build result", "print command output"], ["build_tool_result", "status", "task"], [])],
    }
    rows = table.get(template_id)
    if rows:
        return rows
    return [_scenario(f"{project_type}_ready", "Generated project has a launchable ready path", ["run entrypoint", "verify expected behavior"], ["ready"], [])]


def _scenario(scenario_id: str, description: str, steps: list[str], required_markers: list[str], evidence_files: list[str]) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "description": description,
        "steps": steps,
        "required_markers": required_markers,
        "evidence_files": evidence_files,
    }


def _default_evidence_files(expected_files: list[str]) -> list[str]:
    preferred = [
        path
        for path in expected_files
        if path == "README.md"
        or path.startswith("tests/")
        or path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".html"))
    ]
    return preferred[:4]
