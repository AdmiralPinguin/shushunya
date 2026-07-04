#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


CODE_BRIGADE_ROOT = Path(__file__).resolve().parent
MECHANICUM_ROOT = CODE_BRIGADE_ROOT.parent
EYE_ROOT = MECHANICUM_ROOT.parent
PROJECT_ROOT = EYE_ROOT.parent
for path in reversed((CODE_BRIGADE_ROOT, PROJECT_ROOT)):
    path_text = str(path)
    while path_text in sys.path:
        sys.path.remove(path_text)
    sys.path.insert(0, path_text)

from EyeOfTerror.model_brain import model_settings  # noqa: E402
from greenfield_live_trial import allocate_live_trial_root  # noqa: E402
from greenfield_project import build_greenfield_project_brief  # noqa: E402
from greenfield_verification_worker import run_greenfield_verification_loop  # noqa: E402


DEFAULT_RUN_ROOT = EYE_ROOT / "live_runs" / "greenfield_repair_trials"


def scenario_spec(scenario: str) -> dict[str, Any]:
    if scenario == "return_expression":
        return {
            "task": "Repair a generated math module so add(left, right) returns the sum required by the tests.",
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "calc.py", "content": "def add(left, right):\n    return left - right\n"},
                {"path": "test_calc.py", "content": "import unittest\nimport calc\n\nclass CalcTests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n"},
            ],
            "verification_commands": ["python -m unittest test_calc.py"],
            "module_contracts": [{"module": "calc", "path": "calc.py", "responsibility": "add values", "requirements": ["add values"]}],
        }
    if scenario == "constant":
        return {
            "task": "Repair a generated settings module so enabled() returns the enabled feature flag required by the tests.",
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "settings.py", "content": "FEATURE_ENABLED = False\n\n\ndef enabled():\n    return FEATURE_ENABLED\n"},
                {"path": "test_settings.py", "content": "import unittest\nimport settings\n\nclass SettingsTests(unittest.TestCase):\n    def test_enabled(self):\n        self.assertTrue(settings.enabled())\n"},
            ],
            "verification_commands": ["python -m unittest test_settings.py"],
            "module_contracts": [{"module": "settings", "path": "settings.py", "responsibility": "return enabled feature flag", "requirements": ["return enabled feature flag"]}],
        }
    if scenario == "function_body":
        return {
            "task": "Repair a generated grading module by replacing the grade() function body so negative scores raise ValueError, grade(95) returns A, grade(85) returns B, and grade(70) returns C.",
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "grades.py", "content": "def grade(score):\n    return 'C'\n"},
                {"path": "test_grades.py", "content": "import unittest\nimport grades\n\nclass GradeTests(unittest.TestCase):\n    def test_grade_bands(self):\n        self.assertEqual(grades.grade(95), 'A')\n        self.assertEqual(grades.grade(85), 'B')\n        self.assertEqual(grades.grade(70), 'C')\n\n    def test_negative_score_is_rejected(self):\n        with self.assertRaises(ValueError):\n            grades.grade(-1)\n"},
            ],
            "verification_commands": ["python -m unittest test_grades.py"],
            "module_contracts": [{"module": "grades", "path": "grades.py", "responsibility": "grade score bands and reject invalid scores", "requirements": ["grade A/B/C bands", "raise ValueError for negative scores"]}],
        }
    if scenario == "multi_file":
        return {
            "task": "Repair a generated invoicing package so pricing applies tier discounts and invoice totals include tax and formatted summaries.",
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "billing/__init__.py", "content": "from .invoice import build_invoice\nfrom .pricing import discounted_subtotal\n\n__all__ = ['build_invoice', 'discounted_subtotal']\n"},
                {"path": "billing/pricing.py", "content": "def discounted_subtotal(items):\n    return sum(item['price'] * item['quantity'] for item in items)\n"},
                {"path": "billing/invoice.py", "content": "from .pricing import discounted_subtotal\n\n\ndef build_invoice(customer, items, tax_rate=0.1):\n    subtotal = discounted_subtotal(items)\n    return {'customer': customer, 'subtotal': subtotal, 'total': subtotal}\n"},
                {
                    "path": "tests/test_invoice.py",
                    "content": (
                        "import unittest\n\nfrom billing.invoice import build_invoice\nfrom billing.pricing import discounted_subtotal\n\n\n"
                        "ITEMS = [\n"
                        "    {'name': 'servo', 'price': 100, 'quantity': 2},\n"
                        "    {'name': 'cable', 'price': 50, 'quantity': 1},\n"
                        "]\n\n\n"
                        "class InvoiceTests(unittest.TestCase):\n"
                        "    def test_discounted_subtotal(self):\n"
                        "        self.assertEqual(discounted_subtotal(ITEMS), 225)\n\n"
                        "    def test_invoice_total_and_summary(self):\n"
                        "        invoice = build_invoice('Forge', ITEMS, tax_rate=0.2)\n"
                        "        self.assertEqual(invoice['subtotal'], 225)\n"
                        "        self.assertEqual(invoice['tax'], 45)\n"
                        "        self.assertEqual(invoice['total'], 270)\n"
                        "        self.assertEqual(invoice['summary'], 'Forge: 2 items, total 270.00')\n"
                    ),
                },
            ],
            "verification_commands": ["python -m unittest discover tests"],
            "module_contracts": [
                {"module": "billing.pricing", "path": "billing/pricing.py", "responsibility": "apply tier discount to item subtotal", "requirements": ["apply 10 percent discount when raw subtotal is at least 200"]},
                {"module": "billing.invoice", "path": "billing/invoice.py", "responsibility": "build invoice totals and summary", "requirements": ["include tax", "include total", "include formatted summary"]},
                {"module": "tests.test_invoice", "path": "tests/test_invoice.py", "responsibility": "invoice workflow verification", "requirements": ["prove discount", "prove tax and summary"]},
            ],
        }
    if scenario == "agent_router_multi_file":
        return {
            "task": "Repair a generated command dispatcher `agent-router` so it validates JSON payloads, routes status/echo/summarize actions, rejects unknown actions, records session history, and runs command sequences.",
            "project_name": "agent-router",
            "max_cycles": 4,
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "agent_router/__init__.py", "content": ""},
                {"path": "agent_router/registry.py", "content": "ACTION_REGISTRY = {'status': lambda payload: {'status': 'ready'}}\n\n\ndef available_actions():\n    return ['status']\n"},
                {"path": "agent_router/schema.py", "content": "def validate_payload(payload):\n    return payload or {}\n"},
                {"path": "agent_router/session.py", "content": "class AgentSession:\n    def __init__(self):\n        self.history = []\n"},
                {"path": "agent_router/runner.py", "content": "from .registry import ACTION_REGISTRY\nfrom .schema import validate_payload\n\n\ndef run_action(action='status', payload=None, session=None):\n    data = validate_payload(payload)\n    return ACTION_REGISTRY[action](data)\n"},
                {"path": "agent_router/contract.py", "content": "from .runner import run_action\n\n\ndef build_tool_result(action='status', payload=None):\n    return run_action(action, payload)\n"},
                {"path": "agent_router/tool.py", "content": "from .contract import build_tool_result\n\n\ndef main():\n    print(build_tool_result('status'))\n\n\nif __name__ == '__main__':\n    main()\n"},
                {
                    "path": "tests/test_agent_router.py",
                    "content": (
                        "import unittest\n\n"
                        "from agent_router.contract import build_tool_result\n"
                        "from agent_router.runner import run_action, run_sequence\n"
                        "from agent_router.session import AgentSession\n\n\n"
                        "class AgentRouterTests(unittest.TestCase):\n"
                        "    def test_status_echo_and_summary_actions(self):\n"
                        "        self.assertEqual(build_tool_result('status')['status'], 'ready')\n"
                        "        self.assertEqual(build_tool_result('echo', {'text': '  hello  '})['text'], 'hello')\n"
                        "        summary = build_tool_result('summarize', {'items': ['alpha', 'beta', 'gamma']})\n"
                        "        self.assertEqual(summary['count'], 3)\n"
                        "        self.assertEqual(summary['summary'], 'alpha, beta, gamma')\n\n"
                        "    def test_unknown_action_and_payload_validation(self):\n"
                        "        with self.assertRaises(ValueError):\n"
                        "            build_tool_result('missing', {})\n"
                        "        with self.assertRaises(TypeError):\n"
                        "            build_tool_result('echo', ['not', 'a', 'dict'])\n\n"
                        "    def test_session_history_and_sequence_order(self):\n"
                        "        session = AgentSession()\n"
                        "        first = run_action('echo', {'text': 'first'}, session=session)\n"
                        "        second = run_action('summarize', {'items': ['x', 'y']}, session=session)\n"
                        "        self.assertEqual(first['text'], 'first')\n"
                        "        self.assertEqual(second['count'], 2)\n"
                        "        self.assertEqual([row['action'] for row in session.history], ['echo', 'summarize'])\n"
                        "        sequence = run_sequence([\n"
                        "            {'action': 'echo', 'payload': {'text': 'one'}},\n"
                        "            {'action': 'status', 'payload': {}},\n"
                        "        ])\n"
                        "        self.assertEqual([row['action'] for row in sequence['history']], ['echo', 'status'])\n"
                        "        self.assertEqual(sequence['results'][1]['status'], 'ready')\n"
                    ),
                },
            ],
            "verification_commands": ["python -m unittest discover tests"],
            "module_contracts": [
                {"module": "agent_router.registry", "path": "agent_router/registry.py", "responsibility": "action registry and supported action listing", "requirements": ["support status action", "support echo action", "support summarize action", "reject unknown actions"]},
                {"module": "agent_router.schema", "path": "agent_router/schema.py", "responsibility": "payload validation", "requirements": ["accept dict payloads", "reject non-dict payloads"]},
                {"module": "agent_router.session", "path": "agent_router/session.py", "responsibility": "session history recording", "requirements": ["record action history in order"]},
                {"module": "agent_router.runner", "path": "agent_router/runner.py", "responsibility": "run actions and sequences", "requirements": ["route actions", "record session history", "run command sequences"]},
                {"module": "agent_router.contract", "path": "agent_router/contract.py", "responsibility": "public tool contract", "requirements": ["build tool results for named actions"]},
                {"module": "tests.test_agent_router", "path": "tests/test_agent_router.py", "responsibility": "agent router workflow verification", "requirements": ["prove action routing", "prove payload validation", "prove session sequence history"]},
            ],
        }
    if scenario == "large_exact_replace":
        return {
            "task": "Repair a generated operations dashboard project by making the frontend API contract point at /api/v1 while preserving the larger project structure. The safe repair is one exact text replacement in src/config.js.",
            "project_name": "ops-dashboard-large-exact",
            "required_repair_markers": ["guided_exact_replace"],
            "forbid_module_synthesis_repair": True,
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "src/config.js", "content": "export const API_BASE = \"/api/dev\";\nexport const REFRESH_SECONDS = 30;\nexport const DASHBOARD_TITLE = \"Forge Operations\";\n"},
                {"path": "src/api.js", "content": "import { API_BASE } from './config.js';\n\nexport function endpoint(path) {\n  return `${API_BASE}${path}`;\n}\n"},
                {"path": "src/metrics.js", "content": "export function summarizeMetrics(rows) {\n  return rows.map((row) => `${row.name}:${row.value}`).join(', ');\n}\n"},
                {"path": "src/events.js", "content": "export function newestEvent(events) {\n  return events.slice().sort((a, b) => b.ts - a.ts)[0] || null;\n}\n"},
                {"path": "src/render.js", "content": "import { DASHBOARD_TITLE } from './config.js';\n\nexport function renderHeader() {\n  return `<h1>${DASHBOARD_TITLE}</h1>`;\n}\n"},
                {"path": "README.md", "content": "# Operations Dashboard\n\nRun contract checks with `python -m unittest discover tests`.\n"},
                {
                    "path": "tests/test_frontend_contract.py",
                    "content": (
                        "import unittest\nfrom pathlib import Path\n\n\n"
                        "class FrontendContractTests(unittest.TestCase):\n"
                        "    def test_api_base_uses_versioned_contract(self):\n"
                        "        config = Path('src/config.js').read_text(encoding='utf-8')\n"
                        "        self.assertIn('export const API_BASE = \"/api/v1\";', config)\n"
                        "        self.assertNotIn('/api/dev', config)\n\n"
                        "    def test_larger_project_files_are_still_present(self):\n"
                        "        for path in ['src/api.js', 'src/metrics.js', 'src/events.js', 'src/render.js']:\n"
                        "            self.assertTrue(Path(path).exists(), path)\n"
                    ),
                },
            ],
            "verification_commands": ["python -m unittest discover tests"],
            "module_contracts": [
                {"module": "src.config", "path": "src/config.js", "responsibility": "frontend runtime configuration", "requirements": ["API_BASE must be /api/v1"]},
                {"module": "src.api", "path": "src/api.js", "responsibility": "endpoint construction", "requirements": ["use API_BASE"]},
                {"module": "src.metrics", "path": "src/metrics.js", "responsibility": "dashboard metric summaries", "requirements": ["summarize metrics"]},
                {"module": "src.events", "path": "src/events.js", "responsibility": "event timeline helpers", "requirements": ["select newest event"]},
                {"module": "src.render", "path": "src/render.js", "responsibility": "render dashboard header", "requirements": ["render configured title"]},
                {"module": "tests.test_frontend_contract", "path": "tests/test_frontend_contract.py", "responsibility": "frontend contract verification", "requirements": ["prove API contract", "prove project structure remains intact"]},
            ],
        }
    if scenario == "large_function_body":
        return {
            "task": "Repair a generated incident routing service by replacing only route_incident() with complete severity/team validation, SLA selection, and escalation behavior. Preserve the larger package and tests.",
            "project_name": "incident-router-large-function",
            "required_repair_markers": ["guided_replace_function_body"],
            "forbid_module_synthesis_repair": True,
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "incident_router/__init__.py", "content": "from .routing import route_incident\n\n__all__ = ['route_incident']\n"},
                {"path": "incident_router/models.py", "content": "VALID_TEAMS = {'ops', 'security', 'platform'}\nSEVERITY_SLA = {'low': 72, 'medium': 24, 'high': 4, 'critical': 1}\n"},
                {"path": "incident_router/audit.py", "content": "def audit_row(incident_id, team, severity):\n    return {'incident_id': incident_id, 'team': team, 'severity': severity}\n"},
                {"path": "incident_router/notifications.py", "content": "def notification_channel(team):\n    return f'{team}-alerts'\n"},
                {"path": "incident_router/reporting.py", "content": "def summarize_route(route):\n    return f\"{route['incident_id']}->{route['team']}:{route['severity']}\"\n"},
                {
                    "path": "incident_router/routing.py",
                    "content": (
                        "from .models import SEVERITY_SLA, VALID_TEAMS\n"
                        "from .notifications import notification_channel\n"
                        "from .reporting import summarize_route\n\n\n"
                        "def route_incident(incident_id, severity, team):\n"
                        "    return {'incident_id': incident_id, 'severity': severity, 'team': team}\n"
                    ),
                },
                {
                    "path": "tests/test_routing.py",
                    "content": (
                        "import unittest\n\n"
                        "from incident_router.routing import route_incident\n\n\n"
                        "class IncidentRoutingTests(unittest.TestCase):\n"
                        "    def test_critical_incident_escalates_with_one_hour_sla(self):\n"
                        "        route = route_incident('INC-7', 'critical', 'security')\n"
                        "        self.assertEqual(route['sla_hours'], 1)\n"
                        "        self.assertEqual(route['channel'], 'security-alerts')\n"
                        "        self.assertTrue(route['escalate'])\n"
                        "        self.assertEqual(route['summary'], 'INC-7->security:critical')\n\n"
                        "    def test_low_incident_does_not_escalate(self):\n"
                        "        route = route_incident('INC-8', 'low', 'ops')\n"
                        "        self.assertEqual(route['sla_hours'], 72)\n"
                        "        self.assertFalse(route['escalate'])\n\n"
                        "    def test_invalid_team_and_severity_are_rejected(self):\n"
                        "        with self.assertRaises(ValueError):\n"
                        "            route_incident('INC-9', 'unknown', 'ops')\n"
                        "        with self.assertRaises(ValueError):\n"
                        "            route_incident('INC-10', 'high', 'finance')\n"
                    ),
                },
            ],
            "verification_commands": ["python -m unittest discover tests"],
            "module_contracts": [
                {"module": "incident_router.models", "path": "incident_router/models.py", "responsibility": "routing constants", "requirements": ["valid teams", "severity SLA table"]},
                {"module": "incident_router.audit", "path": "incident_router/audit.py", "responsibility": "audit row helper", "requirements": ["build incident audit rows"]},
                {"module": "incident_router.notifications", "path": "incident_router/notifications.py", "responsibility": "notification channel helper", "requirements": ["team alert channel"]},
                {"module": "incident_router.reporting", "path": "incident_router/reporting.py", "responsibility": "route summary helper", "requirements": ["summarize route"]},
                {"module": "incident_router.routing", "path": "incident_router/routing.py", "responsibility": "incident route construction", "requirements": ["validate severity", "validate team", "set SLA", "set escalation", "include channel", "include summary"]},
                {"module": "tests.test_routing", "path": "tests/test_routing.py", "responsibility": "incident routing verification", "requirements": ["prove critical escalation", "prove low severity", "prove invalid inputs"]},
            ],
        }
    if scenario == "exact_replace":
        return {
            "task": "Repair a generated demo module so value() returns ready as required by the tests.",
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "demo.py", "content": "def value():\n    return 'broken'\n"},
                {"path": "test_demo.py", "content": "import unittest\nimport demo\n\nclass DemoTests(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(demo.value(), 'ready')\n"},
            ],
            "verification_commands": ["python -m unittest test_demo.py"],
            "module_contracts": [{"module": "demo", "path": "demo.py", "responsibility": "return ready", "requirements": ["return ready"]}],
        }
    if scenario == "name_error":
        return {
            "task": "Repair a generated module by removing the stray undefined name line while preserving value().",
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "demo.py", "content": "READY = 'ready'\nstray_symbol\n\ndef value():\n    return READY\n"},
                {"path": "test_demo.py", "content": "import unittest\nimport demo\n\nclass DemoTests(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(demo.value(), 'ready')\n"},
            ],
            "verification_commands": ["python -m unittest test_demo.py"],
            "module_contracts": [{"module": "demo", "path": "demo.py", "responsibility": "return ready", "requirements": ["return ready"]}],
        }
    raise ValueError(f"unsupported repair scenario: {scenario}")


