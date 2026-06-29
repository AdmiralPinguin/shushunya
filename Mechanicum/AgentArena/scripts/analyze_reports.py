#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from report_metrics import failed_check_symptoms, failed_checks, failure_reason, summarize_failed_check


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def load_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"report must be a JSON object: {path}")
    return payload


def add_stat(stats: dict[str, Any], suite: str, agent: str, ok: bool, duration_sec: float, unavailable: bool = False) -> None:
    key = f"{suite}:{agent}"
    item = stats.setdefault(
        key,
        {"suite": suite, "agent": agent, "total": 0, "passed": 0, "failed": 0, "unavailable": 0, "duration_sec": 0.0},
    )
    item["total"] += 1
    item["passed" if ok else "failed"] += 1
    if unavailable:
        item["unavailable"] += 1
    item["duration_sec"] = round(float(item["duration_sec"]) + duration_sec, 3)


def add_orchestration_stat(stats: dict[str, Any], suite: str, agent: str, orchestration: dict[str, Any]) -> None:
    if orchestration.get("ok") is None or orchestration.get("style") == "artifact_reads_before_writes":
        return
    key = f"{suite}:{agent}"
    item = stats.setdefault(
        key,
        {
            "suite": suite,
            "agent": agent,
            "tracked": 0,
            "passed_chain": 0,
            "failed_chain": 0,
            "missing_failing_diagnostic": 0,
            "missing_edit": 0,
            "missing_verification_after_edit": 0,
        },
    )
    item["tracked"] += 1
    if orchestration.get("ok") is True:
        item["passed_chain"] += 1
    else:
        item["failed_chain"] += 1
        if not orchestration.get("failing_diagnostic_steps"):
            item["missing_failing_diagnostic"] += 1
        if not orchestration.get("edit_steps"):
            item["missing_edit"] += 1
        if not orchestration.get("verified_after_last_edit"):
            item["missing_verification_after_edit"] += 1


def add_artifact_stat(stats: dict[str, Any], suite: str, agent: str, orchestration: dict[str, Any]) -> None:
    if orchestration.get("style") != "artifact_reads_before_writes" or orchestration.get("ok") is None:
        return
    key = f"{suite}:{agent}"
    item = stats.setdefault(
        key,
        {
            "suite": suite,
            "agent": agent,
            "tracked": 0,
            "passed_chain": 0,
            "failed_chain": 0,
            "missing_input_reads": 0,
            "missing_output_writes": 0,
        },
    )
    item["tracked"] += 1
    if orchestration.get("ok") is True:
        item["passed_chain"] += 1
    else:
        item["failed_chain"] += 1
        if orchestration.get("missing_input_reads"):
            item["missing_input_reads"] += 1
        if orchestration.get("missing_output_writes"):
            item["missing_output_writes"] += 1


