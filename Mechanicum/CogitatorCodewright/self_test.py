#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from cogitator_codewright import run, test_symbol_links_from_goal


def role_policy(step_id: str) -> dict:
    policies = {
        "repository_survey": {"role": "repository_mapper", "authority": "read_only_repository_mapping", "may_mutate_source": False},
        "change_planning": {"role": "change_strategist", "authority": "scoped_plan_from_repository_evidence", "may_mutate_source": False},
        "implementation": {
            "role": "patchwright",
            "authority": "scoped_source_mutation_from_patch_contract_or_safe_inference",
            "may_mutate_source": True,
        },
        "verification": {"role": "verifier", "authority": "allowlisted_verification_and_narrow_repairs", "may_mutate_source": True},
        "code_review": {"role": "critic", "authority": "read_only_package_review_and_revision_ordering", "may_mutate_source": False},
        "finalize": {"role": "final_packager", "authority": "read_only_final_manifest_packaging", "may_mutate_source": False},
    }
    return policies[step_id]


def request(step_id: str, artifact: str, *, goal: str = "почини python приложение", target_repo_root: Path | None = None) -> dict:
    payload = {
        "task_id": f"ceraxia-test:{step_id}",
        "goal": goal,
        "step": {"step_id": step_id, "expected_artifacts": [artifact]},
        "quality_expectations": {
            "step_quality": {
                "step_id": step_id,
                "role_policy": role_policy(step_id),
            }
        },
    }
    if target_repo_root is not None:
        payload["target_repo_root"] = str(target_repo_root)
    return payload


def run_pipeline(root: Path, *, goal: str = "почини python приложение", target_repo_root: Path | None = None) -> dict:
    steps = [
        ("repository_survey", "/work/code/repo_survey.json"),
        ("change_planning", "/work/code/change_plan.md"),
        ("implementation", "/work/code/patch_manifest.json"),
        ("verification", "/work/code/verification_report.json"),
        ("code_review", "/work/code/code_review.json"),
        ("finalize", "/work/code/final_manifest.json"),
    ]
    for step_id, artifact in steps:
        result = run(request(step_id, artifact, goal=goal, target_repo_root=target_repo_root), root)
        if not result.get("ok") and result.get("status") not in {"blocked", "needs_revision", "passed_with_warnings"}:
            raise AssertionError(f"{step_id} failed: {result}")
        if not (root / artifact.removeprefix("/work/")).exists():
            raise AssertionError(f"{step_id} did not write {artifact}")
    return json.loads((root / "code" / "final_manifest.json").read_text(encoding="utf-8"))


def explicit_patch_goal() -> str:
    return """почини python приложение

CERAXIA_PATCH:
{
  "operations": [
    {
      "type": "replace",
      "path": "sample.py",
      "old": "return 1",
      "new": "return 2"
    }
  ],
  "verification_commands": ["python -m py_compile sample.py"]
}
"""


def forbidden_verify_goal() -> str:
    return """почини python приложение

CERAXIA_PATCH:
{
  "operations": [
    {
      "type": "replace",
      "path": "sample.py",
      "old": "return 1",
      "new": "return 2"
    }
  ],
  "verification_commands": ["bash -lc echo-nope"]
}
"""


def inferred_replace_goal() -> str:
    return """почини python приложение: в файле `sample.py` замени `return 1` на `return 2`.
Проверь `python -m py_compile sample.py`.
"""


def inferred_add_function_goal() -> str:
    return """почини python приложение: в файле `sample.py` добавь функцию `value`, возвращающую `42`.
Проверь `python -m unittest test_sample.py`.
"""


def test_inferred_missing_function_goal() -> str:
    return "почини тест `test_sample.py`."


def test_inferred_arithmetic_goal() -> str:
    return "почини тест `test_calc.py`."


def test_inferred_delegated_arithmetic_goal() -> str:
    return "кодовая задача: python тесты падают, источник ошибки не указан. Найди причину и исправь реализацию."


def test_inferred_api_deprecation_goal() -> str:
    return """кодовая задача без structured patch marker: публичный API payments должен перейти с positional fee на preferred keyword-only service_fee, но старые positional callers должны продолжить работать с DeprecationWarning. Не угадывай: используй тесты, docs и caller evidence.
CERAXIA_VERIFY: python -m unittest tests.test_api_evolution
CERAXIA_VERIFY: python -m py_compile payments/api.py payments/client.py
"""


def test_inferred_data_migration_goal() -> str:
    return """кодовая задача без structured data migration marker: records API переходит с amount на total_amount. Выведи контракт из source, docs и tests; reader принимает old/new shape, writer emits new shape.
CERAXIA_VERIFY: python -m unittest tests.test_records_migration
CERAXIA_VERIFY: python -m py_compile service/records.py
"""


def test_inferred_security_boundary_goal() -> str:
    return """кодовая задача без structured security marker: исправь path traversal boundary. Выведи контракт из tests и docs: malicious absolute/parent paths reject, valid relative paths normalize.
CERAXIA_VERIFY: python -m unittest tests.test_archive_paths
CERAXIA_VERIFY: python -m py_compile archive_paths.py
"""


def test_inferred_cache_concurrency_goal() -> str:
    return """кодовая задача без structured concurrency marker: исправь CacheStore concurrency. Выведи контракт из tests и docs: invalidate idempotent, reload works, concurrent readers share one loaded value, sleep нельзя.
CERAXIA_VERIFY: python -m unittest tests.test_cache_store
CERAXIA_VERIFY: python -m py_compile cache_store.py
"""


def test_inferred_flaky_ordering_goal() -> str:
    return """кодовая задача без structured flaky marker: исправь intermittent ordering failure. Выведи контракт из repeated tests и docs: equal-priority items need deterministic id tie-breaker; skip/sleep запрещены.
CERAXIA_VERIFY: python -m unittest tests.test_scheduler
CERAXIA_VERIFY: python -m unittest tests.test_scheduler
CERAXIA_VERIFY: python -m py_compile scheduler.py
"""


def test_inferred_retry_policy_goal() -> str:
    return """кодовая задача без structured retry marker: исправь integration client retry behavior. Выведи контракт из tests и docs: ConnectionError retry bounded, ValueError validation не retry, sleep запрещён.
CERAXIA_VERIFY: python -m unittest tests.test_client
CERAXIA_VERIFY: python -m py_compile client.py
"""


def test_inferred_self_repair_seed_goal() -> str:
    return """кодовая expert-задача без structured patch marker: проверь self-repair discipline. Выведи цель из tests, сохрани diagnostic от первой failed verification и исправь только source по mismatch.
CERAXIA_VERIFY: python -m unittest tests.test_quota
CERAXIA_VERIFY: python -m py_compile quota.py
"""


def runtime_diagnostic_alias_goal() -> str:
    return """кодовая задача без structured patch marker: тест падает через alias import. Используй runtime diagnostic, traceback и import linkage, не редактируй тест.
CERAXIA_VERIFY: python -m unittest tests.test_quota_alias
CERAXIA_VERIFY: python -m py_compile quota.py
"""


def partial_failure_goal() -> str:
    return """проверь что частично сломанный патч не оставляет мусор

CERAXIA_PATCH:
{
  "operations": [
    {
      "type": "write_file",
      "path": "created_before_failure.py",
      "content": "def value():\\n    return 1\\n"
    },
    {
      "type": "replace",
      "path": "missing.py",
      "old": "return 1",
      "new": "return 2"
    }
  ],
  "verification_commands": ["python -m py_compile created_before_failure.py"]
}
"""


def dirty_worktree_goal() -> str:
    return """кодовая задача: не перетирай пользовательские изменения

CERAXIA_PATCH:
{
  "operations": [
    {
      "type": "replace",
      "path": "settings.py",
      "old": "return 30",
      "new": "return 60"
    }
  ],
  "verification_commands": ["python -m py_compile settings.py"]
}
"""


def ambiguous_goal() -> str:
    return "кодовая задача: улучши обработку ошибок в python приложении, требования и формат ошибки не заданы. Если вариантов несколько, не угадывай."


def create_file_goal() -> str:
    return """создай python файл

CERAXIA_CREATE_FILE: generated.py
CERAXIA_FILE_CONTENT:
def generated_value():
    return 42

CERAXIA_VERIFY: python -m py_compile generated.py
"""


def multi_file_goal() -> str:
    return """создай несколько python файлов и проверь их вместе

CERAXIA_FILES:
{
  "files": [
    {
      "path": "calc.py",
      "content": "def add(left, right):\\n    return left + right\\n"
    },
    {
      "path": "test_calc.py",
      "content": "import unittest\\nfrom calc import add\\n\\nclass CalcTest(unittest.TestCase):\\n    def test_add(self):\\n        self.assertEqual(add(2, 3), 5)\\n\\nif __name__ == '__main__':\\n    unittest.main()\\n"
    }
  ],
  "verification_commands": ["python -m unittest test_calc.py"]
}
"""


def repo_grade_workflow_goal() -> str:
    return """repo-grade architecture refactor compatibility задача: создай большой PR-shaped пакет на 9 файлов с ADR, focused verification, broad verification и PR summary.

CERAXIA_FILES:
{
  "files": [
    {
      "path": "src/billing/pricing.py",
      "content": "def net_amount(gross, fee):\\n    return gross - fee\\n"
    },
    {
      "path": "src/billing/tax.py",
      "content": "def tax_amount(net, rate):\\n    return round(net * rate, 2)\\n"
    },
    {
      "path": "src/billing/invoice.py",
      "content": "from src.billing.pricing import net_amount\\nfrom src.billing.tax import tax_amount\\n\\n\\ndef invoice_total(gross, fee, tax_rate):\\n    net = net_amount(gross, fee)\\n    return net + tax_amount(net, tax_rate)\\n"
    },
    {
      "path": "src/billing/reporting.py",
      "content": "from src.billing.invoice import invoice_total\\n\\n\\ndef invoice_summary(gross, fee, tax_rate):\\n    return {'total': invoice_total(gross, fee, tax_rate), 'currency': 'USD'}\\n"
    },
    {
      "path": "config/billing.json",
      "content": "{\\n  \\"currency\\": \\"USD\\",\\n  \\"default_tax_rate\\": 0.1\\n}\\n"
    },
    {
      "path": "docs/billing.md",
      "content": "# Billing\\n\\nInvoices preserve total and currency in the reporting contract.\\n"
    },
    {
      "path": "tests/test_invoice.py",
      "content": "import unittest\\nfrom src.billing.invoice import invoice_total\\n\\n\\nclass InvoiceTest(unittest.TestCase):\\n    def test_invoice_total(self):\\n        self.assertEqual(invoice_total(100, 10, 0.1), 99.0)\\n\\n\\nif __name__ == '__main__':\\n    unittest.main()\\n"
    },
    {
      "path": "tests/test_reporting.py",
      "content": "import unittest\\nfrom src.billing.reporting import invoice_summary\\n\\n\\nclass ReportingTest(unittest.TestCase):\\n    def test_invoice_summary_contract(self):\\n        self.assertEqual(invoice_summary(100, 10, 0.1), {'total': 99.0, 'currency': 'USD'})\\n\\n\\nif __name__ == '__main__':\\n    unittest.main()\\n"
    },
    {
      "path": "README.md",
      "content": "# Billing Fixture\\n\\nRepo-grade package.\\n"
    }
  ],
  "verification_commands": [
    "python -m unittest tests.test_invoice",
    "python -m unittest tests.test_reporting",
    "python -m unittest discover -s tests",
    "python -m py_compile src/billing/pricing.py src/billing/tax.py src/billing/invoice.py src/billing/reporting.py"
  ]
}
"""


def feature_goal() -> str:
    return """добавь multi-file feature с source, tests, docs, caller

CERAXIA_FEATURE:
{
  "module_path": "billing/discounts.py",
  "function_name": "apply_discount",
  "arguments": ["price", "percent"],
  "return_expression": "price - (price * percent / 100)",
  "test_path": "tests/test_discounts.py",
  "test_cases": [
    {"inputs": [200, 25], "expected": 150.0},
    {"inputs": [80, 10], "expected": 72.0}
  ],
  "docs_path": "docs/discounts.md",
  "docs_title": "Discount helpers",
  "caller_path": "billing/api.py",
  "caller_function": "discounted_total",
  "verification_commands": ["python -m unittest tests.test_discounts"]
}
"""


