from __future__ import annotations

from typing import Any

from .common import replace_project_file


def apply_data_processing_csv_summary_feature(project_name: str, files: list[Any]) -> list[Any]:
    package = project_name.replace("-", "_")
    processor = (
        "import csv\n"
        "from io import StringIO\n\n\n"
        "def _to_number(value: str) -> float | None:\n"
        "    try:\n"
        "        return float(value)\n"
        "    except (TypeError, ValueError):\n"
        "        return None\n\n\n"
        "def summarize_rows(csv_text: str) -> dict[str, object]:\n"
        "    reader = csv.DictReader(StringIO(csv_text))\n"
        "    rows = list(reader)\n"
        "    columns = list(reader.fieldnames or [])\n"
        "    numeric_values: dict[str, list[float]] = {column: [] for column in columns}\n"
        "    for row in rows:\n"
        "        for column in columns:\n"
        "            number = _to_number(row.get(column, ''))\n"
        "            if number is not None:\n"
        "                numeric_values[column].append(number)\n"
        "    numeric_columns = {column: values for column, values in numeric_values.items() if values}\n"
        "    sums = {column: sum(values) for column, values in numeric_columns.items()}\n"
        "    averages = {column: sums[column] / len(values) for column, values in numeric_columns.items()}\n"
        "    return {\n"
        "        'rows': len(rows),\n"
        "        'columns': columns,\n"
        "        'numeric_sums': sums,\n"
        "        'numeric_averages': averages,\n"
        "    }\n"
    )
    cli = (
        "from pathlib import Path\n"
        "import json\n"
        "import sys\n\n"
        "from .processor import summarize_rows\n\n\n"
        "def main(argv: list[str] | None = None) -> None:\n"
        "    args = list(sys.argv[1:] if argv is None else argv)\n"
        "    if not args:\n"
        "        raise SystemExit('input csv path required')\n"
        "    summary = summarize_rows(Path(args[0]).read_text(encoding='utf-8'))\n"
        "    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    tests = (
        f"import unittest\n\nfrom {package}.processor import summarize_rows\n\n\n"
        "class CsvSummaryTests(unittest.TestCase):\n"
        "    def test_counts_rows_columns_sums_and_averages(self):\n"
        "        summary = summarize_rows('name,score,cost\\na,10,2.5\\nb,20,3.5\\n')\n"
        "        self.assertEqual(summary['rows'], 2)\n"
        "        self.assertEqual(summary['columns'], ['name', 'score', 'cost'])\n"
        "        self.assertEqual(summary['numeric_sums'], {'score': 30.0, 'cost': 6.0})\n"
        "        self.assertEqual(summary['numeric_averages'], {'score': 15.0, 'cost': 3.0})\n\n"
        "    def test_ignores_non_numeric_values(self):\n"
        "        summary = summarize_rows('name,score\\na,10\\nb,nope\\n')\n"
        "        self.assertEqual(summary['numeric_sums'], {'score': 10.0})\n"
    )
    readme = (
        f"# {project_name}\n\nA CSV summary tool that reports row count, columns, numeric sums, and numeric averages.\n\n"
        "## Run\n\n```bash\npython -m "
        f"{package}.cli input.csv\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, f"{package}/processor.py", processor)
    rows = replace_project_file(rows, f"{package}/cli.py", cli)
    rows = replace_project_file(rows, "tests/test_processor.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def csv_summary_module_contracts(project_name: str) -> list[dict[str, Any]]:
    package = project_name.replace("-", "_")
    return [
        {
            "module": f"{package}.processor",
            "path": f"{package}/processor.py",
            "responsibility": "CSV summary domain logic",
            "requirements": ["count rows", "list columns", "sum numeric columns", "average numeric columns", "ignore non-numeric values"],
        },
        {
            "module": f"{package}.cli",
            "path": f"{package}/cli.py",
            "responsibility": "file-based CSV summary CLI",
            "requirements": ["read input path", "print JSON summary"],
        },
        {
            "module": "tests.test_processor",
            "path": "tests/test_processor.py",
            "responsibility": "CSV summary behavior verification",
            "requirements": ["prove row and column summary", "prove numeric sums and averages", "prove non-numeric values are ignored"],
        },
    ]

def apply_sales_analytics_pipeline_feature(project_name: str, files: list[Any]) -> list[Any]:
    package = project_name.replace("-", "_")
    loader = (
        "import csv\n"
        "from dataclasses import dataclass\n"
        "from io import StringIO\n\n\n"
        "@dataclass(frozen=True)\n"
        "class SaleRecord:\n"
        "    region: str\n"
        "    product: str\n"
        "    amount: float\n"
        "    channel: str\n\n\n"
        "def load_records(csv_text: str) -> list[SaleRecord]:\n"
        "    reader = csv.DictReader(StringIO(csv_text))\n"
        "    records: list[SaleRecord] = []\n"
        "    for row in reader:\n"
        "        region = (row.get('region') or '').strip()\n"
        "        product = (row.get('product') or '').strip()\n"
        "        channel = (row.get('channel') or '').strip() or 'unknown'\n"
        "        if not region or not product:\n"
        "            continue\n"
        "        records.append(SaleRecord(region=region, product=product, amount=float(row.get('amount') or 0), channel=channel))\n"
        "    return records\n"
    )
    analyzer = (
        "from .loader import SaleRecord\n\n\n"
        "def filter_records(records: list[SaleRecord], *, min_amount: float = 0, channel: str | None = None) -> list[SaleRecord]:\n"
        "    selected = [record for record in records if record.amount >= min_amount]\n"
        "    if channel:\n"
        "        selected = [record for record in selected if record.channel == channel]\n"
        "    return selected\n\n\n"
        "def group_region_totals(records: list[SaleRecord]) -> dict[str, float]:\n"
        "    totals: dict[str, float] = {}\n"
        "    for record in records:\n"
        "        totals[record.region] = totals.get(record.region, 0.0) + record.amount\n"
        "    return dict(sorted(totals.items()))\n\n\n"
        "def top_region(records: list[SaleRecord]) -> str:\n"
        "    totals = group_region_totals(records)\n"
        "    if not totals:\n"
        "        return ''\n"
        "    return max(totals, key=lambda region: totals[region])\n"
    )
    report = (
        "from .analyzer import group_region_totals, top_region\n"
        "from .loader import SaleRecord\n\n\n"
        "def build_summary(records: list[SaleRecord]) -> dict[str, object]:\n"
        "    totals = group_region_totals(records)\n"
        "    return {\n"
        "        'record_count': len(records),\n"
        "        'region_totals': totals,\n"
        "        'top_region': top_region(records),\n"
        "    }\n\n\n"
        "def render_markdown_report(records: list[SaleRecord]) -> str:\n"
        "    summary = build_summary(records)\n"
        "    lines = ['# Sales Analytics Report', '', f\"Records: {summary['record_count']}\", '', '## Region totals']\n"
        "    for region, total in summary['region_totals'].items():\n"
        "        lines.append(f'- {region}: {total:.2f}')\n"
        "    lines.append('')\n"
        "    lines.append(f\"Top region: {summary['top_region'] or 'n/a'}\")\n"
        "    return '\\n'.join(lines)\n"
    )
    cli = (
        "import argparse\n"
        "import json\n"
        "from pathlib import Path\n\n"
        "from .analyzer import filter_records\n"
        "from .loader import load_records\n"
        "from .report import build_summary, render_markdown_report\n\n\n"
        "def build_parser() -> argparse.ArgumentParser:\n"
        "    parser = argparse.ArgumentParser(description='Run the sales analytics pipeline')\n"
        "    parser.add_argument('csv_path')\n"
        "    parser.add_argument('--min-amount', type=float, default=0)\n"
        "    parser.add_argument('--channel')\n"
        "    parser.add_argument('--format', choices=['json', 'markdown'], default='json')\n"
        "    return parser\n\n\n"
        "def run_pipeline(csv_text: str, *, min_amount: float = 0, channel: str | None = None) -> list[object]:\n"
        "    return filter_records(load_records(csv_text), min_amount=min_amount, channel=channel)\n\n\n"
        "def main(argv: list[str] | None = None) -> None:\n"
        "    args = build_parser().parse_args(argv)\n"
        "    records = run_pipeline(Path(args.csv_path).read_text(encoding='utf-8'), min_amount=args.min_amount, channel=args.channel)\n"
        "    if args.format == 'markdown':\n"
        "        print(render_markdown_report(records))\n"
        "    else:\n"
        "        print(json.dumps(build_summary(records), ensure_ascii=False, sort_keys=True))\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    tests = (
        f"import json\nimport tempfile\nimport unittest\nfrom io import StringIO\nfrom pathlib import Path\nimport contextlib\n\n"
        f"from {package}.analyzer import filter_records, group_region_totals, top_region\n"
        f"from {package}.cli import main, run_pipeline\n"
        f"from {package}.loader import load_records\n"
        f"from {package}.report import build_summary, render_markdown_report\n\n\n"
        "SAMPLE = 'region,product,amount,channel\\nNorth,Widget,10,web\\nSouth,Gadget,25,retail\\nNorth,Gadget,15,web\\n'\n\n\n"
        "class SalesAnalyticsPipelineTests(unittest.TestCase):\n"
        "    def test_load_filter_and_group_workflow(self):\n"
        "        records = load_records(SAMPLE)\n"
        "        self.assertEqual(len(records), 3)\n"
        "        selected = filter_records(records, min_amount=12, channel='web')\n"
        "        self.assertEqual([record.product for record in selected], ['Gadget'])\n"
        "        self.assertEqual(group_region_totals(records), {'North': 25.0, 'South': 25.0})\n"
        "        self.assertEqual(top_region(records), 'North')\n\n"
        "    def test_summary_and_markdown_report_workflow(self):\n"
        "        records = load_records(SAMPLE)\n"
        "        summary = build_summary(records)\n"
        "        self.assertEqual(summary['record_count'], 3)\n"
        "        self.assertEqual(summary['region_totals']['North'], 25.0)\n"
        "        markdown = render_markdown_report(records)\n"
        "        self.assertIn('# Sales Analytics Report', markdown)\n"
        "        self.assertIn('Top region: North', markdown)\n\n"
        "    def test_cli_json_output_workflow(self):\n"
        "        with tempfile.TemporaryDirectory() as tmp:\n"
        "            csv_path = Path(tmp) / 'sales.csv'\n"
        "            csv_path.write_text(SAMPLE, encoding='utf-8')\n"
        "            output = StringIO()\n"
        "            with contextlib.redirect_stdout(output):\n"
        "                main([str(csv_path), '--min-amount', '12'])\n"
        "            payload = json.loads(output.getvalue())\n"
        "            self.assertEqual(payload['record_count'], 2)\n"
        "            self.assertEqual(run_pipeline(SAMPLE, min_amount=12)[0].product, 'Gadget')\n"
    )
    readme = (
        f"# {project_name}\n\nA multi-module sales analytics pipeline that loads CSV records, filters them, groups region totals, renders markdown, and prints CLI JSON.\n\n"
        "## Run\n\n```bash\npython -m "
        f"{package}.cli input.csv\n```\n\n"
        "```bash\npython -m "
        f"{package}.cli sales.csv --min-amount 10 --format json\n```\n\n"
        "```bash\npython -m "
        f"{package}.cli sales.csv --format markdown\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n\n"
        "```bash\npython -m py_compile "
        f"{package}/processor.py {package}/cli.py\n```\n"
    )
    rows = replace_project_file(files, f"{package}/loader.py", loader)
    rows = replace_project_file(rows, f"{package}/analyzer.py", analyzer)
    rows = replace_project_file(rows, f"{package}/report.py", report)
    rows = replace_project_file(rows, f"{package}/cli.py", cli)
    rows = replace_project_file(rows, "tests/test_sales_pipeline.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def sales_analytics_pipeline_module_contracts(project_name: str) -> list[dict[str, Any]]:
    package = project_name.replace("-", "_")
    return [
        {
            "module": f"{package}.loader",
            "path": f"{package}/loader.py",
            "responsibility": "load typed sales records from CSV text",
            "requirements": ["parse CSV rows", "skip incomplete rows", "coerce amounts to floats", "preserve region product and channel"],
        },
        {
            "module": f"{package}.analyzer",
            "path": f"{package}/analyzer.py",
            "responsibility": "filter and aggregate sales records",
            "requirements": ["filter by minimum amount", "filter by channel", "group totals by region", "select top region"],
        },
        {
            "module": f"{package}.report",
            "path": f"{package}/report.py",
            "responsibility": "build structured and markdown sales reports",
            "requirements": ["build summary dictionary", "render markdown report", "include top region and region totals"],
        },
        {
            "module": f"{package}.cli",
            "path": f"{package}/cli.py",
            "responsibility": "command-line analytics pipeline entrypoint",
            "requirements": ["read CSV path", "support min amount filter", "support channel filter", "print JSON or markdown"],
        },
        {
            "module": "tests.test_sales_pipeline",
            "path": "tests/test_sales_pipeline.py",
            "responsibility": "multi-workflow sales analytics verification",
            "requirements": ["prove load filter group workflow", "prove report workflow", "prove CLI JSON workflow"],
        },
    ]
