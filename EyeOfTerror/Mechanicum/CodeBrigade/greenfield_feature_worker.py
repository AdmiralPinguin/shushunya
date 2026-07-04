#!/usr/bin/env python3
from __future__ import annotations

from greenfield_features.common import calculator_requested, replace_project_file, task_tokens
from greenfield_features.detection import infer_acceptance_features
from greenfield_features.registry import apply_task_feature_overrides
from greenfield_features.cli import apply_python_cli_calculator_feature, calculator_module_contracts
from greenfield_features.data_tools import apply_data_processing_csv_summary_feature, apply_sales_analytics_pipeline_feature, csv_summary_module_contracts, sales_analytics_pipeline_module_contracts
from greenfield_features.fastapi_services import (
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
from greenfield_features.libraries import apply_python_text_utils_library_feature, python_text_utils_library_module_contracts
from greenfield_features.local_agent_tool import apply_local_agent_command_router_feature, local_agent_command_router_module_contracts
from greenfield_features.static_frontend import apply_static_site_kanban_board_feature, apply_static_site_todo_feature, static_kanban_board_module_contracts, static_todo_module_contracts
from greenfield_features.telegram_bot import apply_telegram_command_bot_feature, telegram_command_bot_module_contracts
from greenfield_features.vite_frontend import (
    apply_vite_counter_app_feature,
    apply_vite_kanban_board_feature,
    apply_vite_todo_dashboard_feature,
    vite_counter_app_module_contracts,
    vite_kanban_board_module_contracts,
    vite_todo_dashboard_module_contracts,
)

__all__ = [
    "apply_data_processing_csv_summary_feature",
    "apply_fastapi_inventory_ops_feature",
    "apply_fastapi_issue_tracker_feature",
    "apply_fastapi_maintenance_work_orders_feature",
    "apply_fastapi_notes_feature",
    "apply_fastapi_operations_dashboard_feature",
    "apply_local_agent_command_router_feature",
    "apply_python_cli_calculator_feature",
    "apply_python_text_utils_library_feature",
    "apply_sales_analytics_pipeline_feature",
    "apply_static_site_kanban_board_feature",
    "apply_static_site_todo_feature",
    "apply_task_feature_overrides",
    "apply_telegram_command_bot_feature",
    "apply_vite_counter_app_feature",
    "apply_vite_kanban_board_feature",
    "apply_vite_todo_dashboard_feature",
    "calculator_module_contracts",
    "calculator_requested",
    "csv_summary_module_contracts",
    "fastapi_inventory_ops_module_contracts",
    "fastapi_issue_tracker_module_contracts",
    "fastapi_maintenance_work_orders_module_contracts",
    "fastapi_notes_module_contracts",
    "fastapi_operations_dashboard_module_contracts",
    "infer_acceptance_features",
    "local_agent_command_router_module_contracts",
    "python_text_utils_library_module_contracts",
    "replace_project_file",
    "sales_analytics_pipeline_module_contracts",
    "static_kanban_board_module_contracts",
    "static_todo_module_contracts",
    "task_tokens",
    "telegram_command_bot_module_contracts",
    "vite_counter_app_module_contracts",
    "vite_kanban_board_module_contracts",
    "vite_todo_dashboard_module_contracts",
]
