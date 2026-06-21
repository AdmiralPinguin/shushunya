#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import sys
from unittest import mock
from urllib.error import HTTPError

from . import agent_runner
from . import server
from .agent_runner import (
    AgentConfig,
    archive_memory_gateway,
    archive_memory_catalog,
    archive_memory_events,
    archive_memory_propose,
    archive_memory_read,
    archive_memory_search,
    archive_request,
    archive_status,
    chat,
    compact_messages_for_model,
    configured_search_providers,
    file_tool,
    python_tool,
    read_task_journal,
    repair_action_json,
    result_for_model,
    run_agent,
    sandbox_status,
    safe_task_id,
    validate_configured_searxng_url,
    validate_public_url,
    web_fetch,
    web_search,
    write_task_journal,
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

    if safe_task_id("bad id / with spaces") != "bad-id-with-spaces":
        raise AssertionError("safe_task_id did not normalize spaces and slashes")
    print("[ok] task id normalization")

    server_config = server.config_from_payload({"max_tokens": "1024"})
    if not server_config.task_id:
        raise AssertionError("server did not assign a task_id")
    print("[ok] server assigns task id")
    if server.config_from_payload({"shell_enabled": True}).shell_enabled:
        raise AssertionError("HTTP shell should be disabled without API key or explicit env override")
    print("[ok] HTTP shell default locked")

    compact_resume = server.compact_resume_events(
        [{"type": "tool_result", "result": {"content": "r" * 10000}, "index": index} for index in range(30)],
        max_chars=5000,
    )
    compact_resume_text = str(compact_resume)
    if not compact_resume or len(compact_resume_text) > 7000:
        raise AssertionError("resume events were not compacted")
    print("[ok] resume context compacted")

    state = server.runtime_state()
    if "busy" not in state or state.get("max_request_bytes", 0) <= 0:
        raise AssertionError(f"runtime state missing expected fields: {state}")
    print("[ok] runtime state payload")
    server.RUN_LOCK.acquire()
    try:
        busy_payload = server.reject_if_busy({"wait_for_slot": False})
    finally:
        server.RUN_LOCK.release()
    if not busy_payload or busy_payload.get("error") != "agent busy":
        raise AssertionError(f"wait_for_slot=false did not reject busy runner: {busy_payload}")
    print("[ok] wait_for_slot busy rejection")

    journal_config = AgentConfig(task_id=safe_task_id("self test journal"))
    write_task_journal(journal_config, "self_test", {"large": "z" * 20000})
    journal = read_task_journal(journal_config.task_id, limit=5)
    assert_ok("task journal read", journal)
    if journal.get("task_id") != journal_config.task_id or not journal.get("events"):
        raise AssertionError(f"unexpected task journal payload: {journal}")
    print("[ok] task journal write/read")

    with mock.patch.object(agent_runner, "chat", return_value='{"action":"final","message":"repaired"}'):
        repaired_action = repair_action_json(config, "```json\n{\"action\":\"final\",\"message\":\"broken\"", ValueError("broken"))
    if repaired_action != {"action": "final", "message": "repaired"}:
        raise AssertionError(f"unexpected repaired action: {repaired_action}")
    print("[ok] JSON repair helper")

    transient_error = HTTPError(
        url="http://archive/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(b'{"error":"busy"}'),
    )
    retry_config = AgentConfig(llm_retries=2, inject_memory=False, archive_internal_steps=False)
    with mock.patch.object(agent_runner, "archive_request", side_effect=[
        transient_error,
        {"choices": [{"message": {"content": '{"action":"final","message":"retry ok"}'}}]},
    ]) as mocked_archive, mock.patch.object(agent_runner.time, "sleep"):
        retry_reply = chat(retry_config, [{"role": "user", "content": "retry"}], inject_memory=False, archive_enabled=False)
    if retry_reply != '{"action":"final","message":"retry ok"}' or mocked_archive.call_count != 2:
        raise AssertionError(f"model retry did not recover: reply={retry_reply}, calls={mocked_archive.call_count}")
    print("[ok] model 429 retry")

    final_events: list[dict] = []
    final_stdout = io.StringIO()
    final_config = AgentConfig(
        task_id=safe_task_id("self-test-final-run"),
        json_output=True,
        max_steps=1,
        inject_memory=False,
        archive_internal_steps=False,
    )
    with mock.patch.object(agent_runner, "chat", return_value='{"action":"final","message":"ok"}'), \
            contextlib.redirect_stdout(final_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        final_code = run_agent("return final", final_config, event_sink=final_events.append)
    final_payload = json.loads(final_stdout.getvalue())
    if final_code != 0 or "duration_sec" not in final_payload:
        raise AssertionError(f"final run did not include duration: code={final_code}, payload={final_payload}")
    if not any(event.get("type") == "final" and "duration_sec" in event for event in final_events):
        raise AssertionError(f"final event did not include duration: {final_events}")
    print("[ok] final event duration")

    limit_stdout = io.StringIO()
    limit_config = AgentConfig(
        task_id=safe_task_id("self-test-runtime-limit"),
        json_output=True,
        max_steps=1,
        max_runtime_sec=-1,
        inject_memory=False,
        archive_internal_steps=False,
    )
    with mock.patch.object(agent_runner, "chat") as mocked_chat, \
            contextlib.redirect_stdout(limit_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        limit_code = run_agent("should stop before model", limit_config)
    limit_payload = json.loads(limit_stdout.getvalue())
    if limit_code != 2 or limit_payload.get("ok") is not False or mocked_chat.called:
        raise AssertionError(f"runtime limit did not stop before model: code={limit_code}, payload={limit_payload}")
    print("[ok] runtime limit")

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
    gateway_events = archive_memory_events(config, limit=5, component="memory_gateway", requester="shushunya-agent")
    assert_ok("archive memory events component filter", gateway_events)
    if gateway_events.get("component") != "memory_gateway" or gateway_events.get("requester") != "shushunya-agent":
        raise AssertionError(f"unexpected component filter in events response: {gateway_events}")
    manifest = archive_memory_gateway(config)
    assert_ok("archive memory gateway manifest tool", manifest)
    if manifest.get("service") != "ArchiveOfHeresy Memory Gateway" or manifest.get("version") != 1:
        raise AssertionError(f"unexpected memory gateway manifest: {manifest}")
    if "magos_context_layers" not in manifest:
        raise AssertionError(f"memory gateway manifest missing magos_context_layers: {manifest}")
    print("[ok] archive memory gateway manifest")
    catalog = archive_memory_catalog(config)
    assert_ok("archive memory catalog tool", catalog)
    if catalog.get("memory_namespace") != config.memory_namespace:
        raise AssertionError(f"unexpected memory namespace in catalog response: {catalog}")
    print("[ok] archive memory catalog namespace")
    memory_search = archive_memory_search(config, "agent memory", limit=2)
    assert_ok("archive memory search tool", memory_search)
    if memory_search.get("memory_namespace") != config.memory_namespace:
        raise AssertionError(f"unexpected memory namespace in memory search response: {memory_search}")
    counts = memory_search.get("counts")
    if not isinstance(counts, dict) or "focus" not in counts or "vector" not in counts:
        raise AssertionError(f"archive memory search missing counts: {memory_search}")
    if memory_search.get("include_content") is not False:
        raise AssertionError(f"archive memory search should be compact by default: {memory_search}")
    for match in memory_search.get("vector", []) or []:
        if "content" in match:
            raise AssertionError(f"compact archive memory search leaked raw vector content: {memory_search}")
    print("[ok] archive memory search namespace")
    focus_only_search = archive_memory_search(config, "agent memory", limit=2, layers="focus")
    assert_ok("archive memory focus-only search tool", focus_only_search)
    if focus_only_search.get("layers") != ["focus"]:
        raise AssertionError(f"archive memory focus-only search did not preserve layers: {focus_only_search}")
    focus_only_counts = focus_only_search.get("counts") or {}
    if focus_only_counts.get("vector") != 0 or focus_only_counts.get("graph_nodes") != 0:
        raise AssertionError(f"archive memory focus-only search leaked lower layers: {focus_only_search}")
    print("[ok] archive memory search layers")
    focus_read = archive_memory_read(config, "focus", "active", max_chars=1000)
    assert_ok("archive memory focus read tool", focus_read)
    if focus_read.get("memory_namespace") != config.memory_namespace:
        raise AssertionError(f"unexpected memory namespace in focus read response: {focus_read}")
    if focus_read.get("max_chars") != 1000 or "content_chars" not in focus_read:
        raise AssertionError(f"focus read did not include size metadata: {focus_read}")
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
