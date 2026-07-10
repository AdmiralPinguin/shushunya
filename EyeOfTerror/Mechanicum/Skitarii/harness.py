"""Skitarii fighter: an agentic tool loop with the functions of a coding agent.

One fighter = one session: it gets the user's goal VERBATIM plus executable
success checks, then loops write -> RUN -> read stderr -> fix until the checks
pass or the budget runs out. All execution goes through an Executor (VM).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
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
- To test a server/service, launch it with bash_background, probe it with bash (curl localhost, read its log), then kill its pid before finishing.
- You MUST actually run the success checks yourself with bash and see them pass before calling done.
- If a check cannot pass for a real external reason, call done with an honest summary of what is missing; never invent success.
- Keep files inside the workdir; use relative paths.
- On anything non-trivial, use memory_note to record key decisions and where you are, and memory_read to recover context — do not rely only on the chat window.
"""


def _llm_settings() -> dict[str, Any]:
    base = os.environ.get("SKITARII_LLM_BASE_URL", "http://127.0.0.1:8081/v1").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return {
        "base_url": base,
        "model": os.environ.get("SKITARII_LLM_MODEL", "Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf"),
        "timeout_sec": float(os.environ.get("SKITARII_LLM_TIMEOUT_SEC", "900")),
        "max_tokens": int(os.environ.get("SKITARII_LLM_MAX_TOKENS", "8192")),
    }


def _chat(messages: list[dict], settings: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "model": settings["model"],
        "messages": messages,
        "tools": TOOLS + _tools.extra_specs(),
        "temperature": 0,
        "max_tokens": settings["max_tokens"],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{settings['base_url']}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=settings["timeout_sec"]) as resp:
        return json.loads(resp.read().decode("utf-8"))


ARCHIVE_URL = os.environ.get("SKITARII_ARCHIVE_URL", "http://127.0.0.1:8090").rstrip("/")


def _memory_note(task_id: str, note: str) -> str:
    body = json.dumps({"task_id": task_id, "note": note}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{ARCHIVE_URL}/archive/task-page", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=15).read()
    return "noted"


def _memory_read(task_id: str) -> str:
    from urllib.parse import quote
    req = urllib.request.Request(f"{ARCHIVE_URL}/archive/task-page?task_id={quote(task_id)}")
    data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
    return str(data.get("content") or "(память задачи пуста)")


def _dispatch_tool(executor: Any, name: str, args: dict[str, Any], task_id: str = "",
                   ask_fn=None) -> str:
    try:
        if name == "ask_user":
            q = str(args.get("question") or "").strip()
            if not q:
                return "ERROR: empty question"
            if ask_fn is None:
                return "No interactive user is available. Proceed with the most sensible default and note the assumption."
            return ask_fn(q) or "(no answer given — proceed on your best judgement)"
        if name == "memory_note":
            return _memory_note(task_id, str(args.get("note") or "")) if task_id else "ERROR: no task_id"
        if name == "memory_read":
            return _memory_read(task_id) if task_id else "ERROR: no task_id"
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


def run_fighter(goal: str, checks: list[Any], executor: Any,
                max_steps: int = 40, max_wall_sec: int = 3600, task_id: str = "",
                ask_fn=None, cancel_fn=None) -> dict[str, Any]:
    """The agentic loop. Returns {ok, summary, artifacts, transcript, steps, seconds}."""
    settings = _llm_settings()
    started = time.monotonic()
    checks_text = "\n".join(_check_text(c) for c in checks) or "- (no explicit checks; prove the program runs)"
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"GOAL (verbatim):\n{goal}\n\nSUCCESS CHECKS you must run and pass:\n{checks_text}"},
    ]
    transcript: list[dict] = []
    for step in range(1, max_steps + 1):
        if cancel_fn is not None and cancel_fn():
            return {"ok": False, "summary": "cancelled by user", "artifacts": [],
                    "transcript": transcript, "steps": step, "seconds": int(time.monotonic() - started),
                    "cancelled": True}
        if time.monotonic() - started > max_wall_sec:
            return {"ok": False, "summary": f"wall-clock budget exceeded ({max_wall_sec}s)",
                    "artifacts": [], "transcript": transcript, "steps": step, "seconds": int(time.monotonic() - started)}
        reply = _chat(messages, settings)
        msg = (reply.get("choices") or [{}])[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            # model answered with prose instead of a tool call: nudge once, then stop
            content = str(msg.get("content") or "").strip()
            transcript.append({"step": step, "prose": content[:500]})
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": "Use the tools. If you are finished and the checks passed, call done."})
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
                return {"ok": True, "summary": str(args.get("summary") or ""),
                        "artifacts": [str(a) for a in (args.get("artifacts") or [])],
                        "transcript": transcript, "steps": step, "seconds": int(time.monotonic() - started)}
            result = _dispatch_tool(executor, name, args, task_id=task_id, ask_fn=ask_fn)
            transcript.append({"step": step, "tool": name,
                               "args": {k: (v[:200] if isinstance(v, str) else v) for k, v in args.items()},
                               "result": result[:800]})
            messages.append({"role": "tool", "tool_call_id": call.get("id") or "", "content": result[:12_000]})
    return {"ok": False, "summary": f"step budget exceeded ({max_steps} steps)",
            "artifacts": [], "transcript": transcript, "steps": max_steps, "seconds": int(time.monotonic() - started)}
