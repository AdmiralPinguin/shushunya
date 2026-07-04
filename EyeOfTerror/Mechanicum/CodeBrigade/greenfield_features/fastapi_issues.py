from __future__ import annotations

from typing import Any

from .common import replace_project_file


def apply_fastapi_issue_tracker_feature(project_name: str, files: list[Any]) -> list[Any]:
    domain = (
        "from dataclasses import dataclass, replace\n"
        "from typing import Literal\n\n\n"
        "IssueStatus = Literal['open', 'in_progress', 'resolved']\n\n\n"
        "@dataclass(frozen=True)\n"
        "class Issue:\n"
        "    id: int\n"
        "    title: str\n"
        "    description: str = ''\n"
        "    assignee: str = ''\n"
        "    status: IssueStatus = 'open'\n\n\n"
        "def create_issue(issue_id: int, title: str, description: str = '') -> Issue:\n"
        "    clean_title = title.strip()\n"
        "    if not clean_title:\n"
        "        raise ValueError('issue title is required')\n"
        "    return Issue(id=issue_id, title=clean_title, description=description.strip())\n\n\n"
        "def assign_issue(issue: Issue, assignee: str) -> Issue:\n"
        "    clean_assignee = assignee.strip()\n"
        "    if not clean_assignee:\n"
        "        raise ValueError('assignee is required')\n"
        "    return replace(issue, assignee=clean_assignee)\n\n\n"
        "def transition_issue(issue: Issue, status: IssueStatus) -> Issue:\n"
        "    if status not in {'open', 'in_progress', 'resolved'}:\n"
        "        raise ValueError(f'unsupported issue status: {status}')\n"
        "    return replace(issue, status=status)\n"
    )
    store = (
        "from .domain import Issue, IssueStatus, assign_issue, create_issue, transition_issue\n\n\n"
        "class IssueStore:\n"
        "    def __init__(self) -> None:\n"
        "        self._issues: dict[int, Issue] = {}\n"
        "        self._next_id = 1\n\n"
        "    def reset(self) -> None:\n"
        "        self._issues = {}\n"
        "        self._next_id = 1\n\n"
        "    def create(self, title: str, description: str = '') -> Issue:\n"
        "        issue = create_issue(self._next_id, title, description)\n"
        "        self._issues[issue.id] = issue\n"
        "        self._next_id += 1\n"
        "        return issue\n\n"
        "    def get(self, issue_id: int) -> Issue:\n"
        "        if issue_id not in self._issues:\n"
        "            raise KeyError(issue_id)\n"
        "        return self._issues[issue_id]\n\n"
        "    def list(self, *, status: IssueStatus | None = None, assignee: str | None = None) -> list[Issue]:\n"
        "        issues = list(self._issues.values())\n"
        "        if status:\n"
        "            issues = [issue for issue in issues if issue.status == status]\n"
        "        if assignee:\n"
        "            issues = [issue for issue in issues if issue.assignee == assignee]\n"
        "        return issues\n\n"
        "    def assign(self, issue_id: int, assignee: str) -> Issue:\n"
        "        issue = assign_issue(self.get(issue_id), assignee)\n"
        "        self._issues[issue.id] = issue\n"
        "        return issue\n\n"
        "    def transition(self, issue_id: int, status: IssueStatus) -> Issue:\n"
        "        issue = transition_issue(self.get(issue_id), status)\n"
        "        self._issues[issue.id] = issue\n"
        "        return issue\n\n\n"
        "STORE = IssueStore()\n"
    )
    routes = (
        "try:\n"
        "    from fastapi import APIRouter, HTTPException\n"
        "    from pydantic import BaseModel\n"
        "except ModuleNotFoundError:\n"
        "    APIRouter = None\n"
        "    HTTPException = None\n"
        "    BaseModel = object\n\n"
        "from .domain import Issue, IssueStatus\n"
        "from .store import STORE, IssueStore\n\n\n"
        "class IssueCreate(BaseModel):\n"
        "    title: str\n"
        "    description: str = ''\n\n\n"
        "class IssueAssignment(BaseModel):\n"
        "    assignee: str\n\n\n"
        "class IssueTransition(BaseModel):\n"
        "    status: IssueStatus\n\n\n"
        "def issue_to_dict(issue: Issue) -> dict[str, object]:\n"
        "    return {'id': issue.id, 'title': issue.title, 'description': issue.description, 'assignee': issue.assignee, 'status': issue.status}\n\n\n"
        "def create_issue_response(payload: dict[str, str], store: IssueStore = STORE) -> dict[str, object]:\n"
        "    return issue_to_dict(store.create(payload.get('title', ''), payload.get('description', '')))\n\n\n"
        "def list_issue_response(status: IssueStatus | None = None, assignee: str | None = None, store: IssueStore = STORE) -> list[dict[str, object]]:\n"
        "    return [issue_to_dict(issue) for issue in store.list(status=status, assignee=assignee)]\n\n\n"
        "def assign_issue_response(issue_id: int, payload: dict[str, str], store: IssueStore = STORE) -> dict[str, object]:\n"
        "    return issue_to_dict(store.assign(issue_id, payload.get('assignee', '')))\n\n\n"
        "def transition_issue_response(issue_id: int, payload: dict[str, str], store: IssueStore = STORE) -> dict[str, object]:\n"
        "    return issue_to_dict(store.transition(issue_id, payload.get('status', 'open')))\n\n\n"
        "router = APIRouter(prefix='/issues') if APIRouter is not None else None\n"
        "if router is not None:\n"
        "    @router.post('')\n"
        "    def create_issue_endpoint(payload: IssueCreate) -> dict[str, object]:\n"
        "        try:\n"
        "            return create_issue_response({'title': payload.title, 'description': payload.description})\n"
        "        except ValueError as exc:\n"
        "            raise HTTPException(status_code=400, detail=str(exc)) from exc\n\n"
        "    @router.get('')\n"
        "    def list_issues_endpoint(status: IssueStatus | None = None, assignee: str | None = None) -> list[dict[str, object]]:\n"
        "        return list_issue_response(status=status, assignee=assignee)\n\n"
        "    @router.post('/{issue_id}/assign')\n"
        "    def assign_issue_endpoint(issue_id: int, payload: IssueAssignment) -> dict[str, object]:\n"
        "        try:\n"
        "            return assign_issue_response(issue_id, {'assignee': payload.assignee})\n"
        "        except (KeyError, ValueError) as exc:\n"
        "            raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 400, detail=str(exc)) from exc\n\n"
        "    @router.post('/{issue_id}/transition')\n"
        "    def transition_issue_endpoint(issue_id: int, payload: IssueTransition) -> dict[str, object]:\n"
        "        try:\n"
        "            return transition_issue_response(issue_id, {'status': payload.status})\n"
        "        except (KeyError, ValueError) as exc:\n"
        "            raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 400, detail=str(exc)) from exc\n"
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
        f"    app = FastAPI(title='{project_name} Issue Tracker API')\n\n"
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
        "from app.domain import assign_issue, create_issue, transition_issue\n"
        "from app.routes import assign_issue_response, create_issue_response, list_issue_response, transition_issue_response\n"
        "from app.store import IssueStore\n\n\n"
        "class IssueTrackerWorkflowTests(unittest.TestCase):\n"
        "    def test_domain_create_assign_transition_workflow(self):\n"
        "        issue = create_issue(1, ' Login bug ', ' fails on mobile ')\n"
        "        self.assertEqual(issue.title, 'Login bug')\n"
        "        assigned = assign_issue(issue, 'Ahriman')\n"
        "        self.assertEqual(assigned.assignee, 'Ahriman')\n"
        "        resolved = transition_issue(assigned, 'resolved')\n"
        "        self.assertEqual(resolved.status, 'resolved')\n\n"
        "    def test_store_filtering_workflow(self):\n"
        "        store = IssueStore()\n"
        "        first = store.create('First')\n"
        "        second = store.create('Second')\n"
        "        store.assign(first.id, 'Khayon')\n"
        "        store.assign(second.id, 'Tezek')\n"
        "        store.transition(second.id, 'in_progress')\n"
        "        self.assertEqual([issue.title for issue in store.list(assignee='Khayon')], ['First'])\n"
        "        self.assertEqual([issue.title for issue in store.list(status='in_progress')], ['Second'])\n\n"
        "    def test_route_adapter_workflow(self):\n"
        "        store = IssueStore()\n"
        "        created = create_issue_response({'title': 'Route bug', 'description': 'bad status'}, store)\n"
        "        assigned = assign_issue_response(created['id'], {'assignee': 'Lheor'}, store)\n"
        "        self.assertEqual(assigned['assignee'], 'Lheor')\n"
        "        transitioned = transition_issue_response(created['id'], {'status': 'resolved'}, store)\n"
        "        self.assertEqual(transitioned['status'], 'resolved')\n"
        "        self.assertEqual(list_issue_response(status='resolved', store=store), [transitioned])\n"
    )
    readme = (
        f"# {project_name}\n\nA multi-module FastAPI issue tracker service with domain logic, in-memory store, route adapters, and workflow tests.\n\n"
        "## Run\n\n```bash\nuvicorn app.main:app --reload\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n\n"
        "```bash\npython -m py_compile app/main.py app/domain.py app/store.py app/routes.py\n```\n"
    )
    rows = replace_project_file(files, "app/domain.py", domain)
    rows = replace_project_file(rows, "app/store.py", store)
    rows = replace_project_file(rows, "app/routes.py", routes)
    rows = replace_project_file(rows, "app/main.py", main)
    rows = replace_project_file(rows, "tests/test_issue_tracker.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def fastapi_issue_tracker_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "app.domain",
            "path": "app/domain.py",
            "responsibility": "issue entity and pure transition behavior",
            "requirements": ["create issues", "assign issues", "transition issue statuses", "reject invalid titles and statuses"],
        },
        {
            "module": "app.store",
            "path": "app/store.py",
            "responsibility": "in-memory issue repository and filters",
            "requirements": ["create stored issues", "get issues by id", "filter by status", "filter by assignee", "update assignment and status"],
        },
        {
            "module": "app.routes",
            "path": "app/routes.py",
            "responsibility": "FastAPI route adapter and pure response helpers",
            "requirements": ["create issue route response", "list issue route response", "assign issue route response", "transition issue route response"],
        },
        {
            "module": "app.main",
            "path": "app/main.py",
            "responsibility": "FastAPI application assembly and health endpoint",
            "requirements": ["expose health", "include issue router when FastAPI is installed"],
        },
        {
            "module": "tests.test_issue_tracker",
            "path": "tests/test_issue_tracker.py",
            "responsibility": "issue tracker multi-workflow verification",
            "requirements": ["prove domain workflow", "prove store filtering workflow", "prove route adapter workflow"],
        },
    ]
