from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .local_executor import ordered_dispatch_paths
from .ledger import TaskLedger


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


def execute_run(run_dir: Path, host: str = "127.0.0.1", timeout_sec: int = 1800) -> dict[str, Any]:
    contract = load_json(run_dir / "contract.json") if (run_dir / "contract.json").exists() else {}
    ledger = TaskLedger.create(
        run_dir / "task_ledger.json",
        str(contract.get("task_id") or run_dir.name),
        str(contract.get("goal") or ""),
        str(contract.get("assigned_governor") or ""),
    )
    ledger.set_status("running")
    results: list[HttpStepResult] = []
    for dispatch_path in ordered_dispatch_paths(run_dir):
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
    summary = {
        "ok": bool(results) and all(item.ok for item in results),
        "run_dir": str(run_dir),
        "host": host,
        "steps": [item.to_dict() for item in results],
    }
    report_path = run_dir / "http_execution_report.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger.set_status("completed" if summary["ok"] else "failed")
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Execute an EyeOfTerror run package through worker HTTP services.")
    parser.add_argument("run_dir")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    args = parser.parse_args()
    summary = execute_run(Path(args.run_dir), args.host, args.timeout_sec)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
