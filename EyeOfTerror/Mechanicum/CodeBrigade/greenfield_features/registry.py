from __future__ import annotations

from typing import Any

from .detection import infer_acceptance_features
from .cli import apply_python_cli_calculator_feature, calculator_module_contracts
from .data_tools import apply_data_processing_csv_summary_feature, apply_sales_analytics_pipeline_feature, csv_summary_module_contracts, sales_analytics_pipeline_module_contracts
from .fastapi_services import (
    apply_fastapi_inventory_ops_feature,
    apply_fastapi_issue_tracker_feature,
    apply_fastapi_maintenance_work_orders_feature,
    apply_fastapi_notes_feature,
    apply_fastapi_operations_dashboard_feature,
    fastapi_inventory_ops_module_contracts,
    fastapi_issue_tracker_module_contracts,
    fastapi_maintenance_work_orders_module_contracts,
    fastapi_notes_module_contracts,
    fastapi_operations_dashboard_module_contracts,
)
from .libraries import apply_python_text_utils_library_feature, python_text_utils_library_module_contracts
from .local_agent_tool import apply_local_agent_command_router_feature, local_agent_command_router_module_contracts
from .static_frontend import apply_static_site_kanban_board_feature, apply_static_site_todo_feature, static_kanban_board_module_contracts, static_todo_module_contracts
from .telegram_bot import apply_telegram_command_bot_feature, telegram_command_bot_module_contracts
from .vite_frontend import (
    apply_vite_counter_app_feature,
    apply_vite_kanban_board_feature,
    apply_vite_todo_dashboard_feature,
    vite_counter_app_module_contracts,
    vite_kanban_board_module_contracts,
    vite_todo_dashboard_module_contracts,
)


def has_feature(features: list[dict[str, Any]], feature_id: str) -> bool:
    return any(feature.get("id") == feature_id for feature in features)


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
    if template_id == "python_cli_basic" and has_feature(features, "calculator_operations"):
        return apply_python_cli_calculator_feature(project_name, files), calculator_module_contracts(project_name), features
    if template_id == "static_site" and has_feature(features, "kanban_board_frontend"):
        return apply_static_site_kanban_board_feature(project_name, files), static_kanban_board_module_contracts(), features
    if template_id == "static_site" and has_feature(features, "todo_list"):
        return apply_static_site_todo_feature(project_name, files), static_todo_module_contracts(), features
    if template_id == "node_vite_app" and has_feature(features, "kanban_board_frontend"):
        return apply_vite_kanban_board_feature(project_name, files), vite_kanban_board_module_contracts(), features
    if template_id == "node_vite_app" and has_feature(features, "todo_list"):
        return apply_vite_todo_dashboard_feature(project_name, files), vite_todo_dashboard_module_contracts(), features
    if template_id == "python_fastapi_service" and has_feature(features, "inventory_ops_api"):
        return apply_fastapi_inventory_ops_feature(project_name, files), fastapi_inventory_ops_module_contracts(), features
    if template_id == "python_fastapi_service" and has_feature(features, "maintenance_work_orders_api"):
        return apply_fastapi_maintenance_work_orders_feature(project_name, files), fastapi_maintenance_work_orders_module_contracts(), features
    if template_id == "python_fastapi_service" and has_feature(features, "issue_tracker_api"):
        return apply_fastapi_issue_tracker_feature(project_name, files), fastapi_issue_tracker_module_contracts(), features
    if template_id == "python_fastapi_service" and has_feature(features, "operations_dashboard_api"):
        return apply_fastapi_operations_dashboard_feature(project_name, files), fastapi_operations_dashboard_module_contracts(), features
    if template_id == "python_fastapi_service" and has_feature(features, "notes_api"):
        return apply_fastapi_notes_feature(project_name, files), fastapi_notes_module_contracts(), features
    if template_id == "data_processing_tool" and has_feature(features, "sales_analytics_pipeline"):
        return apply_sales_analytics_pipeline_feature(project_name, files), sales_analytics_pipeline_module_contracts(project_name), features
    if template_id == "data_processing_tool" and has_feature(features, "csv_summary"):
        return apply_data_processing_csv_summary_feature(project_name, files), csv_summary_module_contracts(project_name), features
    if template_id == "local_agent_tool" and has_feature(features, "local_agent_command_router"):
        return apply_local_agent_command_router_feature(project_name, files), local_agent_command_router_module_contracts(project_name), features
    if template_id == "telegram_bot_python" and has_feature(features, "telegram_command_bot"):
        return apply_telegram_command_bot_feature(project_name, files), telegram_command_bot_module_contracts(project_name), features
    if template_id == "node_vite_app" and has_feature(features, "vite_counter_app"):
        return apply_vite_counter_app_feature(project_name, files), vite_counter_app_module_contracts(), features
    if template_id == "python_library" and has_feature(features, "python_text_utils_library"):
        return apply_python_text_utils_library_feature(project_name, files), python_text_utils_library_module_contracts(project_name), features
    return files, module_contracts, features
