#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from . import agent_runner
from . import server
from . import task_journal
from . import task_watchdog
from . import web_tools
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
    action_fingerprint,
    chat,
    compact_messages_for_model,
    configured_search_providers,
    file_tool,
    parse_action,
    python_tool,
    read_task_journal,
    prune_task_journals,
    repair_action_json,
    result_for_model,
    run_agent,
    GenericHtmlTextParser,
    RanobehubChapterParser,
    WebLinksParser,
    sandbox_status,
    safe_task_id,
    validate_configured_searxng_url,
    validate_public_url,
    web_fetch,
    web_search,
    write_task_journal,
    looks_like_oversized_inline_file_action,
)


def assert_ok(label: str, payload: dict) -> None:
    if not payload.get("ok", True):
        raise AssertionError(f"{label} failed: {payload}")
    print(f"[ok] {label}")


def main() -> int:
    config = AgentConfig()
    offline = os.environ.get("SHUSHUNYA_AGENT_SELF_TEST_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}
    if offline:
        print("[ok] offline self-test mode: Archive integration checks skipped")
    test_journal_tmp = tempfile.TemporaryDirectory()
    task_journal.TASK_JOURNAL_DIR = Path(test_journal_tmp.name)
    if "runtime/task-journals" in str(task_journal.TASK_JOURNAL_DIR):
        raise AssertionError("self-test must not write task journals into runtime/task-journals")
    print("[ok] self-test journal isolation")

    schema_path = Path(__file__).resolve().parents[1] / "tool_schema.json"
    schema_actions = set(json.loads(schema_path.read_text(encoding="utf-8")).get("actions", {}))
    runtime_actions = set(agent_runner.REQUIRED_FIELDS) | agent_runner.FILE_ACTIONS | {
        "sandbox_status",
        "archive_status",
        "archive_memory_gateway",
        "archive_memory_catalog",
        "archive_memory_events",
    }
    if schema_actions != runtime_actions:
        raise AssertionError(f"tool schema/runtime mismatch: missing={sorted(runtime_actions - schema_actions)}, extra={sorted(schema_actions - runtime_actions)}")
    print("[ok] tool schema matches runtime actions")
    if '"limit":100' not in agent_runner.SYSTEM_PROMPT or "is_binary=true" not in agent_runner.SYSTEM_PROMPT:
        raise AssertionError("system prompt missing file pagination or binary web_fetch guidance")
    if "Текущая user task всегда главнее Archive memory" not in agent_runner.SYSTEM_PROMPT:
        raise AssertionError("system prompt missing current-task-over-memory guidance")
    print("[ok] system prompt tool guidance")
    if not server.asks_about_previous_task("Начни прошлую задачу заново"):
        raise AssertionError("previous-task command detector missed restart wording")
    if not server.asks_about_previous_task("Помнишь прошлую задачу?"):
        raise AssertionError("previous-task command detector missed memory question")
    if not server.asks_about_previous_task("Помнишь не законченную задачу?"):
        raise AssertionError("previous-task command detector missed unfinished-task question")
    if not server.asks_about_previous_task("Тогда займись продолжением и закончи уже пожалуйста эту задачу"):
        raise AssertionError("previous-task command detector missed contextless continuation wording")
    if server.asks_about_previous_task("Начни новую задачу"):
        raise AssertionError("previous-task command detector matched unrelated task")
    print("[ok] previous task command detector")
    if not task_journal.is_meta_or_status_task("Помнишь не законченную задачу?"):
        raise AssertionError("unfinished-task question must be filtered from task history")
    if not task_journal.is_meta_or_status_task("Еще раз"):
        raise AssertionError("short repeat command must be filtered from task history")
    if not task_journal.is_meta_or_status_task("Тогда займись продолжением и закончи уже пожалуйста эту задачу"):
        raise AssertionError("contextless continuation command must be filtered from task history")

    class JournalConfig:
        def __init__(self, task_id: str) -> None:
            self.task_id = task_id

    write_task_journal(JournalConfig("journal-real-unfinished"), "start", {"task": "Собери все главы проекта в один файл"})
    write_task_journal(
        JournalConfig("journal-real-unfinished"),
        "final",
        {"ok": False, "message": "Агент остановлен: достигнут лимит времени 1800s."},
    )
    write_task_journal(JournalConfig("journal-repeat-meta"), "start", {"task": "Еще раз"})
    write_task_journal(JournalConfig("journal-repeat-meta"), "final", {"ok": True, "message": "smoke ok"})
    write_task_journal(JournalConfig("journal-question-meta"), "start", {"task": "Помнишь не законченную задачу?"})
    write_task_journal(JournalConfig("journal-question-meta"), "final", {"ok": True, "message": "wrong memory answer"})
    write_task_journal(JournalConfig("journal-contextless-meta"), "start", {"task": "Тогда займись продолжением и закончи уже пожалуйста эту задачу"})
    write_task_journal(JournalConfig("journal-contextless-meta"), "final", {"ok": True, "message": "one-step partial answer"})
    previous_summary = task_journal.latest_completed_task_summary()
    if previous_summary.get("task_id") != "journal-real-unfinished":
        raise AssertionError(f"latest task summary did not skip meta tasks: {previous_summary}")
    print("[ok] previous task journal summary skips meta tasks")
    write_task_journal(JournalConfig("journal-continuation-evidence"), "start", {"task": "Продолжи последнюю незавершенную задачу агента"})
    write_task_journal(
        JournalConfig("journal-continuation-evidence"),
        "tool_result",
        {
            "action": "web_links",
            "result": {
                "ok": True,
                "api_candidates": [{"url": "https://example.com/api/contents", "score": 55}],
            },
        },
    )
    evidence = server.recent_continuation_evidence("self-test-current", limit=2)
    encoded_evidence = json.dumps(evidence, ensure_ascii=False)
    if "https://example.com/api/contents" not in encoded_evidence:
        raise AssertionError(f"recent continuation evidence missed useful tool result: {evidence}")
    previous_context = server.apply_previous_task_context("Продолжи последнюю незавершенную задачу агента", AgentConfig(task_id="self-test-current"))
    if len(previous_context) > 16000:
        raise AssertionError(f"previous task context is too large: {len(previous_context)}")
    print("[ok] previous task context includes continuation evidence")
    watchdog_state = {"attempts": {}, "last_resume_at": {}, "last_final": {}}
    continuable_task = {
        "ok": True,
        "task_id": "watchdog-continuable",
        "running": False,
        "final": {
            "ok": False,
            "continuable": True,
            "resume_task_id": "watchdog-continuable",
            "message": "Агент достиг лимита шагов без final.",
        },
    }
    should_continue, reason = task_watchdog.should_resume(continuable_task, watchdog_state, now=1000.0, max_attempts=2, cooldown_sec=60)
    if not should_continue or reason != "continuable":
        raise AssertionError(f"task watchdog missed continuable task: {should_continue}, {reason}")
    if task_watchdog.normalize_task_id("") != "":
        raise AssertionError("task watchdog must not generate task ids while normalizing empty input")
    task_watchdog.remember_resume_attempt("watchdog-continuable", watchdog_state, 1000.0, continuable_task["final"])
    should_continue, reason = task_watchdog.should_resume(continuable_task, watchdog_state, now=1020.0, max_attempts=2, cooldown_sec=60)
    if should_continue or reason != "cooldown":
        raise AssertionError(f"task watchdog cooldown failed: {should_continue}, {reason}")
    should_continue, reason = task_watchdog.should_resume(continuable_task, watchdog_state, now=1100.0, max_attempts=1, cooldown_sec=60)
    if should_continue or reason != "max_attempts":
        raise AssertionError(f"task watchdog max-attempt guard failed: {should_continue}, {reason}")
    success_task = {"task_id": "watchdog-success", "running": False, "final": {"ok": True, "message": "done"}}
    should_continue, reason = task_watchdog.should_resume(success_task, {"attempts": {}, "last_resume_at": {}, "last_final": {}}, now=1000.0, max_attempts=2, cooldown_sec=60)
    if should_continue or reason != "success":
        raise AssertionError(f"task watchdog should not resume success: {should_continue}, {reason}")
    public_context = task_watchdog.public_resume_context(
        {
            "task_id": "watchdog-public",
            "running": False,
            "final": {"ok": False, "continuable": True, "message": "limit"},
            "events": [
                {"type": "start", "task": "Сделай длинную задачу"},
                {
                    "type": "tool_result",
                    "step": 7,
                    "action": "ranobehub_chapter",
                    "result": {
                        "ok": True,
                        "url": "https://ranobehub.org/ranobe/966/140/3",
                        "path": "/work/novel_data/vol140_ch3.txt",
                        "next_url": "",
                        "preview": "x" * 2000,
                    },
                },
            ],
        }
    )
    if "public_task_snapshot" not in public_context or "vol140_ch3.txt" not in public_context:
        raise AssertionError(f"watchdog public context missed task facts: {public_context}")
    if "Do not use Archive/focus memory" not in public_context:
        raise AssertionError(f"watchdog public context missed Archive warning: {public_context}")
    captured_public_payload: dict = {}

    def fake_watchdog_request(base_url: str, method: str, path: str, api_key: str = "", payload: dict | None = None):
        nonlocal captured_public_payload
        if method == "POST" and path == "/start" and payload and payload.get("resume_task_id"):
            return 401, {"ok": False}
        if method == "GET" and path.startswith("/task?"):
            return 200, {"task_id": "watchdog-public", "events": [{"type": "start", "task": "x"}]}
        if method == "POST" and path == "/start":
            captured_public_payload = dict(payload or {})
            return 202, {"ok": True}
        raise AssertionError(f"unexpected watchdog request: {method} {path}")

    with mock.patch.object(task_watchdog, "request_json", side_effect=fake_watchdog_request):
        status, response, mode = task_watchdog.start_resume("http://agent", "", "watchdog-public", 1)
    if status != 202 or mode != "public_start" or response.get("ok") is not True:
        raise AssertionError(f"watchdog public fallback did not start: {status}, {mode}, {response}")
    if captured_public_payload.get("skip_previous_task_context") is not True:
        raise AssertionError(f"watchdog public fallback must skip previous task context: {captured_public_payload}")
    print("[ok] task watchdog resume guards")
    if parse_action('{"action":"final","message":"ok"}').get("action") != "final":
        raise AssertionError("parse_action failed to parse a valid JSON object")
    for invalid_action_json in ('["final"]', '"final"'):
        try:
            parse_action(invalid_action_json)
            raise AssertionError(f"parse_action accepted non-object JSON: {invalid_action_json}")
        except ValueError:
            pass
    print("[ok] model action JSON must be an object")
    valid_action = agent_runner.validate_action({"action": "web_search", "query": "OpenAI", "limit": 1, "reason": "smoke"})
    if not valid_action.get("ok"):
        raise AssertionError(f"valid action schema was rejected: {valid_action}")
    valid_ranobehub_action = agent_runner.validate_action(
        {
            "action": "ranobehub_chapter",
            "url": "https://ranobehub.org/ranobe/966/10/9",
            "path": "/work/slime/ch09.txt",
            "mode": "write",
        }
    )
    if not valid_ranobehub_action.get("ok"):
        raise AssertionError(f"valid ranobehub_chapter action was rejected: {valid_ranobehub_action}")
    valid_extract_action = agent_runner.validate_action(
        {
            "action": "web_extract_to_file",
            "url": "https://example.com/page",
            "path": "/work/page.txt",
            "mode": "append",
        }
    )
    if not valid_extract_action.get("ok"):
        raise AssertionError(f"valid web_extract_to_file action was rejected: {valid_extract_action}")
    valid_links_action = agent_runner.validate_action(
        {
            "action": "web_links",
            "url": "https://example.com/page",
            "pattern": "chapter|volume",
            "limit": 100,
        }
    )
    if not valid_links_action.get("ok"):
        raise AssertionError(f"valid web_links action was rejected: {valid_links_action}")
    invalid_actions = [
        ({"action": "does_not_exist"}, "unsupported action"),
        ({"action": "web_search", "query": "OpenAI", "limit": "1"}, "invalid action schema"),
        ({"action": "web_search", "query": "OpenAI", "extra": True}, "invalid action schema"),
        ({"action": "archive_memory_read", "kind": "vector"}, "invalid action schema"),
        ({"action": "archive_memory_search", "query": "x", "layers": ["focus", "bad"]}, "invalid action schema"),
        ({"action": "read_file", "path": "/media/host.txt"}, "invalid action schema"),
    ]
    for invalid_action, expected_error in invalid_actions:
        result = agent_runner.validate_action(invalid_action)
        if result.get("ok") is not False or result.get("error") != expected_error:
            raise AssertionError(f"invalid action schema was not rejected: action={invalid_action}, result={result}")
    print("[ok] runtime action schema validation")

    chapter_parser = RanobehubChapterParser()
    chapter_parser.feed(
        """
        <html><head><link rel="canonical" href="https://ranobehub.org/ranobe/1/2/3"></head><body>
        <a data-previous-chapter-link href="https://ranobehub.org/prev">prev</a>
        <a data-next-chapter-link href="https://ranobehub.org/next">next</a>
        <div data-container="123">
          <h1>Глава 1: тест</h1>
          <script>bad()</script>
          <p>Первый абзац.</p>
          <p>Второй <b>абзац</b>.</p>
        </div>
        <p>Внешний мусор.</p>
        </body></html>
        """
    )
    parsed_chapter = chapter_parser.payload()
    if parsed_chapter.get("title") != "Глава 1: тест" or parsed_chapter.get("paragraphs") != ["Первый абзац.", "Второй абзац."]:
        raise AssertionError(f"ranobehub parser failed: {parsed_chapter}")
    if parsed_chapter.get("next_url") != "https://ranobehub.org/next":
        raise AssertionError(f"ranobehub parser missed next URL: {parsed_chapter}")
    print("[ok] ranobehub chapter parser")

    generic_parser = GenericHtmlTextParser()
    generic_parser.feed(
        """
        <html><head><title>Generic Page</title></head><body>
        <nav>menu noise</nav>
        <main><h1>Main title</h1><p>First useful paragraph.</p><p>Second useful paragraph.</p></main>
        <footer>footer noise</footer>
        </body></html>
        """
    )
    generic_payload = generic_parser.payload()
    if generic_payload.get("title") != "Generic Page" or generic_payload.get("blocks") != [
        "Main title",
        "First useful paragraph.",
        "Second useful paragraph.",
    ]:
        raise AssertionError(f"generic HTML parser failed: {generic_payload}")
    print("[ok] generic web extract parser")

    links_parser = WebLinksParser("https://example.com/book/index.html")
    links_parser.feed(
        """
        <html><head><title>Book</title></head><body>
        <script src="/assets/app.js"></script>
        <ranobe-contents :ranobe-id="966" data-kind="toc"></ranobe-contents>
        <a href="/book/1">Volume 1</a>
        <a href="chapter-2.html" data-next-chapter-link>Chapter 2</a>
        <a href="mailto:test@example.com">mail</a>
        </body></html>
        """
    )
    links_payload = links_parser.payload(pattern="volume|chapter", limit=10)
    if links_payload.get("title") != "Book" or [link.get("url") for link in links_payload.get("links", [])] != [
        "https://example.com/book/1",
        "https://example.com/book/chapter-2.html",
    ]:
        raise AssertionError(f"web links parser failed: {links_payload}")
    if not links_payload["links"][1].get("data_next"):
        raise AssertionError(f"web links parser missed data-next flag: {links_payload}")
    if links_payload.get("scripts") != ["https://example.com/assets/app.js"]:
        raise AssertionError(f"web links parser missed scripts: {links_payload}")
    if not any(item.get("tag") == "ranobe-contents" and item.get("attrs", {}).get(":ranobe-id") == "966" for item in links_payload.get("custom_elements", [])):
        raise AssertionError(f"web links parser missed custom elements: {links_payload}")
    candidates = agent_runner.scan_script_api_candidates(
        "https://example.com/book",
        [],
        [{"tag": "ranobe-contents", "attrs": {":ranobe-id": "966"}}],
    )
    if candidates:
        raise AssertionError(f"web links script scanner should not invent candidates without scripts: {candidates}")
    filled = agent_runner.fill_api_placeholders("/api/ranobe/{ranobe}/contents", {":ranobe-id": "966", "ranobe": "966"})
    if filled != "/api/ranobe/966/contents":
        raise AssertionError(f"web links placeholder fill failed: {filled}")
    print("[ok] web links parser")

    if configured_search_providers()[0] != "searxng":
        raise AssertionError(f"search providers must start with searxng: {configured_search_providers()}")
    print("[ok] search provider order starts with searxng")

    try:
        validate_public_url("http://127.0.0.1")
        raise AssertionError("validate_public_url allowed 127.0.0.1")
    except ValueError:
        print("[ok] validate_public_url blocks 127.0.0.1")
    try:
        validate_public_url("https://user:pass@example.com/")
        raise AssertionError("validate_public_url allowed URL credentials")
    except ValueError:
        print("[ok] validate_public_url blocks credentials")

    old_searxng_url = web_tools.SEARXNG_URL
    try:
        web_tools.SEARXNG_URL = "http://127.0.0.1:8888"
        validate_configured_searxng_url("http://127.0.0.1:8888/search?q=test&format=json")
        print("[ok] configured SearXNG localhost URL allowed")
        try:
            validate_configured_searxng_url("https://127.0.0.1:8888/search?q=test&format=json")
            raise AssertionError("configured SearXNG validator allowed scheme mismatch")
        except ValueError:
            print("[ok] configured SearXNG scheme mismatch blocked")
    finally:
        web_tools.SEARXNG_URL = old_searxng_url

    try:
        web_fetch(config, "http://127.0.0.1:8888/search?q=test&format=json")
        raise AssertionError("web_fetch allowed localhost")
    except ValueError:
        print("[ok] web_fetch blocks localhost")
    if not agent_runner.is_textual_content("application/json", b'{"ok":true}'):
        raise AssertionError("JSON content was not detected as textual")
    if agent_runner.is_textual_content("image/png", b"\x89PNG\r\n\x1a\n\x00\x00"):
        raise AssertionError("binary content was detected as textual")
    print("[ok] web_fetch binary detection")
    decoded_text, decoded_encoding = agent_runner.decode_web_text("привет".encode("utf-8"), "not-a-real-charset")
    if decoded_text != "привет" or decoded_encoding != "utf-8":
        raise AssertionError(f"web_fetch charset fallback failed: text={decoded_text}, encoding={decoded_encoding}")
    print("[ok] web_fetch charset fallback")

    old_provider_env = web_tools.SEARCH_PROVIDERS
    old_brave_key = web_tools.BRAVE_SEARCH_API_KEY
    try:
        web_tools.SEARCH_PROVIDERS = "searxng,marginalia,wikipedia"
        web_tools.BRAVE_SEARCH_API_KEY = "fake-key-must-not-be-called"
        calls: list[str] = []

        def fake_provider(name: str, ok: bool = False):
            def _provider(query: str, limit: int) -> dict:
                calls.append(name)
                return {"ok": ok, "provider": name, "results": [], "truncated": False}
            return _provider

        with mock.patch.object(web_tools, "web_search_searxng", fake_provider("searxng")), \
                mock.patch.object(web_tools, "web_search_marginalia", fake_provider("marginalia")), \
                mock.patch.object(web_tools, "web_search_wikipedia", fake_provider("wikipedia")):
            result = web_search(config, "provider-order-test", 3)
        if calls != ["searxng", "marginalia", "wikipedia"] or "brave" in calls:
            raise AssertionError(f"unexpected provider calls with brave disabled: {calls}, result={result}")
        print("[ok] brave not called when absent from SHUSHUNYA_AGENT_SEARCH_PROVIDERS")
    finally:
        web_tools.SEARCH_PROVIDERS = old_provider_env
        web_tools.BRAVE_SEARCH_API_KEY = old_brave_key

    large_result = {"ok": True, "content": "x" * 50000, "size": 50000}
    compacted_result = result_for_model("read_file", large_result, config)
    if len(compacted_result.get("content", "")) > 7000:
        raise AssertionError("read_file result was not compacted for model context")
    print("[ok] read_file result compacted for model context")
    large_json_result = {
        "ok": True,
        "content_type": "application/json",
        "text": json.dumps(
            {
                "volumes": [
                    {
                        "num": index,
                        "name": f"Volume {index}",
                        "chapters": [{"url": f"https://example.com/{index}/{chapter}", "title": f"Chapter {chapter}"} for chapter in range(1, 30)],
                    }
                    for index in range(1, 25)
                ]
            }
        ),
    }
    compacted_json = result_for_model("web_fetch", large_json_result, config)
    if "text" in compacted_json or compacted_json.get("json_summary", {}).get("volumes", {}).get("count") != 24:
        raise AssertionError(f"web_fetch JSON result was not summarized: {compacted_json}")
    first_volume = compacted_json["json_summary"]["volumes"]["items"][0]
    if first_volume.get("chapters", {}).get("count") != 29 or "first" not in first_volume.get("chapters", {}):
        raise AssertionError(f"web_fetch JSON nested list summary failed: {compacted_json}")
    print("[ok] web_fetch JSON result summarized for model context")
    class FakeHeaders(dict):
        def get_content_charset(self):
            return "utf-8"

    class FakeResponse:
        status = 200
        headers = FakeHeaders({"Content-Type": "application/json"})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def geturl(self):
            return "https://example.com/data.json"

        def read(self, size):
            payload = json.dumps({"volumes": [{"num": index, "chapters": [{"num": chapter} for chapter in range(30)]} for index in range(40)]}).encode()
            return payload[:size]

    class FakeOpener:
        def open(self, request, timeout=30):
            return FakeResponse()

    with mock.patch.object(web_tools, "validate_public_url", lambda url: url), mock.patch.object(web_tools, "build_opener", lambda *_args, **_kwargs: FakeOpener()):
        fetched_json = web_tools.web_fetch(AgentConfig(max_tool_output_chars=100), "https://example.com/data.json", 200000)
    if "text" in fetched_json or fetched_json.get("json_summary", {}).get("volumes", {}).get("count") != 40:
        raise AssertionError(f"web_fetch did not summarize full JSON before truncating text: {fetched_json}")
    print("[ok] web_fetch summarizes full JSON responses")

    large_links_result = {
        "ok": True,
        "links": [{"href": f"https://example.com/chapter/{index}", "text": "chapter " + ("x" * 200)} for index in range(80)],
        "api_candidates": [
            {
                "url": f"https://example.com/api/{index}",
                "score": 100 - index,
                "source_script": "https://example.com/assets/app." + ("y" * 500) + ".js",
            }
            for index in range(30)
        ],
        "custom_elements": [{"tag": f"x-reader-{index}", "attrs": {"data": "z" * 200}} for index in range(60)],
        "scripts": ["https://example.com/assets/" + ("s" * 300) + f"{index}.js" for index in range(20)],
    }
    compacted_links = result_for_model("web_links", large_links_result, config)
    if (
        len(compacted_links.get("links", [])) > 25
        or len(compacted_links.get("api_candidates", [])) > 12
        or len(compacted_links.get("scripts", [])) > 8
        or not compacted_links.get("compacted_for_model")
    ):
        raise AssertionError(f"web_links result was not compacted: {compacted_links}")
    encoded_links = json.dumps(compacted_links, ensure_ascii=False)
    if len(encoded_links) > 12000:
        raise AssertionError(f"web_links compacted result is still too large: {len(encoded_links)}")
    print("[ok] web_links result compacted for model context")

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
    shell_gate_config = AgentConfig(shell_enabled=True, shell_approval_required=True)
    shell_gate_result = agent_runner.run_shell(shell_gate_config, "echo should-not-run", timeout=1, approved=False)
    if shell_gate_result.get("ok") is not False or shell_gate_result.get("approval_required") is not True:
        raise AssertionError(f"shell approval gate failed: {shell_gate_result}")
    if not agent_runner.validate_action({"action": "shell", "cmd": "echo ok", "approved": True}).get("ok"):
        raise AssertionError("shell approved field should be valid")
    print("[ok] shell approval gate")
    if server.validate_task_text("").get("status") != 400:
        raise AssertionError("empty task should fail validation")
    old_max_task_chars = server.MAX_TASK_CHARS
    try:
        server.MAX_TASK_CHARS = 4
        task_error = server.validate_task_text("x" * 5)
        if not task_error or task_error.get("status") != 413:
            raise AssertionError(f"oversized task should fail validation: {task_error}")
    finally:
        server.MAX_TASK_CHARS = old_max_task_chars
    print("[ok] HTTP task size validation")
    with server.STATE_LOCK:
        old_queued = server.RUN_STATE["queued"]
        server.RUN_STATE["queued"] = server.MAX_QUEUE
    try:
        queue_error = server.try_enqueue_run()
        if not queue_error or queue_error.get("error") != "agent queue full":
            raise AssertionError(f"queue full guard failed: {queue_error}")
    finally:
        with server.STATE_LOCK:
            server.RUN_STATE["queued"] = old_queued
    print("[ok] HTTP queue full guard")

    class FakeStreamHeaders:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def get(self, key: str, default: str = "") -> str:
            if key == "Content-Length":
                return str(len(self.body))
            return default

    class FakeStreamHandler:
        def __init__(self, body: bytes) -> None:
            self.headers = FakeStreamHeaders(body)
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.statuses: list[int] = []

        def send_response(self, status: int) -> None:
            self.statuses.append(status)

        def send_header(self, key: str, value: str) -> None:
            pass

        def end_headers(self) -> None:
            pass

    stream_body = b'{"task":"queue full self-test","wait_for_slot":true}'
    stream_handler = FakeStreamHandler(stream_body)
    with server.STATE_LOCK:
        old_queued = server.RUN_STATE["queued"]
        server.RUN_STATE["queued"] = server.MAX_QUEUE
    try:
        server.AgentHandler.run_stream(stream_handler)  # type: ignore[arg-type]
    finally:
        with server.STATE_LOCK:
            server.RUN_STATE["queued"] = old_queued
    if stream_handler.statuses[:1] != [429]:
        raise AssertionError(f"run_stream should reject full queue before opening stream: {stream_handler.statuses}")
    print("[ok] run_stream queue full returns HTTP 429")

    class FakeHeaders:
        def __init__(self, authorization: str = "") -> None:
            self.authorization = authorization

        def get(self, key: str, default: str = "") -> str:
            return self.authorization if key == "Authorization" else default

    class FakeHandler:
        def __init__(self, authorization: str = "", body: bytes = b"") -> None:
            self.headers = FakeHeaders(authorization)
            self.rfile = io.BytesIO(body)
            if body:
                self.headers.content_length = str(len(body))

    class FakeJsonHeaders(FakeHeaders):
        def __init__(self, body: bytes) -> None:
            super().__init__("")
            self.body = body

        def get(self, key: str, default: str = "") -> str:
            if key == "Content-Length":
                return str(len(self.body))
            return super().get(key, default)

    class FakeJsonHandler:
        def __init__(self, body: bytes) -> None:
            self.headers = FakeJsonHeaders(body)
            self.rfile = io.BytesIO(body)

    if server.read_json(FakeJsonHandler(b"")) != {}:
        raise AssertionError("empty JSON request body should parse as an empty object")
    if server.read_json(FakeJsonHandler(b'{"task":"ok"}')).get("task") != "ok":
        raise AssertionError("JSON object request body did not parse")
    try:
        server.read_json(FakeJsonHandler(b'["not", "object"]'))
        raise AssertionError("JSON array request body should be rejected")
    except server.RequestError as exc:
        if exc.status != 400:
            raise AssertionError(f"JSON array request body returned wrong status: {exc.status}")
    print("[ok] HTTP JSON body must be an object")

    old_api_key = server.API_KEY
    try:
        server.API_KEY = ""
        if server.health_detail_allowed(FakeHandler()):
            raise AssertionError("health detail should require configured API key")
        if server.privileged_api_allowed(FakeHandler()):
            raise AssertionError("privileged API should require configured API key")
        server.API_KEY = "secret"
        if not server.health_detail_allowed(FakeHandler("Bearer secret")):
            raise AssertionError("health detail should allow valid bearer key")
        if not server.privileged_api_allowed(FakeHandler("Bearer secret")):
            raise AssertionError("privileged API should allow valid bearer key")
    finally:
        server.API_KEY = old_api_key
    print("[ok] privileged endpoints require API key")

    compact_resume = server.compact_resume_events(
        [{"type": "tool_result", "result": {"content": "r" * 10000}, "index": index} for index in range(30)],
        max_chars=5000,
    )
    compact_resume_text = str(compact_resume)
    if not compact_resume or len(compact_resume_text) > 7000:
        raise AssertionError("resume events were not compacted")
    print("[ok] resume context compacted")

    auto_continue_calls: list[tuple[str, dict]] = []

    def fake_auto_continue_success(task: str, run_config: AgentConfig, event_sink=None, **kwargs):
        auto_continue_calls.append((task, kwargs))
        if event_sink is not None:
            event_sink({"type": "action", "action": "noop"})
            event_sink({"type": "tool_result", "ok": True})
        if len(auto_continue_calls) < 3:
            return 2, json.dumps({"ok": False, "continuable": True, "message": "limit"}), ""
        return 0, json.dumps({"ok": True, "message": "done"}), ""

    with mock.patch.object(server, "run_agent_once_locked", side_effect=fake_auto_continue_success):
        code, _stdout, _stderr, result = server.run_agent_with_auto_continue(
            "auto continue self-test",
            AgentConfig(task_id="self-test-auto-continue"),
            {"auto_continue": True, "auto_continue_max_cycles": 3},
        )
    if code != 0 or result.get("ok") is not True or len(auto_continue_calls) != 3:
        raise AssertionError(f"auto-continue did not reach final success: code={code}, result={result}, calls={len(auto_continue_calls)}")
    if result.get("auto_continue", {}).get("cycles_used") != 2:
        raise AssertionError(f"auto-continue cycle metadata is wrong: {result}")
    print("[ok] auto-continue reaches final success")

    loop_calls: list[tuple[str, dict]] = []

    def fake_auto_continue_loop(task: str, run_config: AgentConfig, event_sink=None, **kwargs):
        loop_calls.append((task, kwargs))
        return 2, json.dumps({"ok": False, "continuable": True, "message": "same limit"}), ""

    with mock.patch.object(server, "run_agent_once_locked", side_effect=fake_auto_continue_loop):
        code, _stdout, _stderr, result = server.run_agent_with_auto_continue(
            "auto continue loop self-test",
            AgentConfig(task_id="self-test-auto-loop"),
            {"auto_continue": True, "auto_continue_max_cycles": 5},
        )
    if code != 2 or result.get("auto_continue_exhausted") is not True or len(loop_calls) != 2:
        raise AssertionError(f"auto-continue loop guard failed: code={code}, result={result}, calls={len(loop_calls)}")
    if result.get("auto_continue", {}).get("stop_reason") != "repeated_without_progress":
        raise AssertionError(f"auto-continue loop stop reason is wrong: {result}")
    print("[ok] auto-continue loop guard")

    public_journal = server.public_task_journal_payload({"ok": True, "path": "/private/runtime/task.jsonl", "events": []})
    if "path" in public_journal:
        raise AssertionError(f"public journal payload leaked path: {public_journal}")
    print("[ok] public journal path redaction")

    state = server.runtime_state()
    if "busy" not in state or state.get("max_request_bytes", 0) <= 0:
        raise AssertionError(f"runtime state missing expected fields: {state}")
    if state.get("max_task_chars", 0) <= 0:
        raise AssertionError(f"runtime state missing max_task_chars: {state}")
    if state.get("max_queue", 0) <= 0:
        raise AssertionError(f"runtime state missing max_queue: {state}")
    if not state.get("revision"):
        raise AssertionError(f"runtime state missing revision: {state}")
    if state.get("uptime_sec", -1) < 0 or state.get("started_at", 0) <= 0:
        raise AssertionError(f"runtime state missing uptime: {state}")
    print("[ok] runtime state payload")
    old_metrics = json.loads(json.dumps(server.RUN_METRICS))
    try:
        server.record_run_started()
        server.collect_agent_event({"type": "step"})
        server.collect_agent_event({"type": "warning", "code": "json_parse_error"})
        server.collect_agent_event({"type": "warning", "code": "json_repaired"})
        server.collect_agent_event({"type": "warning", "code": "validation_error"})
        server.collect_agent_event({"type": "tool_result", "action": "web_search", "ok": True, "source": "searxng"})
        server.collect_agent_event({"type": "tool_result", "action": "python", "ok": False, "timeout": True})
        server.record_run_finished(2)
        metrics = server.runtime_state().get("metrics") or {}
        if metrics.get("runs_started") != old_metrics.get("runs_started", 0) + 1:
            raise AssertionError(f"run start metric failed: {metrics}")
        if metrics.get("runs_failed") != old_metrics.get("runs_failed", 0) + 1:
            raise AssertionError(f"run failure metric failed: {metrics}")
        if metrics.get("runs_cancelled") != old_metrics.get("runs_cancelled", 0) + 1:
            raise AssertionError(f"cancel metric failed: {metrics}")
        if metrics.get("json_parse_errors") != old_metrics.get("json_parse_errors", 0) + 1:
            raise AssertionError(f"json parse metric failed: {metrics}")
        if metrics.get("json_repairs") != old_metrics.get("json_repairs", 0) + 1:
            raise AssertionError(f"json repair metric failed: {metrics}")
        if metrics.get("validation_rejects") != old_metrics.get("validation_rejects", 0) + 1:
            raise AssertionError(f"validation metric failed: {metrics}")
        if metrics.get("tool_failures") != old_metrics.get("tool_failures", 0) + 1:
            raise AssertionError(f"tool failure metric failed: {metrics}")
        if metrics.get("timeouts") != old_metrics.get("timeouts", 0) + 1:
            raise AssertionError(f"timeout metric failed: {metrics}")
        if (metrics.get("web_search_sources") or {}).get("searxng") != (old_metrics.get("web_search_sources") or {}).get("searxng", 0) + 1:
            raise AssertionError(f"web source metric failed: {metrics}")
    finally:
        with server.STATE_LOCK:
            server.RUN_METRICS.clear()
            server.RUN_METRICS.update(old_metrics)
    print("[ok] runtime metrics payload")
    if server.STREAM_HEARTBEAT_SEC < 5.0:
        raise AssertionError(f"stream heartbeat interval is unsafe: {server.STREAM_HEARTBEAT_SEC}")
    print("[ok] stream heartbeat interval")

    chapter_a = {
        "action": "ranobehub_chapter",
        "url": "https://ranobehub.org/ranobe/966/130/5",
        "path": "/work/novel_data/vol130_ch05.txt",
        "mode": "write",
    }
    chapter_b = {
        "action": "ranobehub_chapter",
        "url": "https://ranobehub.org/ranobe/966/130/5?unused=1#top",
        "path": "/work/novel_data/vol130_ch5.txt",
        "mode": "append",
    }
    if action_fingerprint(chapter_a) != action_fingerprint(chapter_b):
        raise AssertionError("ranobehub chapter repeat fingerprint should ignore output path/name variants")
    print("[ok] ranobehub repeat fingerprint")

    cancelled_task_id = server.mark_task_cancelled("self test cancel registry")
    if not server.is_task_cancelled(cancelled_task_id):
        raise AssertionError("cancel registry did not preserve marked task")
    server.clear_task_cancelled(cancelled_task_id)
    if server.is_task_cancelled(cancelled_task_id):
        raise AssertionError("cancel registry did not clear marked task")
    print("[ok] cancel registry")

    cancel_events: list[dict] = []
    cancel_stdout = io.StringIO()
    cancel_config = AgentConfig(
        task_id=safe_task_id("self-test-cancel"),
        json_output=True,
        max_steps=1,
        cancel_check=lambda: True,
        inject_memory=False,
        archive_internal_steps=False,
    )
    with mock.patch.object(agent_runner, "chat") as mocked_chat, \
            contextlib.redirect_stdout(cancel_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        cancel_code = run_agent("should cancel before model", cancel_config, event_sink=cancel_events.append)
    cancel_payload = json.loads(cancel_stdout.getvalue())
    if cancel_code != 2 or cancel_payload.get("cancelled") is not True or mocked_chat.called:
        raise AssertionError(f"cooperative cancel failed: code={cancel_code}, payload={cancel_payload}")
    if not any(event.get("type") == "final" and event.get("cancelled") is True for event in cancel_events):
        raise AssertionError(f"cancel event missing: {cancel_events}")
    print("[ok] cooperative cancel")

    health_archive = {"status": "ok", "jsonl_root": "/private/archive/path"}
    minimal_health = {
        "status": "ok",
        "service": "ShushunyaAgent",
        "archive_status": health_archive.get("status", "unknown"),
    }
    if "archive" in minimal_health or "jsonl_root" in json.dumps(minimal_health):
        raise AssertionError(f"minimal health leaked archive details: {minimal_health}")
    print("[ok] minimal health shape")
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

    old_journal_dir = task_journal.TASK_JOURNAL_DIR
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            task_journal.TASK_JOURNAL_DIR = Path(tmpdir)
            for index in range(5):
                path = task_journal.TASK_JOURNAL_DIR / f"journal-{index}.jsonl"
                path.write_text("{}\n", encoding="utf-8")
            prune_task_journals(2)
            remaining = sorted(path.name for path in task_journal.TASK_JOURNAL_DIR.glob("*.jsonl"))
            if len(remaining) != 2:
                raise AssertionError(f"journal retention kept wrong files: {remaining}")
        print("[ok] task journal retention")
    finally:
        task_journal.TASK_JOURNAL_DIR = old_journal_dir

    old_journal_dir = task_journal.TASK_JOURNAL_DIR
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            task_journal.TASK_JOURNAL_DIR = Path(tmpdir)
            task_id = safe_task_id("self-test-large-journal")
            path = agent_runner.task_journal_path(task_id)
            with path.open("w", encoding="utf-8") as fh:
                for index in range(25):
                    fh.write(json.dumps({"type": "event", "index": index}) + "\n")
            journal_tail = read_task_journal(task_id, limit=3)
            assert_ok("large task journal tail", journal_tail)
            indexes = [event.get("index") for event in journal_tail.get("events", [])]
            if journal_tail.get("event_count") != 25 or indexes != [22, 23, 24]:
                raise AssertionError(f"journal tail read failed: {journal_tail}")
        print("[ok] task journal tail read")
    finally:
        task_journal.TASK_JOURNAL_DIR = old_journal_dir

    old_journal_dir = task_journal.TASK_JOURNAL_DIR
    old_journal_max_bytes = task_journal.TASK_JOURNAL_MAX_BYTES
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            task_journal.TASK_JOURNAL_DIR = Path(tmpdir)
            task_journal.TASK_JOURNAL_MAX_BYTES = 128
            rotate_config = AgentConfig(task_id=safe_task_id("self-test-journal-rotate"))
            write_task_journal(rotate_config, "large", {"content": "x" * 1000})
            write_task_journal(rotate_config, "after_rotate", {"ok": True})
            rotated = read_task_journal(rotate_config.task_id, limit=5)
            assert_ok("task journal size rotation", rotated)
            event_types = [event.get("type") for event in rotated.get("events", [])]
            if "journal_rotated" not in event_types or event_types[-1] != "after_rotate":
                raise AssertionError(f"journal size rotation failed: {rotated}")
        print("[ok] task journal size cap")
    finally:
        task_journal.TASK_JOURNAL_DIR = old_journal_dir
        task_journal.TASK_JOURNAL_MAX_BYTES = old_journal_max_bytes

    with mock.patch.object(agent_runner, "chat", return_value='{"action":"final","message":"repaired"}'):
        repaired_action = repair_action_json(config, "```json\n{\"action\":\"final\",\"message\":\"broken\"", ValueError("broken"))
    if repaired_action != {"action": "final", "message": "repaired"}:
        raise AssertionError(f"unexpected repaired action: {repaired_action}")
    try:
        repair_action_json(config, "Обычный чатовый ответ без JSON action", ValueError("not json"))
        raise AssertionError("JSON repair should reject non-JSON chat prose")
    except ValueError:
        pass
    print("[ok] JSON repair helper")
    broken_inline_write = (
        '{"action":"write_file","path":"/work/book.txt","content":"'
        + ("текст главы " * 120)
    )
    if not looks_like_oversized_inline_file_action(
        broken_inline_write,
        ValueError("Unterminated string starting at: line 1 column 60"),
    ):
        raise AssertionError("oversized inline write guard missed truncated write_file JSON")
    if looks_like_oversized_inline_file_action('{"action":"final","message":"ok"}', ValueError("broken")):
        raise AssertionError("oversized inline write guard matched a normal final action")
    print("[ok] oversized inline write guard")

    captured_payloads: list[dict] = []

    def capture_archive_payload(config_arg, method, path, payload=None, timeout=180):
        captured_payloads.append(payload or {})
        return {"choices": [{"message": {"content": '{"action":"final","message":"payload ok"}'}}]}

    with mock.patch.object(agent_runner, "archive_request", side_effect=capture_archive_payload):
        payload_reply = chat(config, [{"role": "user", "content": "payload"}], inject_memory=True, archive_enabled=True)
    if payload_reply != '{"action":"final","message":"payload ok"}':
        raise AssertionError(f"unexpected payload reply: {payload_reply}")
    if not captured_payloads or captured_payloads[0].get("archive_system_prompt_enabled") is not False:
        raise AssertionError(f"agent chat did not disable Archive persona prompt: {captured_payloads}")
    print("[ok] agent chat disables Archive persona prompt")

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

    def context_side_effect(config_arg, method, path, payload=None, timeout=180):
        if payload and payload.get("focus_enabled"):
            raise HTTPError(
                url="http://archive/v1/chat/completions",
                code=400,
                msg="Bad Request",
                hdrs={},
                fp=io.BytesIO(b'{"error":"context too large"}'),
            )
        return {"choices": [{"message": {"content": '{"action":"final","message":"memory off ok"}'}}]}

    context_config = AgentConfig(llm_retries=1, inject_memory=True, archive_internal_steps=False)
    with mock.patch.object(agent_runner, "archive_request", side_effect=context_side_effect) as mocked_archive:
        context_reply = chat(context_config, [{"role": "user", "content": "context"}], inject_memory=True, archive_enabled=False)
    if context_reply != '{"action":"final","message":"memory off ok"}' or mocked_archive.call_count != 4:
        raise AssertionError(f"context retry did not disable memory after compacted attempts: reply={context_reply}, calls={mocked_archive.call_count}")
    print("[ok] model context retry disables memory")

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
    if limit_payload.get("continuable") is not True or limit_payload.get("resume_task_id") != limit_config.task_id:
        raise AssertionError(f"runtime limit should be continuable: {limit_payload}")
    print("[ok] runtime limit")

    tool_error_events: list[dict] = []
    tool_error_stdout = io.StringIO()
    tool_error_config = AgentConfig(
        task_id=safe_task_id("self-test-tool-error"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
        "Обычный чатовый ответ вместо JSON action",
        '{"action":"web_fetch","url":"http://127.0.0.1:8095/health"}',
        '{"action":"final","message":"handled"}',
    ]), contextlib.redirect_stdout(tool_error_stdout), contextlib.redirect_stderr(io.StringIO()):
        tool_error_code = run_agent("handle bad tool", tool_error_config, event_sink=tool_error_events.append)
    tool_error_payload = json.loads(tool_error_stdout.getvalue())
    failed_tool_events = [event for event in tool_error_events if event.get("type") == "tool_result" and event.get("ok") is False]
    if tool_error_code != 0 or tool_error_payload.get("message") != "handled" or not failed_tool_events:
        raise AssertionError(f"tool exception was not fail-soft: code={tool_error_code}, payload={tool_error_payload}, events={tool_error_events}")
    if not any(event.get("code") == "json_repair_failed" for event in tool_error_events):
        raise AssertionError(f"non-JSON model output did not force retry: {tool_error_events}")
    print("[ok] tool exception fail-soft")

    repeat_stdout = io.StringIO()
    repeat_config = AgentConfig(
        task_id=safe_task_id("self-test-cumulative-repeat"),
        json_output=True,
        max_steps=30,
        inject_memory=False,
        archive_internal_steps=False,
    )
    repeat_actions: list[str] = []
    for index in range(18):
        repeat_actions.append('{"action":"list_files","path":"/work","max_depth":1,"limit":1,"offset":0}')
        repeat_actions.append(f'{{"action":"web_search","query":"different filler {index}","limit":1}}')
    with mock.patch.object(agent_runner, "chat", side_effect=repeat_actions), \
            mock.patch.object(agent_runner, "web_search", return_value={"ok": True, "results": []}), \
            contextlib.redirect_stdout(repeat_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        repeat_code = run_agent("stop cumulative repeats", repeat_config)
    repeat_payload = json.loads(repeat_stdout.getvalue())
    if repeat_code != 2 or repeat_payload.get("continuable") is not True:
        raise AssertionError(f"cumulative repeat guard did not stop as continuable: code={repeat_code}, payload={repeat_payload}")
    if "цикл повторяющихся действий" not in repeat_payload.get("message", ""):
        raise AssertionError(f"cumulative repeat guard returned wrong message: {repeat_payload}")
    print("[ok] cumulative repeated action guard")

    if offline:
        print("[skip] archive integration")
    else:
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
    replace_large_guard = file_tool(
        config,
        {"action": "replace_in_file", "path": "/work/self-test/hello.txt", "old": "hello-updated", "new": "x", "max_file_bytes": 4},
    )
    if replace_large_guard.get("ok") is not False or replace_large_guard.get("error") != "file too large for replace_in_file":
        raise AssertionError(f"replace_in_file large file guard failed: {replace_large_guard}")
    print("[ok] replace_in_file size guard")

    info_result = file_tool(config, {"action": "file_info", "path": "/work/self-test/hello.txt", "sha256": True})
    assert_ok("file_info sha256", info_result)
    expected_hash = hashlib.sha256(b"hello-updated").hexdigest()
    if info_result.get("sha256") != expected_hash or info_result.get("hash_bytes") != len("hello-updated"):
        raise AssertionError(f"unexpected file_info hash metadata: {info_result}")
    print("[ok] file hash metadata")

    binary_write = python_tool(
        config,
        {"action": "python", "code": "open('/work/self-test/blob.bin','wb').write(b'abc\\x00def')", "timeout": 30},
    )
    assert_ok("binary fixture", binary_write)
    binary_read = file_tool(config, {"action": "read_file", "path": "/work/self-test/blob.bin"})
    assert_ok("binary read_file", binary_read)
    if binary_read.get("is_binary") is not True or binary_read.get("encoding") != "utf-8-replace":
        raise AssertionError(f"read_file did not mark binary content: {binary_read}")
    print("[ok] binary file detection")

    list_result = file_tool(config, {"action": "list_files", "path": "/work/self-test", "max_depth": 1})
    assert_ok("list_files", list_result)
    if not any(item.get("path") == "/work/self-test/hello.txt" for item in list_result.get("items", [])):
        raise AssertionError(f"created file is absent from listing: {list_result}")
    print("[ok] file listing")

    assert_ok("write_file page fixture a", file_tool(config, {"action": "write_file", "path": "/work/self-test/page-a.txt", "content": "a"}))
    assert_ok("write_file page fixture b", file_tool(config, {"action": "write_file", "path": "/work/self-test/page-b.txt", "content": "b"}))
    paged_listing = file_tool(config, {"action": "list_files", "path": "/work/self-test", "max_depth": 1, "limit": 1, "offset": 1})
    assert_ok("list_files page", paged_listing)
    if len(paged_listing.get("items", [])) != 1 or paged_listing.get("offset") != 1 or paged_listing.get("total_count", 0) < 3:
        raise AssertionError(f"list_files pagination failed: {paged_listing}")
    paged_find = file_tool(config, {"action": "find_files", "path": "/work/self-test", "pattern": "page-*.txt", "limit": 1, "offset": 1})
    assert_ok("find_files page", paged_find)
    if len(paged_find.get("items", [])) != 1 or paged_find.get("offset") != 1 or paged_find.get("total_count") != 2:
        raise AssertionError(f"find_files pagination failed: {paged_find}")
    print("[ok] file pagination")

    search_result = file_tool(config, {"action": "search_text", "path": "/work/self-test", "query": "hello-updated", "max_matches": 5})
    assert_ok("search_text metadata", search_result)
    if search_result.get("scanned_files", 0) < 1 or "truncated_files" not in search_result:
        raise AssertionError(f"search_text metadata missing: {search_result}")
    print("[ok] search_text scan counters")

    python_result = python_tool(config, {"action": "python", "code": "print(sum(range(1, 6)))", "timeout": 30})
    assert_ok("python", python_result)
    if python_result.get("stdout", "").strip() != "15":
        raise AssertionError(f"unexpected python output: {python_result}")
    print("[ok] python output")
    timeout_result = python_tool(config, {"action": "python", "code": "import time; time.sleep(5)", "timeout": 1})
    if timeout_result.get("ok") is not False or timeout_result.get("error") != "command timed out" or timeout_result.get("killed_process_group") is not True:
        raise AssertionError(f"python timeout did not kill process group: {timeout_result}")
    print("[ok] python timeout process group kill")

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