def write_project(workspace: Path, project: dict[str, Any]) -> None:
    for item in project.get("files", []):
        if not isinstance(item, dict):
            continue
        path = workspace / str(item.get("path") or "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(item.get("content") or ""), encoding="utf-8")


def build_repair_trial_project(spec: dict[str, Any]) -> dict[str, Any]:
    project = build_greenfield_project_brief(spec["task"], spec)
    files = [item for item in spec.get("files", []) if isinstance(item, dict)]
    if files:
        project["files"] = files
        project["expected_files"] = [str(item.get("path") or "") for item in files if item.get("path")]
    if isinstance(spec.get("module_contracts"), list):
        project["module_contracts"] = spec["module_contracts"]
    if isinstance(spec.get("verification_commands"), list):
        project["verification_commands"] = spec["verification_commands"]
    if isinstance(spec.get("required_repair_markers"), list):
        project["required_repair_markers"] = spec["required_repair_markers"]
    if "forbid_module_synthesis_repair" in spec:
        project["forbid_module_synthesis_repair"] = bool(spec.get("forbid_module_synthesis_repair"))
    return project


def compact_repair_result(scenario: str, workspace: Path, project: dict[str, Any], loop: dict[str, Any]) -> dict[str, Any]:
    attempts = loop.get("attempts", []) if isinstance(loop.get("attempts"), list) else []
    repair_attempts = [
        attempt.get("repair_execution")
        for attempt in attempts
        if isinstance(attempt, dict) and isinstance(attempt.get("repair_execution"), dict)
    ]
    repaired_files: list[dict[str, Any]] = []
    repair_strategies: list[str] = []
    blockers: list[str] = []
    for repair in repair_attempts:
        if not isinstance(repair, dict):
            continue
        repair_strategy = str(repair.get("repair_strategy") or "")
        if repair_strategy:
            repair_strategies.append(repair_strategy)
        repaired_files.extend(row for row in repair.get("repaired_files", []) if isinstance(row, dict))
        blockers.extend(str(item) for item in repair.get("blockers", []) if isinstance(item, str))
    bounded_repair_applied = any(
        str(row.get("repair") or "").startswith("guided_")
        for row in repaired_files
        if isinstance(row, dict)
    )
    module_synthesis_repair_applied = "module_synthesis_repair" in repair_strategies or any(
        row.get("repair") == "verification_repair_module_synthesis"
        for row in repaired_files
        if isinstance(row, dict)
    )
    repair_markers = sorted({str(row.get("repair") or "") for row in repaired_files if isinstance(row, dict) and row.get("repair")})
    required_markers = [str(marker) for marker in project.get("required_repair_markers", []) if isinstance(marker, str)]
    required_markers_satisfied = all(marker in repair_markers for marker in required_markers)
    forbidden_module_synthesis_satisfied = not (project.get("forbid_module_synthesis_repair") and module_synthesis_repair_applied)
    repaired_paths = sorted({str(row.get("path") or "") for row in repaired_files if isinstance(row, dict) and row.get("path")})
    status = "accepted" if loop.get("status") == "passed" and repaired_files and required_markers_satisfied and forbidden_module_synthesis_satisfied else "blocked"
    return {
        "kind": "code_brigade_greenfield_live_repair_trial_result",
        "contract_version": "eye-mechanicum.v1",
        "scenario": scenario,
        "status": status,
        "model_settings": model_settings(),
        "workspace": str(workspace),
        "verification_commands": project.get("verification_commands", []),
        "loop_status": str(loop.get("status") or ""),
        "stop_reason": str(loop.get("stop_reason") or ""),
        "attempt_count": len(attempts),
        "repair_attempt_count": len(repair_attempts),
        "bounded_repair_applied": bounded_repair_applied,
        "module_synthesis_repair_applied": module_synthesis_repair_applied,
        "required_repair_markers": required_markers,
        "required_repair_markers_satisfied": required_markers_satisfied,
        "forbid_module_synthesis_repair": bool(project.get("forbid_module_synthesis_repair")),
        "forbidden_module_synthesis_satisfied": forbidden_module_synthesis_satisfied,
        "multi_file_repair_applied": len(repaired_paths) > 1,
        "repaired_path_count": len(repaired_paths),
        "repaired_files": repaired_files,
        "repair_strategies": repair_strategies,
        "blockers": blockers,
        "final_verification_status": str((loop.get("final_verification") or {}).get("status") or "") if isinstance(loop.get("final_verification"), dict) else "",
        "stop_condition_evidence": loop.get("stop_condition_evidence", {}) if isinstance(loop.get("stop_condition_evidence"), dict) else {},
    }


def run_live_repair_trial(scenario: str, run_root: Path) -> dict[str, Any]:
    trial_root = allocate_live_trial_root(run_root)
    workspace = trial_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)
    spec = scenario_spec(scenario)
    project = build_repair_trial_project(spec)
    write_project(workspace, project)
    loop = run_greenfield_verification_loop(workspace, project["verification_commands"], project, max_cycles=int(spec.get("max_cycles") or 2))
    result = compact_repair_result(scenario, workspace, project, loop)
    (trial_root / "live_greenfield_repair_trial_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (trial_root / "greenfield_repair_loop.json").write_text(json.dumps(loop, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a live-model GreenfieldRepairWorker verification repair trial.")
    parser.add_argument("--scenario", choices=["name_error", "exact_replace", "return_expression", "constant", "function_body", "multi_file", "agent_router_multi_file", "large_exact_replace", "large_function_body"], default="return_expression")
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--require-accepted", action="store_true")
    args = parser.parse_args()
    result = run_live_repair_trial(args.scenario, args.run_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.require_accepted and result["status"] != "accepted":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
