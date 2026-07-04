from __future__ import annotations

from typing import Any

from .common import replace_project_file


def apply_fastapi_inventory_ops_feature(project_name: str, files: list[Any]) -> list[Any]:
    domain = (
        "from dataclasses import dataclass, replace\n"
        "from typing import Literal\n\n\n"
        "ItemStatus = Literal['active', 'archived']\n\n\n"
        "@dataclass(frozen=True)\n"
        "class InventoryItem:\n"
        "    sku: str\n"
        "    name: str\n"
        "    category: str\n"
        "    quantity: int\n"
        "    reorder_level: int\n"
        "    status: ItemStatus = 'active'\n\n\n"
        "@dataclass(frozen=True)\n"
        "class StockAdjustment:\n"
        "    sku: str\n"
        "    delta: int\n"
        "    reason: str\n"
        "    resulting_quantity: int\n\n\n"
        "def json_error(code: str, message: str) -> dict[str, str]:\n"
        "    return {'error': code, 'message': message}\n\n\n"
        "def create_item(sku: str, name: str, category: str, quantity: int = 0, reorder_level: int = 0) -> InventoryItem:\n"
        "    clean_sku = sku.strip().upper()\n"
        "    clean_name = name.strip()\n"
        "    clean_category = category.strip().lower()\n"
        "    if not clean_sku:\n"
        "        raise ValueError('sku is required')\n"
        "    if not clean_name:\n"
        "        raise ValueError('name is required')\n"
        "    if quantity < 0:\n"
        "        raise ValueError('quantity cannot be negative')\n"
        "    if reorder_level < 0:\n"
        "        raise ValueError('reorder level cannot be negative')\n"
        "    return InventoryItem(clean_sku, clean_name, clean_category, quantity, reorder_level)\n\n\n"
        "def update_item(item: InventoryItem, *, name: str | None = None, category: str | None = None, status: ItemStatus | None = None) -> InventoryItem:\n"
        "    next_status = status or item.status\n"
        "    if next_status not in {'active', 'archived'}:\n"
        "        raise ValueError(f'unsupported item status: {next_status}')\n"
        "    return replace(\n"
        "        item,\n"
        "        name=item.name if name is None else name.strip(),\n"
        "        category=item.category if category is None else category.strip().lower(),\n"
        "        status=next_status,\n"
        "    )\n\n\n"
        "def adjust_stock(item: InventoryItem, delta: int, reason: str) -> tuple[InventoryItem, StockAdjustment]:\n"
        "    clean_reason = reason.strip()\n"
        "    if not clean_reason:\n"
        "        raise ValueError('stock adjustment reason is required')\n"
        "    next_quantity = item.quantity + delta\n"
        "    if next_quantity < 0:\n"
        "        raise ValueError('stock adjustment would make quantity negative')\n"
        "    next_item = replace(item, quantity=next_quantity)\n"
        "    return next_item, StockAdjustment(item.sku, delta, clean_reason, next_quantity)\n\n\n"
        "def is_low_stock(item: InventoryItem) -> bool:\n"
        "    return item.status == 'active' and item.quantity <= item.reorder_level\n"
    )
    store = (
        "from __future__ import annotations\n\n"
        "from .domain import InventoryItem, ItemStatus, StockAdjustment, adjust_stock, create_item, is_low_stock, update_item\n\n\n"
        "class InventoryStore:\n"
        "    def __init__(self) -> None:\n"
        "        self._items: dict[str, InventoryItem] = {}\n"
        "        self._ledger: list[StockAdjustment] = []\n\n"
        "    def reset(self) -> None:\n"
        "        self._items = {}\n"
        "        self._ledger = []\n\n"
        "    def create(self, sku: str, name: str, category: str, quantity: int = 0, reorder_level: int = 0) -> InventoryItem:\n"
        "        item = create_item(sku, name, category, quantity, reorder_level)\n"
        "        if item.sku in self._items:\n"
        "            raise ValueError('sku already exists')\n"
        "        self._items[item.sku] = item\n"
        "        return item\n\n"
        "    def get(self, sku: str) -> InventoryItem:\n"
        "        clean_sku = sku.strip().upper()\n"
        "        if clean_sku not in self._items:\n"
        "            raise KeyError(clean_sku)\n"
        "        return self._items[clean_sku]\n\n"
        "    def update(self, sku: str, *, name: str | None = None, category: str | None = None, status: ItemStatus | None = None) -> InventoryItem:\n"
        "        item = update_item(self.get(sku), name=name, category=category, status=status)\n"
        "        self._items[item.sku] = item\n"
        "        return item\n\n"
        "    def adjust(self, sku: str, delta: int, reason: str) -> StockAdjustment:\n"
        "        next_item, adjustment = adjust_stock(self.get(sku), delta, reason)\n"
        "        self._items[next_item.sku] = next_item\n"
        "        self._ledger.append(adjustment)\n"
        "        return adjustment\n\n"
        "    def list(self, *, sku: str | None = None, category: str | None = None, status: ItemStatus | None = None, search: str | None = None) -> list[InventoryItem]:\n"
        "        rows = list(self._items.values())\n"
        "        if sku:\n"
        "            rows = [item for item in rows if item.sku == sku.strip().upper()]\n"
        "        if category:\n"
        "            rows = [item for item in rows if item.category == category.strip().lower()]\n"
        "        if status:\n"
        "            rows = [item for item in rows if item.status == status]\n"
        "        if search:\n"
        "            needle = search.strip().lower()\n"
        "            rows = [item for item in rows if needle in item.name.lower() or needle in item.sku.lower()]\n"
        "        return rows\n\n"
        "    def low_stock(self) -> list[InventoryItem]:\n"
        "        return [item for item in self._items.values() if is_low_stock(item)]\n\n"
        "    def ledger(self, *, sku: str | None = None) -> list[StockAdjustment]:\n"
        "        if not sku:\n"
        "            return list(self._ledger)\n"
        "        clean_sku = sku.strip().upper()\n"
        "        return [row for row in self._ledger if row.sku == clean_sku]\n\n\n"
        "STORE = InventoryStore()\n"
    )
    reports = (
        "from .domain import InventoryItem, StockAdjustment\n\n\n"
        "def item_to_dict(item: InventoryItem) -> dict[str, object]:\n"
        "    return {\n"
        "        'sku': item.sku,\n"
        "        'name': item.name,\n"
        "        'category': item.category,\n"
        "        'quantity': item.quantity,\n"
        "        'reorder_level': item.reorder_level,\n"
        "        'status': item.status,\n"
        "    }\n\n\n"
        "def adjustment_to_dict(adjustment: StockAdjustment) -> dict[str, object]:\n"
        "    return {\n"
        "        'sku': adjustment.sku,\n"
        "        'delta': adjustment.delta,\n"
        "        'reason': adjustment.reason,\n"
        "        'resulting_quantity': adjustment.resulting_quantity,\n"
        "    }\n\n\n"
        "def low_stock_report(items: list[InventoryItem]) -> dict[str, object]:\n"
        "    return {'count': len(items), 'items': [item_to_dict(item) for item in items]}\n"
    )
    routes = (
        "try:\n"
        "    from fastapi import APIRouter, HTTPException\n"
        "    from pydantic import BaseModel\n"
        "except ModuleNotFoundError:\n"
        "    APIRouter = None\n"
        "    HTTPException = None\n"
        "    BaseModel = object\n\n"
        "from .domain import json_error\n"
        "from .reports import adjustment_to_dict, item_to_dict, low_stock_report\n"
        "from .store import STORE, InventoryStore\n\n\n"
        "class InventoryCreate(BaseModel):\n"
        "    sku: str\n"
        "    name: str\n"
        "    category: str\n"
        "    quantity: int = 0\n"
        "    reorder_level: int = 0\n\n\n"
        "class InventoryUpdate(BaseModel):\n"
        "    name: str | None = None\n"
        "    category: str | None = None\n"
        "    status: str | None = None\n\n\n"
        "class StockAdjustmentPayload(BaseModel):\n"
        "    delta: int\n"
        "    reason: str\n\n\n"
        "def create_item_response(payload: dict[str, object], store: InventoryStore = STORE) -> dict[str, object]:\n"
        "    return item_to_dict(store.create(str(payload.get('sku', '')), str(payload.get('name', '')), str(payload.get('category', '')), int(payload.get('quantity', 0)), int(payload.get('reorder_level', 0))))\n\n\n"
        "def update_item_response(sku: str, payload: dict[str, object], store: InventoryStore = STORE) -> dict[str, object]:\n"
        "    return item_to_dict(store.update(sku, name=payload.get('name'), category=payload.get('category'), status=payload.get('status')))\n\n\n"
        "def list_inventory_response(*, sku: str | None = None, category: str | None = None, status: str | None = None, search: str | None = None, store: InventoryStore = STORE) -> list[dict[str, object]]:\n"
        "    return [item_to_dict(item) for item in store.list(sku=sku, category=category, status=status, search=search)]\n\n\n"
        "def adjust_stock_response(sku: str, payload: dict[str, object], store: InventoryStore = STORE) -> dict[str, object]:\n"
        "    return adjustment_to_dict(store.adjust(sku, int(payload.get('delta', 0)), str(payload.get('reason', ''))))\n\n\n"
        "def low_stock_response(store: InventoryStore = STORE) -> dict[str, object]:\n"
        "    return low_stock_report(store.low_stock())\n\n\n"
        "def error_response(code: str, message: str) -> dict[str, str]:\n"
        "    return json_error(code, message)\n\n\n"
        "router = APIRouter(prefix='/inventory') if APIRouter is not None else None\n"
        "if router is not None:\n"
        "    @router.post('/items')\n"
        "    def create_item_endpoint(payload: InventoryCreate) -> dict[str, object]:\n"
        "        try:\n"
        "            return create_item_response(payload.model_dump())\n"
        "        except ValueError as exc:\n"
        "            raise HTTPException(status_code=400, detail=error_response('invalid_item', str(exc))) from exc\n\n"
        "    @router.patch('/items/{sku}')\n"
        "    def update_item_endpoint(sku: str, payload: InventoryUpdate) -> dict[str, object]:\n"
        "        try:\n"
        "            return update_item_response(sku, payload.model_dump(exclude_none=True))\n"
        "        except (KeyError, ValueError) as exc:\n"
        "            raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 400, detail=error_response('inventory_error', str(exc))) from exc\n\n"
        "    @router.get('/items')\n"
        "    def list_inventory_endpoint(sku: str | None = None, category: str | None = None, status: str | None = None, search: str | None = None) -> list[dict[str, object]]:\n"
        "        return list_inventory_response(sku=sku, category=category, status=status, search=search)\n\n"
        "    @router.post('/items/{sku}/adjustments')\n"
        "    def adjust_stock_endpoint(sku: str, payload: StockAdjustmentPayload) -> dict[str, object]:\n"
        "        try:\n"
        "            return adjust_stock_response(sku, payload.model_dump())\n"
        "        except (KeyError, ValueError) as exc:\n"
        "            raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 400, detail=error_response('stock_adjustment_error', str(exc))) from exc\n\n"
        "    @router.get('/reports/low-stock')\n"
        "    def low_stock_endpoint() -> dict[str, object]:\n"
        "        return low_stock_response()\n"
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
        f"    app = FastAPI(title='{project_name} Inventory API')\n\n"
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
        "from app.domain import adjust_stock, create_item, json_error, update_item\n"
        "from app.reports import low_stock_report\n"
        "from app.routes import adjust_stock_response, create_item_response, error_response, list_inventory_response, low_stock_response, update_item_response\n"
        "from app.store import InventoryStore\n\n\n"
        "class InventoryOpsWorkflowTests(unittest.TestCase):\n"
        "    def test_inventory_crud_and_stock_adjustment_ledger(self):\n"
        "        store = InventoryStore()\n"
        "        created = create_item_response({'sku': ' abc-1 ', 'name': ' Bolt ', 'category': 'Hardware', 'quantity': 5, 'reorder_level': 2}, store)\n"
        "        self.assertEqual(created['sku'], 'ABC-1')\n"
        "        updated = update_item_response('abc-1', {'name': 'Steel bolt', 'status': 'active'}, store)\n"
        "        self.assertEqual(updated['name'], 'Steel bolt')\n"
        "        adjustment = adjust_stock_response('abc-1', {'delta': -4, 'reason': 'picked for order'}, store)\n"
        "        self.assertEqual(adjustment['resulting_quantity'], 1)\n"
        "        self.assertEqual(len(store.ledger(sku='ABC-1')), 1)\n\n"
        "    def test_low_stock_report_and_filters(self):\n"
        "        store = InventoryStore()\n"
        "        store.create('SKU-1', 'Widget', 'tools', 1, 2)\n"
        "        store.create('SKU-2', 'Cable', 'electronics', 8, 2)\n"
        "        self.assertEqual([item['sku'] for item in list_inventory_response(category='tools', store=store)], ['SKU-1'])\n"
        "        self.assertEqual([item['sku'] for item in list_inventory_response(search='cab', store=store)], ['SKU-2'])\n"
        "        report = low_stock_response(store)\n"
        "        self.assertEqual(report['count'], 1)\n"
        "        self.assertEqual(report['items'][0]['sku'], 'SKU-1')\n\n"
        "    def test_domain_validation_and_json_errors(self):\n"
        "        item = create_item('SKU-3', 'Part', 'tools', 4, 2)\n"
        "        updated = update_item(item, status='archived')\n"
        "        self.assertEqual(updated.status, 'archived')\n"
        "        with self.assertRaises(ValueError):\n"
        "            adjust_stock(item, -10, 'bad adjustment')\n"
        "        self.assertEqual(json_error('invalid_item', 'bad'), {'error': 'invalid_item', 'message': 'bad'})\n"
        "        self.assertEqual(error_response('missing', 'not found')['error'], 'missing')\n"
        "        self.assertEqual(low_stock_report([])['count'], 0)\n"
    )
    readme = (
        f"# {project_name}\n\nA multi-module FastAPI inventory operations API with item CRUD, stock adjustment ledger, low-stock reports, filters, JSON error payloads, and workflow tests.\n\n"
        "## Run\n\n```bash\nuvicorn app.main:app --reload\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n\n"
        "```bash\npython -m py_compile app/main.py app/domain.py app/store.py app/reports.py app/routes.py\n```\n"
    )
    rows = replace_project_file(files, "app/domain.py", domain)
    rows = replace_project_file(rows, "app/store.py", store)
    rows = replace_project_file(rows, "app/reports.py", reports)
    rows = replace_project_file(rows, "app/routes.py", routes)
    rows = replace_project_file(rows, "app/main.py", main)
    rows = replace_project_file(rows, "tests/test_inventory_ops.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def fastapi_inventory_ops_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "app.domain",
            "path": "app/domain.py",
            "responsibility": "inventory item domain model, stock adjustment, and JSON error payload helpers",
            "requirements": ["create_item", "update_item", "adjust_stock", "json_error", "low stock predicate"],
        },
        {
            "module": "app.store",
            "path": "app/store.py",
            "responsibility": "inventory repository, SKU/category/status/search filters, and stock adjustment ledger",
            "requirements": ["create stored item", "update stored item", "adjust stock", "filter_inventory", "ledger by SKU", "low_stock report source"],
        },
        {
            "module": "app.reports",
            "path": "app/reports.py",
            "responsibility": "inventory JSON serialization and low-stock report construction",
            "requirements": ["item_to_dict", "adjustment_to_dict", "low_stock_report"],
        },
        {
            "module": "app.routes",
            "path": "app/routes.py",
            "responsibility": "FastAPI route adapters for CRUD, stock adjustment, low-stock report, filters, and JSON error responses",
            "requirements": ["create_item_response", "update_item_response", "list_inventory_response", "adjust_stock_response", "low_stock_response", "error_response"],
        },
        {
            "module": "app.main",
            "path": "app/main.py",
            "responsibility": "FastAPI inventory app assembly and health endpoint",
            "requirements": ["expose health", "include inventory router when FastAPI is installed"],
        },
        {
            "module": "tests.test_inventory_ops",
            "path": "tests/test_inventory_ops.py",
            "responsibility": "inventory operations workflow verification",
            "requirements": ["prove CRUD and stock adjustment ledger", "prove low-stock reports and filters", "prove JSON error payload helpers"],
        },
    ]
