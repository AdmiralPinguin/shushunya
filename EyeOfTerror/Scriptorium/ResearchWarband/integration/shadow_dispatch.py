"""Submit one validated native Iskandar package to the 7201 shadow backend.

This is an explicit operator/canary integration point.  It never contacts or
reconfigures the legacy governor on 7101 and therefore cannot perform cutover.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import quote

from .loopback_http import LoopbackJSONClient


def load_shadow_envelope(run_dir: str | Path) -> dict[str, Any]:
    try:
        from EyeOfTerror.Warmaster.eye_of_terror.native_research_run import (
            load_native_research_run,
            validate_native_research_run_package,
        )
        from EyeOfTerror.common_protocol.iskandar_directive import (
            validate_directive_for_commander,
        )
    except ImportError as exc:
        raise RuntimeError("native ResearchWarband package validators are unavailable") from exc
    target = Path(run_dir)
    errors = validate_native_research_run_package(target)
    if errors:
        raise ValueError("native ResearchWarband package is invalid: " + "; ".join(errors))
    loaded = load_native_research_run(target)
    contract = loaded.get("contract")
    order = loaded.get("commander_order")
    directive = loaded.get("leadership_directive")
    if not all(isinstance(item, dict) for item in (contract, order, directive)):
        raise ValueError("native ResearchWarband package is incomplete")
    task_id = str(contract.get("task_id") or "")
    mission_id = str(contract.get("mission_id") or "")
    normalized = validate_directive_for_commander(
        directive,
        order,
        expected_task_id=task_id,
        expected_mission_id=mission_id,
        require_delegation=True,
    )
    return {
        "mission_id": mission_id,
        "task_id": task_id,
        "leadership_directive": normalized,
        "commander_order": order,
    }


def submit_shadow_package(
    run_dir: str | Path,
    *,
    base_url: str = "http://127.0.0.1:7201",
    bearer_token: str,
) -> tuple[LoopbackJSONClient, dict[str, Any]]:
    if (
        type(bearer_token) is not str
        or len(bearer_token) < 32
        or bearer_token.startswith("REPLACE_")
        or len(set(bearer_token)) < 8
    ):
        raise ValueError("7201 shadow dispatch requires a bearer token of at least 32 characters")
    client = LoopbackJSONClient(
        base_url,
        bearer_token=bearer_token,
        expected_port=7201,
    )
    health = client.request_json("GET", "/health", timeout_sec=5)
    identity = health.get("identity") if isinstance(health, dict) else None
    if (
        health.get("status") != "ok"
        or health.get("service") != "ResearchWarband"
        or not isinstance(identity, dict)
        or identity.get("standalone_test_mode") is not False
        or identity.get("bearer_auth_required") is not True
    ):
        raise RuntimeError("7201 is not the bearer-protected production shadow")
    envelope = load_shadow_envelope(run_dir)
    created = client.request_json(
        "POST", "/missions", payload=envelope, timeout_sec=10
    )
    if created.get("mission_id") != envelope["mission_id"]:
        raise RuntimeError("shadow backend changed the native mission identity")
    return client, created


def wait_for_shadow(
    client: LoopbackJSONClient,
    mission_id: str,
    *,
    timeout_sec: float,
    poll_interval_sec: float = 1.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + float(timeout_sec)
    path = "/missions/" + quote(mission_id, safe="")
    while time.monotonic() < deadline:
        snapshot = client.request_json(
            "GET", path, timeout_sec=min(10.0, max(0.05, deadline - time.monotonic()))
        )
        if (
            snapshot.get("status") in {"done", "needs_user", "blocked", "failed", "cancelled"}
            and snapshot.get("inflight") is False
            and snapshot.get("cleanup_complete") is True
        ):
            return snapshot
        time.sleep(min(poll_interval_sec, max(0.0, deadline - time.monotonic())))
    raise TimeoutError("shadow mission did not become quiescent before the CLI wait limit")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--base-url", default="http://127.0.0.1:7201")
    parser.add_argument("--wait-sec", type=float, default=0.0)
    args = parser.parse_args(argv)
    token = os.environ.get("RESEARCH_WARBAND_BEARER_TOKEN", "")
    client, submitted = submit_shadow_package(
        args.run_dir, base_url=args.base_url, bearer_token=token
    )
    output: dict[str, Any] = {"submitted": submitted}
    if args.wait_sec > 0:
        output["mission"] = wait_for_shadow(
            client,
            str(submitted["mission_id"]),
            timeout_sec=args.wait_sec,
        )
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    mission = output.get("mission")
    if isinstance(mission, dict) and mission.get("status") in {"failed", "cancelled"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "load_shadow_envelope",
    "main",
    "submit_shadow_package",
    "wait_for_shadow",
]
