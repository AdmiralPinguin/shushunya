from __future__ import annotations

from typing import Any

from .common import replace_project_file


def apply_python_cli_calculator_feature(project_name: str, files: list[Any]) -> list[Any]:
    package = project_name.replace("-", "_")
    core = (
        "def calculate(left: float, operator: str, right: float) -> float:\n"
        "    if operator == 'add':\n"
        "        return left + right\n"
        "    if operator == 'subtract':\n"
        "        return left - right\n"
        "    if operator == 'multiply':\n"
        "        return left * right\n"
        "    if operator == 'divide':\n"
        "        if right == 0:\n"
        "            raise ValueError('division by zero')\n"
        "        return left / right\n"
        "    raise ValueError(f'unsupported operator: {operator}')\n\n\n"
        "def run() -> str:\n"
        "    return 'calculator ready'\n"
    )
    cli = (
        "import argparse\n\n"
        "from .core import calculate\n\n\n"
        "def build_parser() -> argparse.ArgumentParser:\n"
        "    parser = argparse.ArgumentParser(description='Run a basic calculator operation')\n"
        "    parser.add_argument('operator', choices=['add', 'subtract', 'multiply', 'divide'])\n"
        "    parser.add_argument('left', type=float)\n"
        "    parser.add_argument('right', type=float)\n"
        "    return parser\n\n\n"
        "def main(argv: list[str] | None = None) -> None:\n"
        "    args = build_parser().parse_args(argv)\n"
        "    print(calculate(args.left, args.operator, args.right))\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    tests = (
        f"import unittest\n\nfrom {package}.core import calculate, run\n\n\n"
        "class CalculatorTests(unittest.TestCase):\n"
        "    def test_operations(self):\n"
        "        self.assertEqual(calculate(2, 'add', 3), 5)\n"
        "        self.assertEqual(calculate(5, 'subtract', 2), 3)\n"
        "        self.assertEqual(calculate(4, 'multiply', 3), 12)\n"
        "        self.assertEqual(calculate(8, 'divide', 2), 4)\n\n"
        "    def test_division_by_zero_is_rejected(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            calculate(1, 'divide', 0)\n\n"
        "    def test_run_status(self):\n"
        "        self.assertEqual(run(), 'calculator ready')\n"
    )
    readme = (
        f"# {project_name}\n\n## Run\n\n```bash\npython -m {package}.cli add 2 3\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n\n"
        "```bash\npython -m py_compile "
        f"{package}/__init__.py {package}/registry.py {package}/schema.py {package}/session.py {package}/runner.py {package}/contract.py {package}/tool.py\n```\n"
    )
    rows = replace_project_file(files, f"{package}/core.py", core)
    rows = replace_project_file(rows, f"{package}/cli.py", cli)
    rows = replace_project_file(rows, "tests/test_core.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def calculator_module_contracts(project_name: str) -> list[dict[str, Any]]:
    package = project_name.replace("-", "_")
    return [
        {
            "module": f"{package}.core",
            "path": f"{package}/core.py",
            "responsibility": "calculator arithmetic behavior",
            "requirements": ["add numbers", "subtract numbers", "multiply numbers", "divide numbers", "reject division by zero"],
        },
        {
            "module": f"{package}.cli",
            "path": f"{package}/cli.py",
            "responsibility": "command-line calculator entrypoint",
            "requirements": ["parse operator and operands", "print calculated result"],
        },
        {
            "module": "tests.test_core",
            "path": "tests/test_core.py",
            "responsibility": "calculator behavior verification",
            "requirements": ["prove arithmetic operations", "prove division by zero rejection"],
        },
    ]
