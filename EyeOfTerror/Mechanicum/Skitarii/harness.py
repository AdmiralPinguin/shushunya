"""Skitarii fighter: an agentic tool loop with the functions of a coding agent.

One fighter = one session: it gets the user's goal VERBATIM plus executable
success checks, then loops write -> RUN -> read stderr -> fix until the checks
pass or the budget runs out. All execution goes through an Executor (VM).
"""
from __future__ import annotations

import json
import hashlib
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

import tools as _tools

TOOLS = [
    {"type": "function", "function": {
        "name": "bash",
        "description": "Run a shell command in the workdir and get stdout/stderr/exit code. Use it to run programs, tests, linters, grep, ls.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "timeout_sec": {"type": "integer", "description": "default 120"},
        }, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file from the workdir. For big files pass offset/limit to read a line range (offset is a 0-based line number, limit is how many lines).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "0-based start line (optional)"},
            "limit": {"type": "integer", "description": "number of lines (optional)"},
        }, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "bash_background",
        "description": "Start a long-running process detached (e.g. launch a web server or DB) and get its pid + log file. Then use bash to probe it (curl localhost, read the log) and to kill it when done. Essential for testing servers/services.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
        }, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or overwrite a file in the workdir with the full content.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace an exact text fragment in a file (old must occur exactly once).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
        }, "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {
        "name": "memory_note",
        "description": "Save a short note to this task's persistent memory page (decisions, what worked/failed, where you are). Survives beyond your context window — write here so you never lose the thread on a long task.",
        "parameters": {"type": "object", "properties": {
            "note": {"type": "string"},
        }, "required": ["note"]}}},
    {"type": "function", "function": {
        "name": "memory_read",
        "description": "Read back this task's memory page (everything you and earlier steps noted). Use it to recover context.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web for information you need for the project (docs, APIs, error messages). Returns titles, URLs and snippets.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "web_fetch",
        "description": "Fetch the text content of a URL (documentation page, raw file). Use after web_search to read a source.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
        }, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "ask_user",
        "description": "Ask the human ONE concrete question when the task is genuinely ambiguous and you cannot decide safely (missing requirement, unclear intent). Blocks until they answer. Use rarely — prefer sensible defaults; do NOT ask about things you can just try.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"},
        }, "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "done",
        "description": "Finish the mission. Call ONLY after the success checks actually passed when you ran them.",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "what was built and how the checks passed"},
            "artifacts": {"type": "array", "items": {"type": "string"}, "description": "paths of the deliverable files"},
        }, "required": ["summary", "artifacts"]}}},
]

SYSTEM_PROMPT = """You are a Skitarii fighter: an autonomous coding agent working inside an isolated sandbox.

Rules:
- Implement EVERY feature the goal describes so it genuinely works at runtime. No placeholders, stubs, TODOs, dead code or no-op logic.
- Work in a loop: write code -> RUN it via bash -> read the errors -> fix -> run again.
- You have internet: use web_search / web_fetch when you need docs, an API reference, a package, or to resolve an error. Prefer official sources.
- Your VM is PERSISTENT: nothing is ever auto-deleted. Your $HOME and /tmp survive across missions, as do tools installed by earlier missions — check FIRST (`which gradle javac java sdkmanager node`, `ls $HOME /tmp /opt`) and reuse what exists; download a tool only if it is genuinely missing. Install tools to stable paths ($HOME or /tmp/tools), not inside your project directory.
- Every bash step runs in a FRESH non-interactive shell: edits to .bashrc/.profile//etc/environment will NEVER take effect, do not try. The only PATH that persists is $HOME/bin and $HOME/.local/bin — symlink or wrap installed tools there once (`ln -sf /tmp/tools/kotlin/kotlinc/bin/kotlinc $HOME/bin/`) and the bare name works in every later step AND in the acceptance checks.
- Keep every tool call SMALL: one oversized call gets truncated at the generation limit into invalid JSON and is rejected. Write long files in pieces — write_file the first ~120 lines, then append further chunks with bash (`cat >> path <<'EOF' ... EOF`). Never inline a whole script/XML into a python3 -c one-liner — write it to a file first, then run the file.
- To test a server/service, launch it with bash_background, probe it with bash (curl localhost, read its log), then kill its pid before finishing.
- You MUST actually run the success checks yourself with bash and see them pass before calling done.
- If a check cannot pass for a real external reason, call done with an honest summary of what is missing; never invent success.
- Keep files inside the workdir; use relative paths.
"""


CHECKPOINT_PREFIX = "SKITARII_CONTEXT_CHECKPOINT_V1 "
CHECKPOINT_FIELDS = (
    "current_state",
    "decisions",
    "completed_work",
    "failed_approaches",
    "working_set",
    "next_actions",
    "checks",
)
MAX_WIKI_CONTEXT_CHARS = int(os.environ.get("SKITARII_WIKI_CONTEXT_MAX_CHARS", "16000"))
MAX_CHECKPOINT_CHARS = int(os.environ.get("SKITARII_CHECKPOINT_MAX_CHARS", "12000"))
MAX_CHECKPOINT_SOURCE_CHARS = int(
    os.environ.get("SKITARII_CHECKPOINT_SOURCE_MAX_CHARS", "16000")
)
MAX_TRANSCRIPT_ENTRIES = int(os.environ.get("SKITARII_TRANSCRIPT_MAX_ENTRIES", "128"))
MAX_TRANSCRIPT_BYTES = int(os.environ.get("SKITARII_TRANSCRIPT_MAX_BYTES", "64000"))
MAX_LLM_ERROR_BODY_CHARS = int(os.environ.get("SKITARII_LLM_ERROR_BODY_MAX_CHARS", "4096"))


