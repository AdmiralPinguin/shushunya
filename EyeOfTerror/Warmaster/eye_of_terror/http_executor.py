from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from EyeOfTerror.common_protocol import validate_protocol_payload

from .local_executor import ordered_dispatch_paths, revision_contexts_from_result
from .ledger import TaskLedger
from .mission_control import record_worker_execution_started, record_worker_protocol_report, worker_report_from_payload
from .native_runs import native_adapter_for_run
from .pipeline import dispatch_packet_with_worker_order, require_dispatch_worker_order, write_json_atomic


@dataclass
class HttpStepResult:
    step_id: str
    worker: str
    port: int
    ok: bool
    payload: dict[str, Any]
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "worker": self.worker,
            "port": self.port,
            "ok": self.ok,
            "payload": self.payload,
            "error": self.error,
        }


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return payload


def post_json(url: str, payload: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("HTTP worker response must be a JSON object")
    return decoded


def get_json(url: str, timeout_sec: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("HTTP worker response must be a JSON object")
    return decoded


def _reject_native_warband_run(run_dir: Path) -> None:
    """Raw worker HTTP dispatch is never a native-warband backend."""
    adapter = native_adapter_for_run(run_dir, declared=True)
    if adapter is None:
        return
    try:
        adapter.is_run(run_dir)
    except Exception as exc:  # noqa: BLE001 - declared native packages fail closed.
        raise RuntimeError(adapter.raw_executor_error) from exc
    # A declared native descriptor may be malformed and return False.  It must
    # still be quarantined rather than falling through to the legacy workers.
    raise RuntimeError(adapter.raw_executor_error)


def preflight_workers(run_dir: Path, host: str, timeout_sec: int, step_ids: list[str] | None = None) -> list[dict[str, Any]]:
    _reject_native_warband_run(run_dir)
    failures: list[dict[str, Any]] = []
    for dispatch_path in ordered_dispatch_paths(run_dir, step_ids=step_ids):
        try:
            packet = load_json(dispatch_path)
        except Exception as exc:  # noqa: BLE001 - preflight should report malformed dispatch packets.
            failures.append({"dispatch": str(dispatch_path), "error": f"dispatch unavailable: {exc}"})
            continue
        worker = str(packet.get("worker") or "")
        port = int(packet.get("port") or 0)
        try:
            require_dispatch_worker_order(packet, expected_step_id=str(packet.get("step_id") or dispatch_path.stem), expected_worker=worker)
        except Exception as exc:  # noqa: BLE001 - preflight should reject non-protocol dispatch packets.
            failures.append({"worker": worker, "port": port, "error": str(exc)})
            continue
        try:
            payload = get_json(f"http://{host}:{port}/health", min(timeout_sec, 10))
            if not payload.get("ok"):
                failures.append({"worker": worker, "port": port, "error": str(payload.get("error") or "health returned ok=false")})
                continue
            reported_worker = str(payload.get("worker") or "")
            if reported_worker != worker:
                failures.append(
                    {
                        "worker": worker,
                        "port": port,
                        "error": f"worker identity mismatch: expected {worker}, got {reported_worker or 'unknown'}",
                    }
                )
        except Exception as exc:  # noqa: BLE001 - preflight should report all unavailable workers.
            failures.append({"worker": worker, "port": port, "error": str(exc)})
    return failures


def terminal_payload_allows_completion(payload: dict[str, Any]) -> bool:
    if not payload.get("ok"):
        return False
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"ready", "completed", "passed", "passed_with_warnings"}:
        return False
    if status in {"blocked", "needs_revision", "failed", "preflight_failed", "cancelled"}:
        return False
    revision_plan = payload.get("revision_plan")
    if isinstance(revision_plan, dict) and revision_plan.get("required"):
        return False
    return True


def ledger_status_for_execution(summary: dict[str, Any], final_payload: dict[str, Any], cancelled: bool, partial_execution: bool) -> str:
    if cancelled:
        return "cancelled"
    if summary.get("ok") and partial_execution:
        return "interrupted"
    if summary.get("ok"):
        return "completed"
    final_status = str(final_payload.get("status") or "").strip().lower()
    revision_plan = final_payload.get("revision_plan") if isinstance(final_payload.get("revision_plan"), dict) else {}
    if final_status in {"blocked", "needs_revision"} or revision_plan.get("required") is True:
        return "blocked"
    return "failed"


def worker_view_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    view: dict[str, Any] = {}
    for key in ("phase", "decision", "display", "next_action", "client_action"):
        value = payload.get(key)
        if isinstance(value, dict):
            view[key] = value
    return view


def run_step(dispatch_path: Path, host: str, timeout_sec: int, revision_context: dict[str, Any] | None = None) -> HttpStepResult:
    try:
        packet = load_json(dispatch_path)
    except Exception as exc:  # noqa: BLE001 - executor should record malformed dispatch as a step failure.
        payload = {"ok": False, "status": "failed", "error": f"dispatch unavailable: {exc}"}
        return HttpStepResult(dispatch_path.stem, "", 0, False, payload, str(exc))
    step_id = str(packet.get("step_id") or dispatch_path.stem)
    worker = str(packet.get("worker") or "")
    port = int(packet.get("port") or 0)
    try:
        require_dispatch_worker_order(packet, expected_step_id=step_id, expected_worker=worker)
    except Exception as exc:  # noqa: BLE001 - executor should record protocol violations as step failures.
        payload = {"ok": False, "status": "failed", "error": f"dispatch worker_order invalid: {exc}", "error_code": "invalid_worker_order"}
        return HttpStepResult(step_id, worker, port, False, payload, str(exc))
    if revision_context:
        packet = dispatch_packet_with_worker_order(packet, revision_context=revision_context)
    else:
        packet = dispatch_packet_with_worker_order(packet)
    url = f"http://{host}:{port}/run"
    try:
        payload = post_json(url, packet, timeout_sec)
        return HttpStepResult(step_id, worker, port, bool(payload.get("ok")), payload)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"ok": False, "error": str(exc)}
        if not isinstance(payload, dict):
            payload = {"ok": False, "error": str(exc)}
        return HttpStepResult(step_id, worker, port, False, payload, str(exc))
    except Exception as exc:  # noqa: BLE001 - executor boundary records worker connectivity failures.
        return HttpStepResult(step_id, worker, port, False, {"ok": False, "error": str(exc)}, str(exc))


