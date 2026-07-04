from __future__ import annotations

from typing import Any

from .common import replace_project_file


def apply_fastapi_maintenance_work_orders_feature(project_name: str, files: list[Any]) -> list[Any]:
    domain = (
        "from dataclasses import dataclass, replace\n\n\n"
        "ALLOWED_STATUSES = {'open', 'in_progress', 'resolved'}\n\n\n"
        "@dataclass(frozen=True)\n"
        "class WorkOrder:\n"
        "    id: int\n"
        "    equipment_id: str\n"
        "    description: str\n"
        "    technician: str = ''\n"
        "    status: str = 'open'\n\n\n"
        "def create_work_order(order_id: int, equipment_id: str, description: str) -> WorkOrder:\n"
        "    equipment = equipment_id.strip()\n"
        "    text = description.strip()\n"
        "    if not equipment:\n"
        "        raise ValueError('equipment_id is required')\n"
        "    if not text:\n"
        "        raise ValueError('description is required')\n"
        "    return WorkOrder(id=order_id, equipment_id=equipment, description=text)\n\n\n"
        "def assign_technician(order: WorkOrder, technician: str) -> WorkOrder:\n"
        "    name = technician.strip()\n"
        "    if not name:\n"
        "        raise ValueError('technician is required')\n"
        "    return replace(order, technician=name)\n\n\n"
        "def transition_status(order: WorkOrder, status: str) -> WorkOrder:\n"
        "    if status not in ALLOWED_STATUSES:\n"
        "        raise ValueError(f'unsupported status: {status}')\n"
        "    return replace(order, status=status)\n\n\n"
        "def serialize_work_order(order: WorkOrder) -> dict[str, object]:\n"
        "    return {\n"
        "        'id': order.id,\n"
        "        'equipment_id': order.equipment_id,\n"
        "        'description': order.description,\n"
        "        'technician': order.technician,\n"
        "        'status': order.status,\n"
        "    }\n"
    )
    store = (
        "from .domain import WorkOrder, assign_technician, create_work_order, transition_status\n\n\n"
        "class WorkOrderStore:\n"
        "    def __init__(self) -> None:\n"
        "        self._orders: dict[int, WorkOrder] = {}\n"
        "        self._next_id = 1\n\n"
        "    def create(self, equipment_id: str, description: str) -> WorkOrder:\n"
        "        order = create_work_order(self._next_id, equipment_id, description)\n"
        "        self._orders[order.id] = order\n"
        "        self._next_id += 1\n"
        "        return order\n\n"
        "    def get(self, order_id: int) -> WorkOrder:\n"
        "        if order_id not in self._orders:\n"
        "            raise KeyError('work order not found')\n"
        "        return self._orders[order_id]\n\n"
        "    def assign(self, order_id: int, technician: str) -> WorkOrder:\n"
        "        order = assign_technician(self.get(order_id), technician)\n"
        "        self._orders[order_id] = order\n"
        "        return order\n\n"
        "    def transition(self, order_id: int, status: str) -> WorkOrder:\n"
        "        order = transition_status(self.get(order_id), status)\n"
        "        self._orders[order_id] = order\n"
        "        return order\n\n"
        "    def list(self, *, status: str | None = None, technician: str | None = None) -> list[WorkOrder]:\n"
        "        orders = list(self._orders.values())\n"
        "        if status:\n"
        "            orders = [order for order in orders if order.status == status]\n"
        "        if technician:\n"
        "            orders = [order for order in orders if order.technician == technician]\n"
        "        return orders\n\n"
        "    def summary_by_status(self) -> dict[str, int]:\n"
        "        summary = {'open': 0, 'in_progress': 0, 'resolved': 0}\n"
        "        for order in self._orders.values():\n"
        "            summary[order.status] += 1\n"
        "        return summary\n"
    )
    routes = (
        "try:\n"
        "    from fastapi import APIRouter\n"
        "except ModuleNotFoundError:\n"
        "    APIRouter = None\n\n"
        "from .domain import serialize_work_order\n"
        "from .store import WorkOrderStore\n\n\n"
        "DEFAULT_STORE = WorkOrderStore()\n\n\n"
        "def create_order_response(payload: dict[str, str], store: WorkOrderStore | None = None) -> dict[str, object]:\n"
        "    active_store = store or DEFAULT_STORE\n"
        "    return serialize_work_order(active_store.create(payload.get('equipment_id', ''), payload.get('description', '')))\n\n\n"
        "def assign_order_response(order_id: int, payload: dict[str, str], store: WorkOrderStore | None = None) -> dict[str, object]:\n"
        "    active_store = store or DEFAULT_STORE\n"
        "    return serialize_work_order(active_store.assign(order_id, payload.get('technician', '')))\n\n\n"
        "def transition_order_response(order_id: int, payload: dict[str, str], store: WorkOrderStore | None = None) -> dict[str, object]:\n"
        "    active_store = store or DEFAULT_STORE\n"
        "    return serialize_work_order(active_store.transition(order_id, payload.get('status', '')))\n\n\n"
        "def list_order_response(status: str | None = None, technician: str | None = None, store: WorkOrderStore | None = None) -> list[dict[str, object]]:\n"
        "    active_store = store or DEFAULT_STORE\n"
        "    return [serialize_work_order(order) for order in active_store.list(status=status, technician=technician)]\n\n\n"
        "def summary_response(store: WorkOrderStore | None = None) -> dict[str, int]:\n"
        "    active_store = store or DEFAULT_STORE\n"
        "    return active_store.summary_by_status()\n\n\n"
        "if APIRouter is not None:\n"
        "    router = APIRouter(prefix='/work-orders')\n\n"
        "    @router.post('')\n"
        "    def create_order(payload: dict[str, str]) -> dict[str, object]:\n"
        "        return create_order_response(payload)\n\n"
        "    @router.get('')\n"
        "    def list_orders(status: str | None = None, technician: str | None = None) -> list[dict[str, object]]:\n"
        "        return list_order_response(status=status, technician=technician)\n\n"
        "    @router.post('/{order_id}/assign')\n"
        "    def assign_order(order_id: int, payload: dict[str, str]) -> dict[str, object]:\n"
        "        return assign_order_response(order_id, payload)\n\n"
        "    @router.post('/{order_id}/status')\n"
        "    def transition_order(order_id: int, payload: dict[str, str]) -> dict[str, object]:\n"
        "        return transition_order_response(order_id, payload)\n\n"
        "    @router.get('/summary')\n"
        "    def summary() -> dict[str, int]:\n"
        "        return summary_response()\n"
        "else:\n"
        "    router = None\n"
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
        "    app = FastAPI(title='Maintenance Work Orders')\n\n"
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
        "from app.domain import assign_technician, create_work_order, transition_status\n"
        "from app.routes import assign_order_response, create_order_response, list_order_response, summary_response, transition_order_response\n"
        "from app.store import WorkOrderStore\n\n\n"
        "class MaintenanceWorkOrderTests(unittest.TestCase):\n"
        "    def test_domain_create_assign_transition(self):\n"
        "        order = create_work_order(1, 'pump-7', 'leaking seal')\n"
        "        assigned = assign_technician(order, 'Mira')\n"
        "        resolved = transition_status(assigned, 'resolved')\n"
        "        self.assertEqual(resolved.equipment_id, 'pump-7')\n"
        "        self.assertEqual(resolved.technician, 'Mira')\n"
        "        self.assertEqual(resolved.status, 'resolved')\n\n"
        "    def test_store_filter_and_summary_workflow(self):\n"
        "        store = WorkOrderStore()\n"
        "        first = store.create('pump-7', 'leaking seal')\n"
        "        second = store.create('press-2', 'sensor fault')\n"
        "        store.assign(first.id, 'Mira')\n"
        "        store.assign(second.id, 'Kara')\n"
        "        store.transition(second.id, 'in_progress')\n"
        "        self.assertEqual([order.equipment_id for order in store.list(technician='Mira')], ['pump-7'])\n"
        "        self.assertEqual([order.equipment_id for order in store.list(status='in_progress')], ['press-2'])\n"
        "        self.assertEqual(store.summary_by_status(), {'open': 1, 'in_progress': 1, 'resolved': 0})\n\n"
        "    def test_route_adapter_helpers(self):\n"
        "        store = WorkOrderStore()\n"
        "        created = create_order_response({'equipment_id': 'crane-1', 'description': 'brake inspection'}, store)\n"
        "        assigned = assign_order_response(created['id'], {'technician': 'Lena'}, store)\n"
        "        transitioned = transition_order_response(created['id'], {'status': 'resolved'}, store)\n"
        "        self.assertEqual(assigned['technician'], 'Lena')\n"
        "        self.assertEqual(transitioned['status'], 'resolved')\n"
        "        self.assertEqual(list_order_response(status='resolved', store=store), [transitioned])\n"
        "        self.assertEqual(summary_response(store), {'open': 0, 'in_progress': 0, 'resolved': 1})\n"
    )
    readme = (
        f"# {project_name}\n\nA multi-module FastAPI maintenance work-order service with domain logic, in-memory store, route adapter helpers, filtering, and status summaries.\n\n"
        "## Run\n\n```bash\nuvicorn app.main:app --reload\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n\n"
        "```bash\npython -m py_compile app/main.py app/domain.py app/store.py app/routes.py\n```\n"
    )
    rows = replace_project_file(files, "app/domain.py", domain)
    rows = replace_project_file(rows, "app/store.py", store)
    rows = replace_project_file(rows, "app/routes.py", routes)
    rows = replace_project_file(rows, "app/main.py", main)
    rows = replace_project_file(rows, "tests/test_maintenance.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def fastapi_maintenance_work_orders_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "app.domain",
            "path": "app/domain.py",
            "responsibility": "maintenance work-order entity and pure transitions",
            "requirements": ["create work orders", "assign technicians", "transition open in_progress resolved statuses", "reject invalid equipment and statuses"],
        },
        {
            "module": "app.store",
            "path": "app/store.py",
            "responsibility": "in-memory maintenance repository, filters, and status summary",
            "requirements": ["store work orders", "filter by status", "filter by technician", "summarize work orders by status"],
        },
        {
            "module": "app.routes",
            "path": "app/routes.py",
            "responsibility": "FastAPI route adapter helpers for maintenance workflows",
            "requirements": ["create order route response", "assign technician route response", "transition status route response", "list and summary route responses"],
        },
        {
            "module": "app.main",
            "path": "app/main.py",
            "responsibility": "FastAPI app assembly and router wiring",
            "requirements": ["expose health", "include maintenance router when FastAPI is installed"],
        },
        {
            "module": "tests.test_maintenance",
            "path": "tests/test_maintenance.py",
            "responsibility": "maintenance workflow verification",
            "requirements": ["prove domain workflow", "prove store filtering and summary workflow", "prove route adapter workflow"],
        },
    ]
