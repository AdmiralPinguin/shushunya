"""Extensible tool registry for the Skitarii fighter.

Instead of a hard-coded if-chain, tools are Tool objects with a permission, a timeout
and a handler. New tools (git, symbol search, glob) plug in here; an allow/deny list
(env SKITARII_TOOLS_ALLOW / SKITARII_TOOLS_DENY) gates them. The harness asks this
registry for the OpenAI tool specs and delegates dispatch to it. Browser/MCP/skills
are future entries; hooks come last (they don't improve coding quality directly).
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_OUTPUT = int(os.environ.get("SKITARII_TOOL_MAX_OUTPUT", "20000"))


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any], Any], str]   # (args, executor) -> str
    permission: str = "sandbox"     # sandbox | network | memory
    timeout: int = 120
    required: list[str] = field(default_factory=list)

    def spec(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.parameters, "required": self.required}}}


def _clip(s: str) -> str:
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + f"\n…(truncated at {MAX_OUTPUT} chars)"


# --- handlers for the extra tools (executor-based) -------------------------------
def _h_git_diff(args: dict[str, Any], ex: Any) -> str:
    return _clip(ex.bash("git diff HEAD 2>/dev/null || git diff", timeout=60).get("stdout") or "(no diff)")


def _h_git_log(args: dict[str, Any], ex: Any) -> str:
    n = int(args.get("count") or 10)
    return _clip(ex.bash(f"git log --oneline -{n} 2>/dev/null", timeout=30).get("stdout") or "(no git history)")


def _h_find_files(args: dict[str, Any], ex: Any) -> str:
    pat = str(args.get("pattern") or "*")
    return _clip(ex.bash(f"find . -type f -name {shlex.quote(pat)} -not -path './.git/*' 2>/dev/null | head -100",
                         timeout=30).get("stdout") or "(none)")


def _h_grep_symbol(args: dict[str, Any], ex: Any) -> str:
    sym = str(args.get("symbol") or "")
    if not sym:
        return "ERROR: empty symbol"
    return _clip(ex.bash(f"grep -rnI --exclude-dir=.git {shlex.quote(sym)} . 2>/dev/null | head -80",
                         timeout=45).get("stdout") or "(no references)")


def _h_list_dir(args: dict[str, Any], ex: Any) -> str:
    d = str(args.get("path") or ".")
    return _clip(ex.bash(f"ls -la {shlex.quote(d)} 2>/dev/null", timeout=20).get("stdout") or "(empty)")


EXTRA_TOOLS: list[Tool] = [
    Tool("git_diff", "Show the current unified diff of your changes vs the project baseline.",
         {}, _h_git_diff, permission="sandbox", timeout=60),
    Tool("git_log", "Show recent commit history (oneline).",
         {"count": {"type": "integer", "description": "how many commits (default 10)"}}, _h_git_log),
    Tool("find_files", "Find files by glob pattern (e.g. '*.py', 'test_*').",
         {"pattern": {"type": "string"}}, _h_find_files, required=["pattern"]),
    Tool("grep_symbol", "Find where a symbol/name is defined or used across the project.",
         {"symbol": {"type": "string"}}, _h_grep_symbol, required=["symbol"]),
    Tool("list_dir", "List a directory's contents.",
         {"path": {"type": "string", "description": "directory, default '.'"}}, _h_list_dir),
]


def _gate() -> tuple[set[str], set[str]]:
    allow = {t.strip() for t in os.environ.get("SKITARII_TOOLS_ALLOW", "").split(",") if t.strip()}
    deny = {t.strip() for t in os.environ.get("SKITARII_TOOLS_DENY", "").split(",") if t.strip()}
    return allow, deny


def enabled_extra_tools() -> list[Tool]:
    allow, deny = _gate()
    out = []
    for t in EXTRA_TOOLS:
        if t.name in deny:
            continue
        if allow and t.name not in allow:
            continue
        out.append(t)
    return out


def extra_specs() -> list[dict[str, Any]]:
    return [t.spec() for t in enabled_extra_tools()]


_BY_NAME = {t.name: t for t in EXTRA_TOOLS}


def dispatch_extra(name: str, args: dict[str, Any], executor: Any) -> str | None:
    """Return the tool result, or None if `name` is not a registry tool (handled elsewhere)."""
    tool = _BY_NAME.get(name)
    if tool is None or tool not in enabled_extra_tools():
        return None
    try:
        return tool.handler(args, executor)
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {type(exc).__name__}: {exc}"
