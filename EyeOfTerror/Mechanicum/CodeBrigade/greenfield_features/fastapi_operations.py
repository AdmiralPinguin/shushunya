from __future__ import annotations

from typing import Any

from .common import replace_project_file


def apply_fastapi_operations_dashboard_feature(project_name: str, files: list[Any]) -> list[Any]:
    domain = (
        "from dataclasses import dataclass, replace\n"
        "from typing import Literal\n\n\n"
        "ServiceTier = Literal['critical', 'standard']\n"
        "IncidentStatus = Literal['open', 'resolved']\n\n\n"
        "@dataclass(frozen=True)\n"
        "class ServiceRecord:\n"
        "    id: int\n"
        "    name: str\n"
        "    owner: str\n"
        "    tier: ServiceTier = 'standard'\n"
        "    uptime: float = 100.0\n\n\n"
        "@dataclass(frozen=True)\n"
        "class Incident:\n"
        "    id: int\n"
        "    service_id: int\n"
        "    title: str\n"
        "    severity: int\n"
        "    status: IncidentStatus = 'open'\n\n\n"
        "def register_service(service_id: int, name: str, owner: str, tier: ServiceTier = 'standard', uptime: float = 100.0) -> ServiceRecord:\n"
        "    clean_name = name.strip()\n"
        "    clean_owner = owner.strip()\n"
        "    if not clean_name:\n"
        "        raise ValueError('service name is required')\n"
        "    if not clean_owner:\n"
        "        raise ValueError('service owner is required')\n"
        "    if tier not in {'critical', 'standard'}:\n"
        "        raise ValueError(f'unsupported service tier: {tier}')\n"
        "    if uptime < 0 or uptime > 100:\n"
        "        raise ValueError('uptime must be between 0 and 100')\n"
        "    return ServiceRecord(id=service_id, name=clean_name, owner=clean_owner, tier=tier, uptime=uptime)\n\n\n"
        "def record_incident(incident_id: int, service_id: int, title: str, severity: int) -> Incident:\n"
        "    clean_title = title.strip()\n"
        "    if not clean_title:\n"
        "        raise ValueError('incident title is required')\n"
        "    if severity < 1 or severity > 5:\n"
        "        raise ValueError('incident severity must be between 1 and 5')\n"
        "    return Incident(id=incident_id, service_id=service_id, title=clean_title, severity=severity)\n\n\n"
        "def resolve_incident(incident: Incident) -> Incident:\n"
        "    return replace(incident, status='resolved')\n"
    )
    store = (
        "from .domain import Incident, ServiceRecord, ServiceTier, register_service, record_incident, resolve_incident\n\n\n"
        "class OperationsStore:\n"
        "    def __init__(self) -> None:\n"
        "        self._services: dict[int, ServiceRecord] = {}\n"
        "        self._incidents: dict[int, Incident] = {}\n"
        "        self._next_service_id = 1\n"
        "        self._next_incident_id = 1\n\n"
        "    def reset(self) -> None:\n"
        "        self._services = {}\n"
        "        self._incidents = {}\n"
        "        self._next_service_id = 1\n"
        "        self._next_incident_id = 1\n\n"
        "    def register(self, name: str, owner: str, tier: ServiceTier = 'standard', uptime: float = 100.0) -> ServiceRecord:\n"
        "        service = register_service(self._next_service_id, name, owner, tier, uptime)\n"
        "        self._services[service.id] = service\n"
        "        self._next_service_id += 1\n"
        "        return service\n\n"
        "    def services(self, *, owner: str | None = None, tier: ServiceTier | None = None) -> list[ServiceRecord]:\n"
        "        rows = list(self._services.values())\n"
        "        if owner:\n"
        "            rows = [service for service in rows if service.owner == owner]\n"
        "        if tier:\n"
        "            rows = [service for service in rows if service.tier == tier]\n"
        "        return rows\n\n"
        "    def get_service(self, service_id: int) -> ServiceRecord:\n"
        "        if service_id not in self._services:\n"
        "            raise KeyError(service_id)\n"
        "        return self._services[service_id]\n\n"
        "    def open_incident(self, service_id: int, title: str, severity: int) -> Incident:\n"
        "        self.get_service(service_id)\n"
        "        incident = record_incident(self._next_incident_id, service_id, title, severity)\n"
        "        self._incidents[incident.id] = incident\n"
        "        self._next_incident_id += 1\n"
        "        return incident\n\n"
        "    def resolve(self, incident_id: int) -> Incident:\n"
        "        if incident_id not in self._incidents:\n"
        "            raise KeyError(incident_id)\n"
        "        incident = resolve_incident(self._incidents[incident_id])\n"
        "        self._incidents[incident.id] = incident\n"
        "        return incident\n\n"
        "    def incidents(self, *, status: str | None = None, service_id: int | None = None) -> list[Incident]:\n"
        "        rows = list(self._incidents.values())\n"
        "        if status:\n"
        "            rows = [incident for incident in rows if incident.status == status]\n"
        "        if service_id:\n"
        "            rows = [incident for incident in rows if incident.service_id == service_id]\n"
        "        return rows\n\n\n"
        "STORE = OperationsStore()\n"
    )
    metrics = (
        "from .domain import Incident, ServiceRecord\n\n\n"
        "def service_health_score(service: ServiceRecord, incidents: list[Incident]) -> float:\n"
        "    open_penalty = sum(incident.severity * 4 for incident in incidents if incident.service_id == service.id and incident.status == 'open')\n"
        "    tier_penalty = 5 if service.tier == 'critical' and service.uptime < 99.0 else 0\n"
        "    return max(0.0, min(100.0, service.uptime - open_penalty - tier_penalty))\n\n\n"
        "def build_dashboard_metrics(services: list[ServiceRecord], incidents: list[Incident]) -> dict[str, object]:\n"
        "    scores = {service.name: service_health_score(service, incidents) for service in services}\n"
        "    open_incidents = [incident for incident in incidents if incident.status == 'open']\n"
        "    critical_services = [service for service in services if service.tier == 'critical']\n"
        "    average_health = sum(scores.values()) / len(scores) if scores else 100.0\n"
        "    return {\n"
        "        'service_count': len(services),\n"
        "        'critical_service_count': len(critical_services),\n"
        "        'open_incident_count': len(open_incidents),\n"
        "        'average_health': round(average_health, 2),\n"
        "        'health_by_service': scores,\n"
        "    }\n"
    )
    events = (
        "from .domain import Incident, ServiceRecord\n\n\n"
        "def service_event(service: ServiceRecord) -> dict[str, object]:\n"
        "    return {'type': 'service_registered', 'service_id': service.id, 'label': service.name, 'owner': service.owner}\n\n\n"
        "def incident_event(incident: Incident) -> dict[str, object]:\n"
        "    return {'type': f'incident_{incident.status}', 'incident_id': incident.id, 'service_id': incident.service_id, 'severity': incident.severity, 'label': incident.title}\n\n\n"
        "def build_event_timeline(services: list[ServiceRecord], incidents: list[Incident]) -> list[dict[str, object]]:\n"
        "    service_rows = [service_event(service) for service in sorted(services, key=lambda item: item.id)]\n"
        "    incident_rows = [incident_event(incident) for incident in sorted(incidents, key=lambda item: item.id)]\n"
        "    return service_rows + incident_rows\n"
    )
    routes = (
        "try:\n"
        "    from fastapi import APIRouter, HTTPException\n"
        "    from pydantic import BaseModel\n"
        "except ModuleNotFoundError:\n"
        "    APIRouter = None\n"
        "    HTTPException = None\n"
        "    BaseModel = object\n\n"
        "from .domain import Incident, ServiceRecord, ServiceTier\n"
        "from .events import build_event_timeline\n"
        "from .metrics import build_dashboard_metrics\n"
        "from .store import STORE, OperationsStore\n\n\n"
        "class ServiceCreate(BaseModel):\n"
        "    name: str\n"
        "    owner: str\n"
        "    tier: ServiceTier = 'standard'\n"
        "    uptime: float = 100.0\n\n\n"
        "class IncidentCreate(BaseModel):\n"
        "    service_id: int\n"
        "    title: str\n"
        "    severity: int\n\n\n"
        "def service_to_dict(service: ServiceRecord) -> dict[str, object]:\n"
        "    return {'id': service.id, 'name': service.name, 'owner': service.owner, 'tier': service.tier, 'uptime': service.uptime}\n\n\n"
        "def incident_to_dict(incident: Incident) -> dict[str, object]:\n"
        "    return {'id': incident.id, 'service_id': incident.service_id, 'title': incident.title, 'severity': incident.severity, 'status': incident.status}\n\n\n"
        "def register_service_response(payload: dict[str, object], store: OperationsStore = STORE) -> dict[str, object]:\n"
        "    return service_to_dict(store.register(str(payload.get('name', '')), str(payload.get('owner', '')), payload.get('tier', 'standard'), float(payload.get('uptime', 100.0))))\n\n\n"
        "def record_incident_response(payload: dict[str, object], store: OperationsStore = STORE) -> dict[str, object]:\n"
        "    return incident_to_dict(store.open_incident(int(payload.get('service_id', 0)), str(payload.get('title', '')), int(payload.get('severity', 1))))\n\n\n"
        "def resolve_incident_response(incident_id: int, store: OperationsStore = STORE) -> dict[str, object]:\n"
        "    return incident_to_dict(store.resolve(incident_id))\n\n\n"
        "def dashboard_response(store: OperationsStore = STORE) -> dict[str, object]:\n"
        "    services = store.services()\n"
        "    incidents = store.incidents()\n"
        "    return {'metrics': build_dashboard_metrics(services, incidents), 'events': build_event_timeline(services, incidents)}\n\n\n"
        "router = APIRouter(prefix='/operations') if APIRouter is not None else None\n"
        "if router is not None:\n"
        "    @router.post('/services')\n"
        "    def register_service_endpoint(payload: ServiceCreate) -> dict[str, object]:\n"
        "        try:\n"
        "            return register_service_response({'name': payload.name, 'owner': payload.owner, 'tier': payload.tier, 'uptime': payload.uptime})\n"
        "        except ValueError as exc:\n"
        "            raise HTTPException(status_code=400, detail=str(exc)) from exc\n\n"
        "    @router.post('/incidents')\n"
        "    def record_incident_endpoint(payload: IncidentCreate) -> dict[str, object]:\n"
        "        try:\n"
        "            return record_incident_response({'service_id': payload.service_id, 'title': payload.title, 'severity': payload.severity})\n"
        "        except (KeyError, ValueError) as exc:\n"
        "            raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 400, detail=str(exc)) from exc\n\n"
        "    @router.post('/incidents/{incident_id}/resolve')\n"
        "    def resolve_incident_endpoint(incident_id: int) -> dict[str, object]:\n"
        "        try:\n"
        "            return resolve_incident_response(incident_id)\n"
        "        except KeyError as exc:\n"
        "            raise HTTPException(status_code=404, detail='incident not found') from exc\n\n"
        "    @router.get('/dashboard')\n"
        "    def dashboard_endpoint() -> dict[str, object]:\n"
        "        return dashboard_response()\n"
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
        f"    app = FastAPI(title='{project_name} Operations Dashboard API')\n\n"
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
        "from app.domain import record_incident, register_service, resolve_incident\n"
        "from app.events import build_event_timeline\n"
        "from app.metrics import build_dashboard_metrics, service_health_score\n"
        "from app.routes import dashboard_response, record_incident_response, register_service_response, resolve_incident_response\n"
        "from app.store import OperationsStore\n\n\n"
        "class OperationsDashboardWorkflowTests(unittest.TestCase):\n"
        "    def test_domain_service_incident_lifecycle(self):\n"
        "        service = register_service(1, ' API ', 'Ops', 'critical', 98.5)\n"
        "        incident = record_incident(1, service.id, ' Latency spike ', 3)\n"
        "        resolved = resolve_incident(incident)\n"
        "        self.assertEqual(service.name, 'API')\n"
        "        self.assertEqual(incident.status, 'open')\n"
        "        self.assertEqual(resolved.status, 'resolved')\n\n"
        "    def test_store_metrics_and_filters_workflow(self):\n"
        "        store = OperationsStore()\n"
        "        api = store.register('API', 'Ops', 'critical', 98.5)\n"
        "        web = store.register('Web', 'Product', 'standard', 99.9)\n"
        "        incident = store.open_incident(api.id, 'Latency spike', 3)\n"
        "        metrics = build_dashboard_metrics(store.services(), store.incidents())\n"
        "        self.assertEqual([service.name for service in store.services(owner='Ops')], ['API'])\n"
        "        self.assertEqual(store.incidents(status='open'), [incident])\n"
        "        self.assertLess(service_health_score(api, store.incidents()), service_health_score(web, store.incidents()))\n"
        "        self.assertEqual(metrics['open_incident_count'], 1)\n\n"
        "    def test_events_and_route_adapters_workflow(self):\n"
        "        store = OperationsStore()\n"
        "        service = register_service_response({'name': 'Search', 'owner': 'Platform', 'tier': 'critical', 'uptime': 99.0}, store)\n"
        "        incident = record_incident_response({'service_id': service['id'], 'title': 'Error budget burn', 'severity': 4}, store)\n"
        "        resolved = resolve_incident_response(incident['id'], store)\n"
        "        dashboard = dashboard_response(store)\n"
        "        timeline = build_event_timeline(store.services(), store.incidents())\n"
        "        self.assertEqual(resolved['status'], 'resolved')\n"
        "        self.assertEqual(dashboard['metrics']['service_count'], 1)\n"
        "        self.assertEqual(timeline[0]['type'], 'service_registered')\n"
        "        self.assertEqual(timeline[-1]['type'], 'incident_resolved')\n"
    )
    readme = (
        f"# {project_name}\n\nA long-form FastAPI operations dashboard with service registry, incident workflow, metrics, timeline events, route adapters, and workflow tests.\n\n"
        "## Run\n\n```bash\nuvicorn app.main:app --reload\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n\n"
        "```bash\npython -m py_compile app/main.py app/domain.py app/store.py app/metrics.py app/events.py app/routes.py\n```\n"
    )
    rows = replace_project_file(files, "app/domain.py", domain)
    rows = replace_project_file(rows, "app/store.py", store)
    rows = replace_project_file(rows, "app/metrics.py", metrics)
    rows = replace_project_file(rows, "app/events.py", events)
    rows = replace_project_file(rows, "app/routes.py", routes)
    rows = replace_project_file(rows, "app/main.py", main)
    rows = replace_project_file(rows, "tests/test_operations_dashboard.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def fastapi_operations_dashboard_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "app.domain",
            "path": "app/domain.py",
            "responsibility": "operations dashboard service and incident domain model",
            "requirements": ["register services", "record incidents", "resolve incidents", "validate service and incident inputs"],
        },
        {
            "module": "app.store",
            "path": "app/store.py",
            "responsibility": "operations dashboard in-memory repository",
            "requirements": ["store services", "filter services", "store incidents", "filter incidents", "resolve stored incidents"],
        },
        {
            "module": "app.metrics",
            "path": "app/metrics.py",
            "responsibility": "operations dashboard health metrics",
            "requirements": ["compute service health score", "count open incidents", "count critical services", "build health by service"],
        },
        {
            "module": "app.events",
            "path": "app/events.py",
            "responsibility": "operations dashboard event timeline",
            "requirements": ["build service events", "build incident events", "order event timeline"],
        },
        {
            "module": "app.routes",
            "path": "app/routes.py",
            "responsibility": "operations dashboard FastAPI route adapters",
            "requirements": ["register service route response", "record incident route response", "resolve incident route response", "dashboard route response"],
        },
        {
            "module": "app.main",
            "path": "app/main.py",
            "responsibility": "operations dashboard FastAPI app assembly",
            "requirements": ["expose health", "include operations router when FastAPI is installed"],
        },
        {
            "module": "tests.test_operations_dashboard",
            "path": "tests/test_operations_dashboard.py",
            "responsibility": "operations dashboard long-form workflow verification",
            "requirements": ["prove domain lifecycle", "prove store metrics workflow", "prove route and event workflow"],
        },
    ]
