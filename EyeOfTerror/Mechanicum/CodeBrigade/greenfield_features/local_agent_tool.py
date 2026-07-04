from __future__ import annotations

from typing import Any

from .common import replace_project_file


def apply_local_agent_command_router_feature(project_name: str, files: list[Any]) -> list[Any]:
    package = project_name.replace("-", "_")
    registry = (
        "from collections.abc import Callable\n"
        "from typing import Any\n\n\n"
        "ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]\n\n\n"
        "def _status(_payload: dict[str, Any]) -> dict[str, Any]:\n"
        "    return {'capabilities': sorted(ACTION_REGISTRY)}\n\n\n"
        "def _echo(payload: dict[str, Any]) -> dict[str, Any]:\n"
        "    message = str(payload.get('message', '')).strip()\n"
        "    if not message:\n"
        "        raise ValueError('message is required')\n"
        "    return {'message': message}\n\n\n"
        "def _summarize(payload: dict[str, Any]) -> dict[str, Any]:\n"
        "    text = str(payload.get('text', '')).strip()\n"
        "    if not text:\n"
        "        raise ValueError('text is required')\n"
        "    words = [word for word in text.split() if word]\n"
        "    return {'characters': len(text), 'words': len(words), 'preview': text[:80]}\n\n\n"
        "def _history(payload: dict[str, Any]) -> dict[str, Any]:\n"
        "    rows = payload.get('history', [])\n"
        "    if not isinstance(rows, list):\n"
        "        raise TypeError('history must be a list')\n"
        "    return {'events': len(rows), 'actions': [str(row.get('action', '')) for row in rows if isinstance(row, dict)]}\n\n\n"
        "ACTION_REGISTRY: dict[str, ToolHandler] = {\n"
        "    'status': _status,\n"
        "    'echo': _echo,\n"
        "    'summarize': _summarize,\n"
        "    'history': _history,\n"
        "}\n\n\n"
        "def available_actions() -> list[str]:\n"
        "    return sorted(ACTION_REGISTRY)\n\n\n"
        "def get_action_handler(action: str) -> ToolHandler:\n"
        "    clean_action = action.strip().lower()\n"
        "    if clean_action not in ACTION_REGISTRY:\n"
        "        raise ValueError(f'unsupported action: {action}')\n"
        "    return ACTION_REGISTRY[clean_action]\n"
    )
    schema = (
        "from typing import Any\n\n"
        "from .registry import available_actions\n\n\n"
        "REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {\n"
        "    'echo': ('message',),\n"
        "    'summarize': ('text',),\n"
        "}\n\n\n"
        "def normalize_action(action: str) -> str:\n"
        "    clean_action = str(action).strip().lower()\n"
        "    if clean_action not in available_actions():\n"
        "        raise ValueError(f'unsupported action: {action}')\n"
        "    return clean_action\n\n\n"
        "def validate_payload(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:\n"
        "    if payload is None:\n"
        "        clean_payload: dict[str, Any] = {}\n"
        "    else:\n"
        "        clean_payload = payload\n"
        "    if not isinstance(clean_payload, dict):\n"
        "        raise TypeError('payload must be a JSON object')\n"
        "    clean_action = normalize_action(action)\n"
        "    for field in REQUIRED_FIELDS.get(clean_action, ()):\n"
        "        if not str(clean_payload.get(field, '')).strip():\n"
        "            raise ValueError(f'{field} is required')\n"
        "    return clean_payload\n"
    )
    session = (
        "from dataclasses import dataclass, field\n"
        "from typing import Any\n\n\n"
        "@dataclass\n"
        "class AgentSession:\n"
        "    history: list[dict[str, Any]] = field(default_factory=list)\n\n"
        "    def record_action(self, action: str, payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:\n"
        "        event = {'action': action, 'payload': payload, 'result': result}\n"
        "        self.history.append(event)\n"
        "        return event\n\n"
        "    def summarize_history(self) -> dict[str, Any]:\n"
        "        return {'events': len(self.history), 'actions': [event['action'] for event in self.history]}\n"
    )
    runner = (
        "from typing import Any\n\n"
        "from .registry import get_action_handler\n"
        "from .schema import normalize_action, validate_payload\n"
        "from .session import AgentSession\n\n\n"
        "def run_action(action: str, payload: dict[str, Any] | None = None, session: AgentSession | None = None) -> dict[str, Any]:\n"
        "    clean_action = normalize_action(action)\n"
        "    clean_payload = validate_payload(clean_action, payload)\n"
        "    if clean_action == 'history' and session is not None:\n"
        "        clean_payload = {**clean_payload, 'history': session.history}\n"
        "    result = get_action_handler(clean_action)(clean_payload)\n"
        "    envelope = {'status': 'ok', 'action': clean_action, 'result': result}\n"
        "    if session is not None and clean_action != 'history':\n"
        "        session.record_action(clean_action, clean_payload, result)\n"
        "        envelope['session'] = session.summarize_history()\n"
        "    return envelope\n\n\n"
        "def run_sequence(commands: list[dict[str, Any]]) -> dict[str, Any]:\n"
        "    session = AgentSession()\n"
        "    outputs = []\n"
        "    for command in commands:\n"
        "        outputs.append(run_action(str(command.get('action', 'status')), command.get('payload', {}), session=session))\n"
        "    return {'status': 'ok', 'events': session.summarize_history(), 'outputs': outputs}\n"
    )
    contract = (
        "from typing import Any\n\n"
        "from .registry import ACTION_REGISTRY, available_actions\n"
        "from .runner import run_action, run_sequence\n\n"
        "__all__ = ['ACTION_REGISTRY', 'available_actions', 'build_tool_result', 'run_action', 'run_sequence']\n\n\n"
        "def build_tool_result(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:\n"
        "    envelope = run_action(action, payload)\n"
        "    result = dict(envelope['result'])\n"
        "    result.setdefault('status', envelope['status'])\n"
        "    return result\n"
    )
    tool = (
        "import argparse\n"
        "import json\n"
        "import sys\n\n"
        "from .registry import available_actions\n"
        "from .runner import run_action, run_sequence\n\n\n"
        "def _load_payload(raw_payload: str) -> dict[str, object]:\n"
        "    try:\n"
        "        payload = json.loads(raw_payload)\n"
        "    except json.JSONDecodeError as exc:\n"
        "        raise SystemExit(f'invalid JSON payload: {exc}') from exc\n"
        "    if not isinstance(payload, dict):\n"
        "        raise SystemExit('payload must be a JSON object')\n"
        "    return payload\n\n\n"
        "def _load_sequence(raw_sequence: str) -> list[dict[str, object]]:\n"
        "    try:\n"
        "        sequence = json.loads(raw_sequence)\n"
        "    except json.JSONDecodeError as exc:\n"
        "        raise SystemExit(f'invalid JSON sequence: {exc}') from exc\n"
        "    if not isinstance(sequence, list) or not all(isinstance(row, dict) for row in sequence):\n"
        "        raise SystemExit('sequence must be a JSON array of command objects')\n"
        "    return sequence\n\n\n"
        "def build_parser() -> argparse.ArgumentParser:\n"
        "    parser = argparse.ArgumentParser(description='Run a local agent tool action')\n"
        "    parser.add_argument('action', nargs='?', default='status', choices=available_actions())\n"
        "    parser.add_argument('--payload', default='{}', help='JSON object payload for the selected action')\n"
        "    parser.add_argument('--sequence', default='', help='JSON array of command objects to run in one session')\n"
        "    return parser\n\n\n"
        "def main(argv: list[str] | None = None) -> None:\n"
        "    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)\n"
        "    if args.sequence:\n"
        "        result = run_sequence(_load_sequence(args.sequence))\n"
        "    else:\n"
        "        result = run_action(args.action, _load_payload(args.payload))\n"
        "    print(json.dumps(result, ensure_ascii=False, sort_keys=True))\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    tests = (
        f"import json\nimport unittest\n\nfrom {package}.contract import available_actions, build_tool_result, run_action, run_sequence\nfrom {package}.session import AgentSession\nfrom {package}.tool import main\n\n\n"
        "class LocalAgentToolTests(unittest.TestCase):\n"
        "    def test_status_reports_capabilities(self):\n"
        "        result = build_tool_result('status')\n"
        "        self.assertEqual(result['status'], 'ok')\n"
        "        self.assertEqual(result['capabilities'], ['echo', 'history', 'status', 'summarize'])\n"
        "        self.assertEqual(available_actions(), result['capabilities'])\n\n"
        "    def test_echo_validates_payload(self):\n"
        "        self.assertEqual(build_tool_result('echo', {'message': ' ping '})['message'], 'ping')\n"
        "        with self.assertRaises(ValueError):\n"
        "            build_tool_result('echo', {})\n\n"
        "    def test_summarize_counts_text(self):\n"
        "        result = build_tool_result('summarize', {'text': 'alpha beta gamma'})\n"
        "        self.assertEqual(result['words'], 3)\n"
        "        self.assertEqual(result['characters'], len('alpha beta gamma'))\n\n"
        "    def test_unknown_action_is_rejected(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            build_tool_result('delete_everything', {})\n\n"
        "    def test_session_records_cross_command_workflow(self):\n"
        "        session = AgentSession()\n"
        "        first = run_action('echo', {'message': 'alpha'}, session=session)\n"
        "        second = run_action('summarize', {'text': 'alpha beta'}, session=session)\n"
        "        history = run_action('history', {}, session=session)\n"
        "        self.assertEqual(first['session']['events'], 1)\n"
        "        self.assertEqual(second['session']['actions'], ['echo', 'summarize'])\n"
        "        self.assertEqual(history['result']['actions'], ['echo', 'summarize'])\n\n"
        "    def test_sequence_runner_preserves_session_order(self):\n"
        "        result = run_sequence([\n"
        "            {'action': 'echo', 'payload': {'message': 'one'}},\n"
        "            {'action': 'summarize', 'payload': {'text': 'one two three'}},\n"
        "        ])\n"
        "        self.assertEqual(result['events']['actions'], ['echo', 'summarize'])\n"
        "        self.assertEqual(result['outputs'][1]['result']['words'], 3)\n\n"
        "    def test_cli_prints_json(self):\n"
        "        from io import StringIO\n"
        "        import contextlib\n\n"
        "        output = StringIO()\n"
        "        with contextlib.redirect_stdout(output):\n"
        "            main(['echo', '--payload', json.dumps({'message': 'hello'})])\n"
        "        self.assertEqual(json.loads(output.getvalue())['result']['message'], 'hello')\n\n"
        "    def test_cli_runs_sequence_json(self):\n"
        "        from io import StringIO\n"
        "        import contextlib\n\n"
        "        output = StringIO()\n"
        "        sequence = json.dumps([{'action': 'echo', 'payload': {'message': 'hello'}}, {'action': 'summarize', 'payload': {'text': 'hello world'}}])\n"
        "        with contextlib.redirect_stdout(output):\n"
        "            main(['--sequence', sequence])\n"
        "        self.assertEqual(json.loads(output.getvalue())['events']['actions'], ['echo', 'summarize'])\n"
    )
    readme = (
        f"# {project_name}\n\nA local agent tool with a registry, payload schema validation, session journal, command runner, and structured JSON output.\n\n"
        "## Run\n\n```bash\npython -m "
        f"{package}.tool status\n```\n\n"
        "```bash\npython -m "
        f"{package}.tool echo --payload '{{\"message\":\"hello\"}}'\n```\n\n"
        "```bash\npython -m "
        f"{package}.tool --sequence '[{{\"action\":\"echo\",\"payload\":{{\"message\":\"hello\"}}}},{{\"action\":\"summarize\",\"payload\":{{\"text\":\"hello world\"}}}}]'\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, f"{package}/registry.py", registry)
    rows = replace_project_file(rows, f"{package}/schema.py", schema)
    rows = replace_project_file(rows, f"{package}/session.py", session)
    rows = replace_project_file(rows, f"{package}/runner.py", runner)
    rows = replace_project_file(rows, f"{package}/contract.py", contract)
    rows = replace_project_file(rows, f"{package}/tool.py", tool)
    rows = replace_project_file(rows, "tests/test_contract.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def local_agent_command_router_module_contracts(project_name: str) -> list[dict[str, Any]]:
    package = project_name.replace("-", "_")
    return [
        {
            "module": f"{package}.registry",
            "path": f"{package}/registry.py",
            "responsibility": "local agent action registry and action handlers",
            "requirements": ["list available actions", "route status action", "route echo action", "route summarize action", "route history action", "reject unknown actions"],
        },
        {
            "module": f"{package}.schema",
            "path": f"{package}/schema.py",
            "responsibility": "local agent action and payload schema validation",
            "requirements": ["normalize actions", "reject unsupported actions", "reject invalid payloads", "require action fields"],
        },
        {
            "module": f"{package}.session",
            "path": f"{package}/session.py",
            "responsibility": "local agent session journal",
            "requirements": ["record action events", "summarize session history"],
        },
        {
            "module": f"{package}.runner",
            "path": f"{package}/runner.py",
            "responsibility": "local agent command execution workflow",
            "requirements": ["run single action", "run command sequence", "preserve session order", "return structured envelopes"],
        },
        {
            "module": f"{package}.contract",
            "path": f"{package}/contract.py",
            "responsibility": "compatibility facade for local agent tool APIs",
            "requirements": ["export registry actions", "export action runner", "preserve build_tool_result compatibility"],
        },
        {
            "module": f"{package}.tool",
            "path": f"{package}/tool.py",
            "responsibility": "JSON command-line interface for local agent tool actions",
            "requirements": ["parse action", "parse JSON payload", "parse JSON command sequence", "print structured JSON result"],
        },
        {
            "module": "tests.test_contract",
            "path": "tests/test_contract.py",
            "responsibility": "local agent tool behavior and workflow verification",
            "requirements": ["prove routed actions", "prove validation failures", "prove session workflow", "prove sequence CLI JSON output"],
        },
    ]