def config_runtime_goal() -> str:
    return """согласуй JSON config, Python loader и shell entrypoint

CERAXIA_CONFIG_RUNTIME:
{
  "config_path": "app/settings.json",
  "loader_path": "app/config_loader.py",
  "entrypoint_path": "bin/run-app.sh",
  "test_path": "tests/test_config_loader.py",
  "setting_key": "service_url",
  "env_var": "SERVICE_URL",
  "default_value": "http://localhost:8080",
  "verification_commands": ["python -m unittest tests.test_config_loader", "python -m py_compile app/config_loader.py"]
}
"""


def unshaped_config_runtime_goal() -> str:
    return """исправь runtime config mismatch: tests describe service_url defaults and SERVICE_URL override, loader and shell entrypoint must agree with JSON config

CERAXIA_VERIFY: python -m unittest tests.test_config_loader
CERAXIA_VERIFY: python -m py_compile app/config_loader.py
"""


def refactor_goal() -> str:
    return """вынеси дублированный расчет в общий helper, не меняя публичные функции

CERAXIA_REFACTOR:
{
  "helper_path": "common/calculations.py",
  "helper_function": "net_amount",
  "arguments": ["gross", "fee"],
  "return_expression": "gross - fee",
  "baseline_verification_commands": ["python -m unittest discover"],
  "replacements": [
    {
      "path": "orders.py",
      "public_function": "order_total",
      "old": "def order_total(gross, fee):\\n    return gross - fee\\n",
      "new": "from common.calculations import net_amount\\n\\n\\ndef order_total(gross, fee):\\n    return net_amount(gross, fee)\\n"
    },
    {
      "path": "refunds.py",
      "public_function": "refund_total",
      "old": "def refund_total(gross, fee):\\n    return gross - fee\\n",
      "new": "from common.calculations import net_amount\\n\\n\\ndef refund_total(gross, fee):\\n    return net_amount(gross, fee)\\n"
    }
  ],
  "verification_commands": ["python -m unittest discover", "python -m py_compile orders.py refunds.py common/calculations.py"]
}
"""


def edge_fix_goal() -> str:
    return """исправь функцию так, чтобы были проверены happy path и негативные edge cases

CERAXIA_EDGE_FIX:
{
  "source_path": "retry_policy.py",
  "function_name": "parse_retry_count",
  "arguments": ["raw"],
  "body_lines": [
    "value = int(raw)",
    "if value < 0 or value > 10:",
    "    raise ValueError('retry count must be between 0 and 10')",
    "return value"
  ],
  "test_path": "test_retry_policy.py",
  "positive_cases": [
    {"inputs": ["0"], "expected": 0},
    {"inputs": ["3"], "expected": 3},
    {"inputs": ["10"], "expected": 10}
  ],
  "negative_cases": [
    {"inputs": ["-1"], "exception": "ValueError"},
    {"inputs": ["11"], "exception": "ValueError"},
    {"inputs": ["bad"], "exception": "ValueError"}
  ],
  "verification_commands": ["python -m unittest test_retry_policy", "python -m py_compile retry_policy.py"]
}
"""


def data_migration_goal() -> str:
    return """введи новую форму record без потери чтения старых записей

CERAXIA_DATA_MIGRATION:
{
  "source_path": "records.py",
  "test_path": "test_records.py",
  "read_function": "normalize_record",
  "write_function": "serialize_record",
  "id_field": "id",
  "old_field": "amount",
  "new_field": "total_amount",
  "verification_commands": ["python -m unittest test_records", "python -m py_compile records.py"]
}
"""


def integration_contract_goal() -> str:
    return """обнови локальный API contract, implementation, caller, tests и summary

CERAXIA_INTEGRATION_CONTRACT:
{
  "contract_path": "contracts/invoice.json",
  "implementation_path": "api/invoice_service.py",
  "caller_path": "client/invoice_client.py",
  "test_path": "tests/test_invoice_contract.py",
  "report_path": "reports/invoice_contract.md",
  "function_name": "calculate_invoice",
  "caller_function": "invoice_total",
  "request_fields": ["gross", "fee"],
  "response_field": "net_total",
  "return_expression": "gross - fee",
  "test_cases": [
    {"inputs": {"gross": 100, "fee": 15}, "expected": 85},
    {"inputs": {"gross": 80, "fee": 5}, "expected": 75}
  ],
  "verification_commands": ["python -m unittest tests.test_invoice_contract", "python -m py_compile api/invoice_service.py client/invoice_client.py"]
}
"""


def public_api_compat_goal() -> str:
    return """измени поведение публичной функции, сохрани ее сигнатуру и caller compatibility

CERAXIA_PUBLIC_API_COMPAT:
{
  "source_path": "billing/public_api.py",
  "caller_path": "billing/client.py",
  "docs_path": "docs/public_api.md",
  "test_path": "tests/test_public_api.py",
  "function_name": "calculate_total",
  "caller_function": "client_total",
  "arguments": ["gross", "fee"],
  "return_expression": "gross - fee",
  "test_cases": [
    {"inputs": [100, 15], "expected": 85},
    {"inputs": [80, 5], "expected": 75}
  ],
  "verification_commands": ["python -m unittest tests.test_public_api", "python -m py_compile billing/public_api.py billing/client.py"]
}
"""


def repair_colon_goal() -> str:
    return """создай python файл и исправь если проверка найдет синтаксис

CERAXIA_CREATE_FILE: repair_me.py
CERAXIA_FILE_CONTENT:
def repaired_value()
    return 42

CERAXIA_VERIFY: python -m py_compile repair_me.py
"""


def repair_assertion_goal() -> str:
    return """создай python файл и исправь по unittest если тест покажет ожидаемое значение

CERAXIA_CREATE_FILE: sample.py
CERAXIA_FILE_CONTENT:
def value():
    return 1

CERAXIA_VERIFY: python -m unittest test_sample.py
"""


def repair_name_error_goal() -> str:
    return """создай python файл и исправь NameError если тест показывает ожидаемый literal

CERAXIA_CREATE_FILE: sample.py
CERAXIA_FILE_CONTENT:
def value():
    return answer

CERAXIA_VERIFY: python -m unittest test_sample.py
"""


