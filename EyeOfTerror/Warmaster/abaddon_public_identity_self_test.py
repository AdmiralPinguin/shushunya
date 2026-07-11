#!/usr/bin/env python3
"""Focused public-name checks without renaming compatibility contracts."""
from __future__ import annotations

import inspect
import json
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from eye_of_terror.capabilities import gateway_capabilities
from eye_of_terror import mission_control
from eye_of_terror.warmaster_gateway import gateway_state, make_handler


def main() -> int:
    capabilities = gateway_capabilities()
    if capabilities.get("gateway") != "WarmasterGateway":
        raise AssertionError(f"machine gateway id changed: {capabilities}")
    if capabilities.get("display", {}).get("headline") != "Abaddon capabilities":
        raise AssertionError(f"public capabilities name is not Abaddon: {capabilities}")
    commander_source = inspect.getsource(mission_control.build_commander_order)
    if "Do not create a detailed brigade work plan" not in commander_source:
        raise AssertionError("Abaddon's commander prompt may create a worker-level plan")
    if "delegates detailed planning, execution" not in commander_source:
        raise AssertionError("commander prompt lost the brigadier/subordinate hierarchy boundary")

    with tempfile.TemporaryDirectory(prefix="abaddon-public-name-") as tmp:
        run_root = Path(tmp)
        state = gateway_state(run_root)
        if state.get("gateway") != "WarmasterGateway" or state.get("display_name") != "Abaddon":
            raise AssertionError(f"state identity contract drifted: {state}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=10) as response:
                health = json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            thread.join(timeout=10)
            server.server_close()
        expected = {"ok": True, "gateway": "WarmasterGateway", "display_name": "Abaddon"}
        if health != expected:
            raise AssertionError(f"health identity contract drifted: {health}")

    print("[ok] Abaddon gateway display and Warmaster machine compatibility")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
