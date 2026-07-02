"""Run-package readers: load contract/oversight/dispatch/ledger from a run dir."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ledger import TaskLedger
from .local_executor import ordered_dispatch_paths


def load_ledger_dict(ledger_path: Path) -> tuple[dict[str, Any], str]:
    if not ledger_path.exists():
        return {}, "ledger not found"
    try:
        return TaskLedger.load(ledger_path).to_dict(), ""
    except Exception as exc:  # noqa: BLE001 - gateway must report corrupt run state instead of crashing.
        return {}, str(exc)


def load_json_object(path: Path, label: str) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, f"{label} not found"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"{label} is corrupt: {exc}"
    if not isinstance(payload, dict):
        return {}, f"{label} is not a JSON object"
    return payload, ""


def load_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return payload


def sandbox_artifact_file_status(workspace_root: str, sandbox_path: str) -> dict[str, Any]:
    item: dict[str, Any] = {"path": sandbox_path}
    if workspace_root and sandbox_path.startswith("/work/"):
        host_path = Path(workspace_root) / sandbox_path.removeprefix("/work/")
        item["host_path"] = str(host_path)
        item["exists"] = host_path.exists()
        item["bytes"] = host_path.stat().st_size if host_path.exists() else 0
    else:
        item["exists"] = False
        item["bytes"] = 0
    return item


def run_contract(run_dir: Path) -> dict[str, Any]:
    contract_path = run_dir / "contract.json"
    if not contract_path.exists():
        return {"ok": False, "error": "contract not found", "error_code": "contract_not_found"}
    payload, error = load_json_object(contract_path, "contract")
    if error:
        return {"ok": False, "error": error, "error_code": "corrupt_contract"}
    return {"ok": True, "contract": payload}


def run_oversight(run_dir: Path) -> dict[str, Any]:
    oversight_path = run_dir / "oversight.json"
    if not oversight_path.exists():
        return {"ok": False, "error": "oversight not found", "error_code": "oversight_not_found"}
    payload, error = load_json_object(oversight_path, "oversight")
    if error:
        return {"ok": False, "error": error, "error_code": "corrupt_oversight"}
    return {"ok": True, "oversight": payload}


def run_dispatch_packets(run_dir: Path) -> dict[str, Any]:
    dispatch_dir = run_dir / "dispatch"
    if not dispatch_dir.exists():
        return {"ok": False, "error": "dispatch directory not found"}
    packets: list[dict[str, Any]] = []
    dispatch_paths = ordered_dispatch_paths(run_dir) if (run_dir / "status.json").exists() else sorted(dispatch_dir.glob("*.json"))
    for dispatch_path in dispatch_paths:
        try:
            packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            packets.append({"path": str(dispatch_path), "ok": False, "error": str(exc)})
            continue
        packets.append({"path": str(dispatch_path), "ok": isinstance(packet, dict), "packet": packet})
    return {"ok": True, "dispatch": packets}
