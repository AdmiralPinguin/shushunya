from __future__ import annotations

from typing import Any


def failed_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [check for check in checks if isinstance(check, dict) and check.get("ok") is not True]


def agent_unavailable(exit_code: int | None, error: str = "") -> bool:
    if exit_code in {78, 127}:
        return True
    lowered = error.lower()
    return any(marker in lowered for marker in ("missing docker", "missing docker/podman", "adapter not configured"))


def failure_reason(exit_code: int | None, checks: list[dict[str, Any]], error: str = "") -> str:
    if agent_unavailable(exit_code, error):
        return "agent_unavailable"
    exit_failed = exit_code not in (0, None)
    checks_failed = bool(failed_checks(checks))
    if exit_failed and checks_failed:
        return "both"
    if checks_failed:
        return "post_run_checks"
    if exit_failed:
        return "agent_exit"
    return "unknown"


def failed_check_symptoms(check: dict[str, Any]) -> list[str]:
    text = "\n".join(str(check.get(key) or "") for key in ("error", "output", "command"))
    markers = {
        "json_decode_error": "JSONDecodeError",
        "assertion_error": "AssertionError",
        "type_error": "TypeError",
        "import_error": "ImportError",
        "module_not_found": "ModuleNotFoundError",
        "syntax_error": "SyntaxError",
        "missing_output": "Expecting value",
    }
    return [symptom for symptom, marker in markers.items() if marker in text]


def summarize_failed_check(check: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "type": check.get("type", "unknown"),
        "path": check.get("path", ""),
    }
    for key in ("exit_code", "error"):
        if check.get(key) not in (None, ""):
            summary[key] = check.get(key)
    if check.get("text"):
        summary["text"] = str(check["text"])[:160]
    if check.get("command"):
        summary["command"] = str(check["command"])[:240]
    if check.get("output"):
        summary["output_tail"] = str(check["output"])[-500:]
    return summary
