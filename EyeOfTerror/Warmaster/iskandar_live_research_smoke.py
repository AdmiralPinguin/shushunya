#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eye_of_terror.orchestrator import research_loop_run
from eye_of_terror.run_state import run_summary
from eye_of_terror.task_prepare import prepare_task


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASK = (
    "Исследуй историю появления RISC-V: найди источники в интернете, "
    "собери краткий русский research report с evidence trace и явно перечисли пробелы."
)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def timestamp_task_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"iskandar-live-research-smoke-{stamp}"


def compact_smoke_report(prepared: dict[str, Any], loop_result: dict[str, Any] | None, summary: dict[str, Any] | None) -> dict[str, Any]:
    run_dir = Path(str(prepared.get("run_dir") or ""))
    oversight = load_json_object(run_dir / "oversight.json") if run_dir else {}
    final_manifest = {}
    result = summary.get("result") if summary and isinstance(summary.get("result"), dict) else {}
    workspace_root = Path(str(result.get("workspace_root") or ""))
    for artifact in result.get("artifacts", []) if isinstance(result.get("artifacts"), list) else []:
        artifact_text = str(artifact)
        if artifact_text.endswith("/final_manifest.json") and workspace_root:
            final_manifest = load_json_object(workspace_root / artifact_text.removeprefix("/work/"))
            break
    return {
        "ok": bool(loop_result and loop_result.get("ok")),
        "prepared_ok": bool(prepared.get("ok")),
        "task_id": prepared.get("task_id", ""),
        "run_dir": prepared.get("run_dir", ""),
        "governor": prepared.get("governor", ""),
        "loop_phase": loop_result.get("phase", "") if loop_result else "",
        "loop_stop_reason": loop_result.get("stop_reason", "") if loop_result else "",
        "revision_cycles": loop_result.get("revision_cycles", 0) if loop_result else 0,
        "final_status": summary.get("status", "") if summary else "",
        "decision": loop_result.get("decision", {}) if loop_result else {},
        "research_intent": oversight.get("research_intent", {}),
        "pipeline_plan": oversight.get("pipeline_plan", {}),
        "final_manifest": final_manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one live Iskandar research smoke with real model/internet dependencies.")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--task-id", default="")
    parser.add_argument("--run-mode", choices=["local", "http"], default="local")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--max-revision-cycles", type=int, default=1)
    parser.add_argument("--disable-live-discovery", action="store_true")
    parser.add_argument("--run-root", default=str(PROJECT_ROOT / "live_runs" / "scriptorium_research_smoke" / "runs"))
    parser.add_argument("--report-dir", default=str(PROJECT_ROOT / "live_runs" / "scriptorium_research_smoke"))
    args = parser.parse_args()

    run_root = Path(args.run_root)
    report_dir = Path(args.report_dir)
    task_id = args.task_id.strip() or timestamp_task_id()
    previous_live_discovery = os.environ.get("LEXMECHANIC_LIVE_DISCOVERY")
    if not args.disable_live_discovery:
        os.environ["LEXMECHANIC_LIVE_DISCOVERY"] = "1"
    prepared = prepare_task(args.task, task_id, run_root, governor_transport="local", governor_host=args.host)
    loop_result: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    if prepared.get("ok"):
        loop_result = research_loop_run(
            run_root,
            str(prepared.get("task_id") or task_id),
            run_mode=args.run_mode,
            host=args.host,
            timeout_sec=args.timeout_sec,
            max_revision_cycles=args.max_revision_cycles,
            allow_resume=True,
        )
        summary = run_summary(Path(str(prepared.get("run_dir"))))
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task": args.task,
        "run_mode": args.run_mode,
        "live_discovery_enabled": os.environ.get("LEXMECHANIC_LIVE_DISCOVERY", ""),
        "prepared": prepared,
        "loop_result": loop_result,
        "run_summary": summary,
        "smoke": compact_smoke_report(prepared, loop_result, summary),
    }
    if previous_live_discovery is None:
        os.environ.pop("LEXMECHANIC_LIVE_DISCOVERY", None)
    else:
        os.environ["LEXMECHANIC_LIVE_DISCOVERY"] = previous_live_discovery
    report_path = report_dir / f"{task_id}.json"
    write_json(report_path, report)
    print(json.dumps({"ok": report["smoke"]["ok"], "report_path": str(report_path), "smoke": report["smoke"]}, ensure_ascii=False, indent=2))
    return 0 if report["smoke"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