def execute_run(
    run_dir: Path,
    host: str = "127.0.0.1",
    timeout_sec: int = 1800,
    workspace_root: Path | None = None,
    step_ids: list[str] | None = None,
    execution_mode: str = "full",
) -> dict[str, Any]:
    _reject_native_warband_run(run_dir)
    contract = load_json(run_dir / "contract.json") if (run_dir / "contract.json").exists() else {}
    ledger_path = run_dir / "task_ledger.json"
    ledger = (
        TaskLedger.load(ledger_path)
        if ledger_path.exists()
        else TaskLedger.create(
            ledger_path,
            str(contract.get("task_id") or run_dir.name),
            str(contract.get("goal") or ""),
            str(contract.get("assigned_governor") or ""),
        )
    )
    ledger.set_status("running")
    revision_contexts = revision_contexts_from_result(ledger.data.get("result", {}) if isinstance(ledger.data.get("result"), dict) else {})
    if step_ids:
        event_type = f"{execution_mode}_execution_started" if execution_mode in {"revision", "resume"} else "restricted_execution_started"
        ledger.record_event(event_type, {"step_ids": step_ids, "mode": "http"})
    if ledger.cancel_requested():
        summary = {"ok": False, "run_dir": str(run_dir), "host": host, "steps": [], "cancelled": True}
        write_json_atomic(run_dir / "http_execution_report.json", summary)
        ledger.set_result({"ok": False, "final_step": "", "artifacts": [], "status": "cancelled", "summary": "Execution cancelled before start."})
        ledger.set_status("cancelled")
        return summary
    preflight_failures = preflight_workers(run_dir, host, timeout_sec, step_ids=step_ids)
    if preflight_failures:
        summary = {
            "ok": False,
            "run_dir": str(run_dir),
            "host": host,
            "steps": [],
            "preflight_failures": preflight_failures,
        }
        write_json_atomic(run_dir / "http_execution_report.json", summary)
        ledger.record_event(
            "http_preflight_failed",
            {
                "failure_count": len(preflight_failures),
                "failures": preflight_failures,
                "report": str(run_dir / "http_execution_report.json"),
            },
        )
        ledger.set_result({"ok": False, "final_step": "", "artifacts": [], "status": "preflight_failed", "summary": "Worker preflight failed."})
        ledger.set_status("failed")
        return summary
    all_dispatch_paths = ordered_dispatch_paths(run_dir)
    selected_dispatch_paths = ordered_dispatch_paths(run_dir, step_ids=step_ids)
    partial_execution = bool(step_ids) and [path.stem for path in selected_dispatch_paths] != [path.stem for path in all_dispatch_paths]
    results: list[HttpStepResult] = []
    for dispatch_path in selected_dispatch_paths:
        ledger = TaskLedger.load(ledger_path)
        if ledger.cancel_requested():
            break
        try:
            record_worker_execution_started(run_dir, load_json(dispatch_path))
        except Exception:  # noqa: BLE001 - progress reporting must not hide the worker result.
            pass
        result = run_step(dispatch_path, host, timeout_sec, revision_context=revision_contexts.get(dispatch_path.stem))
        results.append(result)
        worker_view = worker_view_from_payload(result.payload)
        step_details = {"worker_view": worker_view} if worker_view else None
        try:
            packet = load_json(dispatch_path)
            order = packet.get("worker_order") if isinstance(packet.get("worker_order"), dict) else {}
            raw_report = result.payload.get("worker_report") if isinstance(result.payload.get("worker_report"), dict) else {}
            report = {}
            if raw_report:
                try:
                    validate_protocol_payload(raw_report, expected_type="worker_report")
                    report = raw_report
                except Exception as exc:  # noqa: BLE001 - fall back so old or malformed workers still leave a protocol trace.
                    step_details = {**(step_details or {}), "worker_report_validation_error": str(exc)}
            if not report:
                report = worker_report_from_payload(str(order.get("mission_id") or f"mission-{contract.get('task_id') or run_dir.name}"), result.step_id, result.worker, result.payload, result.ok)
            record_worker_protocol_report(run_dir, report)
            step_details = {**(step_details or {}), "worker_report": report}
        except Exception as exc:  # noqa: BLE001 - protocol reporting must not hide the worker result.
            step_details = {**(step_details or {}), "worker_report_error": str(exc)}
        ledger.record_step(
            result.step_id,
            result.worker,
            str(result.payload.get("status") or ("completed" if result.ok else "failed")),
            [str(item) for item in result.payload.get("artifacts", [])] if isinstance(result.payload.get("artifacts"), list) else [],
            str(result.payload.get("summary") or result.payload.get("error") or result.error),
            step_details,
        )
        if not result.ok:
            ledger.record_event(
                "http_step_failed",
                {
                    "step_id": result.step_id,
                    "worker": result.worker,
                    "port": result.port,
                    "status": str(result.payload.get("status") or "failed"),
                    "error": str(result.payload.get("error") or result.error),
                },
            )
            break
    cancelled = TaskLedger.load(ledger_path).cancel_requested()
    final_payload = results[-1].payload if results else {}
    terminal_ok = terminal_payload_allows_completion(final_payload) if isinstance(final_payload, dict) else False
    summary = {
        "ok": bool(results) and all(item.ok for item in results) and terminal_ok and not cancelled,
        "run_dir": str(run_dir),
        "host": host,
        "steps": [item.to_dict() for item in results],
        "cancelled": cancelled,
    }
    if step_ids:
        summary["step_ids"] = step_ids
        summary["execution_mode"] = execution_mode
        summary["partial_execution"] = partial_execution
        if execution_mode == "revision":
            summary["revision_execution"] = True
        if execution_mode == "resume":
            summary["resume_execution"] = True
    if isinstance(final_payload, dict) and isinstance(final_payload.get("revision_plan"), dict):
        summary["revision_plan"] = final_payload["revision_plan"]
    report_path = run_dir / "http_execution_report.json"
    write_json_atomic(report_path, summary)
    if isinstance(final_payload, dict):
        ledger.set_result(
            {
                "ok": summary["ok"],
                "final_step": results[-1].step_id if results else "",
                "artifacts": final_payload.get("artifacts", []),
                "workspace_root": str(workspace_root) if workspace_root is not None else "",
                "status": "cancelled" if cancelled else ("interrupted" if summary["ok"] and partial_execution else final_payload.get("status", "")),
                "summary": "Execution cancelled before next step." if cancelled else ("Partial execution completed; pending steps remain." if summary["ok"] and partial_execution else final_payload.get("summary", "")),
                "revision_plan": final_payload.get("revision_plan", {}),
            }
        )
    ledger.set_status(ledger_status_for_execution(summary, final_payload if isinstance(final_payload, dict) else {}, cancelled, partial_execution))
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Execute an EyeOfTerror run package through worker HTTP services.")
    parser.add_argument("run_dir")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--step-id", action="append", default=[], help="Restrict execution to one or more dispatch step ids")
    args = parser.parse_args()
    summary = execute_run(Path(args.run_dir), args.host, args.timeout_sec, step_ids=args.step_id or None, execution_mode="restricted" if args.step_id else "full")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
