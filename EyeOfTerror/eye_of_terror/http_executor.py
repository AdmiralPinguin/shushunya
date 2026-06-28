from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .local_executor import ordered_dispatch_paths
from .ledger import TaskLedger
from .pipeline import write_json_atomic


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


def preflight_workers(run_dir: Path, host: str, timeout_sec: int, step_ids: list[str] | None = None) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for dispatch_path in ordered_dispatch_paths(run_dir, step_ids=step_ids):
        packet = load_json(dispatch_path)
        worker = str(packet.get("worker") or "")
        port = int(packet.get("port") or 0)
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
    status = str(payload.get("status") or "").strip().lower()
    if status in {"blocked", "needs_revision", "failed", "preflight_failed", "cancelled"}:
        return False
    revision_plan = payload.get("revision_plan")
    if isinstance(revision_plan, dict) and revision_plan.get("required"):
        return False
    return True


def run_step(dispatch_path: Path, host: str, timeout_sec: int) -> HttpStepResult:
    packet = load_json(dispatch_path)
    step_id = str(packet.get("step_id") or dispatch_path.stem)
    worker = str(packet.get("worker") or "")
    port = int(packet.get("port") or 0)
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
) -> dict[str, Any]:
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
        ledger.set_result({"ok": False, "final_step": "", "artifacts": [], "status": "preflight_failed", "summary": "Worker preflight failed."})
        ledger.set_status("failed")
        return summary
    results: list[HttpStepResult] = []
    for dispatch_path in ordered_dispatch_paths(run_dir, step_ids=step_ids):
        ledger = TaskLedger.load(ledger_path)
        if ledger.cancel_requested():
            break
        result = run_step(dispatch_path, host, timeout_sec)
        results.append(result)
        ledger.record_step(
            result.step_id,
            result.worker,
            str(result.payload.get("status") or ("completed" if result.ok else "failed")),
            [str(item) for item in result.payload.get("artifacts", [])] if isinstance(result.payload.get("artifacts"), list) else [],
            str(result.payload.get("summary") or result.payload.get("error") or result.error),
        )
        if not result.ok:
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
        summary["revision_execution"] = True
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
                "status": "cancelled" if cancelled else final_payload.get("status", ""),
                "summary": "Execution cancelled before next step." if cancelled else final_payload.get("summary", ""),
                "revision_plan": final_payload.get("revision_plan", {}),
            }
        )
    ledger.set_status("completed" if summary["ok"] else ("cancelled" if cancelled else "failed"))
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Execute an EyeOfTerror run package through worker HTTP services.")
    parser.add_argument("run_dir")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--step-id", action="append", default=[], help="Restrict execution to one or more dispatch step ids")
    args = parser.parse_args()
    summary = execute_run(Path(args.run_dir), args.host, args.timeout_sec, step_ids=args.step_id or None)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
