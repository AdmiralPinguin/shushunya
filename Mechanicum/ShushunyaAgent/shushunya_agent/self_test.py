#!/usr/bin/env python3
from __future__ import annotations

import sys
from unittest import mock

from . import agent_runner
from .agent_runner import (
    AgentConfig,
    archive_memory_catalog,
    archive_memory_events,
    archive_memory_propose,
    archive_memory_read,
    archive_memory_search,
    archive_request,
    archive_status,
    compact_messages_for_model,
    configured_search_providers,
    file_tool,
    python_tool,
    result_for_model,
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

    large_result = {"ok": True, "content": "x" * 50000, "size": 50000}
    compacted_result = result_for_model("read_file", large_result, config)
    if len(compacted_result.get("content", "")) > 7000:
        raise AssertionError("read_file result was not compacted for model context")
    print("[ok] read_file result compacted for model context")

    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
        *({"role": "user", "content": "Tool result:\n" + ("y" * 6000)} for _ in range(8)),
    ]
    compacted_messages = compact_messages_for_model(messages, config, budget=9000)
    total_context_chars = sum(len(message.get("content", "")) for message in compacted_messages)
    if total_context_chars > 9000 or len(compacted_messages) >= len(messages):
        raise AssertionError(f"context messages were not compacted: chars={total_context_chars}, count={len(compacted_messages)}")
    print("[ok] model context compacted")

    health = archive_request(config, "GET", "/health", timeout=10)
    if health.get("status") != "ok":
        raise AssertionError(f"Archive health failed: {health}")
    print("[ok] archive health")
    assert_ok("archive status tool", archive_status(config))
    memory_events = archive_memory_events(config, limit=1)
    assert_ok("archive memory events tool", memory_events)
    if memory_events.get("memory_namespace") != config.memory_namespace:
        raise AssertionError(f"unexpected memory namespace in events response: {memory_events}")
    print("[ok] archive memory events namespace")
    gateway_events = archive_memory_events(config, limit=5, component="memory_gateway")
    assert_ok("archive memory events component filter", gateway_events)
    if gateway_events.get("component") != "memory_gateway":
        raise AssertionError(f"unexpected component filter in events response: {gateway_events}")
    catalog = archive_memory_catalog(config)
    assert_ok("archive memory catalog tool", catalog)
    if catalog.get("memory_namespace") != config.memory_namespace:
        raise AssertionError(f"unexpected memory namespace in catalog response: {catalog}")
    print("[ok] archive memory catalog namespace")
    memory_search = archive_memory_search(config, "agent memory", limit=2)
    assert_ok("archive memory search tool", memory_search)
    if memory_search.get("memory_namespace") != config.memory_namespace:
        raise AssertionError(f"unexpected memory namespace in memory search response: {memory_search}")
    print("[ok] archive memory search namespace")
    focus_read = archive_memory_read(config, "focus", "active")
    assert_ok("archive memory focus read tool", focus_read)
    if focus_read.get("memory_namespace") != config.memory_namespace:
        raise AssertionError(f"unexpected memory namespace in focus read response: {focus_read}")
    print("[ok] archive memory focus read namespace")
    missing_wiki = archive_memory_read(config, "wiki", title="__agent_self_test_missing__")
    if missing_wiki.get("ok") is not False or missing_wiki.get("http_status") != 404:
        raise AssertionError(f"missing wiki should be a fail-soft tool result: {missing_wiki}")
    print("[ok] archive memory missing wiki fail-soft")
    with mock.patch.object(
        agent_runner,
        "archive_request",
        return_value={"ok": True, "turn_id": "mock-turn", "memory_namespace": config.memory_namespace},
    ) as mocked_archive:
        proposal = archive_memory_propose(config, {"proposal": "self-test proposal", "target": "focus", "importance": 2})
    assert_ok("archive memory proposal tool", proposal)
    called_payload = mocked_archive.call_args.kwargs["payload"]
    if called_payload.get("namespace") != config.memory_namespace or called_payload.get("proposal") != "self-test proposal":
        raise AssertionError(f"unexpected proposal payload: {called_payload}")
    print("[ok] archive memory proposal payload")
    bad_proposal = archive_memory_propose(config, {"proposal": "bad target self-test", "target": "focuz"})
    if bad_proposal.get("ok") is not False or bad_proposal.get("http_status") != 400:
        raise AssertionError(f"bad proposal should be a fail-soft tool result: {bad_proposal}")
    print("[ok] archive memory bad proposal fail-soft")

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