class LLMRequestError(RuntimeError):
    """A bounded, classified failure returned by the OpenAI-compatible backend."""

    def __init__(
        self,
        *,
        status: int,
        body: str,
        retryable: bool,
        context_overflow: bool,
    ) -> None:
        self.status = int(status)
        self.body = str(body)[:MAX_LLM_ERROR_BODY_CHARS]
        self.retryable = bool(retryable)
        self.context_overflow = bool(context_overflow)
        self.code = "context_overflow" if self.context_overflow else "llm_http_error"
        detail = self.body.strip() or "empty response body"
        super().__init__(f"LLM HTTP {self.status}: {detail}")


def _llm_settings() -> dict[str, Any]:
    base = os.environ.get("SKITARII_LLM_BASE_URL", "http://127.0.0.1:8081/v1").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    # Defaults mirror the live fighter backend: llama-server --ctx-size 65536 with a
    # single slot. The reasoning model spends tokens thinking before each tool call,
    # so the per-reply budget needs headroom too (compaction still fires early).
    # 24576: a tool call truncated at the old 16384 ceiling leaves unterminated JSON
    # that llama.cpp rejects with HTTP 500 — twice that killed a whole mission when
    # the fighter wrote a large file in one call. Headroom + the chunked-write prompt
    # rule attack the same failure from both sides (compact_at shrinks automatically).
    max_tokens = int(os.environ.get("SKITARII_LLM_MAX_TOKENS", "24576"))
    context_window = int(os.environ.get("SKITARII_LLM_CONTEXT_TOKENS", "65536"))
    context_margin = int(os.environ.get("SKITARII_LLM_CONTEXT_MARGIN_TOKENS", "2048"))
    default_compact_at = max(512, context_window - max_tokens - context_margin)
    return {
        "base_url": base,
        "model": os.environ.get("SKITARII_LLM_MODEL", "Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf"),
        "temperature": float(os.environ.get("SKITARII_LLM_TEMPERATURE", "0.2")),
        "timeout_sec": float(os.environ.get("SKITARII_LLM_TIMEOUT_SEC", "900")),
        "max_tokens": max_tokens,
        "context_window": context_window,
        "compact_at_tokens": int(
            os.environ.get("SKITARII_LLM_COMPACT_AT_TOKENS", str(default_compact_at))
        ),
        "checkpoint_max_tokens": int(
            os.environ.get("SKITARII_LLM_CHECKPOINT_MAX_TOKENS", "1200")
        ),
    }