def analyze_reports(report_paths: list[Path]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    orchestration_stats: dict[str, Any] = {}
    artifact_stats: dict[str, Any] = {}
    failure_reasons: dict[str, int] = {}
    failed_check_types: dict[str, int] = {}
    failed_check_symptom_counts: dict[str, int] = {}
    failures: list[dict[str, Any]] = []
    loaded = 0
    for path in report_paths:
        report = load_report(path)
        loaded += 1
        suite = str(report.get("suite") or "unknown")
        for result in report.get("results", []):
            if not isinstance(result, dict):
                continue
            agent = str(result.get("agent") or "unknown")
            ok = bool(result.get("ok"))
            checks = result.get("checks") if isinstance(result.get("checks"), list) else []
            reason = failure_reason(result.get("exit_code"), checks, str(result.get("error") or "")) if not ok else ""
            add_stat(stats, suite, agent, ok, float(result.get("duration_sec") or 0.0), reason == "agent_unavailable")
            orchestration = result.get("orchestration") if isinstance(result.get("orchestration"), dict) else {}
            if orchestration:
                add_orchestration_stat(orchestration_stats, suite, agent, orchestration)
                add_artifact_stat(artifact_stats, suite, agent, orchestration)
            if not ok:
                failed = failed_checks(checks)
                for check in failed:
                    check_type = str(check.get("type") or "unknown")
                    failed_check_types[check_type] = failed_check_types.get(check_type, 0) + 1
                    for symptom in failed_check_symptoms(check):
                        failed_check_symptom_counts[symptom] = failed_check_symptom_counts.get(symptom, 0) + 1
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
                failures.append(
                    {
                        "report": path.name,
                        "suite": suite,
                        "agent": agent,
                        "task_id": result.get("task_id"),
                        "exit_code": result.get("exit_code"),
                        "failure_reason": reason,
                        "failed_check_count": len(failed),
                        "failed_checks": [summarize_failed_check(check) for check in failed[:5]],
                        "error": result.get("error", ""),
                    }
                )
    rows = sorted(stats.values(), key=lambda item: (item["suite"], item["agent"]))
    for row in rows:
        total = int(row["total"])
        runnable_total = total - int(row.get("unavailable", 0))
        row["pass_rate"] = round(float(row["passed"]) / total, 3) if total else 0.0
        row["runnable_total"] = runnable_total
        row["runnable_pass_rate"] = round(float(row["passed"]) / runnable_total, 3) if runnable_total else None
    orchestration_rows = sorted(orchestration_stats.values(), key=lambda item: (item["suite"], item["agent"]))
    for row in orchestration_rows:
        tracked = int(row["tracked"])
        row["chain_pass_rate"] = round(float(row["passed_chain"]) / tracked, 3) if tracked else 0.0
    artifact_rows = sorted(artifact_stats.values(), key=lambda item: (item["suite"], item["agent"]))
    for row in artifact_rows:
        tracked = int(row["tracked"])
        row["chain_pass_rate"] = round(float(row["passed_chain"]) / tracked, 3) if tracked else 0.0
    return {
        "reports": loaded,
        "stats": rows,
        "orchestration_quality": orchestration_rows,
        "artifact_quality": artifact_rows,
        "failure_reasons": failure_reasons,
        "failed_check_types": dict(sorted(failed_check_types.items(), key=lambda item: (-item[1], item[0]))),
        "failed_check_symptoms": dict(sorted(failed_check_symptom_counts.items(), key=lambda item: (-item[1], item[0]))),
        "recent_failures": failures[:20],
    }


def format_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        "# AgentArena Report Summary",
        "",
        f"- reports: {analysis.get('reports', 0)}",
        "",
        "## Agents",
        "",
        "| suite | agent | passed | failed | unavailable | runnable pass rate |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in analysis.get("stats", []):
        if not isinstance(row, dict):
            continue
        pass_rate = row.get("runnable_pass_rate")
        pass_rate_text = "n/a" if pass_rate is None else str(pass_rate)
        lines.append(
            f"| {row.get('suite', '')} | {row.get('agent', '')} | {row.get('passed', 0)} | "
            f"{row.get('failed', 0)} | {row.get('unavailable', 0)} | {pass_rate_text} |"
        )
    lines.extend(["", "## Failure Reasons", ""])
    for reason, count in analysis.get("failure_reasons", {}).items():
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "## Failed Check Symptoms", ""])
    for symptom, count in analysis.get("failed_check_symptoms", {}).items():
        lines.append(f"- {symptom}: {count}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize AgentArena report pass rates.")
    parser.add_argument("--limit", type=int, default=30, help="Analyze the newest N reports.")
    parser.add_argument("--suite", default="", help="Optional suite name filter.")
    parser.add_argument("--markdown", action="store_true", help="Print a compact Markdown summary instead of JSON.")
    args = parser.parse_args()
    paths = sorted(REPORTS.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if args.suite:
        paths = [path for path in paths if path.name.endswith(f"-{args.suite}.json")]
    paths = paths[: max(0, args.limit)]
    analysis = analyze_reports(paths)
    if args.markdown:
        print(format_markdown(analysis), end="")
    else:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
