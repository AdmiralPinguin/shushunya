from __future__ import annotations

"""Goal-marker patch-spec builders for the implementation role."""

import sys
from pathlib import Path

_WORKERS_ROOT = Path(__file__).resolve().parents[1]
for _p in (_WORKERS_ROOT, _WORKERS_ROOT / "OrdinatusVerifier"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from common.codewright_core import *  # noqa: F403 - shared Codewright helper surface.
from verification import *  # noqa: F403,E402 - reuses verifier diagnostics.
from verification import diagnostic_extraction_from_execution  # noqa: E402


def patch_operation_plan_items(patch_spec: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    operations = patch_spec.get("operations") if isinstance(patch_spec.get("operations"), list) else []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        op_type = str(operation.get("type") or "")
        item = {
            "type": op_type,
            "path": operation.get("path", ""),
        }
        for key in ("function_name", "old_expression", "new_expression", "old", "new"):
            if key in operation:
                item[key] = operation.get(key)
        items.append(item)
    return items


def extract_json_after_marker(text: str, marker: str) -> dict[str, Any]:
    start = text.find(marker)
    if start < 0:
        return {}
    payload_text = text[start + len(marker):].strip()
    if payload_text.startswith("```"):
        lines = payload_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if "```" in lines:
            lines = lines[:lines.index("```")]
        payload_text = "\n".join(lines).strip()
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(payload_text)
    except json.JSONDecodeError as exc:
        label = marker.rstrip(":")
        raise ValueError(f"{label} JSON is invalid: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def marker_value(text: str, marker: str) -> str:
    marker_at = text.find(marker)
    if marker_at < 0:
        return ""
    return text[marker_at + len(marker):].strip().splitlines()[0].strip()


def marker_block(text: str, marker: str) -> str:
    marker_at = text.find(marker)
    if marker_at < 0:
        return ""
    block = text[marker_at + len(marker):]
    stop_markers = [
        "\nCERAXIA_TARGET_REPO:",
        "\nCERAXIA_PATCH:",
        "\nCERAXIA_FEATURE:",
        "\nCERAXIA_INTEGRATION_CONTRACT:",
        "\nCERAXIA_PUBLIC_API_COMPAT:",
        "\nCERAXIA_CONFIG_RUNTIME:",
        "\nCERAXIA_REFACTOR:",
        "\nCERAXIA_EDGE_FIX:",
        "\nCERAXIA_DATA_MIGRATION:",
        "\nCERAXIA_FILES:",
        "\nCERAXIA_CREATE_FILE:",
        "\nCERAXIA_FILE_CONTENT:",
        "\nCERAXIA_REPLACE_IN_FILE:",
        "\nCERAXIA_OLD:",
        "\nCERAXIA_NEW:",
        "\nCERAXIA_VERIFY:",
    ]
    stop_positions = [pos for marker_item in stop_markers if (pos := block.find(marker_item)) >= 0]
    if stop_positions:
        block = block[: min(stop_positions)]
    return block.strip("\n")


def verification_commands_from_markers(goal: str) -> list[str]:
    commands: list[str] = []
    for line in goal.splitlines():
        stripped = line.strip()
        if stripped.startswith("CERAXIA_VERIFY:"):
            command = stripped.removeprefix("CERAXIA_VERIFY:").strip()
            if command:
                commands.append(command)
    return commands


def patch_spec_from_feature_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_FEATURE:")
    if not payload:
        return {}
    module_path = str(payload.get("module_path") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    docs_path = str(payload.get("docs_path") or "").strip()
    caller_path = str(payload.get("caller_path") or "").strip()
    if not module_path or not function_name or not test_path or not docs_path or not caller_path:
        raise ValueError("CERAXIA_FEATURE requires module_path, function_name, test_path, docs_path, and caller_path")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name):
        raise ValueError("CERAXIA_FEATURE function_name must be a valid Python identifier")
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in arguments):
        raise ValueError("CERAXIA_FEATURE arguments must be a non-empty list of Python identifiers")
    expression = str(payload.get("return_expression") or "").strip()
    if not expression or "\n" in expression or not re.fullmatch(r"[A-Za-z0-9_ +\-*/().]+", expression):
        raise ValueError("CERAXIA_FEATURE return_expression must be a simple arithmetic expression")
    test_cases = payload.get("test_cases")
    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("CERAXIA_FEATURE test_cases must be a non-empty list")
    rendered_cases: list[str] = []
    for index, item in enumerate(test_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_FEATURE test case {index} must be an object")
        inputs = item.get("inputs")
        expected = item.get("expected")
        if not isinstance(inputs, list) or len(inputs) != len(arguments):
            raise ValueError(f"CERAXIA_FEATURE test case {index} inputs must match arguments")
        if not all(isinstance(value, (int, float)) for value in inputs) or not isinstance(expected, (int, float)):
            raise ValueError(f"CERAXIA_FEATURE test case {index} supports only numeric inputs and expected values")
        rendered_cases.append(f"        self.assertEqual({function_name}({', '.join(str(value) for value in inputs)}), {expected})")
    module_content = f"def {function_name}({', '.join(arguments)}):\n    return {expression}\n"
    class_name = "".join(part.capitalize() for part in function_name.split("_")) + "Test"
    test_content = (
        f"import unittest\nfrom {module_path[:-3].replace('/', '.')} import {function_name}\n\n"
        f"class {class_name}(unittest.TestCase):\n"
        f"    def test_{function_name}(self):\n"
        + "\n".join(rendered_cases)
        + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    docs_title = str(payload.get("docs_title") or function_name.replace("_", " ").title())
    docs_content = f"# {docs_title}\n\nFunction `{function_name}` is available in `{module_path}` and is covered by `{test_path}`.\n"
    caller_function = str(payload.get("caller_function") or f"use_{function_name}").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", caller_function):
        raise ValueError("CERAXIA_FEATURE caller_function must be a valid Python identifier")
    caller_content = (
        f"from {module_path[:-3].replace('/', '.')} import {function_name}\n\n"
        f"def {caller_function}({', '.join(arguments)}):\n"
        f"    return {function_name}({', '.join(arguments)})\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_FEATURE verification_commands must be a list of strings")
    return {
        "source": "feature_marker_synthesis",
        "diagnostics": {
            "kind": "feature_marker_synthesis",
            "function_name": function_name,
            "module_path": module_path,
            "test_path": test_path,
            "docs_path": docs_path,
            "caller_path": caller_path,
        },
        "operations": [
            {"type": "write_file", "path": module_path, "content": module_content},
            {"type": "write_file", "path": test_path, "content": test_content},
            {"type": "write_file", "path": docs_path, "content": docs_content},
            {"type": "write_file", "path": caller_path, "content": caller_content},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_integration_contract_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_INTEGRATION_CONTRACT:")
    if not payload:
        return {}
    contract_path = str(payload.get("contract_path") or "").strip()
    implementation_path = str(payload.get("implementation_path") or "").strip()
    caller_path = str(payload.get("caller_path") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    report_path = str(payload.get("report_path") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    caller_function = str(payload.get("caller_function") or "").strip()
    response_field = str(payload.get("response_field") or "").strip()
    expression = str(payload.get("return_expression") or "").strip()
    required = [contract_path, implementation_path, caller_path, test_path, report_path, function_name, caller_function, response_field, expression]
    if not all(required):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT requires contract, implementation, caller, test, report, function, caller_function, response_field, and return_expression")
    if not implementation_path.endswith(".py") or not caller_path.endswith(".py") or not test_path.endswith(".py"):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT implementation, caller, and test paths must be Python files")
    identifiers = [function_name, caller_function, response_field]
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in identifiers):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT function and field names must be simple identifiers")
    request_fields = payload.get("request_fields")
    if not isinstance(request_fields, list) or not request_fields or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in request_fields):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT request_fields must be a non-empty list of identifiers")
    if "\n" in expression or not re.fullmatch(r"[A-Za-z0-9_ +\-*/().]+", expression):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT return_expression must be a simple arithmetic expression")
    test_cases = payload.get("test_cases")
    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT test_cases must be a non-empty list")
    contract_content = json.dumps(
        {
            "endpoint": function_name,
            "request_fields": request_fields,
            "response_fields": [response_field],
            "caller": caller_function,
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    assignments = "".join(f"    {field} = payload['{field}']\n" for field in request_fields)
    implementation_content = (
        f"def {function_name}(payload):\n"
        f"{assignments}"
        f"    return {{'{response_field}': {expression}}}\n"
    )
    implementation_module = implementation_path[:-3].replace("/", ".")
    caller_args = ", ".join(request_fields)
    caller_payload = ", ".join(f"'{field}': {field}" for field in request_fields)
    caller_content = (
        f"from {implementation_module} import {function_name}\n\n"
        f"def {caller_function}({caller_args}):\n"
        f"    return {function_name}({{{caller_payload}}})['{response_field}']\n"
    )
    rendered_cases: list[str] = []
    for index, item in enumerate(test_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_INTEGRATION_CONTRACT test case {index} must be an object")
        inputs = item.get("inputs")
        expected = item.get("expected")
        if not isinstance(inputs, dict) or set(inputs) != set(request_fields):
            raise ValueError(f"CERAXIA_INTEGRATION_CONTRACT test case {index} inputs must match request_fields")
        if not all(isinstance(inputs[field], (int, float)) for field in request_fields) or not isinstance(expected, (int, float)):
            raise ValueError(f"CERAXIA_INTEGRATION_CONTRACT test case {index} supports only numeric values")
        payload_literal = "{" + ", ".join(f"{field!r}: {inputs[field]!r}" for field in request_fields) + "}"
        args_literal = ", ".join(repr(inputs[field]) for field in request_fields)
        rendered_cases.append(f"        self.assertEqual({function_name}({payload_literal})['{response_field}'], {expected!r})")
        rendered_cases.append(f"        self.assertEqual({caller_function}({args_literal}), {expected!r})")
    caller_module = caller_path[:-3].replace("/", ".")
    class_name = "".join(part.capitalize() for part in function_name.split("_")) + "ContractTest"
    test_content = (
        f"import json\nimport unittest\nfrom pathlib import Path\nfrom {implementation_module} import {function_name}\nfrom {caller_module} import {caller_function}\n\n"
        f"class {class_name}(unittest.TestCase):\n"
        "    def test_contract_declares_response_field(self):\n"
        f"        contract = json.loads(Path('{contract_path}').read_text(encoding='utf-8'))\n"
        f"        self.assertIn('{response_field}', contract['response_fields'])\n\n"
        "    def test_implementation_and_caller_follow_contract(self):\n"
        + "\n".join(rendered_cases)
        + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    report_content = (
        "# Integration Contract Update\n\n"
        f"- Contract: `{contract_path}`\n"
        f"- Implementation: `{implementation_path}`\n"
        f"- Caller: `{caller_path}`\n"
        f"- Tests: `{test_path}`\n"
        f"- Response field: `{response_field}`\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT verification_commands must be a list of strings")
    return {
        "source": "integration_contract_marker_synthesis",
        "diagnostics": {
            "kind": "integration_contract_marker_synthesis",
            "contract_path": contract_path,
            "implementation_path": implementation_path,
            "caller_path": caller_path,
            "test_path": test_path,
            "report_path": report_path,
            "request_fields": request_fields,
            "response_field": response_field,
        },
        "operations": [
            {"type": "write_file", "path": contract_path, "content": contract_content, "overwrite": True},
            {"type": "write_file", "path": implementation_path, "content": implementation_content, "overwrite": True},
            {"type": "write_file", "path": caller_path, "content": caller_content, "overwrite": True},
            {"type": "write_file", "path": test_path, "content": test_content, "overwrite": True},
            {"type": "write_file", "path": report_path, "content": report_content, "overwrite": True},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_public_api_compat_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_PUBLIC_API_COMPAT:")
    if not payload:
        return {}
    source_path = str(payload.get("source_path") or "").strip()
    caller_path = str(payload.get("caller_path") or "").strip()
    docs_path = str(payload.get("docs_path") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    caller_function = str(payload.get("caller_function") or "").strip()
    expression = str(payload.get("return_expression") or "").strip()
    if not all([source_path, caller_path, docs_path, test_path, function_name, caller_function, expression]):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT requires source_path, caller_path, docs_path, test_path, function_name, caller_function, and return_expression")
    if not source_path.endswith(".py") or not caller_path.endswith(".py") or not test_path.endswith(".py"):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT source, caller, and test paths must be Python files")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", caller_function):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT function names must be valid identifiers")
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in arguments):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT arguments must be a non-empty list of identifiers")
    if "\n" in expression or not re.fullmatch(r"[A-Za-z0-9_ +\-*/().]+", expression):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT return_expression must be a simple arithmetic expression")
    test_cases = payload.get("test_cases")
    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT test_cases must be a non-empty list")
    signature = f"{function_name}({', '.join(arguments)})"
    source_content = (
        f"def {signature}:\n"
        f"    \"\"\"Public API: keep signature `{signature}` stable.\"\"\"\n"
        f"    return {expression}\n"
    )
    source_module = source_path[:-3].replace("/", ".")
    caller_content = (
        f"from {source_module} import {function_name}\n\n"
        f"def {caller_function}({', '.join(arguments)}):\n"
        f"    return {function_name}({', '.join(arguments)})\n"
    )
    docs_content = (
        f"# Public API Compatibility\n\n"
        f"`{signature}` is the stable public function. Callers must keep using the same positional arguments.\n"
    )
    rendered_cases: list[str] = []
    for index, item in enumerate(test_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_PUBLIC_API_COMPAT test case {index} must be an object")
        inputs = item.get("inputs")
        expected = item.get("expected")
        if not isinstance(inputs, list) or len(inputs) != len(arguments):
            raise ValueError(f"CERAXIA_PUBLIC_API_COMPAT test case {index} inputs must match arguments")
        if not all(isinstance(value, (int, float)) for value in inputs) or not isinstance(expected, (int, float)):
            raise ValueError(f"CERAXIA_PUBLIC_API_COMPAT test case {index} supports only numeric values")
        args_literal = ", ".join(repr(value) for value in inputs)
        rendered_cases.append(f"        self.assertEqual({function_name}({args_literal}), {expected!r})")
        rendered_cases.append(f"        self.assertEqual({caller_function}({args_literal}), {expected!r})")
    caller_module = caller_path[:-3].replace("/", ".")
    class_name = "".join(part.capitalize() for part in function_name.split("_")) + "CompatTest"
    test_content = (
        f"import inspect\nimport unittest\nfrom {source_module} import {function_name}\nfrom {caller_module} import {caller_function}\n\n"
        f"class {class_name}(unittest.TestCase):\n"
        "    def test_public_signature_stays_compatible(self):\n"
        f"        self.assertEqual(list(inspect.signature({function_name}).parameters), {arguments!r})\n\n"
        "    def test_behavior_and_callers(self):\n"
        + "\n".join(rendered_cases)
        + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT verification_commands must be a list of strings")
    if not any("unittest discover" in command for command in verification_commands):
        verification_commands.append("python -m unittest discover -s tests")
    return {
        "source": "public_api_compat_marker_synthesis",
        "diagnostics": {
            "kind": "public_api_compat_marker_synthesis",
            "source_path": source_path,
            "caller_path": caller_path,
            "docs_path": docs_path,
            "test_path": test_path,
            "function_name": function_name,
            "public_signature": signature,
            "caller_function": caller_function,
        },
        "operations": [
            {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True},
            {"type": "write_file", "path": caller_path, "content": caller_content, "overwrite": True},
            {"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True},
            {"type": "write_file", "path": test_path, "content": test_content, "overwrite": True},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_config_runtime_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_CONFIG_RUNTIME:")
    if not payload:
        return {}
    config_path = str(payload.get("config_path") or "").strip()
    loader_path = str(payload.get("loader_path") or "").strip()
    entrypoint_path = str(payload.get("entrypoint_path") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    setting_key = str(payload.get("setting_key") or "").strip()
    env_var = str(payload.get("env_var") or "").strip()
    default_value = payload.get("default_value")
    if not all([config_path, loader_path, entrypoint_path, test_path, setting_key, env_var]):
        raise ValueError("CERAXIA_CONFIG_RUNTIME requires config_path, loader_path, entrypoint_path, test_path, setting_key, and env_var")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", setting_key):
        raise ValueError("CERAXIA_CONFIG_RUNTIME setting_key must be a simple identifier")
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env_var):
        raise ValueError("CERAXIA_CONFIG_RUNTIME env_var must be an uppercase environment variable name")
    if not isinstance(default_value, (str, int, float, bool)):
        raise ValueError("CERAXIA_CONFIG_RUNTIME default_value must be a JSON scalar")
    config_content = json.dumps({setting_key: default_value}, ensure_ascii=False, indent=2) + "\n"
    loader_module = loader_path[:-3].replace("/", ".")
    config_literal = repr(config_path)
    loader_parent_depth = len(PurePosixPath(loader_path).parent.parts)
    config_root_steps = "\n".join(["CONFIG_ROOT = CONFIG_ROOT.parent" for _ in range(loader_parent_depth)])
    if config_root_steps:
        config_root_steps += "\n"
    loader_content = (
        "import json\n"
        "import os\n"
        "from pathlib import Path\n\n"
        "CONFIG_ROOT = Path(__file__).resolve().parent\n"
        f"{config_root_steps}"
        f"CONFIG_PATH = CONFIG_ROOT / {config_literal}\n\n"
        "def load_settings():\n"
        "    data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))\n"
        f"    value = os.environ.get('{env_var}', data.get('{setting_key}', {default_value!r}))\n"
        f"    return {{'{setting_key}': value}}\n"
    )
    entrypoint_content = (
        "#!/usr/bin/env sh\n"
        "set -eu\n"
        f"export {env_var}=\"${{{env_var}:-{default_value}}}\"\n"
        f"python -m {loader_module}\n"
    )
    test_content = (
        f"import os\nimport unittest\nfrom {loader_module} import load_settings\n\n"
        "class ConfigRuntimeTest(unittest.TestCase):\n"
        "    def test_default_setting(self):\n"
        f"        os.environ.pop('{env_var}', None)\n"
        f"        self.assertEqual(load_settings()['{setting_key}'], {default_value!r})\n\n"
        "    def test_env_override(self):\n"
        f"        os.environ['{env_var}'] = 'override-value'\n"
        "        try:\n"
        f"            self.assertEqual(load_settings()['{setting_key}'], 'override-value')\n"
        "        finally:\n"
        f"            os.environ.pop('{env_var}', None)\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_CONFIG_RUNTIME verification_commands must be a list of strings")
    return {
        "source": "config_runtime_marker_synthesis",
        "diagnostics": {
            "kind": "config_runtime_marker_synthesis",
            "config_path": config_path,
            "loader_path": loader_path,
            "entrypoint_path": entrypoint_path,
            "test_path": test_path,
            "setting_key": setting_key,
            "env_var": env_var,
        },
        "operations": [
            {"type": "write_file", "path": config_path, "content": config_content},
            {"type": "write_file", "path": loader_path, "content": loader_content},
            {"type": "write_file", "path": entrypoint_path, "content": entrypoint_content},
            {"type": "write_file", "path": test_path, "content": test_content},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_refactor_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_REFACTOR:")
    if not payload:
        return {}
    helper_path = str(payload.get("helper_path") or "").strip()
    helper_function = str(payload.get("helper_function") or "").strip()
    expression = str(payload.get("return_expression") or "").strip()
    if not helper_path or not helper_function or not expression:
        raise ValueError("CERAXIA_REFACTOR requires helper_path, helper_function, and return_expression")
    if not helper_path.endswith(".py"):
        raise ValueError("CERAXIA_REFACTOR helper_path must be a Python file")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", helper_function):
        raise ValueError("CERAXIA_REFACTOR helper_function must be a valid Python identifier")
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in arguments):
        raise ValueError("CERAXIA_REFACTOR arguments must be a non-empty list of Python identifiers")
    if "\n" in expression or not re.fullmatch(r"[A-Za-z0-9_ +\-*/().]+", expression):
        raise ValueError("CERAXIA_REFACTOR return_expression must be a simple arithmetic expression")
    replacements = payload.get("replacements")
    if not isinstance(replacements, list) or len(replacements) < 2:
        raise ValueError("CERAXIA_REFACTOR requires at least two replacements")
    operations: list[dict[str, Any]] = [
        {
            "type": "write_file",
            "path": helper_path,
            "content": f"def {helper_function}({', '.join(arguments)}):\n    return {expression}\n",
        }
    ]
    public_functions: list[str] = []
    touched_paths: list[str] = [helper_path]
    for index, item in enumerate(replacements):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_REFACTOR replacement {index} must be an object")
        path = str(item.get("path") or "").strip()
        old = item.get("old")
        new = item.get("new")
        public_function = str(item.get("public_function") or "").strip()
        if not path or not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError(f"CERAXIA_REFACTOR replacement {index} requires path, old, and new")
        if public_function and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", public_function):
            raise ValueError(f"CERAXIA_REFACTOR replacement {index} public_function must be a valid identifier")
        if public_function:
            public_functions.append(public_function)
        touched_paths.append(path)
        operations.append({"type": "replace", "path": path, "old": old, "new": new})
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = ["python -m unittest discover"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_REFACTOR verification_commands must be a list of strings")
    baseline_commands = payload.get("baseline_verification_commands", [])
    if baseline_commands is None:
        baseline_commands = []
    if not isinstance(baseline_commands, list) or not all(isinstance(item, str) for item in baseline_commands):
        raise ValueError("CERAXIA_REFACTOR baseline_verification_commands must be a list of strings")
    return {
        "source": "refactor_marker_synthesis",
        "diagnostics": {
            "kind": "refactor_marker_synthesis",
            "helper_path": helper_path,
            "helper_function": helper_function,
            "public_functions": public_functions,
            "touched_paths": touched_paths,
            "baseline_verification_commands": baseline_commands,
        },
        "operations": operations,
        "verification_commands": verification_commands,
    }


def patch_spec_from_edge_fix_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_EDGE_FIX:")
    if not payload:
        return {}
    source_path = str(payload.get("source_path") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    if not source_path or not function_name or not test_path:
        raise ValueError("CERAXIA_EDGE_FIX requires source_path, function_name, and test_path")
    if not source_path.endswith(".py") or not test_path.endswith(".py"):
        raise ValueError("CERAXIA_EDGE_FIX source_path and test_path must be Python files")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name):
        raise ValueError("CERAXIA_EDGE_FIX function_name must be a valid Python identifier")
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in arguments):
        raise ValueError("CERAXIA_EDGE_FIX arguments must be a non-empty list of Python identifiers")
    body_lines = payload.get("body_lines")
    if not isinstance(body_lines, list) or not body_lines or not all(isinstance(item, str) and item.strip() for item in body_lines):
        raise ValueError("CERAXIA_EDGE_FIX body_lines must be a non-empty list of strings")
    forbidden_body = re.compile(r"\b(import|open|exec|eval|subprocess|socket|requests)\b")
    if any(forbidden_body.search(line) for line in body_lines):
        raise ValueError("CERAXIA_EDGE_FIX body_lines contain unsafe statements")
    positive_cases = payload.get("positive_cases")
    negative_cases = payload.get("negative_cases")
    if not isinstance(positive_cases, list) or not positive_cases:
        raise ValueError("CERAXIA_EDGE_FIX positive_cases must be a non-empty list")
    if not isinstance(negative_cases, list) or not negative_cases:
        raise ValueError("CERAXIA_EDGE_FIX negative_cases must be a non-empty list")
    source_content = f"def {function_name}({', '.join(arguments)}):\n" + "".join(f"    {line}\n" for line in body_lines)
    ast.parse(source_content)
    test_module = source_path[:-3].replace("/", ".")
    class_name = "".join(part.capitalize() for part in function_name.split("_")) + "EdgeTest"
    rendered_positive: list[str] = []
    for index, item in enumerate(positive_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_EDGE_FIX positive case {index} must be an object")
        inputs = item.get("inputs")
        expected = item.get("expected")
        if not isinstance(inputs, list) or len(inputs) != len(arguments):
            raise ValueError(f"CERAXIA_EDGE_FIX positive case {index} inputs must match arguments")
        rendered_positive.append(f"        self.assertEqual({function_name}({', '.join(repr(value) for value in inputs)}), {expected!r})")
    rendered_negative: list[str] = []
    for index, item in enumerate(negative_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_EDGE_FIX negative case {index} must be an object")
        inputs = item.get("inputs")
        exception = str(item.get("exception") or "ValueError")
        if not isinstance(inputs, list) or len(inputs) != len(arguments):
            raise ValueError(f"CERAXIA_EDGE_FIX negative case {index} inputs must match arguments")
        if exception not in {"ValueError", "TypeError", "KeyError"}:
            raise ValueError(f"CERAXIA_EDGE_FIX negative case {index} uses unsupported exception")
        rendered_negative.append(
            f"        with self.assertRaises({exception}):\n"
            f"            {function_name}({', '.join(repr(value) for value in inputs)})"
        )
    test_content = (
        f"import unittest\nfrom {test_module} import {function_name}\n\n"
        f"class {class_name}(unittest.TestCase):\n"
        "    def test_positive_cases(self):\n"
        + "\n".join(rendered_positive)
        + "\n\n"
        "    def test_negative_cases(self):\n"
        + "\n".join(rendered_negative)
        + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_EDGE_FIX verification_commands must be a list of strings")
    return {
        "source": "edge_fix_marker_synthesis",
        "diagnostics": {
            "kind": "edge_fix_marker_synthesis",
            "source_path": source_path,
            "test_path": test_path,
            "function_name": function_name,
            "positive_case_count": len(positive_cases),
            "negative_case_count": len(negative_cases),
        },
        "operations": [
            {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True},
            {"type": "write_file", "path": test_path, "content": test_content, "overwrite": True},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_data_migration_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_DATA_MIGRATION:")
    if not payload:
        return {}
    source_path = str(payload.get("source_path") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    read_function = str(payload.get("read_function") or "").strip()
    write_function = str(payload.get("write_function") or "").strip()
    id_field = str(payload.get("id_field") or "").strip()
    old_field = str(payload.get("old_field") or "").strip()
    new_field = str(payload.get("new_field") or "").strip()
    if not all([source_path, test_path, read_function, write_function, id_field, old_field, new_field]):
        raise ValueError("CERAXIA_DATA_MIGRATION requires source_path, test_path, read_function, write_function, id_field, old_field, and new_field")
    if not source_path.endswith(".py") or not test_path.endswith(".py"):
        raise ValueError("CERAXIA_DATA_MIGRATION source_path and test_path must be Python files")
    identifiers = [read_function, write_function, id_field, old_field, new_field]
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in identifiers):
        raise ValueError("CERAXIA_DATA_MIGRATION function and field names must be simple identifiers")
    if old_field == new_field:
        raise ValueError("CERAXIA_DATA_MIGRATION old_field and new_field must differ")
    source_module = source_path[:-3].replace("/", ".")
    source_content = (
        f"def {read_function}(record):\n"
        f"    if '{new_field}' in record:\n"
        f"        value = record['{new_field}']\n"
        f"    elif '{old_field}' in record:\n"
        f"        value = record['{old_field}']\n"
        "    else:\n"
        f"        raise KeyError('{new_field}')\n"
        f"    return {{'{id_field}': record['{id_field}'], '{new_field}': value}}\n\n"
        f"def {write_function}(record):\n"
        f"    normalized = {read_function}(record)\n"
        f"    return {{'{id_field}': normalized['{id_field}'], '{new_field}': normalized['{new_field}']}}\n"
    )
    test_content = (
        f"import unittest\nfrom {source_module} import {read_function}, {write_function}\n\n"
        "class DataMigrationTest(unittest.TestCase):\n"
        "    def test_reads_old_shape(self):\n"
        f"        self.assertEqual({read_function}({{'{id_field}': 'a1', '{old_field}': 12}}), {{'{id_field}': 'a1', '{new_field}': 12}})\n\n"
        "    def test_reads_new_shape(self):\n"
        f"        self.assertEqual({read_function}({{'{id_field}': 'b2', '{new_field}': 20}}), {{'{id_field}': 'b2', '{new_field}': 20}})\n\n"
        "    def test_writer_emits_new_shape_only(self):\n"
        f"        self.assertEqual({write_function}({{'{id_field}': 'c3', '{old_field}': 7}}), {{'{id_field}': 'c3', '{new_field}': 7}})\n\n"
        "    def test_missing_value_is_rejected(self):\n"
        f"        with self.assertRaises(KeyError):\n"
        f"            {read_function}({{'{id_field}': 'd4'}})\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_DATA_MIGRATION verification_commands must be a list of strings")
    return {
        "source": "data_migration_marker_synthesis",
        "diagnostics": {
            "kind": "data_migration_marker_synthesis",
            "source_path": source_path,
            "test_path": test_path,
            "read_function": read_function,
            "write_function": write_function,
            "old_field": old_field,
            "new_field": new_field,
            "compatibility": "reader accepts old and new shapes; writer emits new shape",
        },
        "operations": [
            {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True},
            {"type": "write_file", "path": test_path, "content": test_content, "overwrite": True},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_multi_file_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_FILES:")
    if not payload:
        return {}
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("CERAXIA_FILES must contain a non-empty files list")
    operations: list[dict[str, Any]] = []
    planned_paths: list[str] = []
    overwrite_paths: list[str] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_FILES item {index} must be an object")
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"CERAXIA_FILES item {index} requires a non-empty string path")
        if not isinstance(content, str):
            raise ValueError(f"CERAXIA_FILES item {index} requires string content")
        operation: dict[str, Any] = {
            "type": "write_file",
            "path": path,
            "content": content,
        }
        if "overwrite" in item:
            operation["overwrite"] = bool(item.get("overwrite"))
        planned_paths.append(path)
        if operation.get("overwrite") is True:
            overwrite_paths.append(path)
        operations.append(operation)
    verification_commands = payload.get("verification_commands", [])
    if verification_commands is None:
        verification_commands = []
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_FILES verification_commands must be a list of strings")
    return {
        "source": "multi_file_marker_synthesis",
        "diagnostics": {
            "kind": "multi_file_marker_synthesis",
            "file_count": len(operations),
            "planned_paths": planned_paths,
            "overwrite_paths": overwrite_paths,
            "created_or_updated_paths": planned_paths,
        },
        "operations": operations,
        "verification_commands": verification_commands,
    }


def synthesized_patch_spec_from_markers(goal: str) -> dict[str, Any]:
    integration_contract = patch_spec_from_integration_contract_marker(goal)
    if integration_contract:
        return integration_contract
    public_api_compat = patch_spec_from_public_api_compat_marker(goal)
    if public_api_compat:
        return public_api_compat
    config_runtime = patch_spec_from_config_runtime_marker(goal)
    if config_runtime:
        return config_runtime
    refactor = patch_spec_from_refactor_marker(goal)
    if refactor:
        return refactor
    edge_fix = patch_spec_from_edge_fix_marker(goal)
    if edge_fix:
        return edge_fix
    data_migration = patch_spec_from_data_migration_marker(goal)
    if data_migration:
        return data_migration
    feature = patch_spec_from_feature_marker(goal)
    if feature:
        return feature
    multi_file = patch_spec_from_multi_file_marker(goal)
    if multi_file:
        return multi_file
    create_path = marker_value(goal, "CERAXIA_CREATE_FILE:")
    if create_path:
        content = marker_block(goal, "CERAXIA_FILE_CONTENT:")
        return {
            "source": "marker_synthesis",
            "operations": [
                {
                    "type": "write_file",
                    "path": create_path,
                    "content": content,
                }
            ],
            "verification_commands": verification_commands_from_markers(goal),
        }
    replace_path = marker_value(goal, "CERAXIA_REPLACE_IN_FILE:")
    if replace_path:
        old = marker_block(goal, "CERAXIA_OLD:")
        new = marker_block(goal, "CERAXIA_NEW:")
        return {
            "source": "marker_synthesis",
            "operations": [
                {
                    "type": "replace",
                    "path": replace_path,
                    "old": old,
                    "new": new,
                }
            ],
            "verification_commands": verification_commands_from_markers(goal),
        }
    return {}