def _chat(messages: list[dict], settings: dict[str, Any]) -> dict[str, Any]:
    # The fighter backend is a reasoning model (Qwen3.6, server runs --reasoning on):
    # its thinking lands in reasoning_content while tool calls still parse, so let it
    # think by default — SKITARII_LLM_ENABLE_THINKING=0 restores the old suppression.
    # Greedy decoding (temperature=0) is explicitly discouraged for Qwen3: it loops,
    # and a failed sample replays IDENTICALLY on retry — three missions died re-sending
    # the same truncated tool call. 0.2 keeps code conservative while letting retries
    # actually sample a different continuation; top_p/top_k follow the vendor rec.
    # (The planner/spec calls keep temperature=0 on purpose: strict JSON, no replay issue.)
    payload = {
        "model": settings["model"],
        "messages": messages,
        "temperature": settings.get("temperature", 0.2),
        "top_p": 0.95,
        "top_k": 20,
        "max_tokens": settings["max_tokens"],
        "chat_template_kwargs": {
            "enable_thinking": os.environ.get("SKITARII_LLM_ENABLE_THINKING", "1") == "1",
        },
    }
    enabled_tools = settings.get("tools", TOOLS + _tools.extra_specs())
    if enabled_tools:
        payload["tools"] = enabled_tools
    req = urllib.request.Request(
        f"{settings['base_url']}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=settings["timeout_sec"]) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(MAX_LLM_ERROR_BODY_CHARS + 1).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        body = body[:MAX_LLM_ERROR_BODY_CHARS]
        lowered = body.lower()
        context_overflow = any(marker in lowered for marker in (
            "exceeds the available context size",
            "context length exceeded",
            "maximum context length",
            "context_window_exceeded",
            "too many tokens",
        ))
        retryable = context_overflow or int(exc.code) in {
            408, 409, 425, 429, 500, 502, 503, 504,
        }
        raise LLMRequestError(
            status=int(exc.code),
            body=body,
            retryable=retryable,
            context_overflow=context_overflow,
        ) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        # Socket timeouts and refused connections killed missions mid-fighter:
        # they flew past the HTTPError-only retry as a naked TimeoutError. A busy
        # backend is transient by definition — surface it as retryable so the
        # loop's in-mission retry (3x with backoff) absorbs it.
        raise LLMRequestError(
            status=0,
            body=f"{type(exc).__name__}: {exc}"[:500],
            retryable=True,
            context_overflow=False,
        ) from exc


ARCHIVE_URL = os.environ.get("SKITARII_ARCHIVE_URL", "http://127.0.0.1:8090").rstrip("/")


def _archive_headers(*, json_body: bool = False) -> dict[str, str]:
    raw_token = str(
        os.environ.get("SKITARII_ARCHIVE_API_KEY")
        or os.environ.get("ARCHIVE_API_KEY")
        or ""
    )
    if "\r" in raw_token or "\n" in raw_token:
        raise ValueError("Archive API key contains a line break")
    token = raw_token.strip()
    headers = {"Content-Type": "application/json"} if json_body else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _memory_note(memory_task_id: str, note: str) -> str:
    """Append to an existing stable task page; never invoke legacy auto-init."""
    clean_note = _clip_text(str(note or "").strip(), 4_000)
    if not clean_note:
        raise ValueError("task memory note is empty")
    idempotency_key = f"skitarii-note-{uuid.uuid4().hex}"
    pending_payload: dict[str, Any] | None = None
    for attempt in range(2):
        document = _task_page_document(memory_task_id)
        revision = document.get("revision")
        page_memory_id = str(document.get("task_memory_id") or "")
        if type(revision) is not int or revision < 1 or page_memory_id != memory_task_id:
            raise RuntimeError("task memory page is not initialized")
        if pending_payload is None:
            pending_payload = {
                "action": "event",
                "task_memory_id": memory_task_id,
                "expected_revision": revision,
                "idempotency_key": idempotency_key,
                "actor": "SkitariiContextController",
                "kind": "note",
                "event": {"note": clean_note, "summary": clean_note},
            }
        try:
            _post_task_page(pending_payload)
            return "noted"
        except urllib.error.HTTPError as exc:
            if int(exc.code) == 409 and attempt == 0:
                pending_payload = None
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 0:
                continue
            raise
    raise RuntimeError("task memory note reconciliation was exhausted")


def _task_page_document(memory_task_id: str) -> dict[str, Any]:
    from urllib.parse import quote
    req = urllib.request.Request(
        f"{ARCHIVE_URL}/archive/task-page?task_memory_id={quote(memory_task_id, safe='')}",
        headers=_archive_headers(),
    )
    data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _post_task_page(payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{ARCHIVE_URL}/archive/task-page/checkpoint",
        data=body,
        headers=_archive_headers(json_body=True),
        method="POST",
    )
    data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _memory_read(task_id: str) -> str:
    data = _task_page_document(task_id)
    canonical_context = data.get("context")
    if isinstance(canonical_context, str) and canonical_context.strip():
        return _clip_text(canonical_context.strip(), MAX_WIKI_CONTEXT_CHARS)
    snapshot = data.get("snapshot")
    if isinstance(snapshot, dict) and snapshot:
        def items(field: str, limit: int) -> list[str]:
            return [
                _clip_text(item, 250)
                for item in _checkpoint_items(snapshot.get(field))[-limit:]
            ]

        priority = {
            "task_memory_id": data.get("task_memory_id") or task_id,
            "root_task_id": data.get("root_task_id") or snapshot.get("root_task_id"),
            "goal_verbatim": _clip_text(snapshot.get("goal_verbatim"), 3_000),
            "desired_outcome": _clip_text(snapshot.get("desired_outcome"), 1_000),
            "state": _clip_text(snapshot.get("state"), 2_000),
            "current_strategy": _clip_text(snapshot.get("current_strategy"), 1_000),
            "decisions": items("decisions", 6),
            "completed_work": items("completed_work", 6),
            "failed_approaches": items("failed_approaches", 4),
            "working_set": items("working_set", 6),
            "next_actions": items("next_actions", 6),
            "open_requirements": items("open_requirements", 4),
        }
        rendered = json.dumps(priority, ensure_ascii=False, separators=(",", ":"))
        while len(rendered) > MAX_WIKI_CONTEXT_CHARS:
            list_fields = [
                field for field, value in priority.items()
                if isinstance(value, list) and value
            ]
            if list_fields:
                longest = max(list_fields, key=lambda field: len(priority[field]))
                priority[longest].pop(0)
            else:
                text_fields = [
                    field for field, value in priority.items()
                    if isinstance(value, str) and value
                ]
                if not text_fields:
                    return "{}"
                longest = max(text_fields, key=lambda field: len(priority[field]))
                current = priority[longest]
                priority[longest] = _clip_text(current, max(0, len(current) // 2))
            rendered = json.dumps(priority, ensure_ascii=False, separators=(",", ":"))
        return rendered
    content = str(data.get("content") or "")
    return content[-MAX_WIKI_CONTEXT_CHARS:]


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= max(0, limit):
        return text
    return text[:max(0, limit - 1)] + ("…" if limit else "")


def _bounded_arg(value: Any) -> Any:
    if isinstance(value, str):
        return _clip_text(value, 200)
    try:
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        rendered = repr(value)
    return value if len(rendered) <= 200 else _clip_text(rendered, 200)


def _transcript_size(transcript: list[dict[str, Any]]) -> int:
    return len(json.dumps(
        transcript, ensure_ascii=False, separators=(",", ":"), default=str,
    ).encode("utf-8"))


def _append_transcript(transcript: list[dict[str, Any]], event: dict[str, Any]) -> None:
    transcript.append(event)
    entry_limit = max(1, MAX_TRANSCRIPT_ENTRIES)
    byte_limit = max(512, MAX_TRANSCRIPT_BYTES)
    while len(transcript) > entry_limit:
        transcript.pop(0)
    while len(transcript) > 1 and _transcript_size(transcript) > byte_limit:
        transcript.pop(0)
    if transcript and _transcript_size(transcript) > byte_limit:
        transcript[0] = {
            "step": event.get("step"),
            "truncated": True,
            "detail": _clip_text(event.get("tool") or event.get("prose") or "event", 120),
        }


def _extract_checkpoint_object(text: str) -> dict[str, Any] | None:
    start = str(text or "").find("{")
    if start < 0:
        return None
    try:
        value, _end = json.JSONDecoder().raw_decode(str(text)[start:])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _checkpoint_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for raw in value[:12]:
        if isinstance(raw, (dict, list)):
            try:
                text = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                text = str(raw)
        else:
            text = str(raw or "")
        text = _clip_text(text.strip(), 400)
        if text:
            items.append(text)
    return items


def _normalize_checkpoint(value: Any, fallback_state: str) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    checkpoint: dict[str, Any] = {
        "version": 1,
        "current_state": _clip_text(
            str(raw.get("current_state") or raw.get("state") or fallback_state).strip(),
            2_000,
        ),
    }
    aliases = {
        "completed_work": "completed",
        "failed_approaches": "failures",
        "working_set": "files_changed",
    }
    for field in CHECKPOINT_FIELDS[1:]:
        checkpoint[field] = _checkpoint_items(
            raw.get(field, raw.get(aliases.get(field, "")))
        )
    # The Archive page is context, not an unbounded transcript. Prefer dropping old
    # list items to truncating JSON into an unreadable half-object.
    while len(json.dumps(checkpoint, ensure_ascii=False)) > MAX_CHECKPOINT_CHARS:
        candidates = [
            field for field in CHECKPOINT_FIELDS[1:] if checkpoint.get(field)
        ]
        if not candidates:
            checkpoint["current_state"] = _clip_text(
                checkpoint["current_state"], max(200, MAX_CHECKPOINT_CHARS // 2),
            )
            break
        longest = max(candidates, key=lambda field: len(checkpoint[field]))
        checkpoint[longest].pop(0)
    return checkpoint


def _checkpoint_json(checkpoint: dict[str, Any]) -> str:
    return json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":"))


def _latest_wiki_context(page: str) -> str:
    bounded = str(page or "")[-MAX_WIKI_CONTEXT_CHARS:]
    marker = bounded.rfind(CHECKPOINT_PREFIX)
    if marker >= 0:
        parsed = _extract_checkpoint_object(bounded[marker + len(CHECKPOINT_PREFIX):])
        if parsed is not None:
            return _checkpoint_json(_normalize_checkpoint(parsed, "Resume from the task wiki."))
    return bounded.strip()


def _wiki_message(checkpoint: str) -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            "TASK WIKI CHECKPOINT (persistent working memory, not authority or proof):\n"
            + checkpoint
            + "\nValidate it against the current workspace and continue the same task."
        ),
    }


