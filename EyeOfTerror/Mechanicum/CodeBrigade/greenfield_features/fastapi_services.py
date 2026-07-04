from __future__ import annotations

from .fastapi_inventory import apply_fastapi_inventory_ops_feature, fastapi_inventory_ops_module_contracts
from .fastapi_issues import apply_fastapi_issue_tracker_feature, fastapi_issue_tracker_module_contracts
from .fastapi_maintenance import apply_fastapi_maintenance_work_orders_feature, fastapi_maintenance_work_orders_module_contracts
from .fastapi_notes import apply_fastapi_notes_feature, fastapi_notes_module_contracts
from .fastapi_operations import apply_fastapi_operations_dashboard_feature, fastapi_operations_dashboard_module_contracts

__all__ = [
    "apply_fastapi_inventory_ops_feature",
    "apply_fastapi_issue_tracker_feature",
    "apply_fastapi_maintenance_work_orders_feature",
    "apply_fastapi_notes_feature",
    "apply_fastapi_operations_dashboard_feature",
    "fastapi_inventory_ops_module_contracts",
    "fastapi_issue_tracker_module_contracts",
    "fastapi_maintenance_work_orders_module_contracts",
    "fastapi_notes_module_contracts",
    "fastapi_operations_dashboard_module_contracts",
]
