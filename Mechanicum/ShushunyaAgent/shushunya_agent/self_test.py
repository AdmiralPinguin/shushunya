#!/usr/bin/env python3
from __future__ import annotations

import sys
from unittest import mock

from . import agent_runner
from .agent_runner import (
    AgentConfig,
    archive_request,
    archive_status,
    configured_search_providers,
    file_tool,
    python_tool,
    sandbox_status,
    validate_configured_searxng_url,
    validate_public_url,
    web_fetch,
    web_search,
)


def assert_ok(label: str, payload: dict) -> None:
    if not payload.get("ok", True):
        raise AssertionError(f"{label} failed: {payload}")
    print(f"[ok] {label}")


def main() -> int:
    config = AgentConfig()

    if configured_search_providers()[0] != "searxng":
        raise AssertionError(f"search providers must start with searxng: {configured_search_providers()}")
    print("[ok] search provider order starts with searxng")

    try:
        validate_public_url("http://127.0.0.1")
        raise AssertionError("validate_public_url allowed 127.0.0.1")
    except ValueError:
        print("[ok] validate_public_url blocks 127.0.0.1")

    old_searxng_url = agent_runner.SEARXNG_URL
    try:
        agent_runner.SEARXNG_URL = "http://127.0.0.1:8888"
        validate_configured_searxng_url("http://127.0.0.1:8888/search?q=test&format=json")
        print("[ok] configured SearXNG localhost URL allowed")
    finally:
        agent_runner.SEARXNG_URL = old_searxng_url

    try:
        web_fetch(config, "http://127.0.0.1:8888/search?q=test&format=json")
        raise AssertionError("web_fetch allowed localhost")
    except ValueError:
        print("[ok] web_fetch blocks localhost")

    old_provider_env = agent_runner.SEARCH_PROVIDERS
    old_brave_key = agent_runner.BRAVE_SEARCH_API_KEY
    try:
        agent_runner.SEARCH_PROVIDERS = "searxng,marginalia,wikipedia"
        agent_runner.BRAVE_SEARCH_API_KEY = "fake-key-must-not-be-called"
        calls: list[str] = []

        def fake_provider(name: str, ok: bool = False):
            def _provider(query: str, limit: int) -> dict:
                calls.append(name)
                return {"ok": ok, "provider": name, "results": [], "truncated": False}
            return _provider

        with mock.patch.object(agent_runner, "web_search_searxng", fake_provider("searxng")), \
                mock.patch.object(agent_runner, "web_search_marginalia", fake_provider("marginalia")), \
                mock.patch.object(agent_runner, "web_search_wikipedia", fake_provider("wikipedia")):
            result = web_search(config, "provider-order-test", 3)
        if calls != ["searxng", "marginalia", "wikipedia"] or "brave" in calls:
            raise AssertionError(f"unexpected provider calls with brave disabled: {calls}, result={result}")
        print("[ok] brave not called when absent from SHUSHUNYA_AGENT_SEARCH_PROVIDERS")
    finally:
        agent_runner.SEARCH_PROVIDERS = old_provider_env
        agent_runner.BRAVE_SEARCH_API_KEY = old_brave_key

    health = archive_request(config, "GET", "/health", timeout=10)
    if health.get("status") != "ok":
        raise AssertionError(f"Archive health failed: {health}")
    print("[ok] archive health")
    assert_ok("archive status tool", archive_status(config))

    status = sandbox_status(config)
    assert_ok("sandbox status", status)
    paths = status.get("paths", {})
    if paths.get("/media") is not False or paths.get("/root") is not False:
        raise AssertionError(f"host paths are visible inside sandbox: {paths}")
    print("[ok] host paths hidden")

    assert_ok("mkdir", file_tool(config, {"action": "mkdir", "path": "/work/self-test"}))
    assert_ok(
        "write_file",
        file_tool(config, {"action": "write_file", "path": "/work/self-test/hello.txt", "content": "hello"}),
    )
    read_result = file_tool(config, {"action": "read_file", "path": "/work/self-test/hello.txt"})
    assert_ok("read_file", read_result)
    if read_result.get("content") != "hello":
        raise AssertionError(f"unexpected file content: {read_result}")
    print("[ok] file content")

    replace_result = file_tool(
        config,
        {"action": "replace_in_file", "path": "/work/self-test/hello.txt", "old": "hello", "new": "hello-updated", "count": 1},
    )
    assert_ok("replace_in_file", replace_result)
    replaced_read = file_tool(config, {"action": "read_file", "path": "/work/self-test/hello.txt"})
    if replaced_read.get("content") != "hello-updated":
        raise AssertionError(f"unexpected replaced content: {replaced_read}")
    print("[ok] replaced file content")

    list_result = file_tool(config, {"action": "list_files", "path": "/work/self-test", "max_depth": 1})
    assert_ok("list_files", list_result)
    if not any(item.get("path") == "/work/self-test/hello.txt" for item in list_result.get("items", [])):
        raise AssertionError(f"created file is absent from listing: {list_result}")
    print("[ok] file listing")

    python_result = python_tool(config, {"action": "python", "code": "print(sum(range(1, 6)))", "timeout": 30})
    assert_ok("python", python_result)
    if python_result.get("stdout", "").strip() != "15":
        raise AssertionError(f"unexpected python output: {python_result}")
    print("[ok] python output")

    network_result = python_tool(
        config,
        {
            "action": "python",
            "code": (
                "import socket\n"
                "sock=socket.socket()\n"
                "sock.settimeout(1)\n"
                "try:\n"
                "    sock.connect(('127.0.0.1', 8090))\n"
                "    print('connected')\n"
                "except OSError:\n"
                "    print('blocked')\n"
            ),
            "timeout": 5,
        },
    )
    assert_ok("network isolation probe", network_result)
    if network_result.get("stdout", "").strip() != "blocked":
        raise AssertionError(f"sandbox network is not blocked: {network_result}")
    print("[ok] network blocked")

    print("self-test complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        raise