def repair_import_error_goal() -> str:
    return """создай модуль и исправь missing import если тест показывает ожидаемый literal

CERAXIA_CREATE_FILE: sample.py
CERAXIA_FILE_CONTENT:

CERAXIA_VERIFY: python -m unittest test_sample.py
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        final = run_pipeline(root)
        if final.get("status") != "blocked" or final.get("next_safe_action") != "handoff_to_patch_worker":
            raise AssertionError(f"final manifest should refuse code completion without source mutation: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "maths.py").write_text("def double(value):\n    return value * 2\n", encoding="utf-8")
        (target_repo / "test_maths.py").write_text(
            "from maths import double\n\n"
            "def test_double():\n"
            "    assert double(4) == 8\n",
            encoding="utf-8",
        )
        links = test_symbol_links_from_goal(target_repo, "`test_maths.py`")
        if not any(
            item.get("test_class") == ""
            and item.get("test_function") == "test_double"
            and item.get("assertion") == "assertEqual"
            and item.get("imported_symbol") == "double"
            and item.get("source_path") == "maths.py"
            and item.get("expected_expression") == "8"
            for item in links
            if isinstance(item, dict)
        ):
            raise AssertionError(f"top-level assert tests should link to imported source symbols: {links}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        (target_repo / "helper.py").write_text("from sample import value\n\ndef doubled():\n    return value() * 2\n", encoding="utf-8")
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 2)\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=explicit_patch_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("next_safe_action") != "inspect_final_package":
            raise AssertionError(f"final manifest should be ready after explicit patch verification: {final}")
        if sample.read_text(encoding="utf-8") != "def value():\n    return 2\n":
            raise AssertionError("explicit replace patch did not mutate the target file")
        changed = final.get("changed_files", [])
        if not changed or changed[0].get("path") != "sample.py" or not changed[0].get("changed"):
            raise AssertionError(f"final manifest should preserve changed file metadata: {final}")
        if final.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"final manifest should preserve verification evidence: {final}")
        scope = final.get("patch_scope_evidence", {})
        if "sample.py" not in scope.get("changed_files_in_repo_map", []):
            raise AssertionError(f"final manifest should preserve patch scope evidence: {final}")
        scope_review = final.get("patch_scope_review", {})
        if (
            scope_review.get("status") != "covered"
            or scope_review.get("mapped_changed_file_count") != 1
            or scope_review.get("source_without_linked_tests")
        ):
            raise AssertionError(f"final manifest should preserve patch scope review: {final}")
        repair_state = final.get("repair_loop_state", {})
        if (
            repair_state.get("status") != "passed"
            or repair_state.get("next_action") != "continue_to_code_review"
            or repair_state.get("commands_executed_count", 0) < 2
        ):
            raise AssertionError(f"final manifest should preserve repair loop state: {final}")
        role_policies = final.get("role_policies", {})
        if (
            role_policies.get("implementation", {}).get("authority")
            != "scoped_source_mutation_from_patch_contract_or_safe_inference"
            or role_policies.get("verification", {}).get("authority") != "allowlisted_verification_and_narrow_repairs"
            or role_policies.get("finalize", {}).get("may_mutate_source") is not False
        ):
            raise AssertionError(f"final manifest should preserve role policy evidence: {final}")
        decision_record = final.get("review_decision_record", [])
        if (
            len(decision_record) < 4
            or not any(
                item.get("check") == "diagnostic_linkage" and item.get("status") == "pass"
                for item in decision_record
                if isinstance(item, dict)
            )
        ):
            raise AssertionError(f"final manifest should preserve review decision record: {final}")
        investigation = final.get("engineering_investigation", {})
        readiness = final.get("engineering_readiness", {})
        dependency_edges = investigation.get("dependency_graph", {}).get("edges", [])
        if not any(edge.get("from") == "helper.py" and edge.get("to") == "sample.py" for edge in dependency_edges):
            raise AssertionError(f"engineering investigation should include import dependency graph: {final}")
        targeted = investigation.get("targeted_reading_plan", [])
        if not any(item.get("path") == "sample.py" and item.get("dependent_count", 0) >= 1 for item in targeted):
            raise AssertionError(f"engineering investigation should include targeted reading with dependents: {final}")
        if not investigation.get("hypotheses"):
            raise AssertionError(f"engineering investigation should preserve hypothesis log: {final}")
        plan_text = (root / "work" / "code" / "change_plan.md").read_text(encoding="utf-8")
        if (
            "## Hypothesis Log" not in plan_text
            or "## Targeted Reading Plan" not in plan_text
            or "## Problem Statement" not in plan_text
            or "## Architecture Options" not in plan_text
            or "## File Impact Matrix" not in plan_text
            or "## Acceptance Criteria" not in plan_text
            or "## Test Strategy" not in plan_text
        ):
            raise AssertionError(f"change plan should include engineering investigation sections: {plan_text}")
        problem_statement = final.get("problem_statement", {})
        architecture_options = final.get("architecture_options", {})
        if (
            problem_statement.get("status") != "recorded"
            or not problem_statement.get("success_criteria")
            or architecture_options.get("status") != "recorded"
            or not architecture_options.get("options")
            or final.get("architect_review", {}).get("problem_statement_present") is not True
        ):
            raise AssertionError(f"final manifest should preserve architect planning evidence: {final}")
        if (
            len(readiness.get("acceptance_criteria", [])) < 5
            or not readiness.get("test_strategy", {}).get("fallback_checks")
            or not readiness.get("impact_matrix")
            or final.get("engineering_readiness_review", {}).get("acceptance_criteria_count", 0) < 5
        ):
            raise AssertionError(f"final manifest should preserve engineering readiness model: {final}")
        if not any(item.get("check") == "readiness_model_present" and item.get("status") == "pass" for item in decision_record):
            raise AssertionError(f"review decision record should gate engineering readiness: {final}")
        patch_manifest = json.loads((root / "work" / "code" / "patch_manifest.json").read_text(encoding="utf-8"))
        candidates = patch_manifest.get("patch_candidates", [])
        if (
            not candidates
            or candidates[0].get("source") != "explicit_json_patch"
            or candidates[0].get("status") != "selected"
            or patch_manifest.get("selected_patch_candidate", {}).get("source") != "explicit_json_patch"
        ):
            raise AssertionError(f"implementation should preserve selected patch candidate journal: {patch_manifest}")
        source_excerpts = patch_manifest.get("source_excerpt_pack", [])
        if not any(item.get("path") == "sample.py" and item.get("status") == "read" for item in source_excerpts):
            raise AssertionError(f"implementation should read targeted source excerpts: {patch_manifest}")
        implementation_record = final.get("implementation_decision_record", [])
        if (
            not any(item.get("check") == "source_evidence_loaded" and item.get("status") == "pass" for item in implementation_record)
            or final.get("selected_patch_candidate", {}).get("source") != "explicit_json_patch"
            or final.get("execution_report", {}).get("patch_candidate_count", 0) < 1
            or final.get("execution_report", {}).get("source_excerpt_count", 0) < 1
        ):
            raise AssertionError(f"final manifest should preserve implementation decision evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        payload = request("implementation", "/work/code/patch_manifest.json", goal=explicit_patch_goal(), target_repo_root=target_repo)
        payload["quality_expectations"]["step_quality"]["role_policy"] = {
            "role": "read_only_test",
            "authority": "read_only_repository_mapping",
            "may_mutate_source": False,
        }
        result = run(payload, root / "work")
        if not result.get("ok"):
            raise AssertionError(f"read-only implementation policy should produce an auditable blocker: {result}")
        manifest = json.loads((root / "work" / "code" / "patch_manifest.json").read_text(encoding="utf-8"))
        if manifest.get("status") != "handoff_required" or "role_policy forbids source mutation for this step" not in manifest.get("blockers", []):
            raise AssertionError(f"read-only implementation policy should block mutation: {manifest}")
        if sample.read_text(encoding="utf-8") != "def value():\n    return 1\n":
            raise AssertionError("read-only implementation policy allowed source mutation")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        broken = target_repo / "repair_me.py"
        broken.write_text("def value()\n    return 42\n", encoding="utf-8")
        work = root / "work"
        (work / "code").mkdir(parents=True)
        (work / "code" / "patch_manifest.json").write_text(
            json.dumps(
                {
                    "status": "applied",
                    "changed_files": [{"path": "repair_me.py", "changed": True}],
                    "verification_commands": [],
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        payload = request("verification", "/work/code/verification_report.json", target_repo_root=target_repo)
        payload["quality_expectations"]["step_quality"]["role_policy"] = {
            "role": "read_only_test",
            "authority": "read_only_verification_without_repair",
            "may_mutate_source": False,
        }
        result = run(payload, work)
        if not result.get("ok"):
            raise AssertionError(f"read-only verification policy should write a blocked report: {result}")
        report = json.loads((work / "code" / "verification_report.json").read_text(encoding="utf-8"))
        repair_state = json.loads((work / "code" / "repair_loop_state.json").read_text(encoding="utf-8"))
        if "role_policy forbids source mutation repair" not in report.get("blockers", []):
            raise AssertionError(f"read-only verification policy should block repair: {report}")
        if not repair_state.get("blocked_repairs") or repair_state.get("repairs_allowed") is not False:
            raise AssertionError(f"read-only verification policy should preserve blocked repair state: {repair_state}")
        if "repair_me.py" not in repair_state.get("candidate_source_paths", []):
            raise AssertionError(f"verification repair state should expose traceback source candidates: {repair_state}")
        if broken.read_text(encoding="utf-8") != "def value()\n    return 42\n":
            raise AssertionError("read-only verification policy allowed repair mutation")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "sample.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        work = root / "work"
        (work / "code").mkdir(parents=True)
        (work / "code" / "repo_survey.json").write_text(
            json.dumps({"repo_map": {"ranked_files": [{"path": "sample.py", "score": 10}, {"path": "test_sample.py", "score": 4}]}}) + "\n",
            encoding="utf-8",
        )
        (work / "code" / "patch_manifest.json").write_text(
            json.dumps(
                {
                    "status": "applied",
                    "changed_files": [{"path": "sample.py", "changed": True}],
                    "verification_commands": ["python -m py_compile missing_file.py"],
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        payload = request("verification", "/work/code/verification_report.json", target_repo_root=target_repo)
        result = run(payload, work)
        if not result.get("ok"):
            raise AssertionError(f"verification with repo-map fallback should write a report: {result}")
        repair_state = json.loads((work / "code" / "repair_loop_state.json").read_text(encoding="utf-8"))
        if repair_state.get("candidate_source_paths") != ["sample.py"]:
            raise AssertionError(f"verification should fall back to repo-map source candidates: {repair_state}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "sample.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        work = root / "work"
        (work / "code").mkdir(parents=True)
        (work / "code" / "patch_manifest.json").write_text(
            json.dumps(
                {
                    "status": "applied",
                    "changed_files": [{"path": "unexpected.py", "changed": True}],
                    "patch_scope_evidence": {
                        "changed_files_in_repo_map": [],
                        "changed_files_outside_repo_map": ["unexpected.py"],
                        "evidence": [{"path": "unexpected.py", "in_repo_map": False}],
                    },
                    "verification_commands": [],
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "verification_report.json").write_text(
            json.dumps({"status": "passed", "blockers": [], "warnings": [], "executed": []}) + "\n",
            encoding="utf-8",
        )
        (work / "code" / "repair_loop_state.json").write_text(
            json.dumps({"status": "passed", "next_action": "continue_to_code_review"}) + "\n",
            encoding="utf-8",
        )
        payload = request("code_review", "/work/code/code_review.json", target_repo_root=target_repo)
        result = run(payload, work)
        if not result.get("ok"):
            raise AssertionError(f"scope-aware review should write a report: {result}")
        review = json.loads((work / "code" / "code_review.json").read_text(encoding="utf-8"))
        if review.get("patch_scope_review", {}).get("status") != "needs_attention":
            raise AssertionError(f"code review should flag unmapped changed files: {review}")
        warning_text = "\n".join(str(item.get("message", "")) for item in review.get("warnings", []) if isinstance(item, dict))
        if "unexpected.py" not in warning_text:
            raise AssertionError(f"code review should explain scope drift warning: {review}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        work = root / "work"
        (work / "code").mkdir(parents=True)
        (work / "code" / "repo_survey.json").write_text(
            json.dumps(
                {
                    "repo_map": {"ranked_files": [{"path": "sample.py", "score": 10}, {"path": "test_sample.py", "score": 9}]},
                    "engineering_investigation": {"hypotheses": [{"hypothesis": "sample.py likely relevant"}]},
                    "engineering_readiness": {
                        "acceptance_criteria": [{"criterion": "tests unchanged", "verification": "review"}],
                        "test_strategy": {"fallback_checks": ["python -m py_compile <changed .py files>"]},
                        "readiness_checks": {"has_acceptance_criteria": True, "has_test_strategy": True},
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "problem_statement.json").write_text(json.dumps({"status": "recorded"}) + "\n", encoding="utf-8")
        (work / "code" / "architecture_options.json").write_text(json.dumps({"status": "recorded"}) + "\n", encoding="utf-8")
        (work / "code" / "patch_manifest.json").write_text(
            json.dumps(
                {
                    "status": "applied",
                    "patch_source": "test_inferred_return_mismatch",
                    "changed_files": [{"path": "test_sample.py", "changed": True}],
                    "patch_scope_evidence": {"changed_files_in_repo_map": ["test_sample.py"], "changed_files_outside_repo_map": [], "evidence": [{"path": "test_sample.py", "in_repo_map": True}]},
                    "engineering_readiness": {
                        "acceptance_criteria": [{"criterion": "tests unchanged", "verification": "review"}],
                        "test_strategy": {"fallback_checks": ["python -m py_compile <changed .py files>"]},
                        "readiness_checks": {"has_acceptance_criteria": True, "has_test_strategy": True},
                    },
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "verification_report.json").write_text(
            json.dumps({"status": "passed", "blockers": [], "warnings": [], "executed": []}) + "\n",
            encoding="utf-8",
        )
        (work / "code" / "repair_loop_state.json").write_text(
            json.dumps({"status": "passed", "next_action": "continue_to_code_review"}) + "\n",
            encoding="utf-8",
        )
        payload = request("code_review", "/work/code/code_review.json", target_repo_root=target_repo)
        result = run(payload, work)
        if not result.get("ok"):
            raise AssertionError(f"review discipline test should write a report: {result}")
        review = json.loads((work / "code" / "code_review.json").read_text(encoding="utf-8"))
        if (
            review.get("approved") is not False
            or not any("must not edit tests" in item.get("message", "") for item in review.get("findings", []) if isinstance(item, dict))
            or review.get("code_review_discipline", {}).get("blocker_count") != 1
            or review.get("review_repair_loop", {}).get("required") is not True
            or "implementation" not in [item.get("step_id") for item in review.get("review_repair_loop", {}).get("rerun_steps", []) if isinstance(item, dict)]
        ):
            raise AssertionError(f"code review should block test-inferred test edits: {review}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "calc.py").write_text("def add(left, right):\n    return 150\n", encoding="utf-8")
        work = root / "work"
        (work / "code").mkdir(parents=True)
        (work / "code" / "repo_survey.json").write_text(
            json.dumps(
                {
                    "repo_map": {"ranked_files": [{"path": "calc.py", "score": 10}, {"path": "test_calc.py", "score": 9}]},
                    "engineering_investigation": {"hypotheses": [{"hypothesis": "calc.py likely relevant"}]},
                    "engineering_readiness": {
                        "acceptance_criteria": [{"criterion": "derive behavior from inputs", "verification": "review"}],
                        "test_strategy": {"fallback_checks": ["python -m py_compile <changed .py files>"]},
                        "readiness_checks": {"has_acceptance_criteria": True, "has_test_strategy": True},
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "problem_statement.json").write_text(json.dumps({"status": "recorded"}) + "\n", encoding="utf-8")
        (work / "code" / "architecture_options.json").write_text(json.dumps({"status": "recorded"}) + "\n", encoding="utf-8")
        diagnostics = {
            "kind": "test_inferred_arithmetic_return",
            "test_path": "test_calc.py",
            "module_path": "calc.py",
            "function_name": "add",
            "actual_expression": "left - right",
            "replacement_expression": "150",
            "example": {"left": 200, "right": 50, "expected": 150},
        }
        (work / "code" / "patch_manifest.json").write_text(
            json.dumps(
                {
                    "status": "applied",
                    "patch_source": "test_inferred_arithmetic_return",
                    "diagnostics": diagnostics,
                    "changed_files": [{"path": "calc.py", "changed": True}],
                    "patch_scope_evidence": {
                        "changed_files_in_repo_map": ["calc.py"],
                        "changed_files_outside_repo_map": [],
                        "evidence": [{"path": "calc.py", "in_repo_map": True}],
                    },
                    "engineering_readiness": {
                        "acceptance_criteria": [{"criterion": "derive behavior from inputs", "verification": "review"}],
                        "test_strategy": {"fallback_checks": ["python -m py_compile <changed .py files>"]},
                        "readiness_checks": {"has_acceptance_criteria": True, "has_test_strategy": True},
                    },
                    "ast_patch_plan": {
                        "status": "recorded",
                        "patch_source": "test_inferred_arithmetic_return",
                        "planned_operations": [
                            {
                                "kind": "replace_return_expression",
                                "path": "calc.py",
                                "function_name": "add",
                                "new_expression": "150",
                            }
                        ],
                        "operation_count": 1,
                    },
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "unshaped_repair_plan.json").write_text(
            json.dumps(
                {
                    "status": "recorded",
                    "mode": "unshaped_repo_repair",
                    "defect_hypotheses": [{"source": "test_inferred_arithmetic_return", "evidence": diagnostics}],
                    "minimal_patch_candidates": [{"source": "test_inferred_arithmetic_return", "status": "selected"}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "diagnostic_extraction.json").write_text(
            json.dumps({"status": "recorded", "parser_coverage": {"static_test_expectations": 1}}) + "\n",
            encoding="utf-8",
        )
        (work / "code" / "verification_report.json").write_text(
            json.dumps({"status": "passed", "blockers": [], "warnings": [], "executed": []}) + "\n",
            encoding="utf-8",
        )
        (work / "code" / "repair_loop_state.json").write_text(
            json.dumps({"status": "passed", "next_action": "continue_to_code_review"}) + "\n",
            encoding="utf-8",
        )
        result = run(request("code_review", "/work/code/code_review.json", target_repo_root=target_repo), work)
        if not result.get("ok"):
            raise AssertionError(f"hardcode review discipline test should write a report: {result}")
        review = json.loads((work / "code" / "code_review.json").read_text(encoding="utf-8"))
        if (
            review.get("approved") is not False
            or not any("hardcodes the example expected value" in item.get("message", "") for item in review.get("findings", []) if isinstance(item, dict))
            or review.get("code_review_discipline", {}).get("blocker_count") != 1
        ):
            raise AssertionError(f"code review should block hardcoded inferred example values: {review}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        (target_repo / "tests").mkdir(parents=True)
        (target_repo / "archive_paths.py").write_text(
            "def safe_archive_path(raw):\n"
            "    parts = [part for part in str(raw).split('/') if part and part != '.']\n"
            "    return '/'.join(parts)\n",
            encoding="utf-8",
        )
        (target_repo / "tests" / "test_archive_paths.py").write_text(
            "import unittest\nfrom archive_paths import safe_archive_path\n\n"
            "class ArchivePathsTest(unittest.TestCase):\n"
            "    def test_valid_relative_paths_are_normalized(self):\n"
            "        self.assertEqual(safe_archive_path('./books//chapter2.txt'), 'books/chapter2.txt')\n",
            encoding="utf-8",
        )
        work = root / "work"
        (work / "code").mkdir(parents=True)
        readiness = {
            "acceptance_criteria": [{"criterion": "reject path traversal", "verification": "negative tests"}],
            "test_strategy": {"fallback_checks": ["python -m unittest tests.test_archive_paths"]},
            "readiness_checks": {"has_acceptance_criteria": True, "has_test_strategy": True},
        }
        (work / "code" / "repo_survey.json").write_text(
            json.dumps(
                {
                    "repo_map": {
                        "ranked_files": [
                            {"path": "archive_paths.py", "score": 10},
                            {"path": "tests/test_archive_paths.py", "score": 9},
                        ]
                    },
                    "engineering_readiness": readiness,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "problem_statement.json").write_text(json.dumps({"status": "recorded"}) + "\n", encoding="utf-8")
        (work / "code" / "architecture_options.json").write_text(json.dumps({"status": "recorded"}) + "\n", encoding="utf-8")
        diagnostics = {
            "kind": "test_inferred_security_boundary",
            "test_path": "tests/test_archive_paths.py",
            "module_path": "archive_paths.py",
            "function_name": "safe_archive_path",
            "validation_exception": "ValueError",
        }
        (work / "code" / "patch_manifest.json").write_text(
            json.dumps(
                {
                    "status": "applied",
                    "patch_source": "test_inferred_security_boundary",
                    "diagnostics": diagnostics,
                    "changed_files": [{"path": "archive_paths.py", "changed": True}],
                    "patch_scope_evidence": {
                        "changed_files_in_repo_map": ["archive_paths.py"],
                        "changed_files_outside_repo_map": [],
                    },
                    "engineering_readiness": readiness,
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "unshaped_repair_plan.json").write_text(
            json.dumps(
                {
                    "status": "recorded",
                    "mode": "unshaped_repo_repair",
                    "defect_hypotheses": [{"source": "test_inferred_security_boundary", "evidence": diagnostics}],
                    "minimal_patch_candidates": [{"source": "test_inferred_security_boundary", "status": "selected"}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "diagnostic_extraction.json").write_text(
            json.dumps({"status": "recorded", "parser_coverage": {"static_test_expectations": 1}}) + "\n",
            encoding="utf-8",
        )
        (work / "code" / "verification_report.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "blockers": [],
                    "warnings": [],
                    "executed": [{"command": "python -m unittest tests.test_archive_paths", "returncode": 0}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "repair_loop_state.json").write_text(
            json.dumps({"status": "passed", "next_action": "continue_to_code_review"}) + "\n",
            encoding="utf-8",
        )
        result = run(request("code_review", "/work/code/code_review.json", target_repo_root=target_repo), work)
        if not result.get("ok"):
            raise AssertionError(f"negative-test review gate should write a report: {result}")
        review = json.loads((work / "code" / "code_review.json").read_text(encoding="utf-8"))
        discipline_findings = review.get("code_review_discipline", {}).get("findings", [])
        if (
            review.get("approved") is not False
            or not any(item.get("check") == "risk_patch_has_negative_tests" for item in discipline_findings if isinstance(item, dict))
            or review.get("code_review_discipline", {}).get("blocker_count") != 1
        ):
            raise AssertionError(f"code review should block security-boundary repairs without negative tests: {review}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        (target_repo / "tests").mkdir(parents=True)
        (target_repo / "app").mkdir(parents=True)
        (target_repo / "app" / "config_loader.py").write_text(
            "def load_settings():\n"
            "    return {'service_url': 'https://prod.example'}\n",
            encoding="utf-8",
        )
        (target_repo / "tests" / "test_config_loader.py").write_text(
            "import os\nimport unittest\nfrom app.config_loader import load_settings\n\n"
            "class ConfigLoaderTest(unittest.TestCase):\n"
            "    def test_env_override(self):\n"
            "        os.environ['SERVICE_URL'] = 'https://prod.example'\n"
            "        try:\n"
            "            self.assertEqual(load_settings()['service_url'], 'https://prod.example')\n"
            "        finally:\n"
            "            os.environ.pop('SERVICE_URL', None)\n",
            encoding="utf-8",
        )
        work = root / "work"
        (work / "code").mkdir(parents=True)
        readiness = {
            "acceptance_criteria": [{"criterion": "preserve default and env override runtime config", "verification": "negative tests"}],
            "test_strategy": {"fallback_checks": ["python -m unittest tests.test_config_loader"]},
            "readiness_checks": {"has_acceptance_criteria": True, "has_test_strategy": True},
        }
        (work / "code" / "repo_survey.json").write_text(
            json.dumps(
                {
                    "repo_map": {
                        "ranked_files": [
                            {"path": "app/config_loader.py", "score": 10},
                            {"path": "tests/test_config_loader.py", "score": 9},
                        ]
                    },
                    "engineering_readiness": readiness,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "problem_statement.json").write_text(json.dumps({"status": "recorded"}) + "\n", encoding="utf-8")
        (work / "code" / "architecture_options.json").write_text(json.dumps({"status": "recorded"}) + "\n", encoding="utf-8")
        diagnostics = {
            "kind": "test_inferred_config_runtime",
            "test_path": "tests/test_config_loader.py",
            "loader_path": "app/config_loader.py",
            "config_path": "app/settings.json",
            "entrypoint_path": "bin/run-app.sh",
            "setting_key": "service_url",
            "env_var": "SERVICE_URL",
        }
        (work / "code" / "patch_manifest.json").write_text(
            json.dumps(
                {
                    "status": "applied",
                    "patch_source": "test_inferred_config_runtime",
                    "diagnostics": diagnostics,
                    "changed_files": [{"path": "app/config_loader.py", "changed": True}],
                    "patch_scope_evidence": {
                        "changed_files_in_repo_map": ["app/config_loader.py"],
                        "changed_files_outside_repo_map": [],
                    },
                    "engineering_readiness": readiness,
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "unshaped_repair_plan.json").write_text(
            json.dumps(
                {
                    "status": "recorded",
                    "mode": "unshaped_repo_repair",
                    "defect_hypotheses": [{"source": "test_inferred_config_runtime", "evidence": diagnostics}],
                    "minimal_patch_candidates": [{"source": "test_inferred_config_runtime", "status": "selected"}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "diagnostic_extraction.json").write_text(
            json.dumps({"status": "recorded", "parser_coverage": {"static_test_expectations": 1}}) + "\n",
            encoding="utf-8",
        )
        (work / "code" / "verification_report.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "blockers": [],
                    "warnings": [],
                    "executed": [{"command": "python -m unittest tests.test_config_loader", "returncode": 0}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "repair_loop_state.json").write_text(
            json.dumps({"status": "passed", "next_action": "continue_to_code_review"}) + "\n",
            encoding="utf-8",
        )
        result = run(request("code_review", "/work/code/code_review.json", target_repo_root=target_repo), work)
        if not result.get("ok"):
            raise AssertionError(f"config negative-test review gate should write a report: {result}")
        review = json.loads((work / "code" / "code_review.json").read_text(encoding="utf-8"))
        discipline_findings = review.get("code_review_discipline", {}).get("findings", [])
        if (
            review.get("approved") is not False
            or not any(item.get("check") == "risk_patch_has_negative_tests" for item in discipline_findings if isinstance(item, dict))
            or review.get("code_review_discipline", {}).get("blocker_count") != 1
        ):
            raise AssertionError(f"code review should block config-runtime repairs without default/fallback tests: {review}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        (target_repo / "payments").mkdir(parents=True)
        (target_repo / "docs").mkdir(parents=True)
        (target_repo / "tests").mkdir(parents=True)
        (target_repo / "payments" / "api.py").write_text(
            "def calculate_total(gross, fee=0, *, service_fee=None):\n"
            "    return gross - (service_fee if service_fee is not None else fee)\n",
            encoding="utf-8",
        )
        work = root / "work"
        (work / "code").mkdir(parents=True)
        readiness = {
            "acceptance_criteria": [{"criterion": "public API compatibility", "verification": "review"}],
            "test_strategy": {"fallback_checks": ["python -m py_compile <changed .py files>"]},
            "readiness_checks": {"has_acceptance_criteria": True, "has_test_strategy": True},
            "impact_matrix": [{"path": "payments/api.py"}],
        }
        (work / "code" / "repo_survey.json").write_text(
            json.dumps({"repo_map": {"ranked_files": [{"path": "payments/api.py", "score": 10}]}, "engineering_readiness": readiness})
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "problem_statement.json").write_text(json.dumps({"status": "recorded"}) + "\n", encoding="utf-8")
        (work / "code" / "architecture_options.json").write_text(json.dumps({"status": "recorded"}) + "\n", encoding="utf-8")
        diagnostics = {
            "kind": "test_inferred_api_deprecation",
            "test_path": "tests/test_api.py",
            "source_path": "payments/api.py",
            "function_name": "calculate_total",
            "old_param": "fee",
            "new_param": "service_fee",
            "docs_path": "docs/payments_api.md",
            "caller": {"caller_path": "payments/client.py", "caller_name": "client_total"},
        }
        (work / "code" / "patch_manifest.json").write_text(
            json.dumps(
                {
                    "status": "applied",
                    "patch_source": "test_inferred_api_deprecation",
                    "diagnostics": diagnostics,
                    "changed_files": [{"path": "payments/api.py", "changed": True}],
                    "patch_scope_evidence": {"changed_files_in_repo_map": ["payments/api.py"], "changed_files_outside_repo_map": []},
                    "engineering_readiness": readiness,
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "unshaped_repair_plan.json").write_text(
            json.dumps(
                {
                    "status": "recorded",
                    "mode": "unshaped_repo_repair",
                    "defect_hypotheses": [{"source": "test_inferred_api_deprecation", "evidence": diagnostics}],
                    "minimal_patch_candidates": [{"source": "test_inferred_api_deprecation", "status": "selected"}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "diagnostic_extraction.json").write_text(
            json.dumps({"status": "recorded", "parser_coverage": {"static_test_expectations": 1}}) + "\n",
            encoding="utf-8",
        )
        (work / "code" / "verification_report.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "blockers": [],
                    "warnings": [],
                    "executed": [{"command": "python -m unittest tests.test_api", "returncode": 0}],
                    "verification_strategy": {"focused_commands": ["python -m unittest tests.test_api"], "broad_commands": []},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "repair_loop_state.json").write_text(
            json.dumps({"status": "passed", "next_action": "continue_to_code_review"}) + "\n",
            encoding="utf-8",
        )
        result = run(request("code_review", "/work/code/code_review.json", target_repo_root=target_repo), work)
        if not result.get("ok"):
            raise AssertionError(f"public surface review gate test should write a report: {result}")
        review = json.loads((work / "code" / "code_review.json").read_text(encoding="utf-8"))
        public_review = review.get("public_surface_review", {})
        if (
            review.get("approved") is not False
            or public_review.get("status") != "blocked"
            or not any("public_surface" in item.get("message", "") or "Public surface review failed" in item.get("message", "") for item in review.get("findings", []) if isinstance(item, dict))
        ):
            raise AssertionError(f"code review should block incomplete public surface evidence: {review}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        final = run_pipeline(root / "work", goal=forbidden_verify_goal(), target_repo_root=target_repo)
        if final.get("status") != "blocked" or final.get("approved"):
            raise AssertionError(f"forbidden verification command should block final readiness: {final}")
        if final.get("verification_summary", {}).get("blocker_count", 0) < 1:
            raise AssertionError(f"blocked final manifest should preserve verification blockers: {final}")
        if final.get("review_repair_loop", {}).get("required") is not True:
            raise AssertionError(f"blocked final manifest should preserve review repair loop: {final}")
        focused_context = final.get("revision_plan", {}).get("focused_context", {})
        if (
            focused_context.get("patch_source") != "explicit_json_patch"
            or "sample.py" not in focused_context.get("changed_files", [])
        ):
            raise AssertionError(f"blocked final manifest should preserve focused revision context: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 2)\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=inferred_replace_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready":
            raise AssertionError(f"inferred replace task should be ready: {final}")
        if final.get("patch_source") != "natural_language_simple_replace" or final.get("operation_count") != 1:
            raise AssertionError(f"inferred replace final manifest should expose patch audit fields: {final}")
        if sample.read_text(encoding="utf-8") != "def value():\n    return 2\n":
            raise AssertionError("inferred replace task did not mutate the target file")
        if final.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"inferred replace final manifest should preserve verification evidence: {final}")
        source_test_links = final.get("patch_scope_evidence", {}).get("changed_sources_with_linked_tests", [])
        if not any(
            item.get("path") == "sample.py" and "test_sample.py" in item.get("tests", [])
            for item in source_test_links
            if isinstance(item, dict)
        ):
            raise AssertionError(f"final manifest should link changed source files to tests: {final}")
        if final.get("patch_scope_review", {}).get("status") != "covered":
            raise AssertionError(f"linked source/test patch should have covered scope review: {final}")
        survey = json.loads((root / "work" / "code" / "repo_survey.json").read_text(encoding="utf-8"))
        if survey.get("role_policy", {}).get("authority") != "read_only_repository_mapping":
            raise AssertionError(f"repository survey should preserve its read-only role policy: {survey}")
        symbol_paths = {item.get("path") for item in survey.get("python_symbols", []) if isinstance(item, dict)}
        if not {"sample.py", "test_sample.py"}.issubset(symbol_paths):
            raise AssertionError(f"repository survey should include Python symbol summaries: {survey}")
        if "python -m unittest discover" not in survey.get("suggested_verification_commands", []):
            raise AssertionError(f"repository survey should suggest Python unittest discovery: {survey}")
        repo_map = survey.get("repo_map", {})
        links = repo_map.get("test_source_links", [])
        if not any(
            item.get("test_path") == "test_sample.py" and "sample.py" in item.get("source_paths", [])
            for item in links
            if isinstance(item, dict)
        ):
            raise AssertionError(f"repository survey should link tests to imported source files: {survey}")
        ranked_paths = [item.get("path") for item in repo_map.get("ranked_files", []) if isinstance(item, dict)]
        if "sample.py" not in ranked_paths[:3]:
            raise AssertionError(f"repository survey should rank imported source files near the top: {survey}")
        if repo_map.get("recommended_read_order", [])[0].get("path") != "sample.py":
            raise AssertionError(f"repository survey should expose recommended read order: {survey}")
        plan_text = (root / "work" / "code" / "change_plan.md").read_text(encoding="utf-8")
        if (
            "## Python Symbol Surface" not in plan_text
            or "## Suggested Verification" not in plan_text
            or "## Ranked Repo Map" not in plan_text
            or "## Test Source Links" not in plan_text
            or "## Recommended Read Order" not in plan_text
        ):
            raise AssertionError(f"change plan should include repo-map, symbol, and verification sections: {plan_text}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("", encoding="utf-8")
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 42)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=inferred_add_function_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready":
            raise AssertionError(f"inferred add-function task should be ready: {final}")
        if final.get("patch_source") != "natural_language_add_function" or final.get("operation_count") != 1:
            raise AssertionError(f"inferred add-function final manifest should expose patch audit fields: {final}")
        if "def value():\n    return 42\n" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("inferred add-function task did not append the target function")
        if final.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"inferred add-function final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        final = run_pipeline(root / "work", goal=inferred_add_function_goal(), target_repo_root=target_repo)
        if final.get("status") != "blocked" or final.get("approved"):
            raise AssertionError(f"inferred add-function duplicate should block final readiness: {final}")
        if sample.read_text(encoding="utf-8").count("def value(") != 1:
            raise AssertionError("inferred add-function duplicate guard allowed duplicate function")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("", encoding="utf-8")
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 42)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_missing_function_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready":
            raise AssertionError(f"test-inferred missing function task should be ready: {final}")
        if final.get("patch_source") != "test_inferred_missing_function" or final.get("operation_count") != 1:
            raise AssertionError(f"test-inferred missing function should expose patch audit fields: {final}")
        if final.get("diagnostics", {}).get("function_name") != "value" or final.get("diagnostics", {}).get("test_path") != "test_sample.py":
            raise AssertionError(f"test-inferred missing function should expose diagnostics: {final}")
        if "def value():\n    return 42\n" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("test-inferred missing function task did not append the target function")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 42)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_missing_function_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready":
            raise AssertionError(f"test-inferred return mismatch task should be ready: {final}")
        if final.get("patch_source") != "test_inferred_return_mismatch" or final.get("operation_count") != 1:
            raise AssertionError(f"test-inferred return mismatch should expose patch audit fields: {final}")
        if final.get("diagnostics", {}).get("actual") != "1" or final.get("diagnostics", {}).get("expected") != "42":
            raise AssertionError(f"test-inferred return mismatch should expose diagnostics: {final}")
        if final.get("recommended_read_order", [])[0].get("path") != "sample.py":
            raise AssertionError(f"test-inferred return mismatch should preserve recommended read order: {final}")
        if sample.read_text(encoding="utf-8") != "def value():\n    return 42\n":
            raise AssertionError("test-inferred return mismatch task did not update the return value")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        (target_repo / "tests").mkdir(parents=True)
        quota = target_repo / "quota.py"
        quota.write_text("def max_daily_exports():\n    return 1\n", encoding="utf-8")
        (target_repo / "tests" / "test_quota_alias.py").write_text(
            "import unittest\n"
            "from quota import max_daily_exports as limit\n\n"
            "class QuotaAliasTest(unittest.TestCase):\n"
            "    def test_alias_limit(self):\n"
            "        self.assertEqual(limit(), 7)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=runtime_diagnostic_alias_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("patch_source") != "runtime_diagnostic_return_mismatch":
            raise AssertionError(f"runtime diagnostic alias task should be repaired by generic runtime candidate: {final}")
        diagnostics = final.get("diagnostics", {})
        runtime_diagnostic = diagnostics.get("runtime_diagnostic_extraction", {})
        runtime_candidates = runtime_diagnostic.get("runtime_minimal_patch_candidates", [])
        if (
            diagnostics.get("function_name") != "max_daily_exports"
            or diagnostics.get("actual") != "1"
            or diagnostics.get("expected") != "7"
            or not any(
                isinstance(item, dict)
                and item.get("path") == "quota.py"
                and item.get("function_name") == "max_daily_exports"
                and item.get("application_status") == "pending"
                for item in runtime_candidates
            )
        ):
            raise AssertionError(f"runtime diagnostic alias should expose minimal patch candidate evidence: {final}")
        if "return 7" not in quota.read_text(encoding="utf-8"):
            raise AssertionError("runtime diagnostic alias task did not patch the source return expression")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        calc = target_repo / "calc.py"
        calc.write_text("def add(left, right):\n    return left - right\n", encoding="utf-8")
        (target_repo / "test_calc.py").write_text(
            "import unittest\nfrom calc import add\n\n"
            "class CalcTest(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_arithmetic_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready":
            raise AssertionError(f"test-inferred arithmetic task should be ready: {final}")
        if final.get("patch_source") != "test_inferred_arithmetic_return" or final.get("operation_count") != 1:
            raise AssertionError(f"test-inferred arithmetic should expose patch audit fields: {final}")
        if (
            final.get("selected_patch_candidate", {}).get("source") != "test_inferred_arithmetic_return"
            or final.get("execution_report", {}).get("patch_candidate_count", 0) < 5
        ):
            raise AssertionError(f"test-inferred arithmetic should preserve candidate resolution chain: {final}")
        if final.get("diagnostics", {}).get("replacement_expression") != "left + right":
            raise AssertionError(f"test-inferred arithmetic diagnostics should explain replacement: {final}")
        repair_plan = final.get("unshaped_repair_plan", {})
        diagnostic_extraction = final.get("diagnostic_extraction", {})
        ast_patch_plan = final.get("ast_patch_plan", {})
        if (
            repair_plan.get("mode") != "unshaped_repo_repair"
            or not repair_plan.get("defect_hypotheses")
            or not repair_plan.get("minimal_patch_candidates")
            or not repair_plan.get("proof_plan", {}).get("focused_verification")
        ):
            raise AssertionError(f"test-inferred arithmetic should preserve an unshaped repair plan: {final}")
        symbol_links = repair_plan.get("test_symbol_links", [])
        if not any(
            isinstance(item, dict)
            and item.get("test_function") == "test_add"
            and item.get("imported_symbol") == "add"
            and item.get("source_path") == "calc.py"
            and item.get("expected_expression") == "5"
            for item in symbol_links
        ):
            raise AssertionError(f"test-inferred arithmetic should link test function to imported source symbol: {final}")
        if (
            diagnostic_extraction.get("status") != "recorded"
            or diagnostic_extraction.get("parser_coverage", {}).get("static_test_expectations", 0) < 1
        ):
            raise AssertionError(f"test-inferred arithmetic should preserve diagnostic extraction: {final}")
        if (
            ast_patch_plan.get("status") != "recorded"
            or ast_patch_plan.get("planned_operations", [{}])[0].get("kind") != "replace_return_expression"
            or ast_patch_plan.get("planned_operations", [{}])[0].get("function_name") != "add"
        ):
            raise AssertionError(f"test-inferred arithmetic should preserve AST minimal patch plan: {final}")
        if "return left + right" not in calc.read_text(encoding="utf-8"):
            raise AssertionError("test-inferred arithmetic did not update the return expression")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        pricing = target_repo / "pricing.py"
        pricing.write_text("def discounted_price(price, percent):\n    return price - percent\n", encoding="utf-8")
        (target_repo / "checkout.py").write_text(
            "from pricing import discounted_price\n\n"
            "def total_after_discount(price, percent):\n"
            "    return discounted_price(price, percent)\n",
            encoding="utf-8",
        )
        (target_repo / "test_checkout.py").write_text(
            "import unittest\nfrom checkout import total_after_discount\n\n"
            "class CheckoutTest(unittest.TestCase):\n"
            "    def test_percentage_discount(self):\n"
            "        self.assertEqual(total_after_discount(200, 25), 150)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_delegated_arithmetic_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("patch_source") != "test_inferred_arithmetic_return":
            raise AssertionError(f"delegated arithmetic task should be ready: {final}")
        diagnostics = final.get("diagnostics", {})
        if (
            diagnostics.get("module_path") != "pricing.py"
            or diagnostics.get("delegated_from", {}).get("module_path") != "checkout.py"
            or diagnostics.get("replacement_expression") != "price - (price * percent / 100)"
        ):
            raise AssertionError(f"delegated arithmetic diagnostics should explain wrapper traversal: {final}")
        repair_plan = final.get("unshaped_repair_plan", {})
        ast_patch_plan = final.get("ast_patch_plan", {})
        if (
            repair_plan.get("mode") != "unshaped_repo_repair"
            or not repair_plan.get("defect_hypotheses")
            or not any(
                isinstance(item, dict)
                and isinstance(item.get("delegated_from"), dict)
                and item.get("delegated_from", {}).get("module_path") == "checkout.py"
                for item in repair_plan.get("defect_hypotheses", [])
            )
        ):
            raise AssertionError(f"delegated arithmetic should preserve wrapper-aware repair plan: {final}")
        symbol_links = repair_plan.get("test_symbol_links", [])
        if not any(
            isinstance(item, dict)
            and item.get("test_function") == "test_percentage_discount"
            and item.get("imported_symbol") == "total_after_discount"
            and item.get("source_path") == "checkout.py"
            and item.get("expected_expression") == "150"
            for item in symbol_links
        ):
            raise AssertionError(f"delegated arithmetic should link public test assertion to imported caller symbol: {final}")
        if (
            ast_patch_plan.get("status") != "recorded"
            or ast_patch_plan.get("planned_operations", [{}])[0].get("path") != "pricing.py"
            or ast_patch_plan.get("planned_operations", [{}])[0].get("function_name") != "discounted_price"
        ):
            raise AssertionError(f"delegated arithmetic should preserve source AST patch plan: {final}")
        if "return price - (price * percent / 100)" not in pricing.read_text(encoding="utf-8"):
            raise AssertionError("delegated arithmetic did not update the implementation source")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "payments").mkdir()
        (target_repo / "tests").mkdir()
        (target_repo / "docs").mkdir()
        (target_repo / "payments" / "api.py").write_text(
            "def calculate_total(gross, fee):\n"
            "    return gross - fee\n",
            encoding="utf-8",
        )
        (target_repo / "payments" / "client.py").write_text(
            "from payments.api import calculate_total\n\n"
            "def client_total(gross, fee):\n"
            "    return calculate_total(gross, fee)\n",
            encoding="utf-8",
        )
        (target_repo / "docs" / "payments_api.md").write_text(
            "# Payments API\n\n"
            "`calculate_total(gross, fee)` subtracts fee from gross.\n",
            encoding="utf-8",
        )
        (target_repo / "tests" / "test_api_evolution.py").write_text(
            "import warnings\n"
            "import unittest\n"
            "from payments.api import calculate_total\n"
            "from payments.client import client_total\n\n"
            "class ApiEvolutionTest(unittest.TestCase):\n"
            "    def test_old_positional_fee_still_works_with_warning(self):\n"
            "        with warnings.catch_warnings(record=True) as caught:\n"
            "            warnings.simplefilter('always')\n"
            "            self.assertEqual(calculate_total(100, 15), 85)\n"
            "        self.assertTrue(any(item.category is DeprecationWarning for item in caught))\n\n"
            "    def test_new_keyword_service_fee_path(self):\n"
            "        self.assertEqual(calculate_total(80, service_fee=5), 75)\n"
            "        self.assertEqual(client_total(80, service_fee=5), 75)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_api_deprecation_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("patch_source") != "test_inferred_api_deprecation":
            raise AssertionError(f"API deprecation should be inferred from tests without marker: {final}")
        diagnostics = final.get("diagnostics", {})
        if (
            diagnostics.get("function_name") != "calculate_total"
            or diagnostics.get("old_param") != "fee"
            or diagnostics.get("new_param") != "service_fee"
            or diagnostics.get("caller", {}).get("caller_path") != "payments/client.py"
            or diagnostics.get("docs_path") != "docs/payments_api.md"
        ):
            raise AssertionError(f"API deprecation diagnostics should identify source/caller/docs: {final}")
        source = (target_repo / "payments" / "api.py").read_text(encoding="utf-8")
        caller = (target_repo / "payments" / "client.py").read_text(encoding="utf-8")
        docs = (target_repo / "docs" / "payments_api.md").read_text(encoding="utf-8")
        if "DeprecationWarning" not in source or "service_fee=service_fee" not in caller or "service_fee" not in docs:
            raise AssertionError("API deprecation inference did not update source, caller, and docs")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "service").mkdir()
        (target_repo / "tests").mkdir()
        (target_repo / "docs").mkdir()
        (target_repo / "service" / "records.py").write_text(
            "def normalize_record(record):\n"
            "    return {'id': record['id'], 'amount': record['amount']}\n",
            encoding="utf-8",
        )
        (target_repo / "docs" / "records.md").write_text(
            "# Records\n\nLegacy records contain `amount`; new records use `total_amount`.\n",
            encoding="utf-8",
        )
        (target_repo / "tests" / "test_records_migration.py").write_text(
            "import unittest\n"
            "from service.records import normalize_record, serialize_record\n\n"
            "class RecordsMigrationTest(unittest.TestCase):\n"
            "    def test_reads_old_shape(self):\n"
            "        self.assertEqual(normalize_record({'id': 'a1', 'amount': 12}), {'id': 'a1', 'total_amount': 12})\n\n"
            "    def test_reads_new_shape(self):\n"
            "        self.assertEqual(normalize_record({'id': 'b2', 'total_amount': 20}), {'id': 'b2', 'total_amount': 20})\n\n"
            "    def test_writer_emits_new_shape_only(self):\n"
            "        self.assertEqual(serialize_record({'id': 'c3', 'amount': 7}), {'id': 'c3', 'total_amount': 7})\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_data_migration_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("patch_source") != "test_inferred_data_migration":
            raise AssertionError(f"data migration should be inferred from tests without marker: {final}")
        diagnostics = final.get("diagnostics", {})
        if (
            diagnostics.get("read_function") != "normalize_record"
            or diagnostics.get("write_function") != "serialize_record"
            or diagnostics.get("old_field") != "amount"
            or diagnostics.get("new_field") != "total_amount"
        ):
            raise AssertionError(f"data migration diagnostics should identify old/new fields: {final}")
        records = (target_repo / "service" / "records.py").read_text(encoding="utf-8")
        if "'amount' in record" not in records or "'total_amount' in record" not in records or "def serialize_record" not in records:
            raise AssertionError("data migration inference did not preserve reader compatibility and writer output")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "tests").mkdir()
        (target_repo / "docs").mkdir()
        (target_repo / "archive_paths.py").write_text(
            "def safe_archive_path(raw):\n"
            "    return str(raw)\n",
            encoding="utf-8",
        )
        (target_repo / "docs" / "archive_paths.md").write_text(
            "# Archive Paths\n\nArchive paths must remain inside the archive root.\n",
            encoding="utf-8",
        )
        (target_repo / "tests" / "test_archive_paths.py").write_text(
            "import unittest\n"
            "from archive_paths import safe_archive_path\n\n"
            "class ArchivePathsTest(unittest.TestCase):\n"
            "    def test_valid_relative_paths_are_normalized(self):\n"
            "        self.assertEqual(safe_archive_path('books/chapter1.txt'), 'books/chapter1.txt')\n"
            "        self.assertEqual(safe_archive_path('./books//chapter2.txt'), 'books/chapter2.txt')\n\n"
            "    def test_traversal_and_absolute_paths_are_rejected(self):\n"
            "        for raw in ('../secret.txt', '/etc/passwd', 'books/../../secret.txt'):\n"
            "            with self.subTest(raw=raw):\n"
            "                with self.assertRaises(ValueError):\n"
            "                    safe_archive_path(raw)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_security_boundary_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("patch_source") != "test_inferred_security_boundary":
            raise AssertionError(f"security boundary should be inferred from tests without marker: {final}")
        diagnostics = final.get("diagnostics", {})
        ast_patch_plan = final.get("ast_patch_plan", {})
        if (
            diagnostics.get("function_name") != "safe_archive_path"
            or diagnostics.get("source_path") != "archive_paths.py"
            or diagnostics.get("malicious_case_count", 0) < 2
        ):
            raise AssertionError(f"security boundary diagnostics should identify threat surface: {final}")
        if (
            ast_patch_plan.get("status") != "recorded"
            or not any(
                isinstance(item, dict)
                and item.get("kind") == "add_validation_branch"
                and item.get("path") == "archive_paths.py"
                and item.get("function_name") == "safe_archive_path"
                and item.get("validation_exception") == "ValueError"
                for item in ast_patch_plan.get("planned_operations", [])
            )
        ):
            raise AssertionError(f"security boundary should preserve AST validation-branch plan: {final}")
        archive_paths = (target_repo / "archive_paths.py").read_text(encoding="utf-8")
        docs = (target_repo / "docs" / "archive_paths.md").read_text(encoding="utf-8")
        if "'..' in parts" not in archive_paths or "startswith('/')" not in archive_paths or "'/'.join(parts)" not in archive_paths:
            raise AssertionError("security boundary inference did not reject traversal and normalize relative paths")
        if "archive-root" not in docs and "archive root" not in docs:
            raise AssertionError("security boundary inference did not update audit docs")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "tests").mkdir()
        (target_repo / "docs").mkdir()
        (target_repo / "cache_store.py").write_text(
            "class CacheStore:\n"
            "    def __init__(self):\n"
            "        self._values = {}\n\n"
            "    def get_or_load(self, key, loader):\n"
            "        if key not in self._values:\n"
            "            self._values[key] = loader()\n"
            "        return self._values[key]\n",
            encoding="utf-8",
        )
        (target_repo / "docs" / "cache_store.md").write_text(
            "# Cache Store\n\nConcurrent readers should share loaded values safely.\n",
            encoding="utf-8",
        )
        (target_repo / "tests" / "test_cache_store.py").write_text(
            "import threading\n"
            "import unittest\n"
            "from cache_store import CacheStore\n\n"
            "class CacheStoreTest(unittest.TestCase):\n"
            "    def test_invalidate_is_idempotent_and_reloadable(self):\n"
            "        store = CacheStore()\n"
            "        self.assertEqual(store.get_or_load('a', lambda: 'old'), 'old')\n"
            "        self.assertEqual(store.invalidate('a'), 1)\n"
            "        self.assertEqual(store.invalidate('a'), 2)\n"
            "        self.assertEqual(store.get_or_load('a', lambda: 'new'), 'new')\n\n"
            "    def test_concurrent_readers_share_loaded_value(self):\n"
            "        store = CacheStore()\n"
            "        calls = []\n"
            "        def loader():\n"
            "            calls.append(1)\n"
            "            return 'value'\n"
            "        results = []\n"
            "        threads = [threading.Thread(target=lambda: results.append(store.get_or_load('k', loader))) for _ in range(8)]\n"
            "        for thread in threads:\n"
            "            thread.start()\n"
            "        for thread in threads:\n"
            "            thread.join()\n"
            "        self.assertEqual(results, ['value'] * 8)\n"
            "        self.assertEqual(len(calls), 1)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_cache_concurrency_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("patch_source") != "test_inferred_cache_concurrency":
            raise AssertionError(f"cache concurrency should be inferred from tests without marker: {final}")
        diagnostics = final.get("diagnostics", {})
        if diagnostics.get("class_name") != "CacheStore" or diagnostics.get("source_path") != "cache_store.py":
            raise AssertionError(f"cache concurrency diagnostics should identify class/source: {final}")
        cache_store = (target_repo / "cache_store.py").read_text(encoding="utf-8")
        docs = (target_repo / "docs" / "cache_store.md").read_text(encoding="utf-8")
        if "RLock" not in cache_store or "pop(key, None)" not in cache_store or "sleep(" in cache_store:
            raise AssertionError("cache concurrency inference did not add lock/idempotent invalidation cleanly")
        if "RLock" not in docs or "sleep-based" not in docs:
            raise AssertionError("cache concurrency inference did not update concurrency docs")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "tests").mkdir()
        (target_repo / "docs").mkdir()
        (target_repo / "scheduler.py").write_text(
            "def schedule_order(items):\n"
            "    return sorted(items, key=lambda item: item['priority'])\n",
            encoding="utf-8",
        )
        (target_repo / "docs" / "scheduler.md").write_text(
            "# Scheduler\n\nEqual-priority item ordering must be deterministic.\n",
            encoding="utf-8",
        )
        (target_repo / "tests" / "test_scheduler.py").write_text(
            "import unittest\n"
            "from scheduler import schedule_order\n\n"
            "class SchedulerTest(unittest.TestCase):\n"
            "    def test_stable_order_for_equal_priority(self):\n"
            "        items = [{'id': 'b', 'priority': 1}, {'id': 'a', 'priority': 1}]\n"
            "        self.assertEqual([item['id'] for item in schedule_order(items)], ['a', 'b'])\n\n"
            "    def test_repeated_stability(self):\n"
            "        for _ in range(20):\n"
            "            items = [{'id': 'c', 'priority': 2}, {'id': 'a', 'priority': 1}, {'id': 'b', 'priority': 1}]\n"
            "            self.assertEqual([item['id'] for item in schedule_order(items)], ['a', 'b', 'c'])\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_flaky_ordering_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("patch_source") != "test_inferred_flaky_ordering":
            raise AssertionError(f"flaky ordering should be inferred from repeated tests without marker: {final}")
        diagnostics = final.get("diagnostics", {})
        if diagnostics.get("function_name") != "schedule_order" or diagnostics.get("tie_breaker") != "id":
            raise AssertionError(f"flaky ordering diagnostics should identify deterministic tie-breaker: {final}")
        scheduler = (target_repo / "scheduler.py").read_text(encoding="utf-8")
        docs = (target_repo / "docs" / "scheduler.md").read_text(encoding="utf-8")
        if "(item['priority'], item['id'])" not in scheduler or "sleep(" in scheduler:
            raise AssertionError("flaky ordering inference did not add deterministic tie-breaker cleanly")
        if "Root cause" not in docs or "tie-breaker" not in docs:
            raise AssertionError("flaky ordering inference did not document root cause")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "tests").mkdir()
        (target_repo / "docs").mkdir()
        (target_repo / "client.py").write_text(
            "def publish_event(transport, event):\n"
            "    return transport.send(event)\n",
            encoding="utf-8",
        )
        (target_repo / "docs" / "client.md").write_text(
            "# Client\n\nTransient transport failures should be retried; validation failures should surface immediately.\n",
            encoding="utf-8",
        )
        (target_repo / "tests" / "test_client.py").write_text(
            "import unittest\n"
            "from client import publish_event\n\n"
            "class FlakyTransport:\n"
            "    def __init__(self):\n"
            "        self.calls = 0\n"
            "    def send(self, event):\n"
            "        self.calls += 1\n"
            "        if self.calls < 3:\n"
            "            raise ConnectionError('temporary outage')\n"
            "        return {'ok': True, 'event': event}\n\n"
            "class ValidationTransport:\n"
            "    def __init__(self):\n"
            "        self.calls = 0\n"
            "    def send(self, event):\n"
            "        self.calls += 1\n"
            "        raise ValueError('invalid event')\n\n"
            "class ClientTest(unittest.TestCase):\n"
            "    def test_retries_transient_connection_errors(self):\n"
            "        transport = FlakyTransport()\n"
            "        self.assertEqual(publish_event(transport, {'id': 'evt-1'}), {'ok': True, 'event': {'id': 'evt-1'}})\n"
            "        self.assertEqual(transport.calls, 3)\n\n"
            "    def test_validation_errors_are_not_retried(self):\n"
            "        transport = ValidationTransport()\n"
            "        with self.assertRaises(ValueError):\n"
            "            publish_event(transport, {'bad': True})\n"
            "        self.assertEqual(transport.calls, 1)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_retry_policy_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("patch_source") != "test_inferred_retry_policy":
            raise AssertionError(f"retry policy should be inferred from tests without marker: {final}")
        diagnostics = final.get("diagnostics", {})
        if diagnostics.get("retry_exception") != "ConnectionError" or diagnostics.get("non_retry_exception") != "ValueError":
            raise AssertionError(f"retry diagnostics should identify retry boundary: {final}")
        client = (target_repo / "client.py").read_text(encoding="utf-8")
        docs = (target_repo / "docs" / "client.md").read_text(encoding="utf-8")
        if "except ConnectionError" not in client or "except Exception" in client or "sleep(" in client:
            raise AssertionError("retry policy inference did not keep retry boundary clean")
        if "Retry policy" not in docs or "Validation" not in docs:
            raise AssertionError("retry policy inference did not document retry boundary")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "tests").mkdir()
        (target_repo / "quota.py").write_text(
            "def max_daily_exports():\n"
            "    return 0\n",
            encoding="utf-8",
        )
        (target_repo / "tests" / "test_quota.py").write_text(
            "import unittest\n"
            "from quota import max_daily_exports\n\n"
            "class QuotaTest(unittest.TestCase):\n"
            "    def test_max_daily_exports(self):\n"
            "        self.assertEqual(max_daily_exports(), 7)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_self_repair_seed_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("patch_source") != "test_inferred_self_repair_seed":
            raise AssertionError(f"self-repair seed should be inferred without marker: {final}")
        summary = final.get("verification_summary", {})
        if summary.get("repair_count") != 1 or summary.get("blocker_count") != 0:
            raise AssertionError(f"self-repair seed should preserve exactly one successful repair: {final}")
        repairs = final.get("verification_repairs", [])
        if not repairs or repairs[0].get("kind") != "assertion_return_mismatch":
            raise AssertionError(f"self-repair seed should repair from AssertionError mismatch: {final}")
        diagnostic_extraction = final.get("diagnostic_extraction", {})
        runtime_failures = diagnostic_extraction.get("runtime_test_failures", [])
        runtime_candidates = diagnostic_extraction.get("runtime_minimal_patch_candidates", [])
        if (
            diagnostic_extraction.get("parser_coverage", {}).get("traceback_frames", 0) < 1
            or diagnostic_extraction.get("parser_coverage", {}).get("runtime_test_failures", 0) < 1
            or diagnostic_extraction.get("parser_coverage", {}).get("runtime_minimal_patch_candidates", 0) < 1
            or not any(
                isinstance(item, dict)
                and item.get("test_path") == "tests/test_quota.py"
                and item.get("test_function") == "test_max_daily_exports"
                and "quota.py" in item.get("candidate_source_paths", [])
                for item in runtime_failures
            )
            or not any(
                isinstance(item, dict)
                and item.get("kind") == "replace_return_expression"
                and item.get("path") == "quota.py"
                and item.get("function_name") == "max_daily_exports"
                and item.get("old_expression") == "1"
                and item.get("new_expression") == "7"
                for item in runtime_candidates
            )
        ):
            raise AssertionError(f"self-repair seed should preserve runtime traceback diagnostic extraction: {final}")
        quota = (target_repo / "quota.py").read_text(encoding="utf-8")
        if "return 7" not in quota or "return 1" in quota:
            raise AssertionError("self-repair seed did not leave the repaired expected value")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=partial_failure_goal(), target_repo_root=target_repo)
        if final.get("status") != "blocked" or final.get("approved"):
            raise AssertionError(f"partial patch failure should block final readiness: {final}")
        if (target_repo / "created_before_failure.py").exists():
            raise AssertionError("partial patch failure left a created file behind")
        patch_manifest = json.loads((root / "work" / "code" / "patch_manifest.json").read_text(encoding="utf-8"))
        rollback = patch_manifest.get("rollback", {})
        if not rollback.get("applied") or rollback.get("files", [{}])[0].get("path") != "created_before_failure.py":
            raise AssertionError(f"partial patch failure should record rollback evidence: {patch_manifest}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        settings = target_repo / "settings.py"
        settings.write_text("def timeout_seconds():\n    return 30\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=target_repo, check=True)
        subprocess.run(["git", "config", "user.email", "ceraxia-test@example.invalid"], cwd=target_repo, check=True)
        subprocess.run(["git", "config", "user.name", "Ceraxia Test"], cwd=target_repo, check=True)
        subprocess.run(["git", "add", "settings.py"], cwd=target_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=target_repo, check=True)
        settings.write_text(
            "def timeout_seconds():\n"
            "    # user local experiment, must not be overwritten\n"
            "    return 45\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=dirty_worktree_goal(), target_repo_root=target_repo)
        if final.get("status") != "blocked" or not any("uncommitted user changes" in item for item in final.get("blockers", [])):
            raise AssertionError(f"dirty worktree target should block mutation with safety evidence: {final}")
        if "return 45" not in settings.read_text(encoding="utf-8"):
            raise AssertionError("dirty worktree guard overwrote user changes")
        dirty = final.get("dirty_worktree", {})
        if not dirty.get("dirty_targets") or dirty.get("dirty_targets", [{}])[0].get("path") != "settings.py":
            raise AssertionError(f"dirty worktree evidence missing target path: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "parser.py").write_text("def parse_amount(raw):\n    return int(raw)\n", encoding="utf-8")
        (target_repo / "api.py").write_text(
            "from parser import parse_amount\n\n"
            "def handle_payload(payload):\n"
            "    return {'amount': parse_amount(payload['amount'])}\n",
            encoding="utf-8",
        )
        (target_repo / "test_api.py").write_text(
            "import unittest\nfrom api import handle_payload\n\n"
            "class ApiTest(unittest.TestCase):\n"
            "    def test_valid_amount(self):\n"
            "        self.assertEqual(handle_payload({'amount': '12'}), {'amount': 12})\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=ambiguous_goal(), target_repo_root=target_repo)
        ambiguity = final.get("ambiguity_analysis", {})
        if final.get("status") != "blocked" or not any("Ambiguous code task" in item for item in final.get("blockers", [])):
            raise AssertionError(f"ambiguous task should block with clarification request: {final}")
        if (
            ambiguity.get("status") != "ambiguous"
            or len(ambiguity.get("candidate_interpretations", [])) < 2
            or "expected behavior" not in ambiguity.get("safe_next_question", "")
        ):
            raise AssertionError(f"ambiguous task should preserve candidate interpretations: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=create_file_goal(), target_repo_root=target_repo)
        generated = target_repo / "generated.py"
        if final.get("status") != "ready" or not generated.exists():
            raise AssertionError(f"marker-synthesized create file task should be ready: {final}")
        if "return 42" not in generated.read_text(encoding="utf-8"):
            raise AssertionError("marker-synthesized create file task wrote wrong content")
        if final.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"marker final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=multi_file_goal(), target_repo_root=target_repo)
        calc = target_repo / "calc.py"
        test_calc = target_repo / "test_calc.py"
        if final.get("status") != "ready" or not calc.exists() or not test_calc.exists():
            raise AssertionError(f"multi-file marker task should be ready: {final}")
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        if changed_paths != {"calc.py", "test_calc.py"}:
            raise AssertionError(f"multi-file marker should preserve both changed files: {final}")
        if final.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"multi-file final manifest should preserve verification evidence: {final}")
        repeated = run_pipeline(root / "work_repeat", goal=multi_file_goal(), target_repo_root=target_repo)
        if repeated.get("status") != "ready":
            raise AssertionError(f"repeated multi-file marker task should remain ready: {repeated}")
        repeated_files = repeated.get("changed_files", [])
        if not repeated_files or not all(item.get("idempotent") for item in repeated_files if isinstance(item, dict)):
            raise AssertionError(f"repeated multi-file marker should report idempotent writes: {repeated}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=repo_grade_workflow_goal(), target_repo_root=target_repo)
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        expected_paths = {
            "src/billing/pricing.py",
            "src/billing/tax.py",
            "src/billing/invoice.py",
            "src/billing/reporting.py",
            "config/billing.json",
            "docs/billing.md",
            "tests/test_invoice.py",
            "tests/test_reporting.py",
            "README.md",
        }
        if final.get("status") != "ready" or changed_paths != expected_paths:
            raise AssertionError(f"repo-grade workflow task should write the full patch surface: {final}")
        if (
            final.get("repo_grade_workflow", {}).get("mode") != "repo_grade"
            or final.get("architecture_decision_record", {}).get("status") != "recorded"
            or final.get("patch_package", {}).get("kind") != "ceraxia_patch_package"
            or not final.get("pr_summary", {}).get("verification")
        ):
            raise AssertionError(f"repo-grade final manifest should preserve architecture and package evidence: {final}")
        strategy = final.get("verification_strategy", {})
        if (
            len(strategy.get("focused_commands", [])) < 3
            or len(strategy.get("broad_commands", [])) < 1
        ):
            raise AssertionError(f"repo-grade task should preserve focused and broad verification: {final}")
        review_checks = {
            item.get("check"): item.get("status")
            for item in final.get("review_decision_record", [])
            if isinstance(item, dict)
        }
        if (
            review_checks.get("architecture_decision_record_present") != "pass"
            or review_checks.get("broad_verification_present") != "pass"
        ):
            raise AssertionError(f"repo-grade review should gate architecture and broad verification: {final}")
        plan_text = (root / "work" / "code" / "change_plan.md").read_text(encoding="utf-8")
        if "## Architecture Decision Record" not in plan_text or "## Repo-Grade Workflow" not in plan_text:
            raise AssertionError(f"repo-grade change plan should expose ADR and workflow sections: {plan_text}")
        if (
            final.get("problem_statement", {}).get("status") != "recorded"
            or final.get("architecture_options", {}).get("status") != "recorded"
            or final.get("architect_review", {}).get("architecture_options_present") is not True
        ):
            raise AssertionError(f"repo-grade final manifest should preserve architect evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=feature_goal(), target_repo_root=target_repo)
        expected_paths = {"billing/discounts.py", "tests/test_discounts.py", "docs/discounts.md", "billing/api.py"}
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        if final.get("status") != "ready" or final.get("patch_source") != "feature_marker_synthesis":
            raise AssertionError(f"feature marker task should be ready: {final}")
        if changed_paths != expected_paths:
            raise AssertionError(f"feature marker should write source/test/docs/caller: {final}")
        if "def apply_discount" not in (target_repo / "billing" / "discounts.py").read_text(encoding="utf-8"):
            raise AssertionError("feature marker did not write source function")
        if "Discount helpers" not in (target_repo / "docs" / "discounts.md").read_text(encoding="utf-8"):
            raise AssertionError("feature marker did not write docs")
        if final.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"feature marker final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=config_runtime_goal(), target_repo_root=target_repo)
        expected_paths = {"app/settings.json", "app/config_loader.py", "bin/run-app.sh", "tests/test_config_loader.py"}
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        if final.get("status") != "ready" or final.get("patch_source") != "config_runtime_marker_synthesis":
            raise AssertionError(f"config/runtime marker task should be ready: {final}")
        if changed_paths != expected_paths:
            raise AssertionError(f"config/runtime marker should write config/loader/entrypoint/test: {final}")
        if "SERVICE_URL" not in (target_repo / "bin" / "run-app.sh").read_text(encoding="utf-8"):
            raise AssertionError("config/runtime marker did not write entrypoint env var")
        if '"service_url"' not in (target_repo / "app" / "settings.json").read_text(encoding="utf-8"):
            raise AssertionError("config/runtime marker did not write JSON config")
        if final.get("verification_summary", {}).get("executed_count", 0) < 3:
            raise AssertionError(f"config/runtime final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        (target_repo / "app").mkdir(parents=True)
        (target_repo / "bin").mkdir(parents=True)
        (target_repo / "tests").mkdir(parents=True)
        (target_repo / "app" / "settings.json").write_text(
            json.dumps({"serviceUrl": "http://wrong.local"}, indent=2) + "\n",
            encoding="utf-8",
        )
        (target_repo / "app" / "config_loader.py").write_text(
            "import json\n"
            "from pathlib import Path\n\n"
            "CONFIG_PATH = Path(__file__).resolve().parent / 'settings.json'\n\n"
            "def load_settings():\n"
            "    data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))\n"
            "    return {'serviceUrl': data['serviceUrl']}\n",
            encoding="utf-8",
        )
        (target_repo / "bin" / "run-app.sh").write_text(
            "#!/usr/bin/env sh\nset -eu\nexport APP_URL=\"${APP_URL:-http://wrong.local}\"\npython -m app.config_loader\n",
            encoding="utf-8",
        )
        (target_repo / "tests" / "test_config_loader.py").write_text(
            "import os\nimport unittest\nfrom app.config_loader import load_settings\n\n"
            "class ConfigLoaderTest(unittest.TestCase):\n"
            "    def test_default_service_url(self):\n"
            "        os.environ.pop('SERVICE_URL', None)\n"
            "        self.assertEqual(load_settings()['service_url'], 'http://localhost:8080')\n\n"
            "    def test_env_override(self):\n"
            "        os.environ['SERVICE_URL'] = 'https://prod.example'\n"
            "        try:\n"
            "            self.assertEqual(load_settings()['service_url'], 'https://prod.example')\n"
            "        finally:\n"
            "            os.environ.pop('SERVICE_URL', None)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=unshaped_config_runtime_goal(), target_repo_root=target_repo)
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        expected_paths = {"app/settings.json", "app/config_loader.py", "bin/run-app.sh"}
        if final.get("status") != "ready" or final.get("patch_source") != "test_inferred_config_runtime":
            raise AssertionError(f"unshaped config/runtime task should be ready through inferred repair: {final}")
        if changed_paths != expected_paths:
            raise AssertionError(f"unshaped config/runtime should mutate config, loader, and entrypoint only: {final}")
        if "SERVICE_URL" not in (target_repo / "bin" / "run-app.sh").read_text(encoding="utf-8"):
            raise AssertionError("unshaped config/runtime did not align entrypoint env var")
        if '"service_url"' not in (target_repo / "app" / "settings.json").read_text(encoding="utf-8"):
            raise AssertionError("unshaped config/runtime did not normalize JSON setting")
        if "assertEqual(load_settings()['service_url']" not in (target_repo / "tests" / "test_config_loader.py").read_text(encoding="utf-8"):
            raise AssertionError("unshaped config/runtime should not edit tests")
        if final.get("unshaped_repair_plan", {}).get("mode") != "unshaped_repo_repair":
            raise AssertionError(f"unshaped config/runtime should preserve repair plan: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "orders.py").write_text(
            "def order_total(gross, fee):\n"
            "    return gross - fee\n",
            encoding="utf-8",
        )
        (target_repo / "refunds.py").write_text(
            "def refund_total(gross, fee):\n"
            "    return gross - fee\n",
            encoding="utf-8",
        )
        (target_repo / "test_totals.py").write_text(
            "import unittest\nfrom orders import order_total\nfrom refunds import refund_total\n\n"
            "class TotalsTest(unittest.TestCase):\n"
            "    def test_order_total(self):\n"
            "        self.assertEqual(order_total(100, 15), 85)\n\n"
            "    def test_refund_total(self):\n"
            "        self.assertEqual(refund_total(80, 5), 75)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=refactor_goal(), target_repo_root=target_repo)
        expected_paths = {"common/calculations.py", "orders.py", "refunds.py"}
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        if final.get("status") != "ready" or final.get("patch_source") != "refactor_marker_synthesis":
            raise AssertionError(f"refactor marker task should be ready: {final}")
        if changed_paths != expected_paths:
            raise AssertionError(f"refactor marker should touch helper and duplicate modules only: {final}")
        if "def net_amount" not in (target_repo / "common" / "calculations.py").read_text(encoding="utf-8"):
            raise AssertionError("refactor marker did not write helper function")
        if "order_total" not in final.get("diagnostics", {}).get("public_functions", []):
            raise AssertionError(f"refactor marker should preserve public function evidence: {final}")
        if final.get("verification_summary", {}).get("executed_count", 0) < 3:
            raise AssertionError(f"refactor final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "retry_policy.py").write_text(
            "def parse_retry_count(raw):\n"
            "    return int(raw)\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=edge_fix_goal(), target_repo_root=target_repo)
        expected_paths = {"retry_policy.py", "test_retry_policy.py"}
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        diagnostics = final.get("diagnostics", {})
        if final.get("status") != "ready" or final.get("patch_source") != "edge_fix_marker_synthesis":
            raise AssertionError(f"edge-fix marker task should be ready: {final}")
        if changed_paths != expected_paths:
            raise AssertionError(f"edge-fix marker should touch source and test only: {final}")
        if diagnostics.get("positive_case_count") != 3 or diagnostics.get("negative_case_count") != 3:
            raise AssertionError(f"edge-fix marker should preserve positive/negative evidence: {final}")
        if "assertRaises" not in (target_repo / "test_retry_policy.py").read_text(encoding="utf-8"):
            raise AssertionError("edge-fix marker did not write negative tests")
        if final.get("verification_summary", {}).get("executed_count", 0) < 3:
            raise AssertionError(f"edge-fix final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "records.py").write_text(
            "def normalize_record(record):\n"
            "    return {'id': record['id'], 'amount': record['amount']}\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=data_migration_goal(), target_repo_root=target_repo)
        expected_paths = {"records.py", "test_records.py"}
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        diagnostics = final.get("diagnostics", {})
        if final.get("status") != "ready" or final.get("patch_source") != "data_migration_marker_synthesis":
            raise AssertionError(f"data migration marker task should be ready: {final}")
        if changed_paths != expected_paths:
            raise AssertionError(f"data migration marker should touch source and tests only: {final}")
        if diagnostics.get("old_field") != "amount" or diagnostics.get("new_field") != "total_amount":
            raise AssertionError(f"data migration diagnostics should preserve schema evidence: {final}")
        test_text = (target_repo / "test_records.py").read_text(encoding="utf-8")
        if "test_reads_old_shape" not in test_text or "test_writer_emits_new_shape_only" not in test_text:
            raise AssertionError("data migration marker did not write compatibility tests")
        if final.get("verification_summary", {}).get("executed_count", 0) < 3:
            raise AssertionError(f"data migration final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=integration_contract_goal(), target_repo_root=target_repo)
        expected_paths = {
            "contracts/invoice.json",
            "api/invoice_service.py",
            "client/invoice_client.py",
            "tests/test_invoice_contract.py",
            "reports/invoice_contract.md",
        }
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        diagnostics = final.get("diagnostics", {})
        if final.get("status") != "ready" or final.get("patch_source") != "integration_contract_marker_synthesis":
            raise AssertionError(f"integration contract marker task should be ready: {final}")
        if changed_paths != expected_paths:
            raise AssertionError(f"integration contract marker should touch contract/implementation/caller/test/report: {final}")
        if diagnostics.get("response_field") != "net_total" or diagnostics.get("caller_path") != "client/invoice_client.py":
            raise AssertionError(f"integration contract diagnostics should preserve contract evidence: {final}")
        if "net_total" not in (target_repo / "contracts" / "invoice.json").read_text(encoding="utf-8"):
            raise AssertionError("integration contract marker did not write response field to contract")
        if "invoice_total" not in (target_repo / "client" / "invoice_client.py").read_text(encoding="utf-8"):
            raise AssertionError("integration contract marker did not write caller")
        if final.get("verification_summary", {}).get("executed_count", 0) < 3:
            raise AssertionError(f"integration contract final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=public_api_compat_goal(), target_repo_root=target_repo)
        expected_paths = {
            "billing/public_api.py",
            "billing/client.py",
            "docs/public_api.md",
            "tests/test_public_api.py",
        }
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        diagnostics = final.get("diagnostics", {})
        if final.get("status") != "ready" or final.get("patch_source") != "public_api_compat_marker_synthesis":
            raise AssertionError(f"public API compat marker task should be ready: {final}")
        if changed_paths != expected_paths:
            raise AssertionError(f"public API compat marker should touch source/caller/docs/test: {final}")
        if diagnostics.get("public_signature") != "calculate_total(gross, fee)":
            raise AssertionError(f"public API compat diagnostics should preserve signature evidence: {final}")
        source_text = (target_repo / "billing" / "public_api.py").read_text(encoding="utf-8")
        test_text = (target_repo / "tests" / "test_public_api.py").read_text(encoding="utf-8")
        if "def calculate_total(gross, fee):" not in source_text:
            raise AssertionError("public API compat marker changed public signature")
        if "inspect.signature" not in test_text:
            raise AssertionError("public API compat marker did not write signature regression test")
        if final.get("verification_summary", {}).get("executed_count", 0) < 3:
            raise AssertionError(f"public API compat final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=repair_colon_goal(), target_repo_root=target_repo)
        repaired = target_repo / "repair_me.py"
        if final.get("status") != "ready" or final.get("verification_summary", {}).get("repair_count") != 1:
            raise AssertionError(f"expected-colon repair should produce ready final manifest: {final}")
        if "def repaired_value():\n" not in repaired.read_text(encoding="utf-8"):
            raise AssertionError("expected-colon repair did not update the source file")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 2)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=repair_assertion_goal(), target_repo_root=target_repo)
        sample = target_repo / "sample.py"
        if final.get("status") != "ready" or final.get("verification_summary", {}).get("repair_count") != 1:
            raise AssertionError(f"assertion repair should produce ready final manifest: {final}")
        if "return 2" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("assertion repair did not update the return value")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 42)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=repair_name_error_goal(), target_repo_root=target_repo)
        sample = target_repo / "sample.py"
        if final.get("status") != "ready" or final.get("verification_summary", {}).get("repair_count") != 1:
            raise AssertionError(f"NameError repair should produce ready final manifest: {final}")
        if "return 42" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("NameError repair did not update the undefined return")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 42)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=repair_import_error_goal(), target_repo_root=target_repo)
        sample = target_repo / "sample.py"
        if final.get("status") != "ready" or final.get("verification_summary", {}).get("repair_count") != 1:
            raise AssertionError(f"ImportError repair should produce ready final manifest: {final}")
        if "def value():\n    return 42\n" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("ImportError repair did not add the missing function")
    print("[ok] CogitatorCodewright code artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
