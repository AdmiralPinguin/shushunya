#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from . import agent_runner
from . import server
from . import task_journal
from . import task_watchdog
from . import tool_contract
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
    extract_sandbox_paths_from_text,
    required_artifact_paths_from_task,
    validate_final_artifacts,
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
    tool_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_actions = set(tool_schema.get("actions", {}))
    runtime_actions = set(agent_runner.REQUIRED_FIELDS) | agent_runner.FILE_ACTIONS | {
        "sandbox_status",
        "archive_status",
        "archive_memory_gateway",
        "archive_memory_catalog",
        "archive_memory_events",
    }
    if schema_actions != runtime_actions:
        raise AssertionError(f"tool schema/runtime mismatch: missing={sorted(runtime_actions - schema_actions)}, extra={sorted(schema_actions - runtime_actions)}")
    generated_schema = tool_contract.build_tool_schema(tool_schema)
    if tool_schema != generated_schema:
        mismatches = tool_contract.schema_contract_mismatches(tool_schema)
        raise AssertionError(f"tool schema/runtime contract mismatch: {mismatches[:10]}")
    print("[ok] tool schema matches runtime contract")
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
    nested_watchdog_text = (
        task_watchdog.PUBLIC_CONTINUE_TASK
        + '\n\nAuthoritative task snapshot:\n{"task_id":"mobile-watchdog-old","events":[{"type":"start","task":"x"}],'
          '"final":{"resume_task_id":"mobile-codex-root-123"}}'
    )
    compact_nested_start = task_watchdog.compact_watchdog_event({"type": "start", "task": nested_watchdog_text})
    if "mobile-codex-root-123" not in compact_nested_start.get("task", "") or "Authoritative task snapshot" in compact_nested_start.get("task", ""):
        raise AssertionError(f"watchdog nested start was not compacted: {compact_nested_start}")
    nested_root = task_watchdog.embedded_root_task_id(
        {
            "task_id": "mobile-watchdog-new",
            "events": [{"type": "start", "task": nested_watchdog_text}],
        }
    )
    if nested_root != "mobile-codex-root-123":
        raise AssertionError(f"watchdog did not resolve nested root task id: {nested_root}")
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
    runner_source = Path(agent_runner.__file__).read_text(encoding="utf-8")
    supervisor_rejection_errors = set(re.findall(r'"([^"]* rejected by supervisor)"', runner_source))
    missing_supervisor_rejection_errors = supervisor_rejection_errors - set(agent_runner.SUPERVISOR_REJECTION_ERRORS)
    if missing_supervisor_rejection_errors:
        raise AssertionError(f"supervisor rejection errors are not counted: {sorted(missing_supervisor_rejection_errors)}")
    print("[ok] supervisor rejection errors are counted")
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
    valid_extract_list_action = agent_runner.validate_action(
        {
            "action": "web_extract_link_list",
            "url": "https://example.com/contents",
            "pattern": "chapter",
            "start_url": "https://example.com/ch1",
            "end_url": "https://example.com/ch2",
            "path_template": "/work/ch_{seq}_{vol}_{chapter}.txt",
            "limit": 10,
        }
    )
    if not valid_extract_list_action.get("ok"):
        raise AssertionError(f"valid web_extract_link_list action was rejected: {valid_extract_list_action}")
    valid_bundle_action = agent_runner.validate_action(
        {
            "action": "bundle_text_files",
            "path": "/work/novel_data",
            "include_glob": "*.txt",
            "exclude_glob": "bundle*.txt,_smoke*",
            "output_txt": "/work/novel_data/book.txt",
            "output_fb2": "/work/novel_data/book.fb2",
            "min_chars": 100,
            "dedupe": True,
        }
    )
    if not valid_bundle_action.get("ok"):
        raise AssertionError(f"valid bundle_text_files action was rejected: {valid_bundle_action}")
    valid_verify_action = agent_runner.validate_action(
        {
            "action": "verify_text_file",
            "path": "/work/novel_data/book.fb2",
            "ordered_patterns": ["Том 10", "Том 11"],
            "must_contain": ["Том 23"],
            "min_bytes": 1000,
        }
    )
    if not valid_verify_action.get("ok"):
        raise AssertionError(f"valid verify_text_file action was rejected: {valid_verify_action}")
    valid_telegram_action = agent_runner.validate_action(
        {
            "action": "telegram_send_document",
            "path": "/work/novel_data/book.fb2",
            "caption": "ready",
        }
    )
    if not valid_telegram_action.get("ok"):
        raise AssertionError(f"valid telegram_send_document action was rejected: {valid_telegram_action}")
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

    large_result = {"ok": True, "content": "x" * 50000, "size": 50000, "bytes_read": 20000, "offset": 0, "next_offset": 20000, "truncated": True}
    compacted_result = result_for_model("read_file", large_result, config)
    if len(compacted_result.get("content", "")) > 3000:
        raise AssertionError("read_file result was not compacted for model context")
    if "next_offset" not in compacted_result.get("supervisor_instruction", ""):
        raise AssertionError(f"read_file result missed next_offset guidance: {compacted_result}")
    read_summary = agent_runner.result_summary("read_file", large_result)
    if "read 20000/50000 byte(s) offset=0 next_offset=20000" not in read_summary:
        raise AssertionError(f"read_file summary used misleading size: {read_summary}")
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
    if server.config_from_payload({"task": "x" * 6000}).max_steps < 1000:
        raise AssertionError("long HTTP tasks should receive a larger default step budget")
    if server.config_from_payload({"task": "Required artifacts: /work/a.md"}).max_steps < 600:
        raise AssertionError("complex artifact tasks should receive a larger default step budget")
    if server.config_from_payload({"task": "tiny", "max_steps": 1500}).max_steps != 1500:
        raise AssertionError("explicit HTTP max_steps should allow long supervised runs")
    if server.config_from_payload({"task": "tiny", "max_steps": 2500}).max_steps != 2000:
        raise AssertionError("HTTP max_steps should remain capped by the hard safety limit")
    print("[ok] adaptive HTTP step budget")
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
    if not agent_runner.looks_like_swe_task("Исправь Python-проект и запусти pytest"):
        raise AssertionError("SWE task detector missed a coding task")
    if not agent_runner.looks_like_swe_task("Fix /work/app.js and run tests"):
        raise AssertionError("SWE task detector missed a JS coding task")
    if agent_runner.looks_like_swe_task("Создай /work/audit.json с полем checks_count=3"):
        raise AssertionError("SWE task detector treated JSON artifact as JS/code")
    if agent_runner.looks_like_swe_task("Короткий smoke-test UI сообщений: создай /work/ui-display-smoke/report.md"):
        raise AssertionError("SWE task detector treated a smoke/artifact task as code repair")
    if agent_runner.action_is_cli_verification("shell", {"cmd": "python3 -m pytest -q"}):
        raise AssertionError("pytest command must not count as CLI verification")
    if not agent_runner.action_is_cli_verification(
        "shell",
        {"cmd": "python3 -m package.cli data.csv | python3 -c \"import sys,json; json.load(sys.stdin)\""},
    ):
        raise AssertionError("module CLI JSON check was not recognized")
    if agent_runner.action_is_cli_verification("shell", {"cmd": "python3 -m package.cli data.csv"}):
        raise AssertionError("unvalidated python -m command must not count as CLI verification")
    if agent_runner.action_is_cli_verification(
        "python",
        {"code": "import subprocess,json\nr=subprocess.run(['python3','-m','scheduler.core'], capture_output=True, text=True)\njson.loads(r.stdout)"},
    ):
        raise AssertionError("running a non-CLI module must not count as CLI verification")
    if not agent_runner.action_is_cli_verification(
        "python",
        {"code": "import subprocess,json\nr=subprocess.run(['python3','-m','scheduler.cli','jobs.csv'], capture_output=True, text=True)\njson.loads(r.stdout)\nassert r.returncode == 0"},
    ):
        raise AssertionError("subprocess CLI JSON check was not recognized")
    cli_task = "Исправь CLI и проверь python3 -m package.cli data.csv."
    if agent_runner.cli_modules_from_task(cli_task) != ["package.cli"]:
        raise AssertionError("CLI module extractor missed requested python -m entrypoint")
    if agent_runner.cli_modules_from_task("bad resume tried python3 -m and input.json"):
        raise AssertionError("CLI module extractor accepted resume stopword as module")
    discovered_cli = agent_runner.cli_module_from_path("/work/project/scheduler/cli.py", "/work/project")
    if discovered_cli != "scheduler.cli":
        raise AssertionError(f"CLI module path detector missed scheduler/cli.py: {discovered_cli}")
    text_cli = agent_runner.cli_modules_from_text_paths(
        '{"path": "/work/project/scheduler/cli.py", "type": "file"}',
        "/work/project",
    )
    if text_cli != ["scheduler.cli"]:
        raise AssertionError(f"CLI text path detector missed list_files path: {text_cli}")
    with tempfile.TemporaryDirectory() as tmp:
        package_dir = Path(tmp) / "scheduler"
        package_dir.mkdir()
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
        (package_dir / "cli.py").write_text("def main(): pass\n", encoding="utf-8")
        if agent_runner.cli_modules_from_workspace(tmp) != ["scheduler.cli"]:
            raise AssertionError("workspace CLI module discovery missed package cli.py")
    if agent_runner.action_is_cli_verification(
        "python",
        {
            "code": (
                "import json\n"
                "from package.core import build\n"
                "payload=json.dumps(build([]), default=str)\n"
                "json.loads(payload)"
            )
        },
        cli_task,
    ):
        raise AssertionError("ad-hoc core JSON serialization must not satisfy requested CLI verification")
    if agent_runner.action_is_cli_verification(
        "shell",
        {"cmd": "python3 -m other.cli data.csv | python3 -c \"import sys,json; json.load(sys.stdin)\""},
        cli_task,
    ):
        raise AssertionError("wrong python -m module must not satisfy requested CLI verification")
    if agent_runner.action_is_cli_verification(
        "shell",
        {"cmd": "python3 scheduler/core.py"},
        "",
        {"scheduler.cli"},
    ):
        raise AssertionError("running an internal source file must not satisfy discovered CLI verification")
    if agent_runner.action_is_cli_verification(
        "python",
        {"code": "import scheduler.core as core\nprint(dir(core))"},
        "",
        {"scheduler.cli"},
    ):
        raise AssertionError("inspecting an internal module must not satisfy discovered CLI verification")
    input_item = {"path": "/work/project/jobs.csv", "type": "file"}
    if agent_runner.cli_input_path_from_listing_item(input_item) != "/work/project/jobs.csv":
        raise AssertionError("CLI input detector missed workspace csv input")
    if agent_runner.action_is_cli_verification(
        "python",
        {
            "code": (
                "import subprocess,json\n"
                "open('test_input.json','w').write('[]')\n"
                "r=subprocess.run(['python3','-m','scheduler.cli','test_input.json'], capture_output=True, text=True)\n"
                "json.loads(r.stdout)\nassert r.returncode == 0"
            )
        },
        "",
        {"scheduler.cli"},
        {"/work/project/jobs.csv"},
    ):
        raise AssertionError("generated dummy CLI input must not satisfy known workspace input verification")
    if not agent_runner.action_is_cli_verification(
        "python",
        {
            "code": (
                "import subprocess,json\n"
                "r=subprocess.run(['python3','-m','scheduler.cli','jobs.csv'], capture_output=True, text=True)\n"
                "json.loads(r.stdout)\nassert r.returncode == 0"
            )
        },
        "",
        {"scheduler.cli"},
        {"/work/project/jobs.csv"},
    ):
        raise AssertionError("known workspace input CLI verification was not recognized")
    semantic_task = "CLI должен вернуть scheduled_count, owners и rejected reason в JSON."
    if agent_runner.action_is_cli_verification(
        "python",
        {
            "code": (
                "import subprocess,json\n"
                "r=subprocess.run(['python3','-m','scheduler.cli','jobs.csv'], capture_output=True, text=True)\n"
                "json.loads(r.stdout)\nassert r.returncode == 0"
            )
        },
        semantic_task,
        {"scheduler.cli"},
        {"/work/project/jobs.csv"},
    ):
        raise AssertionError("CLI semantic task must not accept JSON-only verification")
    if not agent_runner.action_is_cli_verification(
        "python",
        {
            "code": (
                "import subprocess,json\n"
                "r=subprocess.run(['python3','-m','scheduler.cli','jobs.csv'], capture_output=True, text=True)\n"
                "data=json.loads(r.stdout)\n"
                "assert data['summary']['scheduled_count'] == 4\n"
                "assert data['summary']['owners']\n"
                "assert data['plan']['rejected'][0]['reason']"
            )
        },
        semantic_task,
        {"scheduler.cli"},
        {"/work/project/jobs.csv"},
    ):
        raise AssertionError("CLI semantic task should accept field-level JSON assertions")
    if not agent_runner.action_is_cli_verification(
        "shell",
        {"cmd": "python3 -m package.cli data.csv | python3 -c \"import sys,json; json.load(sys.stdin)\""},
        cli_task,
    ):
        raise AssertionError("requested python -m CLI JSON check was not recognized")
    if agent_runner.action_is_cli_verification(
        "python",
        {"code": "with open('scheduler/cli.py', 'r') as f:\n    print(f.read())"},
    ):
        raise AssertionError("reading cli.py must not count as CLI verification")
    written_code_paths = agent_runner.python_action_written_code_paths(
        "python",
        {"cwd": "/work/project", "code": "with open('scheduler/core.py', 'w') as f:\n    f.write('x')"},
    )
    if written_code_paths != ["/work/project/scheduler/core.py"]:
        raise AssertionError(f"python code write detector missed open(..., w): {written_code_paths}")
    shape_failure_result = {
        "stdout": "TypeError: 'Job' object is not subscriptable\nassert jobs[0]['duration_min'] == 60",
        "stderr": "",
    }
    if not agent_runner.result_indicates_public_shape_contract_failure(shape_failure_result):
        raise AssertionError("public shape contract detector missed subscriptable traceback")
    if not agent_runner.action_risks_public_shape_contract_regression(
        "write_file",
        {"content": "from dataclasses import dataclass\n@dataclass\nclass Job:\n    id: str\n"},
    ):
        raise AssertionError("public shape contract edit detector missed dataclass/class rewrite")
    if agent_runner.action_risks_public_shape_contract_regression(
        "write_file",
        {"content": "def parse_jobs(rows):\n    return [{'duration_min': int(row['duration_min'])} for row in rows]\n"},
    ):
        raise AssertionError("public shape contract edit detector rejected dict-preserving rewrite")
    nested_cli_failure_result = {
        "ok": True,
        "returncode": 0,
        "stdout": "STDERR: Traceback ...\nRETURN_CODE: 1\nCLI_FAILED: True\nCLI_JSON_VALID: False\n",
        "stderr": "",
    }
    if not agent_runner.python_result_printed_nested_cli_failure(nested_cli_failure_result):
        raise AssertionError("nested CLI failure detector missed captured non-zero CLI result")
    print("[ok] public shape contract detectors")
    swe_profile_task = agent_runner.task_with_execution_profile(
        "Исправь Python-проект и запусти pytest",
        AgentConfig(shell_enabled=True),
    )
    if "Executor profile: SWE/code task" not in swe_profile_task or "reproduce-edit-verify" not in swe_profile_task:
        raise AssertionError(f"SWE execution profile was not appended: {swe_profile_task}")
    if agent_runner.task_with_execution_profile("Создай краткий отчет", AgentConfig()) != "Создай краткий отчет":
        raise AssertionError("SWE execution profile should not affect unrelated tasks")
    swe_no_shell_profile = agent_runner.task_with_execution_profile(
        "Исправь Python-проект и запусти pytest",
        AgentConfig(shell_enabled=False),
    )
    if "Shell is disabled for this run" not in swe_no_shell_profile or "Do not emit shell actions" not in swe_no_shell_profile:
        raise AssertionError(f"SWE no-shell profile missed strict shell guidance: {swe_no_shell_profile}")
    shell_disabled_payload = agent_runner.result_for_model(
        "shell",
        {"ok": False, "error": "shell tool is disabled by supervisor policy"},
        AgentConfig(shell_enabled=False),
    )
    if "Do not emit another shell action" not in shell_disabled_payload.get("supervisor_instruction", ""):
        raise AssertionError(f"shell disabled payload missed no-shell instruction: {shell_disabled_payload}")
    if (shell_disabled_payload.get("suggested_python_action") or {}).get("action") != "python":
        raise AssertionError(f"shell disabled payload missed suggested python action: {shell_disabled_payload}")
    print("[ok] SWE execution profile")
    if agent_runner.planner_should_run("Создай /work/report.md с текстом ok", AgentConfig(planner_enabled=True)):
        raise AssertionError("simple create tasks should not spend a planner call")
    if agent_runner.planner_should_run(
        "Создай отчет.\n\nОбязательные артефакты:\n- /work/report.md",
        AgentConfig(planner_enabled=True),
    ):
        raise AssertionError("single short required artifact tasks should not spend a planner call")
    if agent_runner.planner_should_run(
        'Создай report.md и audit.json в текущем каталоге. После создания проверь содержимое report.md и валидность audit.json.',
        AgentConfig(planner_enabled=True),
    ):
        raise AssertionError("short local artifact-only tasks should not spend a planner call")
    if agent_runner.planner_should_run(
        'Создай report.md и audit.json. Обязательные артефакты: /work/task/report.md и /work/task/audit.json.',
        AgentConfig(planner_enabled=True),
    ):
        raise AssertionError("relative plus absolute names for same artifacts should not inflate planner complexity")
    if not agent_runner.planner_should_run(
        "Исследуй и сравни источники, затем создай report.md и audit.json",
        AgentConfig(planner_enabled=True),
    ):
        raise AssertionError("research/compare artifact tasks should still use planner")
    if not agent_runner.planner_should_run(
        "Сложный стресс-тест. Обязательные артефакты: /work/report.md и /work/audit.json",
        AgentConfig(planner_enabled=True),
    ):
        raise AssertionError("complex artifact/stress tasks should use planner")
    print("[ok] planner gating")
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
        if not server.privileged_api_allowed(FakeHandler()):
            raise AssertionError("privileged API should follow open local mode when API key is not configured")
        server.API_KEY = "secret"
        if not server.health_detail_allowed(FakeHandler("Bearer secret")):
            raise AssertionError("health detail should allow valid bearer key")
        if not server.privileged_api_allowed(FakeHandler("Bearer secret")):
            raise AssertionError("privileged API should allow valid bearer key")
        if server.privileged_api_allowed(FakeHandler("Bearer wrong")):
            raise AssertionError("privileged API should reject invalid bearer key")
    finally:
        server.API_KEY = old_api_key
    print("[ok] privileged endpoints follow auth mode")

    compact_resume = server.compact_resume_events(
        [{"type": "tool_result", "result": {"content": "r" * 10000}, "index": index} for index in range(30)],
        max_chars=5000,
    )
    compact_resume_text = str(compact_resume)
    if not compact_resume or len(compact_resume_text) > 7000:
        raise AssertionError("resume events were not compacted")
    print("[ok] resume context compacted")
    resume_with_start = server.compact_resume_events(
        [
            {
                "type": "start",
                "task": "Create /work/report.md and /work/matrix.md.",
                "required_artifacts": ["/work/report.md", "/work/matrix.md"],
            },
            *({"type": "tool_result", "result": {"content": "x" * 2000}, "index": index} for index in range(40)),
        ],
        max_chars=5000,
    )
    if not resume_with_start or resume_with_start[0].get("type") != "start":
        raise AssertionError(f"resume context lost start event: {resume_with_start[:2]}")
    if resume_with_start[0].get("required_artifacts") != ["/work/report.md", "/work/matrix.md"]:
        raise AssertionError(f"resume context lost required artifacts: {resume_with_start[0]}")
    print("[ok] resume context preserves required artifacts")
    resume_with_data_sources = server.compact_resume_events(
        [
            {
                "type": "start",
                "task": "Read /work/events.jsonl and create /work/timeline.csv.",
                "required_artifacts": ["/work/timeline.csv"],
                "data_sources": ["/work/events.jsonl"],
            },
            {
                "type": "tool_result",
                "action": "read_file",
                "result": {
                    "ok": True,
                    "path": "/work/events.jsonl",
                    "content": "{\"service\":\"api\",\"message\":\"real source row\"}\n",
                },
            },
            *({"type": "step", "step": index, "noise": "x" * 1000} for index in range(120)),
            {
                "type": "final",
                "ok": False,
                "continuable": True,
                "message": "repeated actions without progress",
            },
        ],
        max_chars=5000,
    )
    resume_with_data_text = json.dumps(resume_with_data_sources, ensure_ascii=False)
    if "real source row" not in resume_with_data_text or "/work/events.jsonl" not in resume_with_data_text:
        raise AssertionError(f"resume context lost data source content: {resume_with_data_sources}")
    print("[ok] resume context preserves data source excerpts")
    resume_with_test_result = server.compact_resume_events(
        [
            {"type": "start", "task": "Fix tests."},
            {
                "type": "tool_result",
                "action": "shell",
                "result": {
                    "ok": False,
                    "passing_tests": ["tests/test_textkit.py::test_normalize_title"],
                    "failing_tests": ["tests/test_textkit.py::test_slugify_lowercase"],
                },
            },
            *({"type": "step", "step": index} for index in range(120)),
        ],
        max_chars=5000,
    )
    resume_with_test_text = json.dumps(resume_with_test_result, ensure_ascii=False)
    if "passing_tests" not in resume_with_test_text or "test_normalize_title" not in resume_with_test_text:
        raise AssertionError(f"resume context lost latest test result: {resume_with_test_result}")
    parsed_passing, parsed_failing = agent_runner.latest_pytest_sets_from_text(resume_with_test_text)
    if parsed_passing != {"tests/test_textkit.py::test_normalize_title"} or parsed_failing != {"tests/test_textkit.py::test_slugify_lowercase"}:
        raise AssertionError(f"resume pytest sets were not parsed: {parsed_passing}, {parsed_failing}")
    print("[ok] resume context preserves latest test result")
    contaminated_resume = server.compact_resume_events(
        [
            {
                "type": "start",
                "task": (
                    "Create /work/report.md.\n\nAuthoritative previous agent task context:\n"
                    "{\"summary\":{\"task\":\"Create /work/old.json\"}}"
                ),
                "required_artifacts": ["/work/report.md", "/work/old.json"],
            }
        ],
        max_chars=5000,
    )
    if "/work/old.json" in json.dumps(contaminated_resume, ensure_ascii=False):
        raise AssertionError(f"resume context kept contaminated previous task data: {contaminated_resume}")
    if contaminated_resume[0].get("required_artifacts") != ["/work/report.md"]:
        raise AssertionError(f"resume context failed to filter contaminated required artifacts: {contaminated_resume}")
    print("[ok] resume context strips nested previous task context")
    long_resume_events = [
        {
            "type": "start",
            "task": "Create /work/report.md and /work/matrix.md.",
            "required_artifacts": ["/work/report.md", "/work/matrix.md"],
        },
        *({"type": "step", "step": index} for index in range(140)),
    ]

    def fake_long_journal(task_id, limit=80):
        if limit < 500:
            raise AssertionError(f"resume journal limit too small: {limit}")
        return {"ok": True, "task_id": task_id, "events": long_resume_events}

    with mock.patch.object(server, "read_task_journal", side_effect=fake_long_journal):
        long_resume_config = AgentConfig(task_id="long-resume")
        long_resume_task = server.apply_resume_context("continue", long_resume_config, {"resume_task_id": "previous"})
    if "/work/report.md" not in long_resume_task or "/work/matrix.md" not in long_resume_task:
        raise AssertionError(f"long resume context lost required artifact paths: {long_resume_task[-1000:]}")
    if long_resume_config.initial_required_artifact_paths != ("/work/report.md", "/work/matrix.md"):
        raise AssertionError(f"resume config lost required artifact paths: {long_resume_config.initial_required_artifact_paths}")
    print("[ok] long resume context keeps original required artifacts")
    verified_events = [
        {"type": "action", "action": {"action": "verify_text_file", "path": "/work/report.md"}},
        {"type": "tool_result", "action": "verify_text_file", "result": {"ok": True, "path": "/work/report.md"}},
        {"type": "action", "action": {"action": "write_file", "path": "/work/report.md"}},
        {"type": "action", "action": {"action": "verify_text_file", "path": "/work/report.md"}},
        {"type": "tool_result", "action": "verify_text_file", "result": {"ok": True, "path": "/work/report.md"}},
        {"type": "tool_result", "action": "verify_text_file", "ok": True, "message": "verified=True path=/work/matrix.md failures=0"},
    ]
    if server.verified_text_paths_from_events(verified_events) != ["/work/matrix.md", "/work/report.md"]:
        raise AssertionError(f"resume verified path extraction failed: {server.verified_text_paths_from_events(verified_events)}")
    print("[ok] resume context restores verified text paths")
    required_events = [
        {"type": "start", "required_artifacts": ["/work/report.md", "/work/report.md", "/work/metrics.json"]},
        {"type": "start", "required_artifacts": ["/work/audit.csv"]},
    ]
    if server.required_artifact_paths_from_events(required_events) != ["/work/report.md", "/work/metrics.json", "/work/audit.csv"]:
        raise AssertionError(f"resume required artifact extraction failed: {server.required_artifact_paths_from_events(required_events)}")
    print("[ok] resume context restores required artifact paths")
    if server.should_apply_previous_task_context({"task_id": "new-explicit-task"}):
        raise AssertionError("explicit new task_id should not inherit previous task context")
    if server.should_apply_previous_task_context({"resume_task_id": "old-task"}):
        raise AssertionError("resume_task_id should not inherit separate previous task context")
    if server.should_apply_previous_task_context({"skip_previous_task_context": True}):
        raise AssertionError("skip_previous_task_context should disable previous context")
    print("[ok] explicit task id skips previous task context")

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

    repeated_continuation = server.continuation_task(
        "base",
        "self-test-repeated",
        1,
        {"message": "Агент остановлен супервизором: обнаружен цикл повторяющихся действий без прогресса."},
    )
    if "запрещены inspection-действия" not in repeated_continuation or "write_file" not in repeated_continuation:
        raise AssertionError(f"repeated continuation task did not force productive mode: {repeated_continuation}")
    print("[ok] auto-continue repeated action prompt")

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
        server.record_run_finished(2, {"cancelled": True})
        server.record_run_finished(2, {"stop_reason": "runtime_limit"})
        server.record_run_finished(2, {"stop_reason": "max_steps"})
        metrics = server.runtime_state().get("metrics") or {}
        if metrics.get("runs_started") != old_metrics.get("runs_started", 0) + 1:
            raise AssertionError(f"run start metric failed: {metrics}")
        if metrics.get("runs_failed") != old_metrics.get("runs_failed", 0) + 3:
            raise AssertionError(f"run failure metric failed: {metrics}")
        if metrics.get("runs_cancelled") != old_metrics.get("runs_cancelled", 0) + 1:
            raise AssertionError(f"cancel metric failed: {metrics}")
        if metrics.get("runs_runtime_limited") != old_metrics.get("runs_runtime_limited", 0) + 1:
            raise AssertionError(f"runtime limit metric failed: {metrics}")
        if metrics.get("runs_max_steps") != old_metrics.get("runs_max_steps", 0) + 1:
            raise AssertionError(f"max steps metric failed: {metrics}")
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
    extract_a = {
        "action": "web_extract_to_file",
        "url": "https://example.com/docs/page?utm_source=test#section",
        "path": "/work/source_a.txt",
        "mode": "write",
    }
    extract_b = {
        "action": "web_extract_to_file",
        "url": "https://example.com/docs/page/",
        "path": "/work/source_b.txt",
        "mode": "append",
    }
    if action_fingerprint(extract_a) != action_fingerprint(extract_b):
        raise AssertionError("web_extract_to_file repeat fingerprint should ignore output path/name variants")
    print("[ok] web extract repeat fingerprint")

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
    loose_python = (
        '{"action":"python","cwd":"/work/project","code":"print(\\"ok\\")\\ntext = "quoted value"\\nprint(text)","timeout":60}'
    )
    loose_action = repair_action_json(config, loose_python, ValueError("Expecting ',' delimiter"))
    if (
        loose_action.get("action") != "python"
        or loose_action.get("cwd") != "/work/project"
        or 'text = "quoted value"' not in loose_action.get("code", "")
    ):
        raise AssertionError(f"loose python action was not salvaged: {loose_action}")
    loose_double_escaped = '{"action":"python","code":"print(\\"one\\")\\\\nprint(\\"two\\")","timeout":60}'
    loose_double_action = repair_action_json(config, loose_double_escaped, ValueError("Expecting ',' delimiter"))
    if 'print("one")\nprint("two")' not in loose_double_action.get("code", ""):
        raise AssertionError(f"double-escaped python code was not salvaged: {loose_double_action}")
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

    parse_stall_stdout = io.StringIO()
    parse_stall_config = AgentConfig(
        task_id=safe_task_id("self-test-json-parse-stall"),
        json_output=True,
        max_steps=10,
        inject_memory=False,
        archive_internal_steps=False,
    )
    with mock.patch.object(agent_runner, "chat", return_value='{"action":"python","code":"unterminated'), \
            mock.patch.object(agent_runner, "repair_action_json", side_effect=ValueError("no repair")), \
            contextlib.redirect_stdout(parse_stall_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        parse_stall_code = run_agent("parse stall", parse_stall_config)
    parse_stall_payload = json.loads(parse_stall_stdout.getvalue())
    if parse_stall_code != 2 or parse_stall_payload.get("continuable") is not True:
        raise AssertionError(f"json parse stall did not stop as continuable: code={parse_stall_code}, payload={parse_stall_payload}")
    if "невалидный JSON" not in parse_stall_payload.get("message", ""):
        raise AssertionError(f"json parse stall returned wrong message: {parse_stall_payload}")
    print("[ok] JSON parse stall guard")

    intermittent_parse_stdout = io.StringIO()
    intermittent_parse_config = AgentConfig(
        task_id=safe_task_id("self-test-json-intermittent-stall"),
        json_output=True,
        max_steps=20,
        inject_memory=False,
        archive_internal_steps=False,
    )
    intermittent_actions = [
        '{"action":"python","code":"unterminated',
        '{"action":"mkdir","path":"/work/intermittent"}',
        '{"action":"python","code":"unterminated',
        '{"action":"mkdir","path":"/work/intermittent2"}',
        '{"action":"python","code":"unterminated',
        '{"action":"mkdir","path":"/work/intermittent3"}',
        '{"action":"python","code":"unterminated',
        '{"action":"mkdir","path":"/work/intermittent4"}',
        '{"action":"python","code":"unterminated',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=intermittent_actions), \
            mock.patch.object(agent_runner, "repair_action_json", side_effect=ValueError("no repair")), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/intermittent"}), \
            contextlib.redirect_stdout(intermittent_parse_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        intermittent_parse_code = run_agent("intermittent parse stall", intermittent_parse_config)
    intermittent_parse_payload = json.loads(intermittent_parse_stdout.getvalue())
    if intermittent_parse_code != 2 or intermittent_parse_payload.get("continuable") is not True:
        raise AssertionError(
            f"intermittent json parse stall did not stop as continuable: code={intermittent_parse_code}, payload={intermittent_parse_payload}"
        )
    if len(intermittent_parse_payload.get("steps", [])) > 4:
        raise AssertionError(f"intermittent json parse stall allowed too much filler progress: {intermittent_parse_payload}")
    print("[ok] intermittent JSON parse stall guard")

    repeated_write_stdout = io.StringIO()
    repeated_write_config = AgentConfig(
        task_id=safe_task_id("self-test-repeated-write-file"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
    )
    repeated_write_actions = [
        '{"action":"write_file","path":"/work/report.md","content":"first draft"}',
        '{"action":"write_file","path":"/work/report.md","content":"same size?"}',
        '{"action":"write_file","path":"/work/report.md","content":"short"}',
        '{"action":"final","message":"done"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=repeated_write_actions), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/report.md"}), \
            contextlib.redirect_stdout(repeated_write_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        repeated_write_code = run_agent("repeated write file", repeated_write_config)
    repeated_write_payload = json.loads(repeated_write_stdout.getvalue())
    rejected_write = [
        step for step in repeated_write_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "repeated write_file path rejected by supervisor"
    ]
    if repeated_write_code != 0 or not rejected_write:
        raise AssertionError(f"repeated write_file path guard failed: code={repeated_write_code}, payload={repeated_write_payload}")
    print("[ok] repeated write_file path guard")

    repeated_mkdir_stdout = io.StringIO()
    repeated_mkdir_config = AgentConfig(
        task_id=safe_task_id("self-test-repeated-mkdir"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
    )
    repeated_mkdir_actions = [
        '{"action":"mkdir","path":"/work/repeated-mkdir"}',
        '{"action":"write_file","path":"/work/repeated-mkdir/report.md","content":"ready"}',
        '{"action":"mkdir","path":"/work/repeated-mkdir"}',
        '{"action":"final","message":"done"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=repeated_mkdir_actions), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/repeated-mkdir"}), \
            contextlib.redirect_stdout(repeated_mkdir_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        repeated_mkdir_code = run_agent("repeated mkdir", repeated_mkdir_config)
    repeated_mkdir_payload = json.loads(repeated_mkdir_stdout.getvalue())
    repeated_mkdir_errors = [
        (step.get("result") or {}).get("error")
        for step in repeated_mkdir_payload.get("steps", [])
        if isinstance(step, dict)
    ]
    if repeated_mkdir_code != 0 or "repeated mkdir rejected by supervisor" not in repeated_mkdir_errors:
        raise AssertionError(f"repeated mkdir guard failed: code={repeated_mkdir_code}, payload={repeated_mkdir_payload}")
    print("[ok] repeated mkdir guard")

    ready_workspace_stdout = io.StringIO()
    ready_workspace_config = AgentConfig(
        task_id=safe_task_id("self-test-ready-workspace-inspection"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
    )
    ready_workspace_actions = [
        '{"action":"mkdir","path":"/work/ready-artifact"}',
        '{"action":"read_file","path":"/work/ready-artifact/input.jsonl"}',
        '{"action":"mkdir","path":"/work/ready-artifact"}',
        '{"action":"read_file","path":"/work/ready-artifact/input.jsonl"}',
        '{"action":"final","message":"done"}',
    ]

    def fake_ready_workspace_file_tool(_config: AgentConfig, action: dict) -> dict:
        if action.get("action") == "read_file":
            return {"ok": True, "path": action.get("path"), "size": 8, "bytes_read": 8, "offset": 0, "content": "payload\n"}
        return {"ok": True, "path": action.get("path")}

    with mock.patch.object(agent_runner, "chat", side_effect=ready_workspace_actions), \
            mock.patch.object(agent_runner, "file_tool", side_effect=fake_ready_workspace_file_tool), \
            contextlib.redirect_stdout(ready_workspace_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        ready_workspace_code = run_agent("ready workspace repeat", ready_workspace_config)
    ready_workspace_payload = json.loads(ready_workspace_stdout.getvalue())
    ready_workspace_errors = [
        (step.get("result") or {}).get("error")
        for step in ready_workspace_payload.get("steps", [])
        if isinstance(step, dict)
    ]
    if (
        ready_workspace_code != 2
        or ready_workspace_payload.get("continuable") is not True
        or "ready workspace inspection rejected by supervisor" not in ready_workspace_errors
    ):
        raise AssertionError(f"ready workspace inspection guard failed: code={ready_workspace_code}, payload={ready_workspace_payload}")
    print("[ok] ready workspace inspection guard")

    growing_rewrite_stdout = io.StringIO()
    growing_rewrite_config = AgentConfig(
        task_id=safe_task_id("self-test-growing-write-file"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
    )
    growing_rewrite_actions = [
        '{"action":"write_file","path":"/work/report.md","content":"draft"}',
        '{"action":"write_file","path":"/work/report.md","content":"draft plus more"}',
        '{"action":"write_file","path":"/work/report.md","content":"draft plus more and final section"}',
        '{"action":"final","message":"done"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=growing_rewrite_actions), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/report.md"}), \
            contextlib.redirect_stdout(growing_rewrite_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        growing_rewrite_code = run_agent("growing write file", growing_rewrite_config)
    growing_rewrite_payload = json.loads(growing_rewrite_stdout.getvalue())
    rejected_growing_rewrite = [
        step for step in growing_rewrite_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "repeated write_file path rejected by supervisor"
    ]
    if growing_rewrite_code != 0 or rejected_growing_rewrite:
        raise AssertionError(f"growing repeated write_file should be allowed: code={growing_rewrite_code}, payload={growing_rewrite_payload}")
    print("[ok] growing write_file draft rewrite allowed")

    json_append_stdout = io.StringIO()
    json_append_config = AgentConfig(
        task_id=safe_task_id("self-test-json-append-guard"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
    )
    json_append_actions = [
        '{"action":"append_file","path":"/work/summary.json","content":"not-json-tail"}',
        '{"action":"final","message":"done"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=json_append_actions), \
            contextlib.redirect_stdout(json_append_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        json_append_code = run_agent("do not append json", json_append_config)
    json_append_payload = json.loads(json_append_stdout.getvalue())
    json_append_rejections = [
        step for step in json_append_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "append_file to JSON rejected by supervisor"
    ]
    if not json_append_rejections:
        raise AssertionError(f"append_file JSON guard failed: code={json_append_code}, payload={json_append_payload}")
    print("[ok] append_file JSON guard")

    invalid_json_write_stdout = io.StringIO()
    invalid_json_write_config = AgentConfig(
        task_id=safe_task_id("self-test-invalid-json-write-guard"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
    )
    invalid_json_write_actions = [
        '{"action":"write_file","path":"/work/bad.json","content":"{\\"ok\\": true}`"}',
        '{"action":"write_files","files":[{"path":"/work/good.txt","content":"ok"},{"path":"/work/bad-batch.json","content":"{\\"ok\\": true}`"}]}',
        '{"action":"write_file","path":"/work/good.json","content":"{\\"ok\\": true}"}',
        '{"action":"final","message":"done"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=invalid_json_write_actions), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/good.json"}), \
            contextlib.redirect_stdout(invalid_json_write_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        invalid_json_write_code = run_agent("json artifacts must be valid", invalid_json_write_config)
    invalid_json_write_payload = json.loads(invalid_json_write_stdout.getvalue())
    invalid_json_write_rejections = [
        step for step in invalid_json_write_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "invalid JSON write rejected by supervisor"
    ]
    if invalid_json_write_code != 0 or len(invalid_json_write_rejections) < 2:
        raise AssertionError(f"invalid JSON write guard failed: code={invalid_json_write_code}, payload={invalid_json_write_payload}")
    print("[ok] invalid JSON write guard")

    verified_mutation_stdout = io.StringIO()
    verified_mutation_config = AgentConfig(
        task_id=safe_task_id("self-test-verified-mutation-guard"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
        initial_verified_text_paths=("/work/report.md",),
    )
    verified_mutation_actions = [
        '{"action":"write_file","path":"/work/report.md","content":"rewrite verified"}',
        '{"action":"final","message":"done"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=verified_mutation_actions), \
            contextlib.redirect_stdout(verified_mutation_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        verified_mutation_code = run_agent("do not mutate verified", verified_mutation_config)
    verified_mutation_payload = json.loads(verified_mutation_stdout.getvalue())
    verified_mutation_rejections = [
        step for step in verified_mutation_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "verified text artifact mutation rejected by supervisor"
    ]
    if (
        verified_mutation_code != 2
        or verified_mutation_payload.get("continuable") is not True
        or not verified_mutation_rejections
    ):
        raise AssertionError(f"verified mutation guard failed: code={verified_mutation_code}, payload={verified_mutation_payload}")
    print("[ok] verified artifact mutation guard")

    required_rewrite_stdout = io.StringIO()
    required_rewrite_config = AgentConfig(
        task_id=safe_task_id("self-test-required-rewrite-before-verify"),
        json_output=True,
        max_steps=8,
        inject_memory=False,
        archive_internal_steps=False,
    )
    required_rewrite_actions = [
        '{"action":"write_file","path":"/work/report.md","content":"# Summary\\nSTATUS: PASS"}',
        '{"action":"write_file","path":"/work/audit.json","content":"{\\"status\\": \\"pass\\"}"}',
        '{"action":"write_file","path":"/work/report.md","content":"# Summary\\n# Evidence\\nSTATUS: PASS"}',
        '{"action":"verify_text_file","path":"/work/report.md","must_contain":["STATUS: PASS"]}',
        '{"action":"verify_text_file","path":"/work/audit.json","must_contain":["pass"]}',
        '{"action":"final","message":"Готово: /work/report.md, /work/audit.json"}',
        '{"action":"final","message":"Готово: /work/report.md, /work/audit.json"}',
    ]
    def fake_required_rewrite_verify(config_arg, action):
        return {"ok": True, "path": action.get("path"), "failures": []}

    def fake_required_rewrite_file_tool(config_arg, action):
        return {
            "ok": True,
            "path": action.get("path") or "/work/report.md",
            "exists": True,
            "type": "file",
            "size": 16,
        }

    with mock.patch.object(agent_runner, "chat", side_effect=required_rewrite_actions) as mocked_required_rewrite_chat, \
            mock.patch.object(agent_runner, "file_tool", side_effect=fake_required_rewrite_file_tool), \
            mock.patch.object(agent_runner, "verify_text_file_tool", side_effect=fake_required_rewrite_verify), \
            contextlib.redirect_stdout(required_rewrite_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        required_rewrite_code = run_agent(
            "Обязательные артефакты: /work/report.md и /work/audit.json",
            required_rewrite_config,
        )
    required_rewrite_payload = json.loads(required_rewrite_stdout.getvalue())
    required_rewrite_rejections = [
        step for step in required_rewrite_payload.get("steps", [])
        if (step.get("result") or {}).get("error") in {
            "required artifact rewrite before verification rejected by supervisor",
            "artifact verification mode action rejected by supervisor",
        }
    ]
    required_rewrite_auto_verified = (
        required_rewrite_code == 0
        and mocked_required_rewrite_chat.call_count == 2
        and [
            step.get("action", {}).get("action")
            for step in required_rewrite_payload.get("steps", [])
        ] == ["write_file", "write_file", "verify_text_file", "verify_text_file"]
    )
    if required_rewrite_code != 0 or (not required_rewrite_rejections and not required_rewrite_auto_verified):
        raise AssertionError(
            f"required artifact rewrite-before-verify guard failed: code={required_rewrite_code}, payload={required_rewrite_payload}"
        )
    print("[ok] required artifact rewrite before verification guard")

    inspection_stall_stdout = io.StringIO()
    inspection_stall_config = AgentConfig(
        task_id=safe_task_id("self-test-inspection-stall"),
        json_output=True,
        max_steps=12,
        inject_memory=False,
        archive_internal_steps=False,
    )
    inspection_actions = [
        json.dumps({"action": "read_file", "path": f"/work/source-{index}.txt", "max_bytes": 1000, "offset": 0})
        for index in range(9)
    ]
    inspection_actions.append('{"action":"final","message":"done"}')
    with mock.patch.object(agent_runner, "chat", side_effect=inspection_actions), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/source.txt", "content": "x", "size": 1}), \
            contextlib.redirect_stdout(inspection_stall_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        inspection_stall_code = run_agent("inspection stall", inspection_stall_config)
    inspection_stall_payload = json.loads(inspection_stall_stdout.getvalue())
    rejected_inspection = [
        step for step in inspection_stall_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "inspection stall rejected by supervisor"
    ]
    if inspection_stall_code != 0 or not rejected_inspection:
        raise AssertionError(f"inspection stall guard failed: code={inspection_stall_code}, payload={inspection_stall_payload}")
    print("[ok] inspection stall guard")

    verify_after_append_stdout = io.StringIO()
    verify_after_append_config = AgentConfig(
        task_id=safe_task_id("self-test-verify-after-append"),
        json_output=True,
        max_steps=6,
        inject_memory=False,
        archive_internal_steps=False,
    )
    verify_after_append_actions = [
        '{"action":"verify_text_file","path":"/work/report.md","must_contain":["done"],"min_chars":10}',
        '{"action":"append_file","path":"/work/report.md","content":"more"}',
        '{"action":"verify_text_file","path":"/work/report.md","must_contain":["done"],"min_chars":10}',
        '{"action":"append_file","path":"/work/report.md","content":"more"}',
        '{"action":"verify_text_file","path":"/work/report.md","must_contain":["done"],"min_chars":10}',
        '{"action":"final","message":"Готово: /work/report.md"}',
    ]
    verify_calls = 0

    def fake_verify_after_append(config_arg, action):
        nonlocal verify_calls
        verify_calls += 1
        return {"ok": False, "path": action.get("path"), "failures": ["still short"]}

    with mock.patch.object(agent_runner, "chat", side_effect=verify_after_append_actions), \
            mock.patch.object(agent_runner, "verify_text_file_tool", side_effect=fake_verify_after_append), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/report.md"}), \
            contextlib.redirect_stdout(verify_after_append_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        verify_after_append_code = run_agent("append then verify repeatedly", verify_after_append_config)
    verify_after_append_payload = json.loads(verify_after_append_stdout.getvalue())
    if verify_calls != 3:
        raise AssertionError(f"verify after append should not be blocked by repeat guard: calls={verify_calls}, payload={verify_after_append_payload}")
    print("[ok] verify repeat reset after file mutation")

    repeated_verify_stdout = io.StringIO()
    repeated_verify_config = AgentConfig(
        task_id=safe_task_id("self-test-repeated-verified-text"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
    )
    repeated_verify_actions = [
        '{"action":"verify_text_file","path":"/work/report.md","must_contain":["done"]}',
        '{"action":"verify_text_file","path":"/work/report.md","must_contain":["done"]}',
        '{"action":"final","message":"done"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=repeated_verify_actions), \
            mock.patch.object(agent_runner, "verify_text_file_tool", return_value={"ok": True, "path": "/work/report.md", "failures": []}), \
            contextlib.redirect_stdout(repeated_verify_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        repeated_verify_code = run_agent("do not verify same text forever", repeated_verify_config)
    repeated_verify_payload = json.loads(repeated_verify_stdout.getvalue())
    repeated_verify_rejections = [
        step for step in repeated_verify_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "repeated verified text verification rejected by supervisor"
    ]
    if repeated_verify_code != 0 or not repeated_verify_rejections:
        raise AssertionError(f"repeated verified text guard failed: code={repeated_verify_code}, payload={repeated_verify_payload}")
    print("[ok] repeated verified text guard")

    repeated_verify_rewrite_stdout = io.StringIO()
    repeated_verify_rewrite_config = AgentConfig(
        task_id=safe_task_id("self-test-repeated-verify-does-not-unlock-rewrite"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
    )
    repeated_verify_rewrite_actions = [
        '{"action":"verify_text_file","path":"/work/report.md","must_contain":["done"]}',
        '{"action":"verify_text_file","path":"/work/report.md","must_contain":["done"]}',
        '{"action":"write_file","path":"/work/report.md","content":"rewrite after rejected repeated verification"}',
        '{"action":"final","message":"done"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=repeated_verify_rewrite_actions), \
            mock.patch.object(agent_runner, "verify_text_file_tool", return_value={"ok": True, "path": "/work/report.md", "failures": []}), \
            contextlib.redirect_stdout(repeated_verify_rewrite_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        repeated_verify_rewrite_code = run_agent("do not unlock rewrite after rejected repeated verification", repeated_verify_rewrite_config)
    repeated_verify_rewrite_payload = json.loads(repeated_verify_rewrite_stdout.getvalue())
    repeated_verify_rewrite_rejections = [
        step for step in repeated_verify_rewrite_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "verified text artifact mutation rejected by supervisor"
    ]
    if (
        repeated_verify_rewrite_code != 2
        or repeated_verify_rewrite_payload.get("continuable") is not True
        or not repeated_verify_rewrite_rejections
    ):
        raise AssertionError(
            "rejected repeated verification unlocked rewrite: "
            f"code={repeated_verify_rewrite_code}, payload={repeated_verify_rewrite_payload}"
        )
    print("[ok] rejected repeated verify does not unlock rewrite")

    rewrite_after_verify_stdout = io.StringIO()
    rewrite_after_verify_config = AgentConfig(
        task_id=safe_task_id("self-test-rewrite-after-verify-failure"),
        json_output=True,
        max_steps=6,
        inject_memory=False,
        archive_internal_steps=False,
    )
    rewrite_after_verify_actions = [
        '{"action":"write_file","path":"/work/checklist.md","content":"draft"}',
        '{"action":"verify_text_file","path":"/work/checklist.md","must_contain":["done"]}',
        '{"action":"write_file","path":"/work/checklist.md","content":"draft\\ndone"}',
        '{"action":"verify_text_file","path":"/work/checklist.md","must_contain":["done"]}',
        '{"action":"final","message":"Готово: /work/checklist.md"}',
    ]

    def fake_rewrite_verify(_config, action):
        if action.get("path") == "/work/checklist.md" and fake_rewrite_verify.calls == 0:
            fake_rewrite_verify.calls += 1
            return {"ok": False, "path": "/work/checklist.md", "failures": [{"check": "must_contain", "pattern": "done"}]}
        return {"ok": True, "path": "/work/checklist.md", "failures": []}

    fake_rewrite_verify.calls = 0
    with mock.patch.object(agent_runner, "chat", side_effect=rewrite_after_verify_actions), \
            mock.patch.object(agent_runner, "verify_text_file_tool", side_effect=fake_rewrite_verify), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/checklist.md"}), \
            contextlib.redirect_stdout(rewrite_after_verify_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        rewrite_after_verify_code = run_agent("rewrite after failed verification", rewrite_after_verify_config)
    rewrite_after_verify_payload = json.loads(rewrite_after_verify_stdout.getvalue())
    rejected_rewrite = [
        step for step in rewrite_after_verify_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "repeated write_file path rejected by supervisor"
    ]
    if rejected_rewrite:
        raise AssertionError(f"rewrite after failed verification was blocked: code={rewrite_after_verify_code}, payload={rewrite_after_verify_payload}")
    print("[ok] write_file correction allowed after failed verification")

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
    if captured_payloads[0].get("response_format") != {"type": "json_object"}:
        raise AssertionError(f"agent chat did not request JSON response format: {captured_payloads}")
    print("[ok] agent chat disables Archive persona prompt")

    def reasoning_content_payload(config_arg, method, path, payload=None, timeout=180):
        return {"choices": [{"message": {"content": "", "reasoning_content": '{"action":"final","message":"reasoning fallback ok"}'}}]}

    with mock.patch.object(agent_runner, "archive_request", side_effect=reasoning_content_payload):
        reasoning_reply = chat(config, [{"role": "user", "content": "reasoning fallback"}], inject_memory=False, archive_enabled=False)
    if reasoning_reply != '{"action":"final","message":"reasoning fallback ok"}':
        raise AssertionError(f"reasoning_content fallback failed: {reasoning_reply}")
    print("[ok] agent chat reads reasoning_content fallback")

    planner_payloads: list[dict] = []

    def planner_archive_payload(config_arg, method, path, payload=None, timeout=180):
        planner_payloads.append(payload or {})
        if len(planner_payloads) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "planner ok",
                                    "required_artifacts": ["/work/report.md"],
                                    "steps": ["write report", "verify report"],
                                    "verification": [{"path": "/work/report.md", "checks": ["marker"]}],
                                    "risks": ["repeat"],
                                    "executor_rules": ["do not rewrite verified artifacts"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"content": '{"action":"final","message":"planner execution ok"}'}}]}

    planner_stdout = io.StringIO()
    planner_events: list[dict] = []
    planner_config = AgentConfig(
        task_id=safe_task_id("self-test-planner-thinking"),
        json_output=True,
        max_steps=1,
        inject_memory=False,
        archive_internal_steps=False,
        planner_enabled=True,
        planner_thinking=True,
    )
    with mock.patch.object(agent_runner, "archive_request", side_effect=planner_archive_payload), \
            contextlib.redirect_stdout(planner_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        planner_code = run_agent(
            "Это сложный стресс-тест планирования: сначала сформируй стратегию, затем выполни минимальный итог.",
            planner_config,
            event_sink=planner_events.append,
        )
    planner_result = json.loads(planner_stdout.getvalue())
    if planner_code != 0 or planner_result.get("message") != "planner execution ok":
        raise AssertionError(f"planner run failed: code={planner_code}, result={planner_result}")
    if not planner_payloads or planner_payloads[0].get("chat_template_kwargs") != {"enable_thinking": True}:
        raise AssertionError(f"planner did not enable thinking: {planner_payloads}")
    if planner_payloads[0].get("focus_enabled") is not False or planner_payloads[0].get("archive_enabled") is not False:
        raise AssertionError(f"planner should not use archive memory/internal archiving: {planner_payloads[0]}")
    if len(planner_payloads) < 2 or planner_payloads[1].get("chat_template_kwargs"):
        raise AssertionError(f"executor should not inherit planner thinking kwargs: {planner_payloads}")
    if not any(event.get("type") == "planner" and event.get("thinking_enabled") is True for event in planner_events):
        raise AssertionError(f"planner event missing: {planner_events}")
    print("[ok] planner thinking phase")

    planner_repair_payloads: list[dict] = []

    def planner_repair_archive_payload(config_arg, method, path, payload=None, timeout=180):
        planner_repair_payloads.append(payload or {})
        if len(planner_repair_payloads) == 1:
            return {"choices": [{"message": {"content": '{"summary":"bad\njson","steps":["write"]}'}}]}
        if len(planner_repair_payloads) == 2:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "repaired planner ok",
                                    "steps": ["write"],
                                    "verification": [],
                                    "risks": [],
                                    "executor_rules": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"content": '{"action":"final","message":"planner repair execution ok"}'}}]}

    planner_repair_stdout = io.StringIO()
    planner_repair_events: list[dict] = []
    planner_repair_config = AgentConfig(
        task_id=safe_task_id("self-test-planner-json-repair"),
        json_output=True,
        max_steps=1,
        inject_memory=False,
        archive_internal_steps=False,
        planner_enabled=True,
        planner_thinking=True,
    )
    with mock.patch.object(agent_runner, "archive_request", side_effect=planner_repair_archive_payload), \
            contextlib.redirect_stdout(planner_repair_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        planner_repair_code = run_agent(
            "Это сложный стресс-тест планирования: составь план, затем верни итог.",
            planner_repair_config,
            event_sink=planner_repair_events.append,
        )
    planner_repair_result = json.loads(planner_repair_stdout.getvalue())
    if planner_repair_code != 0 or planner_repair_result.get("message") != "planner repair execution ok":
        raise AssertionError(f"planner repair run failed: code={planner_repair_code}, result={planner_repair_result}")
    planner_repair_event = next((event for event in planner_repair_events if event.get("type") == "planner"), {})
    if planner_repair_event.get("ok") is not True or planner_repair_event.get("repaired") is not True:
        raise AssertionError(f"planner repair event missing repaired marker: {planner_repair_events}")
    if len(planner_repair_payloads) < 3 or planner_repair_payloads[1].get("chat_template_kwargs"):
        raise AssertionError(f"planner repair should not use thinking/template kwargs: {planner_repair_payloads}")
    print("[ok] planner JSON repair")

    verify_summary = agent_runner.result_summary(
        "verify_text_file",
        {
            "ok": False,
            "path": "/work/report.md",
            "failures": [
                {"check": "must_contain", "pattern": "reconnect"},
                {"check": "min_chars", "expected": 3000, "actual": 2400},
            ],
        },
    )
    if "must_contain:reconnect" not in verify_summary or "min_chars:expected=3000 actual=2400" not in verify_summary:
        raise AssertionError(f"verify_text_file summary missed failure details: {verify_summary}")
    if "must_contain patterns are exact literal substrings" not in verify_summary:
        raise AssertionError(f"verify_text_file summary missed literal must_contain guidance: {verify_summary}")
    verify_payload = result_for_model(
        "verify_text_file",
        {
            "ok": False,
            "path": "/work/report.md",
            "failures": [{"check": "must_contain", "pattern": "thin mobile client"}],
        },
        config,
    )
    if "thin mobile client" not in verify_payload.get("missing_literal_patterns", []):
        raise AssertionError(f"verify_text_file model payload missed missing literals: {verify_payload}")
    if "verbatim" not in verify_payload.get("supervisor_instruction", ""):
        raise AssertionError(f"verify_text_file model payload missed verbatim guidance: {verify_payload}")
    suggested_append = verify_payload.get("suggested_append_file_action")
    if not isinstance(suggested_append, dict) or "- thin mobile client" not in str(suggested_append.get("content", "")):
        raise AssertionError(f"verify_text_file payload missed suggested append action: {verify_payload}")
    json_verify_payload = result_for_model(
        "verify_text_file",
        {
            "ok": False,
            "path": "/work/summary.json",
            "failures": [{"check": "must_contain", "pattern": "section_count"}],
        },
        config,
    )
    if "suggested_append_file_action" in json_verify_payload:
        raise AssertionError(f"JSON verify payload should not suggest append_file: {json_verify_payload}")
    if "json.load assertions" not in json_verify_payload.get("supervisor_instruction", ""):
        raise AssertionError(f"JSON verify payload missed semantic guidance: {json_verify_payload}")
    if json_verify_payload.get("suggested_verify_json_action", {}).get("must_contain"):
        raise AssertionError(f"JSON verify payload should suggest verification without literals: {json_verify_payload}")
    if (json_verify_payload.get("suggested_python_json_check_action") or {}).get("action") != "python":
        raise AssertionError(f"JSON verify payload missed suggested python check: {json_verify_payload}")
    python_syntax_payload = result_for_model(
        "python",
        {"ok": False, "stdout": "SyntaxError: invalid syntax", "stderr": "", "returncode": 1},
        config,
    )
    if "Do not retry the same code" not in python_syntax_payload.get("supervisor_instruction", ""):
        raise AssertionError(f"Python SyntaxError payload missed retry guidance: {python_syntax_payload}")
    python_file_payload = result_for_model(
        "python",
        {"ok": False, "stdout": "", "stderr": "NameError: name '__file__' is not defined", "returncode": 1},
        config,
    )
    if "__file__ is not defined" not in python_file_payload.get("supervisor_instruction", ""):
        raise AssertionError(f"Python __file__ payload missed retry guidance: {python_file_payload}")
    shell_failure_payload = result_for_model(
        "shell",
        {"ok": False, "stdout": "", "stderr": "AssertionError", "returncode": 1},
        config,
    )
    if "Do not repeat the identical command" not in shell_failure_payload.get("supervisor_instruction", ""):
        raise AssertionError(f"shell failure payload missed retry guidance: {shell_failure_payload}")
    case_summary = agent_runner.result_summary(
        "verify_text_file",
        {
            "ok": False,
            "path": "/work/report.md",
            "failures": [{"check": "must_contain", "pattern": "thin mobile client", "case_mismatch": True}],
        },
    )
    if "case_mismatch" not in case_summary:
        raise AssertionError(f"verify_text_file summary missed case mismatch: {case_summary}")
    ordered_summary = agent_runner.result_summary(
        "verify_text_file",
        {"ok": False, "path": "/work/report.md", "failures": [{"check": "ordered_patterns", "pattern": "A"}]},
    )
    if "retry verify_text_file without ordered_patterns" not in ordered_summary:
        raise AssertionError(f"ordered pattern summary missed retry hint: {ordered_summary}")
    print("[ok] verify_text_file summary includes failure details")

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
    last_call = mocked_archive.call_args_list[-1]
    last_payload = last_call.kwargs.get("payload") or (last_call.args[3] if len(last_call.args) > 3 else {})
    if (
        context_reply != '{"action":"final","message":"memory off ok"}'
        or mocked_archive.call_count < 2
        or last_payload.get("focus_enabled") is not False
    ):
        raise AssertionError(f"context retry did not disable memory after compacted attempts: reply={context_reply}, calls={mocked_archive.call_count}")
    print("[ok] model context retry disables memory")

    compact_prompt_calls: list[str] = []

    def compact_prompt_side_effect(config_arg, method, path, payload=None, timeout=180):
        messages = (payload or {}).get("messages") or []
        system_content = messages[0].get("content", "") if messages else ""
        compact_prompt_calls.append(system_content)
        if "Разрешенные действия:" in system_content:
            raise HTTPError(
                url="http://archive/v1/chat/completions",
                code=400,
                msg="Bad Request",
                hdrs={},
                fp=io.BytesIO(b'{"error":"context too large"}'),
            )
        return {"choices": [{"message": {"content": '{"action":"final","message":"compact ok"}'}}]}

    compact_context_config = AgentConfig(llm_retries=1, inject_memory=False, archive_internal_steps=False)
    with mock.patch.object(agent_runner, "archive_request", side_effect=compact_prompt_side_effect):
        compact_prompt_reply = chat(
            compact_context_config,
            [{"role": "system", "content": agent_runner.SYSTEM_PROMPT}, {"role": "user", "content": "context fallback"}],
            inject_memory=False,
            archive_enabled=False,
        )
    if compact_prompt_reply != '{"action":"final","message":"compact ok"}' or not any("Доступные действия:" in call for call in compact_prompt_calls):
        raise AssertionError(f"compact system prompt fallback failed: reply={compact_prompt_reply}, calls={len(compact_prompt_calls)}")
    print("[ok] compact system prompt fallback")

    rejected_summary = agent_runner.result_summary(
        "list_files",
        {"ok": False, "error": "repeated identical action rejected by supervisor", "items": []},
    )
    if "repeated identical action rejected" not in rejected_summary:
        raise AssertionError(f"supervisor rejection summary hid the error: {rejected_summary}")
    print("[ok] supervisor rejection result summary")

    step_message_events: list[dict] = []
    step_message_stdout = io.StringIO()
    step_message_config = AgentConfig(
        task_id=safe_task_id("self-test-step-display-messages"),
        json_output=True,
        max_steps=2,
        inject_memory=False,
        archive_internal_steps=False,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"read_file","path":"/work/input.txt","max_bytes":1000}',
            '{"action":"final","message":"ok"}',
    ]), mock.patch.object(agent_runner, "file_tool", return_value={
            "ok": True,
            "path": "/work/input.txt",
            "content": "hello",
            "size": 5,
            "bytes_read": 5,
            "offset": 0,
    }), contextlib.redirect_stdout(step_message_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        step_message_code = run_agent("read then final", step_message_config, event_sink=step_message_events.append)
    action_event = next((event for event in step_message_events if event.get("type") == "action"), {})
    tool_event = next((event for event in step_message_events if event.get("type") == "tool_result"), {})
    if (
        step_message_code != 0
        or "Смотрю файл input.txt" not in str(action_event.get("message") or "")
        or "Прочитал input.txt" not in str(tool_event.get("display_message") or "")
        or "read 5/5 byte(s)" not in str(tool_event.get("message") or "")
    ):
        raise AssertionError(f"step display messages missing: code={step_message_code}, events={step_message_events}")
    print("[ok] step events include display messages")

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

    extracted_paths = extract_sandbox_paths_from_text(
        "Готово: /work/book.txt, /artifacts/out.fb2 и https://example.com/work/nope"
    )
    if extracted_paths != ["/work/book.txt", "/artifacts/out.fb2"]:
        raise AssertionError(f"final artifact path extraction failed: {extracted_paths}")
    with mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/missing.txt", "exists": False}):
        artifact_check = validate_final_artifacts(config, "Создан /work/missing.txt")
    if artifact_check.get("ok") or artifact_check.get("failures", [{}])[0].get("reason") != "missing":
        raise AssertionError(f"missing final artifact was not rejected: {artifact_check}")
    rejected_final_events: list[dict] = []
    rejected_final_stdout = io.StringIO()
    rejected_final_config = AgentConfig(
        task_id=safe_task_id("self-test-final-artifact-validation"),
        json_output=True,
        max_steps=2,
        inject_memory=False,
        archive_internal_steps=False,
    )
    final_replies = [
        '{"action":"final","message":"Создан /work/missing.txt"}',
        '{"action":"final","message":"Файл не был создан; задача не выполнена."}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=final_replies), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/missing.txt", "exists": False}), \
            contextlib.redirect_stdout(rejected_final_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        rejected_final_code = run_agent("return missing artifact final", rejected_final_config, event_sink=rejected_final_events.append)
    rejected_final_payload = json.loads(rejected_final_stdout.getvalue())
    if rejected_final_code != 0 or rejected_final_payload.get("message") != "Файл не был создан; задача не выполнена.":
        raise AssertionError(f"artifact validation did not recover to corrected final: {rejected_final_payload}")
    if not any(event.get("code") == "final_artifact_validation_failed" for event in rejected_final_events):
        raise AssertionError(f"missing artifact warning was not emitted: {rejected_final_events}")
    print("[ok] final artifact validation")

    required_paths = required_artifact_paths_from_task(
        "Required artifacts: /work/report.md and /work/matrix.md. "
        "summary.md and /work/analysis.json are not substitutes. Use /work/sources as input."
    )
    if required_paths != ["/work/report.md", "/work/matrix.md"]:
        raise AssertionError(f"required artifact extraction failed: {required_paths}")
    numbered_required_paths = required_artifact_paths_from_task(
        "Обязательные артефакты:\n"
        "1. /work/nightly-finalization/plan.md\n"
        "2. /work/nightly-finalization/result.md\n"
        "3. /work/nightly-finalization/audit.json\n"
        "4. /work/nightly-finalization/final.md\n\n"
        "Требования:\n"
        "- Не используй /work/nightly-finalization/source.tmp как финальный результат."
    )
    if numbered_required_paths != [
        "/work/nightly-finalization/plan.md",
        "/work/nightly-finalization/result.md",
        "/work/nightly-finalization/audit.json",
        "/work/nightly-finalization/final.md",
    ]:
        raise AssertionError(f"numbered required artifact extraction failed: {numbered_required_paths}")
    recovery_required_paths = required_artifact_paths_from_task(
        "Обязательные артефакты: /work/nightly-error-recovery/report.md и /work/nightly-error-recovery/recovery_log.json. "
        "Один раз попробуй read_file для /work/nightly-error-recovery/missing-input.md, ожидаемо получишь ошибку, "
        "после этого не повторяй чтение missing-input.md и создай fallback input сам."
    )
    if recovery_required_paths != [
        "/work/nightly-error-recovery/report.md",
        "/work/nightly-error-recovery/recovery_log.json",
    ]:
        raise AssertionError(f"recovery task required artifact extraction failed: {recovery_required_paths}")
    print("[ok] required artifact path extraction")

    omitted_final_events: list[dict] = []
    omitted_final_stdout = io.StringIO()
    omitted_final_config = AgentConfig(
        task_id=safe_task_id("self-test-final-required-artifacts"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
    )
    omitted_replies = [
        '{"action":"final","message":"Готово: /work/report.md"}',
        '{"action":"final","message":"Готово: /work/report.md и /work/matrix.md"}',
    ]
    def fake_omitted_final_missing_verifications(paths: list[str], verified_paths: set[str]) -> list[str]:
        return ["/work/matrix.md"] if set(paths) == {"/work/matrix.md"} else []

    with mock.patch.object(agent_runner, "chat", side_effect=omitted_replies), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "exists": True, "type": "file", "size": 2000}), \
            mock.patch.object(agent_runner, "missing_text_verifications", side_effect=fake_omitted_final_missing_verifications), \
            contextlib.redirect_stdout(omitted_final_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        omitted_final_code = run_agent(
            "Required artifacts: /work/report.md and /work/matrix.md. summary.md and /work/analysis.json are not substitutes.",
            omitted_final_config,
            event_sink=omitted_final_events.append,
        )
    omitted_final_payload = json.loads(omitted_final_stdout.getvalue())
    if omitted_final_code != 0 or "/work/matrix.md" not in omitted_final_payload.get("message", ""):
        raise AssertionError(f"required final artifacts were not enforced: {omitted_final_payload}")
    if not any(event.get("code") == "final_required_artifacts_omitted" for event in omitted_final_events):
        raise AssertionError(f"missing required artifact warning was not emitted: {omitted_final_events}")
    print("[ok] final required artifact coverage")

    omitted_verified_stdout = io.StringIO()
    omitted_verified_config = AgentConfig(
        task_id=safe_task_id("self-test-final-omitted-but-verified-artifacts"),
        json_output=True,
        max_steps=1,
        inject_memory=False,
        archive_internal_steps=False,
    )
    with mock.patch.object(agent_runner, "chat", return_value='{"action":"final","message":"Готово: /work/report.md"}'), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "exists": True, "type": "file", "size": 2000}), \
            mock.patch.object(agent_runner, "missing_text_verifications", return_value=[]), \
            contextlib.redirect_stdout(omitted_verified_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        omitted_verified_code = run_agent(
            "Required artifacts: /work/report.md and /work/matrix.md.",
            omitted_verified_config,
        )
    omitted_verified_payload = json.loads(omitted_verified_stdout.getvalue())
    if omitted_verified_code != 0 or "/work/matrix.md" not in omitted_verified_payload.get("message", ""):
        raise AssertionError(f"verified omitted required artifacts should be appended to final: {omitted_verified_payload}")
    print("[ok] verified omitted required artifacts appended to final")

    class FakeTelegramProcess:
        returncode = 0

        def communicate(self, timeout=None):
            return (
                json.dumps(
                    {
                        "ok": True,
                        "message_id": 123,
                        "chat_id": 7791909246,
                        "file_name": "book.fb2",
                        "file_size": 42,
                    },
                    ensure_ascii=False,
                ),
                "",
            )

    with mock.patch.dict(os.environ, {"SHUSHUNYA_AGENT_TELEGRAM_BOT_TOKEN": "token", "SHUSHUNYA_AGENT_TELEGRAM_CHAT_ID": "7791909246"}), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/book.fb2", "exists": True, "type": "file", "size": 42}), \
            mock.patch.object(agent_runner, "sandbox_path_to_host_path", return_value=Path("/sandbox/work/book.fb2")), \
            mock.patch.object(agent_runner.subprocess, "Popen", return_value=FakeTelegramProcess()):
        telegram_result = agent_runner.telegram_send_document_tool(
            config,
            {"action": "telegram_send_document", "path": "/work/book.fb2", "caption": "ready"},
        )
    if telegram_result.get("ok") is not True or telegram_result.get("message_id") != 123:
        raise AssertionError(f"telegram_send_document mocked tool failed: {telegram_result}")
    print("[ok] telegram document tool")

    verified_final_events: list[dict] = []
    verified_final_stdout = io.StringIO()
    verified_final_config = AgentConfig(
        task_id=safe_task_id("self-test-final-text-verification"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
    )
    verification_replies = [
        '{"action":"final","message":"Готово: /work/book.fb2"}',
        '{"action":"verify_text_file","path":"/work/book.fb2","ordered_patterns":["Том 10","Том 23"],"min_bytes":1000}',
        '{"action":"final","message":"Готово: /work/book.fb2"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=verification_replies), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/book.fb2", "exists": True, "type": "file", "size": 2000}), \
            mock.patch.object(agent_runner, "verify_text_file_tool", return_value={"ok": True, "path": "/work/book.fb2", "size": 2000, "chars": 1500, "failures": []}), \
            contextlib.redirect_stdout(verified_final_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        verified_final_code = run_agent("create verified book", verified_final_config, event_sink=verified_final_events.append)
    verified_final_payload = json.loads(verified_final_stdout.getvalue())
    if verified_final_code != 0 or verified_final_payload.get("message") != "Готово: /work/book.fb2":
        raise AssertionError(f"text verification final did not complete: {verified_final_payload}")
    if not any(event.get("code") == "final_text_verification_required" for event in verified_final_events):
        raise AssertionError(f"missing text verification warning was not emitted: {verified_final_events}")
    print("[ok] final text verification required")

    auto_final_events: list[dict] = []
    auto_final_stdout = io.StringIO()
    auto_final_config = AgentConfig(
        task_id=safe_task_id("self-test-auto-final-required-artifacts"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
    )
    auto_final_replies = [
        '{"action":"verify_text_file","path":"/work/report.md","must_contain":["Summary"],"min_chars":1000}',
        '{"action":"verify_text_file","path":"/work/matrix.md","must_contain":["Risk"],"min_chars":1000}',
        '{"action":"verify_text_file","path":"/work/report.md","must_contain":["Summary"],"min_chars":1000}',
    ]

    def fake_file_info(_config, action):
        return {"ok": True, "path": action.get("path"), "exists": True, "type": "file", "size": 2000}

    def fake_verify(_config, action):
        return {"ok": True, "path": action.get("path"), "size": 2000, "chars": 1500, "failures": []}

    with mock.patch.object(agent_runner, "chat", side_effect=auto_final_replies), \
            mock.patch.object(agent_runner, "file_tool", side_effect=fake_file_info), \
            mock.patch.object(agent_runner, "verify_text_file_tool", side_effect=fake_verify), \
            contextlib.redirect_stdout(auto_final_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        auto_final_code = run_agent(
            "Required artifacts: /work/report.md and /work/matrix.md.",
            auto_final_config,
            event_sink=auto_final_events.append,
        )
    auto_final_payload = json.loads(auto_final_stdout.getvalue())
    if auto_final_code != 0 or "/work/report.md" not in auto_final_payload.get("message", "") or "/work/matrix.md" not in auto_final_payload.get("message", ""):
        raise AssertionError(f"required artifact auto-final did not complete: {auto_final_payload}")
    if not any(event.get("code") == "auto_final_required_artifacts_verified" for event in auto_final_events):
        raise AssertionError(f"required artifact auto-final warning was not emitted: {auto_final_events}")
    print("[ok] required artifacts auto-final after verification")

    artifact_verify_mode_events: list[dict] = []
    artifact_verify_mode_stdout = io.StringIO()
    artifact_verify_mode_config = AgentConfig(
        task_id=safe_task_id("self-test-artifact-verify-mode"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"write_file","path":"/work/report.md","content":"Summary\\nSTATUS: PASS"}',
            '{"action":"write_file","path":"/work/audit.json","content":"{\\"status\\":\\"pass\\"}"}',
            '{"action":"verify_text_file","path":"/work/report.md","must_contain":["STATUS: PASS"],"min_bytes":1}',
            '{"action":"verify_text_file","path":"/work/audit.json","must_contain":["status=\\"pass\\""],"min_bytes":1}',
    ]) as mocked_artifact_verify_chat, mock.patch.object(agent_runner, "file_tool", side_effect=fake_file_info), \
            mock.patch.object(agent_runner, "verify_text_file_tool", side_effect=fake_verify), \
            contextlib.redirect_stdout(artifact_verify_mode_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        artifact_verify_mode_code = run_agent(
            "Required artifacts: /work/report.md and /work/audit.json. report.md must contain STATUS: PASS. audit.json status=\"pass\".",
            artifact_verify_mode_config,
            event_sink=artifact_verify_mode_events.append,
        )
    artifact_verify_mode_payload = json.loads(artifact_verify_mode_stdout.getvalue())
    artifact_verify_actions = [step.get("action", {}).get("action") for step in artifact_verify_mode_payload.get("steps", [])]
    if (
        artifact_verify_mode_code != 0
        or mocked_artifact_verify_chat.call_count != 2
        or artifact_verify_actions != ["write_file", "write_file", "verify_text_file", "verify_text_file"]
        or not any(event.get("code") == "artifact_verify_mode" for event in artifact_verify_mode_events)
        or "/work/report.md" not in artifact_verify_mode_payload.get("message", "")
        or "/work/audit.json" not in artifact_verify_mode_payload.get("message", "")
    ):
        raise AssertionError(
            "artifact verification mode did not narrow the post-write step: "
            f"code={artifact_verify_mode_code}, payload={artifact_verify_mode_payload}, actions={artifact_verify_actions}, events={artifact_verify_mode_events}"
        )
    print("[ok] artifact verification mode narrows post-write steps")

    data_source_guard_events: list[dict] = []
    data_source_guard_stdout = io.StringIO()
    data_source_guard_config = AgentConfig(
        task_id=safe_task_id("self-test-data-source-artifact-guard"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
    )

    def fake_data_file_tool(_config, action):
        if action.get("action") == "read_file":
            return {"ok": True, "path": action.get("path"), "content": "id,value\n1,42\n"}
        return fake_file_info(_config, action)

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"write_file","path":"/work/report.md","content":"Summary\\nTotal: 42"}',
            '{"action":"read_file","path":"/work/input.csv"}',
            '{"action":"write_file","path":"/work/report.md","content":"Summary\\nTotal: 42"}',
    ]) as mocked_data_source_chat, mock.patch.object(agent_runner, "file_tool", side_effect=fake_data_file_tool), \
            mock.patch.object(agent_runner, "verify_text_file_tool", side_effect=fake_verify), \
            contextlib.redirect_stdout(data_source_guard_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        data_source_guard_code = run_agent(
            "Workspace: /work/.\nRead input.csv and derive the required artifact.\nRequired artifacts: /work/report.md. report.md must contain Total: 42.",
            data_source_guard_config,
            event_sink=data_source_guard_events.append,
        )
    data_source_guard_payload = json.loads(data_source_guard_stdout.getvalue())
    data_source_guard_actions = [step.get("action", {}).get("action") for step in data_source_guard_payload.get("steps", [])]
    first_result = data_source_guard_payload.get("steps", [{}])[0].get("result", {})
    if (
        data_source_guard_code != 0
        or mocked_data_source_chat.call_count != 3
        or data_source_guard_actions != ["write_file", "read_file", "write_file", "verify_text_file"]
        or first_result.get("error") != "data source inspection required by supervisor"
        or not any(event.get("code") == "data_source_inspection_required" for event in data_source_guard_events)
    ):
        raise AssertionError(
            "data source artifact guard did not reject write-before-read: "
            f"code={data_source_guard_code}, payload={data_source_guard_payload}, actions={data_source_guard_actions}, events={data_source_guard_events}"
        )
    print("[ok] data source artifacts require input inspection before writing")
    data_source_reread_stdout = io.StringIO()
    data_source_reread_config = AgentConfig(
        task_id=safe_task_id("self-test-data-source-reread-guard"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"read_file","path":"/work/input.csv"}',
            '{"action":"read_file","path":"/work/input.csv"}',
            '{"action":"write_file","path":"/work/report.md","content":"Summary\\nTotal: 42"}',
    ]), mock.patch.object(agent_runner, "file_tool", side_effect=fake_data_file_tool), \
            mock.patch.object(agent_runner, "verify_text_file_tool", side_effect=fake_verify), \
            contextlib.redirect_stdout(data_source_reread_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        data_source_reread_code = run_agent(
            "Workspace: /work/.\nRead input.csv and derive the required artifact.\nRequired artifacts: /work/report.md. report.md must contain Total: 42.",
            data_source_reread_config,
        )
    data_source_reread_payload = json.loads(data_source_reread_stdout.getvalue())
    reread_errors = [
        (step.get("result") or {}).get("error")
        for step in data_source_reread_payload.get("steps", [])
    ]
    if data_source_reread_code != 0 or "data source reread rejected by supervisor" not in reread_errors:
        raise AssertionError(f"data source reread guard failed: {data_source_reread_payload}")
    print("[ok] data source reread guard")
    if agent_runner.action_reads_data_source(
        {"action": "python", "code": "# events.jsonl is mentioned here\nopen('timeline.csv', 'w').write('fake')"},
        ["/work/events.jsonl"],
    ):
        raise AssertionError("data source read detector accepted a comment-only source mention")
    if not agent_runner.action_reads_data_source(
        {"action": "python", "code": "import json\nrows=[json.loads(line) for line in open('events.jsonl', encoding='utf-8')]"},
        ["/work/events.jsonl"],
    ):
        raise AssertionError("data source read detector rejected a real relative open()")
    multi_source_reads = set(agent_runner.action_read_data_sources(
        {
            "action": "python",
            "code": (
                "import csv, json\n"
                "events=[json.loads(line) for line in open('events.jsonl', encoding='utf-8')]\n"
                "owners=list(csv.DictReader(open('owners.csv', encoding='utf-8')))\n"
            ),
        },
        ["/work/events.jsonl", "/work/owners.csv"],
    ))
    if multi_source_reads != {"/work/events.jsonl", "/work/owners.csv"}:
        raise AssertionError(f"data source read detector missed multi-source python IO: {multi_source_reads}")
    if agent_runner.action_reads_data_source(
        {"action": "shell", "cmd": "ls -la /work && echo ledger.csv"},
        ["/work/ledger.csv"],
    ):
        raise AssertionError("data source read detector accepted shell listing as source read")
    if not agent_runner.action_reads_data_source(
        {"action": "shell", "cmd": "cat /work/ledger.csv"},
        ["/work/ledger.csv"],
    ):
        raise AssertionError("data source read detector rejected shell cat source read")
    restored_sources = agent_runner.resume_context_inspected_data_sources(
        'Resume context from previous agent task journal x:\n'
        '[{"type":"tool_result","action":"read_file","result":{"ok":true,"path":"/work/events.jsonl","content":"row"}}]',
        ["/work/events.jsonl"],
    )
    if restored_sources != ["/work/events.jsonl"]:
        raise AssertionError(f"resume context did not restore inspected data sources: {restored_sources}")
    falsely_restored_sources = agent_runner.resume_context_inspected_data_sources(
        'Resume context from previous agent task journal x:\n'
        '[{"type":"tool_result","action":"read_file","result":{"ok":true,"path":"/work/customers.csv","content":"row"}},'
        '{"type":"tool_result","action":"read_file","result":{"ok":false,"path":"/work/tickets.jsonl","missing_data_sources":["/work/tickets.jsonl"]}}]',
        ["/work/customers.csv", "/work/tickets.jsonl"],
    )
    if falsely_restored_sources != ["/work/customers.csv"]:
        raise AssertionError(f"resume context falsely restored missing data source: {falsely_restored_sources}")
    print("[ok] data source read detector requires same-line IO")

    restored_required_events: list[dict] = []
    restored_required_stdout = io.StringIO()
    restored_required_config = AgentConfig(
        task_id=safe_task_id("self-test-restored-required-artifacts"),
        json_output=True,
        max_steps=2,
        inject_memory=False,
        archive_internal_steps=False,
        initial_required_artifact_paths=("/work/report.md", "/work/matrix.md"),
        initial_verified_text_paths=("/work/report.md",),
    )
    with mock.patch.object(agent_runner, "chat", return_value='{"action":"verify_text_file","path":"/work/matrix.md","must_contain":["Risk"]}'), \
            mock.patch.object(agent_runner, "file_tool", side_effect=fake_file_info), \
            mock.patch.object(agent_runner, "verify_text_file_tool", side_effect=fake_verify), \
            contextlib.redirect_stdout(restored_required_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        restored_required_code = run_agent(
            "Continuation cycle: 1\nResume context includes input /work/events.jsonl and required_artifacts [\"/work/report.md\", \"/work/matrix.md\"].",
            restored_required_config,
            event_sink=restored_required_events.append,
        )
    restored_required_payload = json.loads(restored_required_stdout.getvalue())
    if restored_required_code != 0 or "/work/events.jsonl" in restored_required_payload.get("message", ""):
        raise AssertionError(f"restored required artifact auto-final failed: {restored_required_payload}")
    if "/work/report.md" not in restored_required_payload.get("message", "") or "/work/matrix.md" not in restored_required_payload.get("message", ""):
        raise AssertionError(f"restored required artifact auto-final omitted restored paths: {restored_required_payload}")
    print("[ok] restored required artifacts ignore resume context noise")

    progress_hint_events: list[dict] = []
    progress_hint_stdout = io.StringIO()
    progress_hint_config = AgentConfig(
        task_id=safe_task_id("self-test-required-artifact-verification-hint"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
        initial_verified_text_paths=("/work/plan.md",),
    )
    progress_hint_replies = [
        '{"action":"write_file","path":"/work/final.md","content":"FINAL"}',
        '{"action":"verify_text_file","path":"/work/final.md","must_contain":["FINAL"]}',
    ]

    with mock.patch.object(agent_runner, "chat", side_effect=progress_hint_replies), \
            mock.patch.object(agent_runner, "file_tool", side_effect=fake_file_info), \
            mock.patch.object(agent_runner, "verify_text_file_tool", side_effect=fake_verify), \
            contextlib.redirect_stdout(progress_hint_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        progress_hint_code = run_agent(
            "Required artifacts: /work/plan.md and /work/final.md.",
            progress_hint_config,
            event_sink=progress_hint_events.append,
        )
    progress_hint_payload = json.loads(progress_hint_stdout.getvalue())
    hint_events = [event for event in progress_hint_events if event.get("code") == "required_artifact_verification_hint"]
    if progress_hint_code != 0 or not hint_events or hint_events[0].get("missing_verification") != ["/work/final.md"]:
        raise AssertionError(
            f"required artifact verification hint failed: code={progress_hint_code}, "
            f"payload={progress_hint_payload}, events={progress_hint_events}"
        )
    print("[ok] required artifact verification hint")

    timeout_stdout = io.StringIO()
    timeout_events: list[dict] = []
    timeout_config = AgentConfig(
        task_id=safe_task_id("self-test-model-timeout-continuable"),
        json_output=True,
        max_steps=1,
        inject_memory=False,
        archive_internal_steps=False,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=TimeoutError("model read timed out")), \
            contextlib.redirect_stdout(timeout_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        timeout_code = run_agent("model timeout should be continuable", timeout_config, event_sink=timeout_events.append)
    timeout_payload = json.loads(timeout_stdout.getvalue())
    if timeout_code != 2 or timeout_payload.get("continuable") is not True:
        raise AssertionError(f"model timeout was not converted to continuable final: code={timeout_code}, payload={timeout_payload}")
    if not any(event.get("stop_reason") == "model_request_failed" for event in timeout_events):
        raise AssertionError(f"model timeout final event missing stop reason: {timeout_events}")
    print("[ok] model timeout becomes continuable final")

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

    with mock.patch.object(
        agent_runner,
        "web_links_tool",
        return_value={
            "ok": True,
            "links": [
                {"url": "https://example.com/vol14/1.5", "text": "14 - 1.5"},
                {"url": "https://example.com/vol14/2", "text": "14 - 2"},
            ],
        },
    ), mock.patch.object(
        agent_runner,
        "web_extract_to_file_tool",
        side_effect=[
            {"ok": True, "path": "/work/batch_001_14_1_5.txt", "chars": 1000},
            {"ok": True, "path": "/work/batch_002_14_2.txt", "chars": 2000},
        ],
    ):
        batch_result = agent_runner.web_extract_link_list_tool(
            AgentConfig(),
            {
                "action": "web_extract_link_list",
                "url": "https://example.com/contents",
                "start_url": "https://example.com/vol14/1.5",
                "end_url": "https://example.com/vol14/2",
                "path_template": "/work/batch_{seq}_{vol}_{chapter}.txt",
                "limit": 10,
            },
        )
    if batch_result.get("files_written") != 2 or batch_result.get("selected_links") != 2:
        raise AssertionError(f"web_extract_link_list batch smoke failed: {batch_result}")
    print("[ok] web extract link list")

    with mock.patch.object(
        agent_runner,
        "run_sandbox_argv",
        return_value={
            "ok": True,
            "stdout": json.dumps(
                {
                    "ok": True,
                    "output_txt": "/work/book.txt",
                    "output_fb2": "/work/book.fb2",
                    "included_files": 2,
                },
                ensure_ascii=False,
            ),
        },
    ):
        bundle_result = agent_runner.bundle_text_files_tool(
            AgentConfig(),
            {
                "action": "bundle_text_files",
                "path": "/work",
                "output_txt": "/work/book.txt",
                "output_fb2": "/work/book.fb2",
            },
        )
    if bundle_result.get("included_files") != 2:
        raise AssertionError(f"bundle_text_files smoke failed: {bundle_result}")
    print("[ok] bundle text files")

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
    if len(repeat_payload.get("steps", [])) > 10:
        raise AssertionError(f"cumulative repeat guard stopped too late: {len(repeat_payload.get('steps', []))} steps")
    print("[ok] cumulative repeated action guard")

    failed_web_fetch_repeat_stdout = io.StringIO()
    failed_web_fetch_repeat_config = AgentConfig(
        task_id=safe_task_id("self-test-failed-web-fetch-repeat"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
    )
    failed_url = "https://example.com/blocked"
    failed_web_fetch_repeat_actions = [
        json.dumps({"action": "web_fetch", "url": failed_url}, ensure_ascii=False),
        json.dumps({"action": "web_fetch", "url": failed_url}, ensure_ascii=False),
        '{"action":"web_search","query":"alternate source","limit":1}',
        '{"action":"final","message":"done"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=failed_web_fetch_repeat_actions), \
            mock.patch.object(agent_runner, "web_fetch", return_value={"ok": False, "error": "HTTP Error 403: Forbidden"}) as mocked_failed_fetch, \
            mock.patch.object(agent_runner, "web_search", return_value={"ok": True, "results": []}), \
            contextlib.redirect_stdout(failed_web_fetch_repeat_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        failed_web_fetch_repeat_code = run_agent("recover from blocked web source", failed_web_fetch_repeat_config)
    failed_web_fetch_repeat_payload = json.loads(failed_web_fetch_repeat_stdout.getvalue())
    failed_web_fetch_repeat_errors = [
        (step.get("result") or {}).get("error")
        for step in failed_web_fetch_repeat_payload.get("steps", [])
        if isinstance(step.get("result"), dict)
    ]
    if failed_web_fetch_repeat_code != 0 or "web_fetch failed url rejected by supervisor" not in failed_web_fetch_repeat_errors:
        raise AssertionError(
            "failed web_fetch repeat guard failed: "
            f"code={failed_web_fetch_repeat_code}, payload={failed_web_fetch_repeat_payload}"
        )
    if mocked_failed_fetch.call_count != 1:
        raise AssertionError(f"failed URL should be fetched once, got {mocked_failed_fetch.call_count}")
    print("[ok] failed web_fetch repeat guard")

    productive_reset_stdout = io.StringIO()
    productive_reset_config = AgentConfig(
        task_id=safe_task_id("self-test-repeat-reset-on-progress"),
        json_output=True,
        max_steps=8,
        inject_memory=False,
        archive_internal_steps=False,
    )
    productive_reset_actions = [
        '{"action":"list_files","path":"/work","max_depth":1,"limit":1,"offset":0}',
        '{"action":"list_files","path":"/work","max_depth":1,"limit":1,"offset":0}',
        '{"action":"list_files","path":"/work","max_depth":1,"limit":1,"offset":0}',
        '{"action":"write_file","path":"/work/report.md","content":"progress"}',
        '{"action":"list_files","path":"/work","max_depth":1,"limit":1,"offset":0}',
        '{"action":"list_files","path":"/work","max_depth":1,"limit":1,"offset":0}',
        '{"action":"final","message":"done"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=productive_reset_actions), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/report.md"}), \
            contextlib.redirect_stdout(productive_reset_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        productive_reset_code = run_agent("repeat rejection reset on progress", productive_reset_config)
    productive_reset_payload = json.loads(productive_reset_stdout.getvalue())
    if productive_reset_code != 0:
        raise AssertionError(f"productive progress should reset cumulative repeat guard: code={productive_reset_code}, payload={productive_reset_payload}")
    print("[ok] repeated rejection total resets on productive progress")

    shell_python_syntax_stdout = io.StringIO()
    shell_python_syntax_config = AgentConfig(
        task_id=safe_task_id("self-test-shell-python-syntax-loop"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    shell_python_syntax_actions = [
        '{"action":"shell","cmd":"python3 -c \\"print((\\"","timeout":60}',
        '{"action":"shell","cmd":"python3 -c \\"print(((\\"","timeout":60}',
        '{"action":"shell","cmd":"python3 -c \\"print((((\\"","timeout":60}',
        '{"action":"final","message":"done"}',
    ]
    syntax_error_result = {"ok": False, "returncode": 1, "stdout": "", "stderr": "SyntaxError: '(' was never closed"}
    with mock.patch.object(agent_runner, "chat", side_effect=shell_python_syntax_actions), \
            mock.patch.object(agent_runner, "run_shell", return_value=syntax_error_result) as mocked_syntax_shell, \
            contextlib.redirect_stdout(shell_python_syntax_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        shell_python_syntax_code = run_agent("debug python quoting", shell_python_syntax_config)
    shell_python_syntax_payload = json.loads(shell_python_syntax_stdout.getvalue())
    syntax_rejections = [
        step for step in shell_python_syntax_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "shell python inline syntax loop rejected by supervisor"
    ]
    if shell_python_syntax_code != 0 or not syntax_rejections or mocked_syntax_shell.call_count != 2:
        raise AssertionError(
            "shell inline python syntax loop guard failed: "
            f"code={shell_python_syntax_code}, shell_calls={mocked_syntax_shell.call_count}, payload={shell_python_syntax_payload}"
        )
    print("[ok] shell inline python syntax loop guard")

    swe_shell_python_stdout = io.StringIO()
    swe_shell_python_config = AgentConfig(
        task_id=safe_task_id("self-test-swe-shell-python-reject"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -c \\"print(1)\\"","timeout":60}',
            '{"action":"final","message":"done"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={"ok": True, "stdout": "1\n"}) as mocked_swe_shell, \
            contextlib.redirect_stdout(swe_shell_python_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        swe_shell_python_code = run_agent(
            "Исправь Python-проект.\n\nРабочий каталог для этой задачи: /work/project",
            swe_shell_python_config,
        )
    swe_shell_python_payload = json.loads(swe_shell_python_stdout.getvalue())
    swe_shell_rejections = [
        step for step in swe_shell_python_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "swe shell inline python rejected by supervisor"
    ]
    if swe_shell_python_code != 0 or not swe_shell_rejections or mocked_swe_shell.called:
        raise AssertionError(
            "SWE shell inline python guard failed: "
            f"code={swe_shell_python_code}, shell_called={mocked_swe_shell.called}, payload={swe_shell_python_payload}"
        )
    suggested = (swe_shell_rejections[0].get("result") or {}).get("suggested_action") or {}
    if suggested.get("action") != "python" or suggested.get("cwd") != "/work/project":
        raise AssertionError(f"SWE shell inline python guard did not provide a valid suggested python action: {suggested}")
    print("[ok] SWE shell inline python rejected")

    noop_replace_stdout = io.StringIO()
    noop_replace_config = AgentConfig(
        task_id=safe_task_id("self-test-noop-replace"),
        json_output=True,
        max_steps=2,
        inject_memory=False,
        archive_internal_steps=False,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"replace_in_file","path":"/work/project/a.py","old":"return x","new":"return x","count":1}',
            '{"action":"final","message":"done"}',
    ]), mock.patch.object(agent_runner, "file_tool", return_value={"ok": True}) as mocked_noop_file, \
            contextlib.redirect_stdout(noop_replace_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        noop_replace_code = run_agent("patch no-op replace", noop_replace_config)
    noop_replace_payload = json.loads(noop_replace_stdout.getvalue())
    noop_replace_rejections = [
        step for step in noop_replace_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "no-op replace_in_file rejected by supervisor"
    ]
    if noop_replace_code != 0 or not noop_replace_rejections or mocked_noop_file.called:
        raise AssertionError(
            "no-op replace guard failed: "
            f"code={noop_replace_code}, file_called={mocked_noop_file.called}, payload={noop_replace_payload}"
        )
    print("[ok] no-op replace_in_file rejected")

    public_shape_stdout = io.StringIO()
    public_shape_config = AgentConfig(
        task_id=safe_task_id("self-test-public-shape-contract"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )

    def fake_public_shape_fallback(_config: AgentConfig, _action: dict) -> dict:
        return {
            "ok": False,
            "returncode": 1,
            "stdout": json.dumps({
                "results": [
                    {
                        "ok": False,
                        "file": "tests/test_scheduler.py",
                        "test": "test_parse_jobs_uses_datetime_and_int_duration",
                        "traceback": "TypeError: 'Job' object is not subscriptable\nassert jobs[0]['duration_min'] == 60",
                    }
                ],
                "source_hints": ["/work/project/scheduler/core.py"],
            }),
            "stderr": "",
        }

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"write_file","path":"/work/project/scheduler/core.py","content":"from dataclasses import dataclass\\n@dataclass\\nclass Job:\\n    id: str\\n\\ndef parse_jobs(rows):\\n    return [Job(row[\\\"id\\\"]) for row in rows]\\n"}',
            '{"action":"final","message":"done"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_public_shape_fallback), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True}) as mocked_public_shape_file, \
            contextlib.redirect_stdout(public_shape_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        public_shape_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            public_shape_config,
        )
    public_shape_payload = json.loads(public_shape_stdout.getvalue())
    public_shape_rejections = [
        step for step in public_shape_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "swe public contract regression rejected by supervisor"
    ]
    if public_shape_code != 0 or not public_shape_rejections or mocked_public_shape_file.called:
        raise AssertionError(
            "public shape contract guard failed: "
            f"code={public_shape_code}, file_called={mocked_public_shape_file.called}, payload={public_shape_payload}"
        )
    print("[ok] public shape contract guard")

    stale_replace_stdout = io.StringIO()
    stale_replace_config = AgentConfig(
        task_id=safe_task_id("self-test-stale-replace-loop"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
    )
    stale_replace_actions = [
        '{"action":"replace_in_file","path":"/work/project/a.py","old":"old one","new":"new","count":1}',
        '{"action":"replace_in_file","path":"/work/project/a.py","old":"old two","new":"new","count":1}',
        '{"action":"replace_in_file","path":"/work/project/a.py","old":"old three","new":"new","count":1}',
        '{"action":"final","message":"done"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=stale_replace_actions), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": False, "error": "old text not found", "path": "/work/project/a.py"}) as mocked_stale_file, \
            contextlib.redirect_stdout(stale_replace_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        stale_replace_code = run_agent("patch stale file", stale_replace_config)
    stale_replace_payload = json.loads(stale_replace_stdout.getvalue())
    stale_replace_rejections = [
        step for step in stale_replace_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "stale replace_in_file rejected by supervisor"
    ]
    if stale_replace_code != 0 or not stale_replace_rejections or mocked_stale_file.call_count != 1:
        raise AssertionError(
            "stale replace guard failed: "
            f"code={stale_replace_code}, file_calls={mocked_stale_file.call_count}, payload={stale_replace_payload}"
        )
    print("[ok] stale replace_in_file loop guard")

    stale_repair_prompt_stdout = io.StringIO()
    stale_repair_prompt_config = AgentConfig(
        task_id=safe_task_id("self-test-stale-repair-prompt-refresh"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    stale_repair_chat_calls = 0
    stale_repair_prompts: list[str] = []

    def fake_stale_repair_chat(_config, messages, **_kwargs):
        nonlocal stale_repair_chat_calls
        stale_repair_chat_calls += 1
        combined = "\n".join(str(item.get("content", "")) for item in messages if isinstance(item, dict))
        stale_repair_prompts.append(combined)
        if stale_repair_chat_calls == 1:
            return '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}'
        if stale_repair_chat_calls == 2:
            return '{"action":"read_file","path":"/work/project/calc.py","max_bytes":20000}'
        if stale_repair_chat_calls == 3:
            return '{"action":"replace_in_file","path":"/work/project/calc.py","old":"return a - b","new":"return a + b","count":1}'
        return '{"action":"replace_in_file","path":"/work/project/calc.py","old":"return a * b","new":"return a + b","count":1}'

    stale_repair_python_results = [
        {
            "ok": False,
            "returncode": 1,
            "stdout": json.dumps({
                "results": [{"ok": False, "file": "tests/test_calc.py", "test": "test_add"}],
                "source_hints": ["/work/project/calc.py"],
            }),
            "stderr": "",
        },
        {
            "ok": True,
            "returncode": 0,
            "stdout": json.dumps({
                "results": [{"ok": True, "file": "tests/test_calc.py", "test": "test_add"}],
                "source_hints": ["/work/project/calc.py"],
            }),
            "stderr": "",
        },
    ]

    def fake_stale_repair_file(_config, action):
        if action.get("action") == "read_file":
            return {
                "ok": True,
                "path": "/work/project/calc.py",
                "content": "def add(a, b):\n    return a - b\n",
                "size": 32,
            }
        if action.get("old") == "return a - b":
            return {
                "ok": False,
                "error": "old text not found",
                "path": "/work/project/calc.py",
                "current_excerpt": "def add(a, b):\n    return a * b\n",
            }
        return {"ok": True, "path": "/work/project/calc.py", "replaced": 1, "size": 32}

    with mock.patch.object(agent_runner, "chat", side_effect=fake_stale_repair_chat), \
            mock.patch.object(agent_runner, "run_shell", return_value={
                "ok": False,
                "returncode": 1,
                "stdout": "",
                "stderr": "/usr/bin/python3: No module named pytest\n",
            }), \
            mock.patch.object(agent_runner, "python_tool", side_effect=stale_repair_python_results), \
            mock.patch.object(agent_runner, "file_tool", side_effect=fake_stale_repair_file), \
            contextlib.redirect_stdout(stale_repair_prompt_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        stale_repair_prompt_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            stale_repair_prompt_config,
        )
    stale_repair_prompt_payload = json.loads(stale_repair_prompt_stdout.getvalue())
    if stale_repair_prompt_code != 0 or not any("return a * b" in prompt for prompt in stale_repair_prompts[3:]):
        raise AssertionError(
            "stale replace current_excerpt was not reused by SWE repair prompt: "
            f"code={stale_repair_prompt_code}, payload={stale_repair_prompt_payload}, prompts={stale_repair_prompts}"
        )
    print("[ok] stale replace current excerpt refreshes SWE repair prompt")

    swe_diagnostic_stdout = io.StringIO()
    swe_diagnostic_config = AgentConfig(
        task_id=safe_task_id("self-test-swe-diagnostic-before-edit"),
        json_output=True,
        max_steps=6,
        inject_memory=False,
        archive_internal_steps=False,
    )
    first_edit = '{"action":"replace_in_file","path":"/work/project/calc.py","old":"return a - b","new":"return a + b","count":1}'
    swe_diagnostic_actions = [
        first_edit,
        '{"action":"read_file","path":"/work/project/tests/test_calc.py","max_bytes":4000}',
        first_edit,
        '{"action":"final","message":"done"}',
    ]
    swe_file_results = [
        {"ok": True, "path": "/work/project/tests/test_calc.py", "content": "assert add(2, 3) == 5"},
        {"ok": True, "path": "/work/project/calc.py"},
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=swe_diagnostic_actions), \
            mock.patch.object(agent_runner, "file_tool", side_effect=swe_file_results) as mocked_swe_file_tool, \
            contextlib.redirect_stdout(swe_diagnostic_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        swe_diagnostic_code = run_agent("Исправь Python-проект и запусти тесты", swe_diagnostic_config)
    swe_diagnostic_payload = json.loads(swe_diagnostic_stdout.getvalue())
    first_step_result = (swe_diagnostic_payload.get("steps") or [{}])[0].get("result") or {}
    if (
        swe_diagnostic_code != 0
        or first_step_result.get("error") != "swe edit before diagnostic rejected by supervisor"
        or mocked_swe_file_tool.call_count != 2
    ):
        raise AssertionError(
            "SWE pre-edit diagnostic guard failed: "
            f"code={swe_diagnostic_code}, file_tool_calls={mocked_swe_file_tool.call_count}, payload={swe_diagnostic_payload}"
        )
    print("[ok] SWE edits require initial diagnostic")

    swe_test_diagnostic_stdout = io.StringIO()
    swe_test_diagnostic_config = AgentConfig(
        task_id=safe_task_id("self-test-swe-test-diagnostic-before-edit"),
        json_output=True,
        max_steps=6,
        inject_memory=False,
        archive_internal_steps=False,
    )
    test_edit = '{"action":"replace_in_file","path":"/work/project/calc.py","old":"return a - b","new":"return a + b","count":1}'
    swe_test_diagnostic_actions = [
        '{"action":"read_file","path":"/work/project/calc.py","max_bytes":4000}',
        test_edit,
        '{"action":"read_file","path":"/work/project/tests/test_calc.py","max_bytes":4000}',
        test_edit,
        '{"action":"final","message":"done"}',
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=swe_test_diagnostic_actions), \
            mock.patch.object(agent_runner, "file_tool", side_effect=[
                {"ok": True, "path": "/work/project/calc.py", "content": "def add(a,b): return a-b"},
                {"ok": True, "path": "/work/project/tests/test_calc.py", "content": "def test_add(): assert add(2,3)==5"},
                {"ok": True, "path": "/work/project/calc.py"},
            ]) as mocked_swe_test_file_tool, \
            contextlib.redirect_stdout(swe_test_diagnostic_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        swe_test_diagnostic_code = run_agent("Исправь Python-проект и запусти pytest", swe_test_diagnostic_config)
    swe_test_diagnostic_payload = json.loads(swe_test_diagnostic_stdout.getvalue())
    test_rejections = [
        step for step in swe_test_diagnostic_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "swe edit before test diagnostic rejected by supervisor"
    ]
    if swe_test_diagnostic_code != 0 or not test_rejections or mocked_swe_test_file_tool.call_count != 3:
        raise AssertionError(
            "SWE test diagnostic guard failed: "
            f"code={swe_test_diagnostic_code}, file_calls={mocked_swe_test_file_tool.call_count}, payload={swe_test_diagnostic_payload}"
        )
    print("[ok] SWE edits require test diagnostic when tests are requested")

    pytest_fallback_stdout = io.StringIO()
    pytest_fallback_config = AgentConfig(
        task_id=safe_task_id("self-test-pytest-fallback"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )

    def fake_pytest_fallback(_config: AgentConfig, action: dict) -> dict:
        if action.get("cwd") != "/work/project" or "test_files" not in action.get("code", ""):
            raise AssertionError(f"bad pytest fallback action: {action}")
        return {
            "ok": False,
            "returncode": 1,
            "stdout": '{"results":[{"ok":false,"file":"tests/test_calc.py","test":"test_add"}],"failures":[{"test":"test_add"}]}',
            "stderr": "",
        }

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"final","message":"done"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }) as mocked_pytest_shell, mock.patch.object(agent_runner, "python_tool", side_effect=fake_pytest_fallback) as mocked_pytest_python, \
            contextlib.redirect_stdout(pytest_fallback_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        pytest_fallback_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            pytest_fallback_config,
        )
    pytest_fallback_payload = json.loads(pytest_fallback_stdout.getvalue())
    first_pytest_result = (pytest_fallback_payload.get("steps") or [{}])[0].get("result") or {}
    if (
        pytest_fallback_code != 0
        or mocked_pytest_shell.call_count != 1
        or mocked_pytest_python.call_count != 1
        or first_pytest_result.get("fallback") != "simple_pytest_runner"
        or first_pytest_result.get("failing_tests") != ["tests/test_calc.py::test_add"]
        or "Do not ignore" not in str(first_pytest_result.get("supervisor_instruction") or "")
    ):
        raise AssertionError(
            "pytest fallback runner was not used: "
            f"code={pytest_fallback_code}, shell_calls={mocked_pytest_shell.call_count}, "
            f"python_calls={mocked_pytest_python.call_count}, payload={pytest_fallback_payload}"
        )
    print("[ok] pytest unavailable falls back to simple runner")

    python_pytest_fallback_stdout = io.StringIO()
    python_pytest_fallback_config = AgentConfig(
        task_id=safe_task_id("self-test-python-pytest-fallback"),
        json_output=True,
        max_steps=1,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    python_pytest_calls = 0

    def fake_python_pytest_fallback(_config: AgentConfig, action: dict) -> dict:
        nonlocal python_pytest_calls
        python_pytest_calls += 1
        if python_pytest_calls == 1:
            return {
                "ok": False,
                "returncode": 1,
                "stdout": "",
                "stderr": "ModuleNotFoundError: No module named 'pytest'\n",
            }
        return {
            "ok": False,
            "returncode": 1,
            "stdout": json.dumps({
                "results": [{"ok": False, "file": "tests/test_calc.py", "test": "test_add"}],
                "source_hints": ["/work/project/calc.py"],
            }),
            "stderr": "",
        }

    with mock.patch.object(agent_runner, "chat", return_value='{"action":"python","cwd":"/work/project","code":"import pytest; pytest.main([\\"-q\\"])","timeout":60}'), \
            mock.patch.object(agent_runner, "python_tool", side_effect=fake_python_pytest_fallback), \
            contextlib.redirect_stdout(python_pytest_fallback_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        python_pytest_fallback_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            python_pytest_fallback_config,
        )
    python_pytest_fallback_payload = json.loads(python_pytest_fallback_stdout.getvalue())
    python_pytest_result = (python_pytest_fallback_payload.get("steps") or [{}])[0].get("result") or {}
    if (
        python_pytest_fallback_code != 2
        or python_pytest_calls != 2
        or python_pytest_result.get("fallback") != "simple_pytest_runner"
        or python_pytest_result.get("failing_tests") != ["tests/test_calc.py::test_add"]
        or not python_pytest_result.get("original_python")
    ):
        raise AssertionError(
            "python pytest fallback runner was not used: "
            f"code={python_pytest_fallback_code}, calls={python_pytest_calls}, payload={python_pytest_fallback_payload}"
        )
    print("[ok] python pytest unavailable falls back to simple runner")

    enriched_source_hints = agent_runner.enrich_pytest_fallback_result({
        "ok": False,
        "stdout": json.dumps({
            "results": [{"ok": False, "file": "tests/test_textkit.py", "test": "test_slugify_lowercase"}],
            "failures": [{"test": "test_slugify_lowercase"}],
            "source_hints": ["/work/project/textkit/normalize.py"],
        }),
    })
    if (
        enriched_source_hints.get("candidate_source_paths") != ["/work/project/textkit/normalize.py"]
        or "candidate_source_paths" not in str(enriched_source_hints.get("supervisor_instruction") or "")
    ):
        raise AssertionError(f"pytest fallback source hints missing from model result: {enriched_source_hints}")
    print("[ok] pytest fallback includes source hints")

    compacted_fallback_result = agent_runner.result_for_model("shell", {
        "ok": False,
        "stdout": "",
        "stderr": "",
        "supervisor_instruction": "Specific fallback instruction.",
    }, AgentConfig())
    compacted_fallback_instruction = str(compacted_fallback_result.get("supervisor_instruction") or "")
    if "Specific fallback instruction." not in compacted_fallback_instruction or "The shell command failed" not in compacted_fallback_instruction:
        raise AssertionError(f"specific shell supervisor instruction was not preserved: {compacted_fallback_result}")
    print("[ok] specific shell supervisor instruction preserved")

    if not agent_runner.pytest_unavailable_output("/usr/bin/bash: line 1: pytest: command not found\n"):
        raise AssertionError("pytest command-not-found output was not recognized")
    print("[ok] pytest command-not-found recognized")

    regression_stdout = io.StringIO()
    regression_config = AgentConfig(
        task_id=safe_task_id("self-test-pytest-regression"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    regression_fallback_calls = 0

    def fake_regression_fallback(_config: AgentConfig, action: dict) -> dict:
        nonlocal regression_fallback_calls
        regression_fallback_calls += 1
        if regression_fallback_calls == 1:
            stdout = (
                '{"results":['
                '{"ok":true,"file":"tests/test_calc.py","test":"test_old"},'
                '{"ok":false,"file":"tests/test_calc.py","test":"test_new"}'
                ']}'
            )
        elif regression_fallback_calls == 2:
            stdout = (
                '{"results":['
                '{"ok":false,"file":"tests/test_calc.py","test":"test_old"},'
                '{"ok":true,"file":"tests/test_calc.py","test":"test_new"}'
                ']}'
            )
        else:
            stdout = (
                '{"results":['
                '{"ok":true,"file":"tests/test_calc.py","test":"test_old"},'
                '{"ok":true,"file":"tests/test_calc.py","test":"test_new"}'
                ']}'
            )
        return {"ok": regression_fallback_calls > 1, "returncode": 0 if regression_fallback_calls > 1 else 1, "stdout": stdout, "stderr": ""}

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"replace_in_file","path":"/work/project/calc.py","old":"old","new":"new","count":1}',
            '{"action":"replace_in_file","path":"/work/project/calc.py","old":"new","new":"fixed","count":1}',
            '{"action":"final","message":"done"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }) as mocked_regression_shell, mock.patch.object(agent_runner, "python_tool", side_effect=fake_regression_fallback), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/project/calc.py"}), \
            contextlib.redirect_stdout(regression_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        regression_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            regression_config,
        )
    regression_payload = json.loads(regression_stdout.getvalue())
    regression_results = [
        (step.get("result") or {})
        for step in regression_payload.get("steps", [])
        if (step.get("result") or {}).get("fallback") == "simple_pytest_runner"
    ]
    if (
        regression_code != 0
        or len(regression_results) != 3
        or mocked_regression_shell.call_count != 1
        or regression_results[1].get("regression_tests") != ["tests/test_calc.py::test_old"]
        or regression_results[-1].get("failing_tests")
    ):
        raise AssertionError(
            "pytest regression tracking failed: "
            f"code={regression_code}, shell_calls={mocked_regression_shell.call_count}, payload={regression_payload}"
        )
    print("[ok] pytest regression tests tracked")

    protected_edit_stdout = io.StringIO()
    protected_edit_config = AgentConfig(
        task_id=safe_task_id("self-test-protected-passing-edit"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )

    def fake_protected_tests(_config: AgentConfig, action: dict) -> dict:
        return {
            "ok": False,
            "returncode": 1,
            "stdout": '{"results":[{"ok":true,"file":"tests/test_calc.py","test":"test_normalize_title"},{"ok":false,"file":"tests/test_calc.py","test":"test_slugify_lowercase"}]}',
            "stderr": "",
        }

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"replace_in_file","path":"/work/project/calc.py","old":"def normalize_title(value):\\n    return value.title()","new":"def normalize_title(value):\\n    return value.lower()","count":1}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_protected_tests), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/project/calc.py"}), \
            contextlib.redirect_stdout(protected_edit_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        protected_edit_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            protected_edit_config,
        )
    protected_edit_payload = json.loads(protected_edit_stdout.getvalue())
    protected_edit_errors = [
        (step.get("result") or {}).get("error")
        for step in protected_edit_payload.get("steps", [])
    ]
    if protected_edit_code != 0 or "swe passing-test edit rejected by supervisor" not in protected_edit_errors:
        raise AssertionError(f"passing-test edit guard failed: code={protected_edit_code}, payload={protected_edit_payload}")
    print("[ok] passing-test edit guard")

    test_file_edit_stdout = io.StringIO()
    test_file_edit_config = AgentConfig(
        task_id=safe_task_id("self-test-test-file-edit-before-source-fix"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )

    def fake_test_file_edit_failures(_config: AgentConfig, action: dict) -> dict:
        return {
            "ok": False,
            "returncode": 1,
            "stdout": '{"results":[{"ok":false,"file":"tests/test_calc.py","test":"test_slugify"}]}',
            "stderr": "",
        }

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"write_file","path":"/work/project/test_calc.py","content":"def test_new():\\n    assert True\\n"}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_test_file_edit_failures), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/project/test_calc.py"}), \
            contextlib.redirect_stdout(test_file_edit_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        test_file_edit_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            test_file_edit_config,
        )
    test_file_edit_payload = json.loads(test_file_edit_stdout.getvalue())
    test_file_edit_errors = [
        (step.get("result") or {}).get("error")
        for step in test_file_edit_payload.get("steps", [])
    ]
    if test_file_edit_code != 0 or "swe test-file edit before source fix rejected by supervisor" not in test_file_edit_errors:
        raise AssertionError(f"test-file edit guard failed: code={test_file_edit_code}, payload={test_file_edit_payload}")
    print("[ok] test-file edit before source fix guard")

    repeated_test_stdout = io.StringIO()
    repeated_test_config = AgentConfig(
        task_id=safe_task_id("self-test-repeated-failing-test"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )

    def fake_repeated_failing_tests(_config: AgentConfig, action: dict) -> dict:
        return {
            "ok": False,
            "returncode": 1,
            "stdout": '{"results":[{"ok":true,"file":"tests/test_calc.py","test":"test_old"},{"ok":false,"file":"tests/test_calc.py","test":"test_new"}]}',
            "stderr": "",
        }

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_repeated_failing_tests), \
            contextlib.redirect_stdout(repeated_test_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        repeated_test_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            repeated_test_config,
        )
    repeated_test_payload = json.loads(repeated_test_stdout.getvalue())
    repeated_test_errors = [
        (step.get("result") or {}).get("error")
        for step in repeated_test_payload.get("steps", [])
    ]
    if repeated_test_code != 0 or "swe repeated failing test diagnostic rejected by supervisor" not in repeated_test_errors:
        raise AssertionError(f"repeated failing test guard failed: code={repeated_test_code}, payload={repeated_test_payload}")
    print("[ok] repeated failing test diagnostic guard")

    repeated_test_candidates_stdout = io.StringIO()
    repeated_test_candidates_config = AgentConfig(
        task_id=safe_task_id("self-test-repeated-failing-test-candidates"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"list_files","path":"/work/project","max_depth":2,"limit":100}',
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_repeated_failing_tests), \
            mock.patch.object(agent_runner, "file_tool", return_value={
                "ok": True,
                "items": [
                    {"path": "/work/project/tests/test_calc.py", "type": "file"},
                    {"path": "/work/project/calc.py", "type": "file"},
                    {"path": "/work/project/__init__.py", "type": "file"},
                    {"path": "/work/project/__pycache__/calc.cpython-312.pyc", "type": "file"},
                ],
            }), \
            contextlib.redirect_stdout(repeated_test_candidates_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        repeated_test_candidates_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            repeated_test_candidates_config,
        )
    repeated_test_candidates_payload = json.loads(repeated_test_candidates_stdout.getvalue())
    candidate_results = [
        step.get("result") or {}
        for step in repeated_test_candidates_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "swe repeated failing test diagnostic rejected by supervisor"
    ]
    if (
        repeated_test_candidates_code != 0
        or not candidate_results
        or "/work/project/calc.py" not in candidate_results[-1].get("candidate_source_paths", [])
        or "/work/project/tests/test_calc.py" in candidate_results[-1].get("candidate_source_paths", [])
    ):
        raise AssertionError(f"repeated failing test guard omitted source candidates: {repeated_test_candidates_payload}")
    print("[ok] repeated failing test guard includes source candidates")

    source_hint_read_stdout = io.StringIO()
    source_hint_read_config = AgentConfig(
        task_id=safe_task_id("self-test-source-hint-read-nudge"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )

    def fake_source_hint_failing_tests(_config: AgentConfig, action: dict) -> dict:
        return {
            "ok": False,
            "returncode": 1,
            "stdout": json.dumps({
                "results": [{"ok": False, "file": "tests/test_calc.py", "test": "test_add"}],
                "source_hints": ["/work/project/calc.py"],
            }),
            "stderr": "",
        }

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"read_file","path":"/work/project/calc.py","max_bytes":20000}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_source_hint_failing_tests), \
            mock.patch.object(agent_runner, "file_tool", return_value={
                "ok": True,
                "path": "/work/project/calc.py",
                "content": "def add(a, b):\n    return a - b\n",
                "size": 32,
            }), \
            contextlib.redirect_stdout(source_hint_read_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        source_hint_read_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            source_hint_read_config,
        )
    source_hint_read_payload = json.loads(source_hint_read_stdout.getvalue())
    read_results = [
        step.get("result") or {}
        for step in source_hint_read_payload.get("steps", [])
        if (step.get("action") or {}).get("action") == "read_file"
    ]
    if (
        not read_results
        or read_results[-1].get("failing_tests") != ["tests/test_calc.py::test_add"]
        or read_results[-1].get("candidate_source_paths") != ["/work/project/calc.py"]
        or "narrow write_file/replace_in_file edit" not in str(read_results[-1].get("supervisor_instruction") or "")
    ):
        raise AssertionError(f"source hint read nudge failed: code={source_hint_read_code}, payload={source_hint_read_payload}")
    print("[ok] source hint read nudges immediate edit")

    swe_repair_mode_stdout = io.StringIO()
    swe_repair_mode_config = AgentConfig(
        task_id=safe_task_id("self-test-swe-repair-mode"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    swe_repair_python_calls = 0

    def fake_swe_repair_pytest(_config: AgentConfig, action: dict) -> dict:
        nonlocal swe_repair_python_calls
        swe_repair_python_calls += 1
        if swe_repair_python_calls == 1:
            return {
                "ok": False,
                "returncode": 1,
                "stdout": json.dumps({
                    "results": [{"ok": False, "file": "tests/test_calc.py", "test": "test_add"}],
                    "source_hints": ["/work/project/calc.py"],
                }),
                "stderr": "",
            }
        return {
            "ok": True,
            "returncode": 0,
            "stdout": json.dumps({
                "results": [{"ok": True, "file": "tests/test_calc.py", "test": "test_add"}],
                "source_hints": ["/work/project/calc.py"],
            }),
            "stderr": "",
        }

    def fake_swe_repair_file(_config: AgentConfig, action: dict) -> dict:
        if action.get("action") == "read_file":
            return {
                "ok": True,
                "path": "/work/project/calc.py",
                "content": "def add(a, b):\n    return a - b\n",
                "size": 32,
            }
        if action.get("action") == "replace_in_file":
            return {"ok": True, "path": "/work/project/calc.py", "replaced": 1, "size": 32}
        raise AssertionError(f"unexpected file action in repair mode test: {action}")

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"read_file","path":"/work/project/calc.py","max_bytes":20000}',
            '{"action":"replace_in_file","path":"/work/project/calc.py","old":"def add(a, b):\\n    return a - b","new":"def add(a, b):\\n    return a + b","count":1}',
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
    ]) as mocked_repair_chat, mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_swe_repair_pytest), \
            mock.patch.object(agent_runner, "file_tool", side_effect=fake_swe_repair_file), \
            contextlib.redirect_stdout(swe_repair_mode_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        swe_repair_mode_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            swe_repair_mode_config,
        )
    swe_repair_mode_payload = json.loads(swe_repair_mode_stdout.getvalue())
    repair_chat_messages = mocked_repair_chat.call_args_list[2].args[1]
    if (
        swe_repair_mode_code != 0
        or repair_chat_messages[0].get("content") != agent_runner.SWE_REPAIR_SYSTEM_PROMPT
        or "source_excerpt" not in repair_chat_messages[1].get("content", "")
        or not any(step.get("action", {}).get("action") == "replace_in_file" for step in swe_repair_mode_payload.get("steps", []))
        or not swe_repair_mode_payload.get("ok")
    ):
        raise AssertionError(
            "SWE repair mode did not run through narrow repair prompt and verification: "
            f"code={swe_repair_mode_code}, payload={swe_repair_mode_payload}, repair_messages={repair_chat_messages}"
        )
    print("[ok] SWE repair mode uses narrow prompt before edit")

    extra_source_read_stdout = io.StringIO()
    extra_source_read_config = AgentConfig(
        task_id=safe_task_id("self-test-extra-source-read-before-edit"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )

    def fake_slugify_failing_tests(_config: AgentConfig, action: dict) -> dict:
        return {
            "ok": False,
            "returncode": 1,
            "stdout": '{"results":[{"ok":false,"file":"tests/test_textkit.py","test":"test_slugify_lowercase"}]}',
            "stderr": "",
        }

    def fake_extra_source_file(_config: AgentConfig, action: dict) -> dict:
        path = str(action.get("path") or "")
        if path.endswith("normalize.py"):
            return {"ok": True, "path": path, "content": "def slugify(value):\n    return value\n", "size": 32}
        return {"ok": True, "path": path, "content": "def main(): pass\n", "size": 16}

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"read_file","path":"/work/project/textkit/normalize.py","max_bytes":20000}',
            '{"action":"read_file","path":"/work/project/textkit/cli.py","max_bytes":20000}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_slugify_failing_tests), \
            mock.patch.object(agent_runner, "file_tool", side_effect=fake_extra_source_file), \
            contextlib.redirect_stdout(extra_source_read_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        extra_source_read_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            extra_source_read_config,
        )
    extra_source_read_payload = json.loads(extra_source_read_stdout.getvalue())
    extra_source_results = [
        step.get("result") or {}
        for step in extra_source_read_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "swe extra source read before edit rejected by supervisor"
    ]
    if (
        not extra_source_results
        or "/work/project/textkit/normalize.py" not in extra_source_results[-1].get("matching_source_paths", [])
    ):
        raise AssertionError(f"extra source read guard failed: {extra_source_read_payload}")
    print("[ok] extra source read before edit guard")

    repeated_assert_stdout = io.StringIO()
    repeated_assert_config = AgentConfig(
        task_id=safe_task_id("self-test-repeated-assert-before-edit"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"python","cwd":"/work/project","code":"assert calc() == 2","timeout":60}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_repeated_failing_tests), \
            contextlib.redirect_stdout(repeated_assert_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        repeated_assert_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            repeated_assert_config,
        )
    repeated_assert_payload = json.loads(repeated_assert_stdout.getvalue())
    repeated_assert_errors = [
        (step.get("result") or {}).get("error")
        for step in repeated_assert_payload.get("steps", [])
    ]
    if repeated_assert_code != 0 or "swe repeated failing test diagnostic rejected by supervisor" not in repeated_assert_errors:
        raise AssertionError(f"repeated assert-before-edit guard failed: code={repeated_assert_code}, payload={repeated_assert_payload}")
    print("[ok] repeated assert before edit guard")

    failing_test_read_stdout = io.StringIO()
    failing_test_read_config = AgentConfig(
        task_id=safe_task_id("self-test-failing-test-read-allowed"),
        json_output=True,
        max_steps=4,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"read_file","path":"/work/project/tests/test_calc.py","max_bytes":20000,"offset":0}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_repeated_failing_tests), \
            mock.patch.object(agent_runner, "file_tool", return_value={
                "ok": True,
                "path": "/work/project/tests/test_calc.py",
                "content": "def test_new(): assert calc() == 2",
                "size": 34,
            }), \
            contextlib.redirect_stdout(failing_test_read_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        failing_test_read_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            failing_test_read_config,
        )
    failing_test_read_payload = json.loads(failing_test_read_stdout.getvalue())
    failing_test_read_errors = [
        (step.get("result") or {}).get("error")
        for step in failing_test_read_payload.get("steps", [])
    ]
    if "swe repeated failing test diagnostic rejected by supervisor" in failing_test_read_errors:
        raise AssertionError(f"reading a failing test file should be allowed: {failing_test_read_payload}")
    if failing_test_read_code != 0:
        raise AssertionError(f"failing test read scenario should reach final: code={failing_test_read_code}, payload={failing_test_read_payload}")
    print("[ok] failing test file read allowed")

    repeated_failing_read_stdout = io.StringIO()
    repeated_failing_read_config = AgentConfig(
        task_id=safe_task_id("self-test-repeated-failing-file-read"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"read_file","path":"/work/project/calc.py","max_bytes":20000,"offset":0}',
            '{"action":"read_file","path":"/work/project/calc.py","max_bytes":20000,"offset":0}',
            '{"action":"read_file","path":"/work/project/calc.py","max_bytes":20000,"offset":0}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_repeated_failing_tests), \
            mock.patch.object(agent_runner, "file_tool", return_value={
                "ok": True,
                "path": "/work/project/calc.py",
                "content": "def calc(): return 1",
                "size": 20,
            }), \
            contextlib.redirect_stdout(repeated_failing_read_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        repeated_failing_read_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            repeated_failing_read_config,
        )
    repeated_failing_read_payload = json.loads(repeated_failing_read_stdout.getvalue())
    repeated_failing_read_errors = [
        (step.get("result") or {}).get("error")
        for step in repeated_failing_read_payload.get("steps", [])
    ]
    if repeated_failing_read_code != 0 or "swe repeated failing-test file read rejected by supervisor" not in repeated_failing_read_errors:
        raise AssertionError(
            f"repeated failing-test file read guard failed: code={repeated_failing_read_code}, payload={repeated_failing_read_payload}"
        )
    if "repeated identical action rejected by supervisor" in repeated_failing_read_errors:
        raise AssertionError(f"SWE repeated file read guard should take priority over generic identical guard: {repeated_failing_read_payload}")
    repeated_read_results = [
        step.get("result") or {}
        for step in repeated_failing_read_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "swe repeated failing-test file read rejected by supervisor"
    ]
    if not repeated_read_results or "def calc()" not in str(repeated_read_results[0].get("last_read_excerpt") or ""):
        raise AssertionError(f"repeated failing-test read guard omitted last read excerpt: {repeated_failing_read_payload}")
    print("[ok] repeated failing-test file read guard")

    reread_after_tests_stdout = io.StringIO()
    reread_after_tests_config = AgentConfig(
        task_id=safe_task_id("self-test-reread-after-failing-tests"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"read_file","path":"/work/project/calc.py","max_bytes":20000}',
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"read_file","path":"/work/project/calc.py","max_bytes":20000,"offset":0}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_repeated_failing_tests), \
            mock.patch.object(agent_runner, "file_tool", return_value={
                "ok": True,
                "path": "/work/project/calc.py",
                "content": "def calc(): return 1",
                "size": 20,
            }), \
            contextlib.redirect_stdout(reread_after_tests_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        reread_after_tests_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            reread_after_tests_config,
        )
    reread_after_tests_payload = json.loads(reread_after_tests_stdout.getvalue())
    reread_after_tests_errors = [
        (step.get("result") or {}).get("error")
        for step in reread_after_tests_payload.get("steps", [])
    ]
    if reread_after_tests_code != 0 or "swe repeated failing-test file read rejected by supervisor" not in reread_after_tests_errors:
        raise AssertionError(f"reread after failing tests guard failed: code={reread_after_tests_code}, payload={reread_after_tests_payload}")
    print("[ok] reread after failing tests guard")

    caught_assert_stdout = io.StringIO()
    caught_assert_config = AgentConfig(
        task_id=safe_task_id("self-test-caught-assertion-verification"),
        json_output=True,
        max_steps=3,
        inject_memory=False,
        archive_internal_steps=False,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"python","cwd":"/work/project","code":"try:\\n    assert calc() == 2\\nexcept AssertionError:\\n    print(\\"AssertionError\\")","timeout":60}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "python_tool", return_value={
            "ok": True,
            "returncode": 0,
            "stdout": "AssertionError\n",
            "stderr": "",
    }), contextlib.redirect_stdout(caught_assert_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        caught_assert_code = run_agent("Исправь Python-проект и запусти тест", caught_assert_config)
    caught_assert_payload = json.loads(caught_assert_stdout.getvalue())
    caught_assert_results = [step.get("result") or {} for step in caught_assert_payload.get("steps", [])]
    if caught_assert_code != 0 or not caught_assert_results or caught_assert_results[0].get("ok") is not False:
        raise AssertionError(f"caught AssertionError verification should be failed: code={caught_assert_code}, payload={caught_assert_payload}")
    if "printed AssertionError" not in caught_assert_results[0].get("supervisor_instruction", ""):
        raise AssertionError(f"caught AssertionError guidance missing: {caught_assert_payload}")
    print("[ok] caught AssertionError verification fails")

    swe_auto_final_stdout = io.StringIO()
    swe_auto_final_config = AgentConfig(
        task_id=safe_task_id("self-test-swe-auto-final"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    swe_auto_final_python_results = [
        {
            "ok": False,
            "returncode": 1,
            "stdout": '{"results":[{"ok":false,"file":"tests/test_calc.py","test":"test_new"}]}',
            "stderr": "",
        },
        {
            "ok": True,
            "returncode": 0,
            "stdout": '{"results":[{"ok":true,"file":"tests/test_calc.py","test":"test_new"}]}',
            "stderr": "",
            "passing_tests": ["tests/test_calc.py::test_new"],
            "failing_tests": [],
        },
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"replace_in_file","path":"/work/project/calc.py","old":"return 1","new":"return 2"}',
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"final","message":"should not be needed"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=swe_auto_final_python_results), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/project/calc.py"}), \
            contextlib.redirect_stdout(swe_auto_final_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        swe_auto_final_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            swe_auto_final_config,
        )
    swe_auto_final_payload = json.loads(swe_auto_final_stdout.getvalue())
    if swe_auto_final_code != 0 or swe_auto_final_payload.get("message") != "Готово: code edit verified by tests/fallback.":
        raise AssertionError(f"SWE auto-final after passing tests failed: code={swe_auto_final_code}, payload={swe_auto_final_payload}")
    if len(swe_auto_final_payload.get("steps", [])) != 3:
        raise AssertionError(f"SWE auto-final should not consume a final model step: {swe_auto_final_payload}")
    print("[ok] SWE auto-final after passing tests")

    swe_final_requires_full_verify_stdout = io.StringIO()
    swe_final_requires_full_verify_config = AgentConfig(
        task_id=safe_task_id("self-test-swe-final-requires-full-verify"),
        json_output=True,
        max_steps=6,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    swe_final_requires_full_verify_python_results = [
        {
            "ok": False,
            "returncode": 1,
            "stdout": '{"results":[{"ok":false,"file":"tests/test_calc.py","test":"test_new"}]}',
            "stderr": "",
        },
        {
            "ok": True,
            "returncode": 0,
            "stdout": "manual check passed\n",
            "stderr": "",
        },
        {
            "ok": True,
            "returncode": 0,
            "stdout": '{"results":[{"ok":true,"file":"tests/test_calc.py","test":"test_new"}]}',
            "stderr": "",
            "passing_tests": ["tests/test_calc.py::test_new"],
            "failing_tests": [],
        },
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"replace_in_file","path":"/work/project/calc.py","old":"return 1","new":"return 2"}',
            '{"action":"python","cwd":"/work/project","code":"assert calc() == 2","timeout":60}',
            '{"action":"final","message":"premature"}',
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"final","message":"should not be needed"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=swe_final_requires_full_verify_python_results), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/project/calc.py"}), \
            contextlib.redirect_stdout(swe_final_requires_full_verify_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        swe_final_requires_full_verify_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            swe_final_requires_full_verify_config,
        )
    swe_final_requires_full_verify_payload = json.loads(swe_final_requires_full_verify_stdout.getvalue())
    if swe_final_requires_full_verify_code != 0 or swe_final_requires_full_verify_payload.get("message") != "Готово: code edit verified by tests/fallback.":
        raise AssertionError(
            "SWE final after focused check should require full verification: "
            f"code={swe_final_requires_full_verify_code}, payload={swe_final_requires_full_verify_payload}"
        )
    if len(swe_final_requires_full_verify_payload.get("steps", [])) != 4:
        raise AssertionError(f"SWE final rejection should force one full verification step: {swe_final_requires_full_verify_payload}")
    focused_verify_result = (swe_final_requires_full_verify_payload.get("steps") or [{}, {}, {}])[2].get("result") or {}
    if focused_verify_result.get("error") != "swe focused verification after failing tests rejected by supervisor":
        raise AssertionError(f"SWE focused verification after failing tests was not rejected: {swe_final_requires_full_verify_payload}")
    print("[ok] SWE final requires full verification after edit")

    swe_cli_requires_verify_stdout = io.StringIO()
    swe_cli_requires_verify_config = AgentConfig(
        task_id=safe_task_id("self-test-swe-cli-requires-verify"),
        json_output=True,
        max_steps=7,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    swe_cli_requires_verify_events: list[dict] = []
    swe_cli_python_results = [
        {
            "ok": False,
            "returncode": 1,
            "stdout": '{"results":[{"ok":false,"file":"tests/test_core.py","test":"test_core"}]}',
            "stderr": "",
            "passing_tests": [],
            "failing_tests": ["tests/test_core.py::test_core"],
        },
        {
            "ok": True,
            "returncode": 0,
            "stdout": '{"results":[{"ok":true,"file":"tests/test_core.py","test":"test_core"}]}',
            "stderr": "",
            "passing_tests": ["tests/test_core.py::test_core"],
            "failing_tests": [],
        },
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"python","cwd":"/work/project","code":"assert core_ok()","timeout":60}',
            '{"action":"replace_in_file","path":"/work/project/core.py","old":"return 1","new":"return 2"}',
            '{"action":"python","cwd":"/work/project","code":"assert core_ok()","timeout":60}',
            '{"action":"read_file","path":"/work/project/core.py","max_bytes":2000,"offset":0}',
            '{"action":"final","message":"premature"}',
            '{"action":"shell","cmd":"cd /work/project && python3 -m package.cli data.csv | python3 -c \\"import sys,json; json.load(sys.stdin)\\"","timeout":60}',
            '{"action":"final","message":"should not be needed"}',
    ]), mock.patch.object(agent_runner, "python_tool", side_effect=swe_cli_python_results), \
            mock.patch.object(agent_runner, "run_shell", return_value={"ok": True, "returncode": 0, "stdout": "", "stderr": ""}), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/project/core.py"}), \
            contextlib.redirect_stdout(swe_cli_requires_verify_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        swe_cli_requires_verify_code = run_agent(
            "Исправь Python-проект. CLI должен печатать валидный JSON; проверь python3 -m package.cli data.csv.",
            swe_cli_requires_verify_config,
            event_sink=swe_cli_requires_verify_events.append,
        )
    swe_cli_requires_verify_payload = json.loads(swe_cli_requires_verify_stdout.getvalue())
    if swe_cli_requires_verify_code != 0 or swe_cli_requires_verify_payload.get("message") != "Готово: code edit verified by tests/fallback.":
        raise AssertionError(
            "SWE CLI task should require post-edit CLI verification before final: "
            f"code={swe_cli_requires_verify_code}, payload={swe_cli_requires_verify_payload}"
        )
    if not any(event.get("code") == "final_swe_cli_verification_required" for event in swe_cli_requires_verify_events):
        raise AssertionError(f"SWE CLI final rejection warning missing: {swe_cli_requires_verify_payload}")
    cli_required_results = [step.get("result") or {} for step in swe_cli_requires_verify_payload.get("steps", [])]
    if not any(result.get("error") == "swe cli verification required by supervisor" for result in cli_required_results):
        raise AssertionError(f"SWE CLI non-CLI action was not rejected before CLI verification: {swe_cli_requires_verify_payload}")
    if len(swe_cli_requires_verify_payload.get("steps", [])) != 5:
        raise AssertionError(f"SWE CLI verification should run after rejected final: {swe_cli_requires_verify_payload}")
    print("[ok] SWE final requires CLI verification after edit")

    swe_resume_cli_stdout = io.StringIO()
    swe_resume_cli_config = AgentConfig(
        task_id=safe_task_id("self-test-swe-resume-cli-requires-verify"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    swe_resume_cli_events: list[dict] = []
    resume_cli_task = (
        "Продолжи выполнение той же задачи по task journal.\n\n"
        "Resume context from previous agent task journal:\n"
        "[{\"type\":\"start\",\"task\":\"Исправь Python-проект. CLI должен печатать валидный JSON; "
        "проверь python3 -m package.cli data.csv.\"},"
        "{\"type\":\"tool_result\",\"action\":\"shell\",\"result\":{\"ok\":true,\"passing_tests\":[\"tests/test_core.py::test_core\"],\"failing_tests\":[]}}]"
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"python","cwd":"/work/project","code":"import scheduler.core as core; print(dir(core))","timeout":60}',
            '{"action":"final","message":"premature from resume"}',
            '{"action":"shell","cmd":"cd /work/project && python3 -m package.cli data.csv | python3 -c \\"import sys,json; json.load(sys.stdin)\\"","timeout":60}',
            '{"action":"final","message":"done after cli"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={"ok": True, "returncode": 0, "stdout": "", "stderr": ""}), \
            mock.patch.object(agent_runner, "python_tool", return_value={"ok": True, "stdout": "should not run"}) as mocked_resume_cli_python, \
            contextlib.redirect_stdout(swe_resume_cli_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        swe_resume_cli_code = run_agent(resume_cli_task, swe_resume_cli_config, event_sink=swe_resume_cli_events.append)
    swe_resume_cli_payload = json.loads(swe_resume_cli_stdout.getvalue())
    if swe_resume_cli_code != 0 or swe_resume_cli_payload.get("message") != "done after cli":
        raise AssertionError(f"SWE resume CLI verification did not recover after CLI command: {swe_resume_cli_payload}")
    if not any(event.get("code") == "final_swe_cli_verification_required" for event in swe_resume_cli_events):
        raise AssertionError(f"SWE resume final was not blocked before CLI verification: {swe_resume_cli_payload}")
    resume_cli_results = [step.get("result") or {} for step in swe_resume_cli_payload.get("steps", [])]
    if not any(result.get("error") == "swe cli verification required by supervisor" for result in resume_cli_results):
        raise AssertionError(f"SWE resume non-CLI action was not blocked before CLI verification: {swe_resume_cli_payload}")
    if mocked_resume_cli_python.called:
        raise AssertionError(f"SWE resume non-CLI python action reached python_tool: {swe_resume_cli_payload}")
    print("[ok] SWE resume final requires CLI verification")

    swe_resume_failing_cli_stdout = io.StringIO()
    swe_resume_failing_cli_config = AgentConfig(
        task_id=safe_task_id("self-test-swe-resume-failing-cli-allows-edit"),
        json_output=True,
        max_steps=2,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    resume_failing_cli_task = (
        "Продолжи выполнение той же задачи по task journal.\n\n"
        "Resume context from previous agent task journal:\n"
        "[{\"type\":\"start\",\"task\":\"Исправь Python-проект. CLI должен печатать валидный JSON; "
        "проверь python3 -m package.cli data.csv.\"},"
        "{\"type\":\"tool_result\",\"action\":\"shell\",\"result\":{\"ok\":false,"
        "\"passing_tests\":[\"tests/test_core.py::test_parse\"],"
        "\"failing_tests\":[\"tests/test_core.py::test_schedule\"],"
        "\"stdout\":\"TypeError: unsupported operand type(s) for +: 'datetime.datetime' and 'int'\"}}]"
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"replace_in_file","path":"/work/project/core.py","old":"start + minutes","new":"start + timedelta(minutes=minutes)"}',
            '{"action":"final","message":"blocked until tests"}',
    ]), mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/project/core.py"}) as mocked_resume_failing_file, \
            contextlib.redirect_stdout(swe_resume_failing_cli_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        run_agent(resume_failing_cli_task, swe_resume_failing_cli_config)
    swe_resume_failing_cli_payload = json.loads(swe_resume_failing_cli_stdout.getvalue())
    resume_failing_cli_results = [step.get("result") or {} for step in swe_resume_failing_cli_payload.get("steps", [])]
    if not mocked_resume_failing_file.called:
        raise AssertionError(f"SWE resume with failing tests should allow source edit before CLI verification: {swe_resume_failing_cli_payload}")
    if any(result.get("error") == "swe cli verification required by supervisor" for result in resume_failing_cli_results):
        raise AssertionError(f"SWE resume CLI gate blocked a failing-test repair: {swe_resume_failing_cli_payload}")
    print("[ok] SWE resume failing tests allow repair before CLI verification")

    failing_stall_stdout = io.StringIO()
    failing_stall_config = AgentConfig(
        task_id=safe_task_id("self-test-failing-test-stall"),
        json_output=True,
        max_steps=6,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )

    def fake_failing_tests(_config: AgentConfig, action: dict) -> dict:
        return {
            "ok": False,
            "returncode": 1,
            "stdout": '{"results":[{"ok":false,"file":"tests/test_calc.py","test":"test_new"}]}',
            "stderr": "",
        }

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"read_file","path":"/work/project/a.py","max_bytes":20000,"offset":0}',
            '{"action":"read_file","path":"/work/project/b.py","max_bytes":20000,"offset":0}',
            '{"action":"read_file","path":"/work/project/c.py","max_bytes":20000,"offset":0}',
            '{"action":"read_file","path":"/work/project/d.py","max_bytes":20000,"offset":0}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=fake_failing_tests), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "content": "x"}), \
            contextlib.redirect_stdout(failing_stall_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        failing_stall_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            failing_stall_config,
        )
    failing_stall_payload = json.loads(failing_stall_stdout.getvalue())
    failing_stall_errors = [
        (step.get("result") or {}).get("error")
        for step in failing_stall_payload.get("steps", [])
    ]
    if failing_stall_code != 0 or "swe failing tests inspection stall rejected by supervisor" not in failing_stall_errors:
        raise AssertionError(f"failing test inspection stall guard failed: code={failing_stall_code}, payload={failing_stall_payload}")
    print("[ok] failing test inspection stall guard")

    test_diag_stall_stdout = io.StringIO()
    test_diag_stall_config = AgentConfig(
        task_id=safe_task_id("self-test-test-diagnostic-stall"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"read_file","path":"/work/project/a.py","max_bytes":20000,"offset":0}',
            '{"action":"read_file","path":"/work/project/b.py","max_bytes":20000,"offset":0}',
            '{"action":"read_file","path":"/work/project/c.py","max_bytes":20000,"offset":0}',
            '{"action":"read_file","path":"/work/project/d.py","max_bytes":20000,"offset":0}',
            '{"action":"final","message":"blocked"}',
    ]), mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "content": "x"}), \
            contextlib.redirect_stdout(test_diag_stall_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        test_diag_stall_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            test_diag_stall_config,
        )
    test_diag_stall_payload = json.loads(test_diag_stall_stdout.getvalue())
    test_diag_stall_errors = [
        (step.get("result") or {}).get("error")
        for step in test_diag_stall_payload.get("steps", [])
    ]
    if test_diag_stall_code != 0 or "swe test diagnostic inspection stall rejected by supervisor" not in test_diag_stall_errors:
        raise AssertionError(f"test diagnostic inspection stall guard failed: code={test_diag_stall_code}, payload={test_diag_stall_payload}")
    print("[ok] test diagnostic inspection stall guard")

    same_file_edit_stdout = io.StringIO()
    same_file_edit_config = AgentConfig(
        task_id=safe_task_id("self-test-same-file-edit-before-test"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )

    same_file_test_results = [
        {
            "ok": False,
            "returncode": 1,
            "stdout": '{"results":[{"ok":true,"file":"tests/test_calc.py","test":"test_old"},{"ok":false,"file":"tests/test_calc.py","test":"test_new"}]}',
            "stderr": "",
        },
        {
            "ok": True,
            "returncode": 0,
            "stdout": '{"results":[{"ok":true,"file":"tests/test_calc.py","test":"test_old"},{"ok":true,"file":"tests/test_calc.py","test":"test_new"}]}',
            "stderr": "",
            "passing_tests": ["tests/test_calc.py::test_old", "tests/test_calc.py::test_new"],
            "failing_tests": [],
        },
    ]

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"replace_in_file","path":"/work/project/calc.py","old":"old","new":"new","count":1}',
            '{"action":"write_file","path":"/work/project/calc.py","content":"whole rewrite"}',
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"final","message":"blocked"}',
    ]) as mocked_same_file_chat, mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=same_file_test_results), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/project/calc.py"}), \
            contextlib.redirect_stdout(same_file_edit_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        same_file_edit_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            same_file_edit_config,
        )
    same_file_edit_payload = json.loads(same_file_edit_stdout.getvalue())
    same_file_edit_errors = [
        (step.get("result") or {}).get("error")
        for step in same_file_edit_payload.get("steps", [])
    ]
    same_file_auto_verified = (
        same_file_edit_code == 0
        and mocked_same_file_chat.call_count == 2
        and [step.get("action", {}).get("action") for step in same_file_edit_payload.get("steps", [])]
        == ["shell", "replace_in_file", "shell"]
    )
    if (
        same_file_edit_code != 0
        or (
            "swe repeated same-file edit before verification rejected by supervisor" not in same_file_edit_errors
            and not same_file_auto_verified
        )
    ):
        raise AssertionError(f"same-file edit before verification guard failed: code={same_file_edit_code}, payload={same_file_edit_payload}")
    print("[ok] same-file edit before verification guard")

    inspect_after_edit_stdout = io.StringIO()
    inspect_after_edit_config = AgentConfig(
        task_id=safe_task_id("self-test-inspection-after-edit-before-test"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    inspect_after_edit_test_results = [
        {
            "ok": False,
            "returncode": 1,
            "stdout": '{"results":[{"ok":true,"file":"tests/test_calc.py","test":"test_old"},{"ok":false,"file":"tests/test_calc.py","test":"test_new"}]}',
            "stderr": "",
        },
        {
            "ok": True,
            "returncode": 0,
            "stdout": '{"results":[{"ok":true,"file":"tests/test_calc.py","test":"test_old"},{"ok":true,"file":"tests/test_calc.py","test":"test_new"}]}',
            "stderr": "",
            "passing_tests": ["tests/test_calc.py::test_old", "tests/test_calc.py::test_new"],
            "failing_tests": [],
        },
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"replace_in_file","path":"/work/project/calc.py","old":"old","new":"new","count":1}',
            '{"action":"read_file","path":"/work/project/calc.py","max_bytes":20000}',
            '{"action":"shell","cmd":"cd /work/project && python3 -m pytest -q","timeout":60}',
            '{"action":"final","message":"blocked"}',
    ]) as mocked_inspect_after_edit_chat, mock.patch.object(agent_runner, "run_shell", return_value={
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "/usr/bin/python3: No module named pytest\n",
    }), mock.patch.object(agent_runner, "python_tool", side_effect=inspect_after_edit_test_results), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/project/calc.py"}), \
            contextlib.redirect_stdout(inspect_after_edit_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        inspect_after_edit_code = run_agent(
            "Исправь Python-проект и запусти pytest.\n\nРабочий каталог для этой задачи: /work/project",
            inspect_after_edit_config,
        )
    inspect_after_edit_payload = json.loads(inspect_after_edit_stdout.getvalue())
    inspect_after_edit_errors = [
        (step.get("result") or {}).get("error")
        for step in inspect_after_edit_payload.get("steps", [])
    ]
    inspect_after_edit_auto_verified = (
        inspect_after_edit_code == 0
        and mocked_inspect_after_edit_chat.call_count == 2
        and [step.get("action", {}).get("action") for step in inspect_after_edit_payload.get("steps", [])]
        == ["shell", "replace_in_file", "shell"]
    )
    if (
        inspect_after_edit_code != 0
        or (
            "swe inspection after edit before verification rejected by supervisor" not in inspect_after_edit_errors
            and not inspect_after_edit_auto_verified
        )
    ):
        raise AssertionError(f"inspection after edit before verification guard failed: code={inspect_after_edit_code}, payload={inspect_after_edit_payload}")
    print("[ok] inspection after edit before verification guard")

    workspace_task = "Запусти проверку Python.\n\nРабочий каталог для этой задачи: /work/project"
    if agent_runner.explicit_workspace_from_task(workspace_task) != "/work/project":
        raise AssertionError("explicit workspace was not extracted from task text")
    memory_config = AgentConfig(inject_memory=True, task_memory=True)
    if not agent_runner.should_inject_step_memory(memory_config, "", 1):
        raise AssertionError("ordinary tasks should keep memory injection")
    if agent_runner.should_inject_step_memory(memory_config, "/work/project", 1):
        raise AssertionError("explicit workspace tasks should not inject prior memory paths")
    print("[ok] explicit task workspace extracted")
    if agent_runner.action_workspace_violations({"path": "/work/other/file.py"}, "/work/project") != [{"field": "path", "path": "/work/other/file.py"}]:
        raise AssertionError("workspace boundary violation was not detected")
    if agent_runner.action_workspace_violations({"path": "/work/project/file.py", "cwd": "/work/project"}, "/work/project"):
        raise AssertionError("valid workspace paths were rejected")
    print("[ok] explicit workspace boundary guard")
    workspace_autocorrect_stdout = io.StringIO()
    workspace_autocorrect_events: list[dict] = []
    workspace_autocorrect_config = AgentConfig(
        task_id=safe_task_id("self-test-workspace-path-autocorrect"),
        json_output=True,
        max_steps=2,
        inject_memory=False,
        archive_internal_steps=False,
    )

    def fake_workspace_autocorrect_verify(_config: AgentConfig, action: dict) -> dict:
        if action.get("path") != "/work/project/audit.json":
            raise AssertionError(f"workspace path was not autocorrected before verify: {action}")
        return {"ok": True, "path": action.get("path"), "failures": [], "size": 42, "chars": 42}

    with mock.patch.object(agent_runner, "chat", side_effect=[
            '{"action":"verify_text_file","path":"/work/projct/audit.json","must_contain":["status"],"min_bytes":1}',
            '{"action":"final","message":"Готово: /work/project/audit.json"}',
    ]), mock.patch.object(agent_runner, "file_tool", return_value={
            "ok": True,
            "path": "/work/project/audit.json",
            "exists": True,
            "type": "file",
            "size": 42,
    }), \
            mock.patch.object(agent_runner, "verify_text_file_tool", side_effect=fake_workspace_autocorrect_verify), \
            contextlib.redirect_stdout(workspace_autocorrect_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        workspace_autocorrect_code = run_agent(
            "Required artifacts: /work/project/audit.json\n\nРабочий каталог для этой задачи: /work/project",
            workspace_autocorrect_config,
            event_sink=workspace_autocorrect_events.append,
        )
    workspace_autocorrect_payload = json.loads(workspace_autocorrect_stdout.getvalue())
    if (
        workspace_autocorrect_code != 0
        or not any(event.get("code") == "workspace_path_autocorrected" for event in workspace_autocorrect_events)
        or any((step.get("result") or {}).get("error") == "explicit workspace boundary rejected by supervisor" for step in workspace_autocorrect_payload.get("steps", []))
    ):
        raise AssertionError(
            "workspace required artifact path autocorrect failed: "
            f"code={workspace_autocorrect_code}, payload={workspace_autocorrect_payload}, events={workspace_autocorrect_events}"
        )
    print("[ok] workspace required artifact path autocorrect")
    escaped_workspace_task = "Рабочий каталог для этой задачи: /work/project\\nВсе файлы внутри него"
    if agent_runner.explicit_workspace_from_task(escaped_workspace_task) != "/work/project":
        raise AssertionError("explicit workspace parser kept escaped newline suffix")
    print("[ok] escaped workspace suffix stripped")
    resume_diagnostic_task = 'Resume context from previous agent task journal x:\n[{"type":"action","action":{"action":"read_file","path":"/work/project/a.py"}}]'
    if not agent_runner.resume_context_has_swe_diagnostic(resume_diagnostic_task):
        raise AssertionError("SWE diagnostic was not detected in resume context")
    print("[ok] resume context preserves SWE diagnostic state")

    verify_after_edit_stdout = io.StringIO()
    verify_after_edit_config = AgentConfig(
        task_id=safe_task_id("self-test-repeat-verification-after-edit"),
        json_output=True,
        max_steps=5,
        inject_memory=False,
        archive_internal_steps=False,
        shell_enabled=True,
    )
    same_test_action = '{"action":"shell","cmd":"cd /work/project && python3 -c \\"from calc import add; assert add(2, 3) == 5\\"","timeout":60}'
    verify_after_edit_actions = [
        same_test_action,
        same_test_action,
        '{"action":"replace_in_file","path":"/work/project/calc.py","old":"return a - b","new":"return a + b","count":1}',
        same_test_action,
        '{"action":"final","message":"done"}',
    ]
    shell_results = [
        {"ok": False, "returncode": 1, "stderr": "AssertionError"},
        {"ok": False, "returncode": 1, "stderr": "AssertionError"},
        {"ok": True, "returncode": 0, "stdout": ""},
    ]
    with mock.patch.object(agent_runner, "chat", side_effect=verify_after_edit_actions), \
            mock.patch.object(agent_runner, "run_shell", side_effect=shell_results), \
            mock.patch.object(agent_runner, "file_tool", return_value={"ok": True, "path": "/work/project/calc.py"}), \
            contextlib.redirect_stdout(verify_after_edit_stdout), \
            contextlib.redirect_stderr(io.StringIO()):
        verify_after_edit_code = run_agent("allow same verification after code edit", verify_after_edit_config)
    verify_after_edit_payload = json.loads(verify_after_edit_stdout.getvalue())
    rejected_after_edit = [
        step for step in verify_after_edit_payload.get("steps", [])
        if (step.get("result") or {}).get("error") == "repeated identical action rejected by supervisor"
    ]
    if verify_after_edit_code != 0 or rejected_after_edit:
        raise AssertionError(f"same verification after edit should be allowed: code={verify_after_edit_code}, payload={verify_after_edit_payload}")
    print("[ok] repeated verification allowed after state mutation")

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
    write_files_result = agent_runner.write_files_tool(
        config,
        {
            "action": "write_files",
            "files": [
                {"path": "/work/self-test/batch-a.txt", "content": "A"},
                {"path": "/work/self-test/batch-b.txt", "content": "B"},
            ],
        },
    )
    assert_ok("write_files", write_files_result)
    if sorted(write_files_result.get("written", [])) != ["/work/self-test/batch-a.txt", "/work/self-test/batch-b.txt"]:
        raise AssertionError(f"write_files did not report written paths: {write_files_result}")
    batch_read = file_tool(config, {"action": "read_file", "path": "/work/self-test/batch-b.txt"})
    if batch_read.get("content") != "B":
        raise AssertionError(f"write_files did not write expected content: {batch_read}")
    print("[ok] write_files content")

    assert_ok(
        "write_file json fixture",
        file_tool(
            config,
            {
                "action": "write_file",
                "path": "/work/self-test/audit.json",
                "content": '{"status": "pass", "checks_count": 3, "files": ["report.md", "audit.json"]}',
            },
        ),
    )
    json_literal_verify = agent_runner.verify_text_file_tool(
        config,
        {
            "action": "verify_text_file",
            "path": "/work/self-test/audit.json",
            "must_contain": ['"status":"pass"', '"checks_count":3', '"files":["report.md","audit.json"]'],
            "min_bytes": 1,
        },
    )
    assert_ok("verify_text_file json whitespace-insensitive literals", json_literal_verify)
    if json_literal_verify.get("checks", {}).get("json_whitespace_insensitive_matches") != 3:
        raise AssertionError(f"JSON whitespace-insensitive literal matches missing: {json_literal_verify}")
    json_weak_verify = agent_runner.verify_text_file_tool(
        config,
        {
            "action": "verify_text_file",
            "path": "/work/self-test/audit.json",
            "min_bytes": 1,
        },
    )
    if json_weak_verify.get("ok") is not False or not any(
        failure.get("check") == "structured_content_checks"
        for failure in json_weak_verify.get("failures", [])
        if isinstance(failure, dict)
    ):
        raise AssertionError(f"structured JSON without content checks should fail: {json_weak_verify}")
    print("[ok] verify_text_file rejects weak structured checks")
    assert_ok(
        "write_file json metrics fixture",
        file_tool(
            config,
            {
                "action": "write_file",
                "path": "/work/self-test/metrics.json",
                "content": '{"total_events":5,"error_count":1,"services":{"api":3,"worker":2},"max_latency_ms":900}',
            },
        ),
    )
    json_semantic_verify = agent_runner.verify_text_file_tool(
        config,
        {
            "action": "verify_text_file",
            "path": "/work/self-test/metrics.json",
            "must_contain": ["total_events=5", "error_count=1", "api:3", "worker:2", "metrics.json"],
            "ordered_patterns": ['services={"api":3,"worker":2}', "max_latency_ms=900"],
            "min_bytes": 1,
        },
    )
    assert_ok("verify_text_file json key=value semantic checks", json_semantic_verify)
    if json_semantic_verify.get("checks", {}).get("json_semantic_matches") != 6:
        raise AssertionError(f"JSON key=value semantic matches missing: {json_semantic_verify}")
    if json_semantic_verify.get("checks", {}).get("path_metadata_matches") != 1:
        raise AssertionError(f"path metadata match missing: {json_semantic_verify}")
    assert_ok(
        "write_file csv fixture",
        file_tool(
            config,
            {
                "action": "write_file",
                "path": "/work/self-test/anomalies.csv",
                "content": "service,status,latency_ms,reason\napi,error,900,error_status\n",
            },
        ),
    )
    csv_min_size_verify = agent_runner.verify_text_file_tool(
        config,
        {
            "action": "verify_text_file",
            "path": "/work/self-test/anomalies.csv",
            "must_contain": ["api,error,900,error_status"],
            "min_bytes": 100,
        },
    )
    assert_ok("verify_text_file structured min size advisory", csv_min_size_verify)
    if not csv_min_size_verify.get("structured_min_size_ignored"):
        raise AssertionError(f"structured min size was not advisory: {csv_min_size_verify}")

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

    stale_replace_result = file_tool(
        config,
        {"action": "replace_in_file", "path": "/work/self-test/hello.txt", "old": "missing block", "new": "x", "count": 1},
    )
    if (
        stale_replace_result.get("ok") is not False
        or stale_replace_result.get("error") != "old text not found"
        or "hello-updated" not in str(stale_replace_result.get("current_excerpt") or "")
    ):
        raise AssertionError(f"replace_in_file stale result missed current excerpt: {stale_replace_result}")
    print("[ok] replace_in_file stale excerpt")

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
    pycwd_dir = file_tool(config, {"action": "mkdir", "path": "/work/self-test/pycwd"})
    assert_ok("mkdir python cwd", pycwd_dir)
    pycwd_module = file_tool(config, {"action": "write_file", "path": "/work/self-test/pycwd/localmod.py", "content": "VALUE = 42\n"})
    assert_ok("write python cwd module", pycwd_module)
    python_cwd_result = python_tool(
        config,
        {"action": "python", "cwd": "/work/self-test/pycwd", "code": "import localmod; print(localmod.VALUE)", "timeout": 30},
    )
    assert_ok("python cwd", python_cwd_result)
    if python_cwd_result.get("stdout", "").strip() != "42":
        raise AssertionError(f"python cwd did not expose project root on PYTHONPATH: {python_cwd_result}")
    print("[ok] python cwd imports local modules")
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