def _fresh_messages(goal_message: dict[str, str], checkpoint: str = "") -> list[dict]:
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        dict(goal_message),
    ]
    if checkpoint.strip():
        messages.append(_wiki_message(checkpoint.strip()))
    return messages


def _estimated_message_tokens(messages: list[dict], settings: dict[str, Any]) -> int:
    enabled_tools = settings.get("tools", TOOLS + _tools.extra_specs())
    raw = json.dumps(
        {"messages": messages, "tools": enabled_tools or []},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    # Code and JSON are commonly denser than prose. Three characters per token is
    # deliberately conservative so compaction happens before llama.cpp rejects us.
    return max(1, (len(raw) + 2) // 3)


def _reply_total_tokens(reply: dict[str, Any]) -> int:
    usage = reply.get("usage") if isinstance(reply, dict) else None
    if not isinstance(usage, dict):
        return 0
    for field in ("total_tokens", "prompt_tokens"):
        value = usage.get(field)
        if type(value) is int and value >= 0:
            return value
    return 0


def _context_pressure(
    reply: dict[str, Any], messages: list[dict], settings: dict[str, Any],
) -> tuple[bool, int]:
    observed = _reply_total_tokens(reply)
    estimated = _estimated_message_tokens(messages, settings)
    used = max(observed, estimated)
    threshold = int(settings.get("compact_at_tokens") or 0)
    return bool(threshold > 0 and used >= threshold), used


def _fallback_checkpoint(transcript: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    recent: list[str] = []
    failures: list[str] = []
    for event in transcript[-10:]:
        if event.get("tool"):
            line = (
                f"step {event.get('step')}: {event.get('tool')} "
                f"{event.get('args', {})} -> {event.get('result', '')}"
            )
            recent.append(_clip_text(line, 400))
            if "ERROR:" in str(event.get("result") or ""):
                failures.append(_clip_text(line, 400))
        elif event.get("prose"):
            recent.append(_clip_text(f"step {event.get('step')}: {event['prose']}", 400))
    return _normalize_checkpoint({
        "current_state": reason,
        "working_set": recent,
        "failed_approaches": failures,
        "next_actions": ["Inspect the current workspace and continue from its actual state."],
    }, reason)


def _bounded_checkpoint_source(
    messages: list[dict], transcript: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build a valid, small summarizer input after the original request overflowed."""
    recent_messages: list[dict[str, Any]] = []
    for message in messages[-12:]:
        compact: dict[str, Any] = {"role": str(message.get("role") or "")}
        content = str(message.get("content") or "").strip()
        if content:
            compact["content"] = _clip_text(content, 1_500)
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            compact["tool_calls"] = [
                {
                    "name": _clip_text((call.get("function") or {}).get("name"), 100),
                    "arguments": _clip_text(
                        (call.get("function") or {}).get("arguments"), 800,
                    ),
                }
                for call in calls[-4:]
                if isinstance(call, dict)
            ]
        recent_messages.append(compact)
    snapshot = {
        "recent_messages": recent_messages,
        "controller_transcript": transcript[-16:],
    }
    rendered = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), default=str)
    rendered = _clip_text(rendered, max(1_000, MAX_CHECKPOINT_SOURCE_CHARS))
    return [
        {
            "role": "system",
            "content": (
                "You are a context compactor. Summarize only the supplied coding-session "
                "facts into the requested checkpoint JSON. Do not invent work or results."
            ),
        },
        {"role": "user", "content": "BOUNDED SESSION SNAPSHOT:\n" + rendered},
    ]


_CHECKPOINT_REQUEST = """The controller must compact this coding session before the context limit.
Do not perform more work and do not call tools. Return ONE short JSON object and nothing else.
Use exactly these keys:
{"current_state":"where the task stands", "decisions":["important choice and why"],
 "completed_work":["verified work"], "failed_approaches":["error and why"],
 "working_set":["path: current change"], "checks":["command: result"],
 "next_actions":["concrete next step"]}
Record only facts from this session. Keep it compact enough to resume in a fresh context."""


def _make_checkpoint(
    messages: list[dict], settings: dict[str, Any], transcript: list[dict[str, Any]],
    *, reason: str, force_bounded_source: bool = False,
) -> dict[str, Any]:
    compact_settings = dict(settings)
    compact_settings["tools"] = []
    main_max_tokens = max(1, int(settings.get("max_tokens") or 1_200))
    compact_settings["max_tokens"] = min(
        main_max_tokens,
        max(128, int(settings.get("checkpoint_max_tokens") or 1_200)),
    )
    try:
        source = (
            _bounded_checkpoint_source(messages, transcript)
            if force_bounded_source else messages
        )
        reply = _chat(source + [{"role": "user", "content": _CHECKPOINT_REQUEST}], compact_settings)
        message = (reply.get("choices") or [{}])[0].get("message") or {}
        parsed = _extract_checkpoint_object(str(message.get("content") or ""))
        if parsed is not None:
            return _normalize_checkpoint(parsed, reason)
    except Exception:
        # Compaction is a reliability mechanism. If the summarizer is unavailable,
        # a bounded controller-built checkpoint is safer than killing the task.
        pass
    return _fallback_checkpoint(transcript, reason)


def _bounded_unique(existing: Any, additions: Any, limit: int = 24) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for item in [
        *(existing if isinstance(existing, list) else []),
        *(additions if isinstance(additions, list) else []),
    ]:
        try:
            identity = json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
        except (TypeError, ValueError):
            identity = repr(item)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(item)
    return merged[-max(1, limit):]


def _checkpoint_patch(
    checkpoint: dict[str, Any], snapshot: dict[str, Any] | None = None,
    *, authoritative: bool = False,
) -> dict[str, Any]:
    current = snapshot if isinstance(snapshot, dict) else {}
    patch = {
        "working_set": _bounded_unique(
            current.get("working_set"), checkpoint.get("working_set"),
        ),
    }
    if authoritative:
        patch["state"] = str(checkpoint.get("current_state") or "")
        patch["next_actions"] = list(checkpoint.get("next_actions") or [])[-24:]
        patch["decisions"] = _bounded_unique(
            current.get("decisions"), checkpoint.get("decisions"),
        )
        patch["completed_work"] = _bounded_unique(
            current.get("completed_work"), checkpoint.get("completed_work"),
        )
        patch["failed_approaches"] = _bounded_unique(
            current.get("failed_approaches"), checkpoint.get("failed_approaches"),
        )
    else:
        journal_entry = {
            "actor": "SkitariiContextController",
            "kind": "unverified_fighter_context",
            "state": str(checkpoint.get("current_state") or ""),
            "decisions": list(checkpoint.get("decisions") or []),
            "claimed_completed_work": list(checkpoint.get("completed_work") or []),
            "observed_failures": list(checkpoint.get("failed_approaches") or []),
            "checks": list(checkpoint.get("checks") or []),
            "next_actions": list(checkpoint.get("next_actions") or []),
        }
        patch["journal"] = _bounded_unique(
            current.get("journal"), [journal_entry], limit=16,
        )
    return patch


def _structured_memory_checkpoint(
    memory_task_id: str,
    checkpoint: dict[str, Any],
    *,
    idempotency_key: str,
    authoritative: bool = False,
) -> dict[str, Any]:
    """CAS-write one checkpoint, rereading once when another writer won."""
    payload_base: dict[str, Any] = {
        "action": "checkpoint",
        "task_memory_id": memory_task_id,
        "actor": "SkitariiContextController",
    }
    pending_payload: dict[str, Any] | None = None
    for attempt in range(2):
        document = _task_page_document(memory_task_id)
        revision = document.get("revision")
        if type(revision) is not int or revision < 1:
            raise RuntimeError("task memory page is not initialized")
        if pending_payload is None:
            merged_patch = _checkpoint_patch(
                checkpoint,
                (
                    document.get("snapshot")
                    if isinstance(document.get("snapshot"), dict) else {}
                ),
                authoritative=authoritative,
            )
            patch_digest = hashlib.sha256(json.dumps(
                merged_patch,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            pending_payload = {
                **payload_base,
                "expected_revision": revision,
                "idempotency_key": f"{idempotency_key[:120]}-{patch_digest}",
                "patch": merged_patch,
            }
        try:
            return _post_task_page(pending_payload)
        except urllib.error.HTTPError as exc:
            if int(exc.code) == 409 and attempt == 0:
                # A stale CAS was not committed: recompute the union from the
                # winner's fresh snapshot before trying the same logical event.
                pending_payload = None
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 0:
                # The write may have committed and only its response was lost.
                # Re-read for reconciliation, then replay byte-identical intent;
                # Archive's idempotency key resolves it without duplication.
                continue
            raise
    raise RuntimeError("task memory checkpoint reconciliation was exhausted")


def _persist_checkpoint(
    memory_task_id: str,
    checkpoint: dict[str, Any],
    *,
    authoritative: bool = False,
    idempotency_key: str = "",
) -> str:
    rendered = _checkpoint_json(checkpoint)
    if not memory_task_id:
        return rendered
    _structured_memory_checkpoint(
        memory_task_id,
        checkpoint,
        idempotency_key=(
            idempotency_key
            or "skitarii-context-"
            + hashlib.sha256(
                (memory_task_id + "\0" + rendered).encode("utf-8")
            ).hexdigest()
        ),
        authoritative=authoritative,
    )
    return rendered


def _dispatch_tool(
    executor: Any,
    name: str,
    args: dict[str, Any],
    task_id: str = "",
    ask_fn=None,
    memory_task_id: str | None = None,
) -> str:
    # task_id names an immutable execution attempt. It must never become a
    # durable wiki key merely because the stable memory id was omitted.
    memory_id = str(memory_task_id or "").strip()
    try:
        if name == "ask_user":
            q = str(args.get("question") or "").strip()
            if not q:
                return "ERROR: empty question"
            if ask_fn is None:
                return "No interactive user is available. Proceed with the most sensible default and note the assumption."
            return ask_fn(q) or "(no answer given — proceed on your best judgement)"
        if name == "memory_note":
            return _memory_note(memory_id, str(args.get("note") or "")) if memory_id else "ERROR: no memory_task_id"
        if name == "memory_read":
            if not memory_id:
                return "ERROR: no memory_task_id"
            return _memory_read(memory_id) or "(память задачи пуста)"
        if name == "bash":
            result = executor.bash(str(args.get("command") or ""), timeout=int(args.get("timeout_sec") or 120))
            return json.dumps(result, ensure_ascii=False)
        if name == "read_file":
            return executor.read_file(str(args.get("path") or ""),
                                      offset=int(args.get("offset") or 0),
                                      limit=int(args.get("limit") or 0))
        if name == "bash_background":
            info = executor.bash_background(str(args.get("command") or ""))
            return json.dumps(info, ensure_ascii=False)
        if name == "write_file":
            executor.write_file(str(args.get("path") or ""), str(args.get("content") or ""))
            return "written"
        if name == "edit_file":
            path = str(args.get("path") or "")
            old, new = str(args.get("old") or ""), str(args.get("new") or "")
            text = executor.read_file(path, max_bytes=1_000_000)
            count = text.count(old)
            if count != 1:
                return f"ERROR: old fragment occurs {count} times (must be exactly 1)"
            executor.write_file(path, text.replace(old, new, 1))
            return "edited"
        if name == "web_search":
            # runs from inside the sandbox VM (curl), so the host is never exposed
            q = str(args.get("query") or "").replace("'", "")
            cmd = (f"curl -sL --max-time 30 -A 'Mozilla/5.0' "
                   f"'https://lite.duckduckgo.com/lite/?q={q.replace(' ', '+')}' "
                   "| sed -e 's/<[^>]*>//g' | grep -vE '^\\s*$' | head -40")
            return executor.bash(cmd, timeout=45)["stdout"] or "(no results)"
        if name == "web_fetch":
            url = str(args.get("url") or "")
            cmd = (f"curl -sL --max-time 40 -A 'Mozilla/5.0' {url!r} "
                   "| sed -e 's/<script[^>]*>.*<\\/script>//g' -e 's/<[^>]*>//g' "
                   "| grep -vE '^\\s*$' | head -400")
            return executor.bash(cmd, timeout=55)["stdout"][:15_000] or "(empty)"
        extra = _tools.dispatch_extra(name, args, executor)
        if extra is not None:
            return extra
        return f"ERROR: unknown tool {name}"
    except Exception as exc:  # tool errors go back to the model, they don't kill the loop
        return f"ERROR: {type(exc).__name__}: {exc}"


def _check_text(check: Any) -> str:
    if isinstance(check, str):
        return f"- run: {check}"
    cmd = str(check.get("cmd") or "")
    if "expect_stdout" in check:
        return f"- run `{cmd}` — its stdout must equal: {check['expect_stdout']!r}"
    if "oracle" in check:
        return f"- run `{cmd}` — its stdout must equal the output of `{check['oracle']}`"
    return f"- run `{cmd}` — it must succeed (exit code 0)"


def _returncode_from_result(result: str) -> int | None:
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("returncode"), int):
        return int(data["returncode"])
    return None


def _describe_action(name: str, args: dict[str, Any], result: str) -> str:
    """Plain-language Russian line describing one concrete fighter action, for the
    owner's live feed — he wants to read exactly what the Skitarii does, step by step."""
    path = str(args.get("path") or "").strip()
    if name == "write_file":
        return f"Пишу файл {path}" if path else "Пишу файл"
    if name == "edit_file":
        return f"Правлю файл {path}" if path else "Правлю файл"
    if name == "read_file":
        return f"Читаю файл {path}" if path else "Читаю файл"
    if name in {"bash", "bash_background"}:
        cmd = " ".join(str(args.get("command") or "").split())[:140]
        prefix = "Запускаю в фоне" if name == "bash_background" else "Запускаю"
        rc = _returncode_from_result(result)
        if rc is None:
            return f"{prefix}: {cmd}"
        return f"{prefix}: {cmd} — {'готово' if rc == 0 else f'ошибка (код {rc})'}"
    if name == "web_search":
        return f"Ищу в сети: {str(args.get('query') or '').strip()[:120]}"
    if name == "web_fetch":
        return f"Читаю страницу: {str(args.get('url') or '').strip()[:120]}"
    if name == "memory_note":
        return f"Помечаю в память: {str(args.get('note') or '').strip()[:120]}"
    if name == "memory_read":
        return "Читаю память задачи"
    if name == "ask_user":
        return f"Спрашиваю хозяина: {str(args.get('question') or '').strip()[:140]}"
    if name == "list_dir":
        return f"Смотрю каталог {str(args.get('path') or '.').strip()}"
    if name == "find_files":
        return f"Ищу файлы по шаблону {str(args.get('pattern') or '').strip()[:80]}"
    if name == "grep_symbol":
        return f"Ищу по проекту символ {str(args.get('symbol') or '').strip()[:80]}"
    if name == "git_diff":
        return "Смотрю свои изменения (git diff)"
    if name == "git_log":
        return "Смотрю историю коммитов"
    return f"Действие: {name}"


def run_fighter(goal: str, checks: list[Any], executor: Any,
                max_steps: int = 40, max_wall_sec: int = 3600, task_id: str = "",
                ask_fn=None, cancel_fn=None,
                memory_task_id: str | None = None,
                durable_checkpoint_fn=None, progress=None) -> dict[str, Any]:
    """The agentic loop. Returns {ok, summary, artifacts, transcript, steps, seconds}.

    `progress(text)` is an optional callback for a live plain-language feed of what the
    fighter is actually doing (write/run/read …). It is best-effort and never fatal."""
    def emit(text: str) -> None:
        if progress is None:
            return
        line = str(text or "").strip()
        if not line:
            return
        try:
            progress(line)
        except Exception:
            pass

    settings = _llm_settings()
    started = time.monotonic()
    checks_text = "\n".join(_check_text(c) for c in checks) or "- (no explicit checks; prove the program runs)"
    # An absent stable id disables wiki I/O. Falling back to the run id would
    # fork task memory on every retry, which is deliberately forbidden.
    memory_id = str(memory_task_id or "").strip()
    goal_message = {
        "role": "user",
        "content": f"GOAL (verbatim):\n{goal}\n\nSUCCESS CHECKS you must run and pass:\n{checks_text}",
    }
    transcript: list[dict] = []
    wiki_context = ""
    if memory_id:
        try:
            wiki_context = _latest_wiki_context(_memory_read(memory_id))
        except Exception as exc:
            _append_transcript(transcript, {
                "step": 0,
                "memory_load_error": _clip_text(f"{type(exc).__name__}: {exc}", 500),
            })
    messages = _fresh_messages(goal_message, wiki_context)
    consecutive_overflows = 0
    consecutive_llm_errors = 0

    def durable_workspace_checkpoint(*, step: int, boundary: str) -> None:
        if durable_checkpoint_fn is None:
            return
        # Continuing after this callback failed would reopen the exact
        # power-loss window the WAL exists to close, so failures propagate.
        durable_checkpoint_fn(executor, step=step, boundary=boundary)
        _append_transcript(transcript, {
            "step": step,
            "event": "workspace_checkpoint",
            "boundary": _clip_text(boundary, 160),
        })

    def compact_session(*, step: int, reason: str, used_tokens: int,
                        ask_model: bool = True,
                        force_bounded_source: bool = False) -> list[dict]:
        durable_workspace_checkpoint(step=step, boundary="context_compaction")
        checkpoint = (
            _make_checkpoint(
                messages,
                settings,
                transcript,
                reason=reason,
                force_bounded_source=force_bounded_source,
            )
            if ask_model else _fallback_checkpoint(transcript, reason)
        )
        memory_error = ""
        try:
            rendered_checkpoint = _checkpoint_json(checkpoint)
            checkpoint_key = hashlib.sha256(
                (task_id + "\0compact\0" + str(step) + "\0" + rendered_checkpoint).encode("utf-8")
            ).hexdigest()
            rendered = _persist_checkpoint(
                memory_id,
                checkpoint,
                idempotency_key=f"skitarii-compact-{checkpoint_key}",
            )
        except Exception as exc:
            rendered = _checkpoint_json(checkpoint)
            memory_error = _clip_text(f"{type(exc).__name__}: {exc}", 500)
        event: dict[str, Any] = {
            "step": step,
            "event": "context_compacted",
            "used_tokens": max(0, int(used_tokens)),
            "memory_task_id": memory_id,
        }
        if memory_error:
            event["memory_error"] = memory_error
        _append_transcript(transcript, event)
        return _fresh_messages(goal_message, rendered)

    def lifecycle_checkpoint(
        *, step: int, state: str, completed: str = "", handoff: str = "",
        failure: str = "",
        next_actions: list[str] | None = None,
    ) -> None:
        durable_workspace_checkpoint(step=step, boundary="lifecycle:" + state[:100])
        if not memory_id:
            return
        checkpoint = _fallback_checkpoint(transcript, state)
        checkpoint["current_state"] = _clip_text(state, 2_000)
        if completed:
            checkpoint["completed_work"] = _bounded_unique(
                checkpoint.get("completed_work"), [_clip_text(completed, 400)], limit=12,
            )
        if handoff:
            checkpoint["decisions"] = _bounded_unique(
                checkpoint.get("decisions"),
                ["Unverified fighter handoff: " + _clip_text(handoff, 360)],
                limit=12,
            )
        if failure:
            checkpoint["failed_approaches"] = _bounded_unique(
                checkpoint.get("failed_approaches"), [_clip_text(failure, 400)], limit=12,
            )
        checkpoint["next_actions"] = [
            _clip_text(item, 400) for item in (next_actions or [])[:12]
        ]
        event: dict[str, Any] = {"step": step, "event": "lifecycle_checkpoint"}
        try:
            rendered_checkpoint = _checkpoint_json(checkpoint)
            checkpoint_key = hashlib.sha256(
                (task_id + "\0lifecycle\0" + str(step) + "\0" + rendered_checkpoint).encode("utf-8")
            ).hexdigest()
            _persist_checkpoint(
                memory_id,
                checkpoint,
                idempotency_key=f"skitarii-lifecycle-{checkpoint_key}",
            )
        except Exception as exc:
            event["memory_error"] = _clip_text(f"{type(exc).__name__}: {exc}", 500)
        _append_transcript(transcript, event)

    for step in range(1, max_steps + 1):
        if cancel_fn is not None and cancel_fn():
            lifecycle_checkpoint(
                step=step,
                state="The fighter was cancelled before the task completed.",
                failure="Execution was cancelled by the user.",
                next_actions=["Resume only when the user requests continuation."],
            )
            return {"ok": False, "summary": "cancelled by user", "artifacts": [],
                    "transcript": transcript, "steps": step, "seconds": int(time.monotonic() - started),
                    "cancelled": True}
        if time.monotonic() - started > max_wall_sec:
            lifecycle_checkpoint(
                step=step,
                state="The fighter stopped at its wall-clock budget.",
                failure=f"Wall-clock budget exceeded ({max_wall_sec}s).",
                next_actions=["Resume from the current workspace with a fresh time budget."],
            )
            return {"ok": False, "summary": f"wall-clock budget exceeded ({max_wall_sec}s)",
                    "artifacts": [], "transcript": transcript, "steps": step, "seconds": int(time.monotonic() - started)}
        try:
            reply = _chat(messages, settings)
        except LLMRequestError as exc:
            if exc.context_overflow and consecutive_overflows < 2:
                messages = compact_session(
                    step=step,
                    reason=(
                        "The LLM backend rejected the current session as over its context window. "
                        "The controller reset the conversation; inspect the unchanged workspace."
                    ),
                    used_tokens=int(settings.get("context_window") or 0),
                    ask_model=True,
                    force_bounded_source=True,
                )
                consecutive_overflows += 1
                continue
            # A transient backend error must not kill the mission. temperature=0 makes
            # a bare retry replay the SAME broken sample (a tool call truncated at
            # max_tokens once burned a whole attempt this way), so a malformed-call
            # rejection also appends a corrective message — new prompt, new sample.
            if exc.retryable and not exc.context_overflow and consecutive_llm_errors < 3:
                consecutive_llm_errors += 1
                emit(f"LLM-бэкенд ответил {exc.status} — повтор {consecutive_llm_errors}/3.")
                lowered_body = str(exc.body or "").lower()
                if any(marker in lowered_body for marker in (
                        "parse tool call", "parse_error", "invalid string",
                        "missing closing quote")):
                    if consecutive_llm_errors == 1:
                        correction = (
                            "Your previous tool call was rejected by the backend: its JSON was "
                            "malformed or truncated (most likely the call was too large and got "
                            "cut at the generation token limit). Redo the intended action with a "
                            "SHORTER valid call — write big files in several smaller pieces "
                            "(write_file the head, then append chunks via bash cat >> heredoc)."
                        )
                    else:
                        # temperature=0: the model just replayed the same oversized call.
                        # Force a hard change of plan, not another polite hint.
                        correction = (
                            "STOP. You repeated an oversized tool call and it was rejected "
                            "again. Do NOT retry that call. Your next tool call must be under "
                            "40 lines total. Break the work down: create the file with "
                            "write_file containing ONLY its first small part, verify with "
                            "read_file, then append the remaining parts one small bash "
                            "cat >> chunk at a time."
                        )
                    messages.append({"role": "user", "content": correction})
                time.sleep(min(2 * consecutive_llm_errors, 6))
                continue
            raise
        consecutive_overflows = 0
        consecutive_llm_errors = 0
        msg = (reply.get("choices") or [{}])[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            content = str(msg.get("content") or "").strip()
            _append_transcript(transcript, {"step": step, "prose": content[:500]})
            # A reasoning model can spend an entire multi-minute generation thinking
            # without calling a tool. Silence in the owner's feed looks like a hang,
            # so surface these steps too — he reads the feed to catch exactly this.
            reasoning = str(msg.get("reasoning_content") or "").strip()
            if content:
                emit(f"Пишет без действия: {content[:140]}")
            elif reasoning:
                emit(f"Думает: …{reasoning[-140:]}")
            else:
                emit("Пустой шаг без действия — подталкиваю к инструментам.")
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": "Use the tools. If you are finished and the checks passed, call done."})
            pressured, used_tokens = _context_pressure(reply, messages, settings)
            if pressured:
                messages = compact_session(
                    step=step,
                    reason="Continue the same coding task from this controller checkpoint.",
                    used_tokens=used_tokens,
                )
            continue
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
        for call in tool_calls:
            fn = call.get("function") or {}
            name = str(fn.get("name") or "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "done":
                summary = str(args.get("summary") or "")
                emit(f"Готово, сдаю на приёмку: {summary[:160]}" if summary else "Готово, сдаю на приёмку.")
                lifecycle_checkpoint(
                    step=step,
                    state="The fighter handed its candidate to warband verification.",
                    handoff=summary or "The fighter declared its candidate ready for verification.",
                    next_actions=["Warband acceptance and independent verification must decide the outcome."],
                )
                return {"ok": True, "summary": summary,
                        "artifacts": [str(a) for a in (args.get("artifacts") or [])],
                        "transcript": transcript, "steps": step, "seconds": int(time.monotonic() - started)}
            result = _dispatch_tool(
                executor,
                name,
                args,
                task_id=task_id,
                memory_task_id=memory_id,
                ask_fn=ask_fn,
            )
            _append_transcript(transcript, {
                "step": step,
                "tool": name,
                "args": {k: _bounded_arg(v) for k, v in args.items()},
                "result": result[:800],
            })
            emit(_describe_action(name, args, result))
            messages.append({"role": "tool", "tool_call_id": call.get("id") or "", "content": result[:12_000]})
            if name in {"bash", "bash_background", "write_file", "edit_file"}:
                durable_workspace_checkpoint(
                    step=step,
                    boundary=f"tool:{name}",
                )
        pressured, used_tokens = _context_pressure(reply, messages, settings)
        if pressured:
            messages = compact_session(
                step=step,
                reason="Continue the same coding task from this controller checkpoint.",
                used_tokens=used_tokens,
            )
    lifecycle_checkpoint(
        step=max_steps,
        state="The fighter stopped at its step budget without a verified handoff.",
        failure=f"Step budget exceeded ({max_steps} steps).",
        next_actions=["Inspect the current workspace and resume with a different concrete step."],
    )
    return {"ok": False, "summary": f"step budget exceeded ({max_steps} steps)",
            "artifacts": [], "transcript": transcript, "steps": max_steps, "seconds": int(time.monotonic() - started)}
