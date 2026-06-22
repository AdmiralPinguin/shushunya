#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .agent_runner import safe_task_id


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "runtime"
STATE_PATH = Path(os.environ.get("SHUSHUNYA_AGENT_TASK_WATCH_STATE", str(RUNTIME_DIR / "task-watchdog-state.json")))
DEFAULT_BASE_URL = f"http://{os.environ.get('SHUSHUNYA_AGENT_HOST', '127.0.0.1')}:{os.environ.get('SHUSHUNYA_AGENT_PORT', '8095')}"
DEFAULT_CONTINUE_TASK = (
    "Продолжи выполнение текущей задачи по task journal. "
    "Не повторяй уже выполненные действия. Сначала оцени, что уже сделано, затем продолжай с ближайшего незавершенного шага. "
    "Для сайтов с оглавлениями, SPA и переводами используй найденные tool result ссылки, api_candidates и JSON-карты; "
    "не угадывай URL арифметикой, если предыдущие результаты уже показали структуру разделов. "
    "Если предыдущий прогон остановился на цикле повторяющихся действий, выбери новое продуктивное действие или final вместо тех же проверок. "
    "Если задача уже завершена или дальше продолжать нельзя, верни final и коротко объясни состояние. "
    "Не начинай задачу заново."
)
PUBLIC_CONTINUE_TASK = (
    "Продолжи задачу, описанную в authoritative task snapshot ниже. "
    "Опирайся только на этот task snapshot и текущие tool results, не на Archive semantic memory. "
    "Используй уже найденные tool result ссылки, api_candidates и JSON-карты; не угадывай URL арифметикой. "
    "Если предыдущий прогон остановился на цикле повторяющихся действий, выбери новое продуктивное действие или final вместо тех же проверок. "
    "Не повторяй уже выполненные действия и не начинай задачу заново."
)
USEFUL_CONTEXT_TYPES = {"start", "action", "tool_result", "final", "error", "warning"}


def normalize_task_id(raw: str | None) -> str:
    text = str(raw or "").strip()
    return safe_task_id(text) if text else ""


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(message: str, **fields: Any) -> None:
    payload = {"ts": utc_now(), "message": message, **fields}
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except (OSError, json.JSONDecodeError):
        pass
    return {"attempts": {}, "last_resume_at": {}, "last_final": {}}


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def headers(api_key: str) -> dict[str, str]:
    result = {"Content-Type": "application/json; charset=utf-8"}
    if api_key:
        result["Authorization"] = f"Bearer {api_key}"
    return result


def request_json(base_url: str, method: str, path: str, api_key: str = "", payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers(api_key))
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            return response.status, parsed if isinstance(parsed, dict) else {"raw": parsed}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return exc.code, parsed if isinstance(parsed, dict) else {"raw": parsed}


def final_signature(final: dict[str, Any] | None) -> str:
    if not isinstance(final, dict):
        return ""
    message = str(final.get("message") or final.get("error") or "")
    return json.dumps(
        {
            "ok": final.get("ok"),
            "cancelled": final.get("cancelled"),
            "continuable": final.get("continuable"),
            "resume_task_id": final.get("resume_task_id"),
            "message": message[:500],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def looks_continuable(task: dict[str, Any]) -> bool:
    final = task.get("final")
    if not isinstance(final, dict):
        return True
    if final.get("cancelled") is True:
        return False
    if final.get("ok") is True:
        return False
    if final.get("continuable") is True or final.get("resume_task_id"):
        return True
    message = str(final.get("message") or "").lower()
    markers = (
        "лимит времени",
        "лимит шагов",
        "достигнут лимит",
        "max_runtime",
        "max steps",
        "resume_task_id",
        "можно продолжить",
        "continuable",
    )
    return any(marker in message for marker in markers)


def should_resume(task: dict[str, Any], state: dict[str, Any], now: float, max_attempts: int, cooldown_sec: int) -> tuple[bool, str]:
    task_id = normalize_task_id(str(task.get("task_id") or ""))
    if not task_id:
        return False, "missing_task_id"
    if task.get("running") is True:
        return False, "running"
    final = task.get("final")
    if isinstance(final, dict) and final.get("ok") is True:
        return False, "success"
    if isinstance(final, dict) and final.get("cancelled") is True:
        return False, "cancelled"
    if not looks_continuable(task):
        return False, "not_continuable"

    attempts = state.setdefault("attempts", {})
    last_resume_at = state.setdefault("last_resume_at", {})
    count = int(attempts.get(task_id, 0))
    if count >= max_attempts:
        return False, "max_attempts"
    previous_resume = float(last_resume_at.get(task_id, 0) or 0)
    if previous_resume and now - previous_resume < cooldown_sec:
        return False, "cooldown"
    return True, "continuable"


def remember_resume_attempt(task_id: str, state: dict[str, Any], now: float, final: dict[str, Any] | None) -> None:
    safe_id = normalize_task_id(task_id)
    attempts = state.setdefault("attempts", {})
    last_resume_at = state.setdefault("last_resume_at", {})
    last_final = state.setdefault("last_final", {})
    attempts[safe_id] = int(attempts.get(safe_id, 0)) + 1
    last_resume_at[safe_id] = now
    last_final[safe_id] = final_signature(final)


def reset_attempts_on_progress(task_id: str, state: dict[str, Any], final: dict[str, Any] | None) -> None:
    safe_id = normalize_task_id(task_id)
    if not safe_id:
        return
    signature = final_signature(final)
    last_final = state.setdefault("last_final", {})
    if signature and last_final.get(safe_id) and last_final.get(safe_id) != signature:
        state.setdefault("attempts", {})[safe_id] = 0
    if signature:
        last_final[safe_id] = signature


def target_task_id(base_url: str, api_key: str, explicit_task_id: str) -> str:
    explicit = normalize_task_id(explicit_task_id)
    if explicit:
        return explicit
    status, payload = request_json(base_url, "GET", "/state", api_key)
    if status == 200:
        state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
        task_id = normalize_task_id(str(state.get("current_task_id") or state.get("last_task_id") or ""))
        if task_id:
            return task_id
    status, payload = request_json(base_url, "GET", "/tasks?limit=1", api_key)
    if status != 200:
        return ""
    tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
    if not tasks or not isinstance(tasks[0], dict):
        return ""
    return normalize_task_id(str(tasks[0].get("task_id") or ""))


def task_snapshot(base_url: str, api_key: str, task_id: str) -> tuple[int, dict[str, Any]]:
    query = urllib.parse.urlencode({"task_id": task_id, "limit": 160})
    return request_json(base_url, "GET", f"/task?{query}", api_key)


def compact_value(value: Any, *, string_limit: int = 600, list_limit: int = 8) -> Any:
    if isinstance(value, str):
        return value if len(value) <= string_limit else value[:string_limit] + "\n...[truncated]..."
    if isinstance(value, list):
        return [compact_value(item, string_limit=string_limit, list_limit=list_limit) for item in value[:list_limit]]
    if isinstance(value, dict):
        return {str(key): compact_value(item, string_limit=string_limit, list_limit=list_limit) for key, item in value.items()}
    return value


def compact_watchdog_event(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("type") or "")
    if event_type == "start":
        return {
            "type": "start",
            "task": compact_value(event.get("task"), string_limit=1200),
        }
    if event_type == "action":
        return {
            "type": "action",
            "step": event.get("step"),
            "action": compact_value(event.get("action"), string_limit=400, list_limit=6),
        }
    if event_type == "tool_result":
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        brief_result: dict[str, Any] = {}
        for key in (
            "ok",
            "error",
            "url",
            "status",
            "title",
            "path",
            "chars",
            "bytes_written",
            "skipped_no_text",
            "skipped_not_found",
            "previous_url",
            "next_url",
            "canonical_url",
            "instruction",
            "json_summary",
            "api_candidates",
        ):
            if key in result:
                brief_result[key] = result[key]
        return {
            "type": "tool_result",
            "step": event.get("step"),
            "action": event.get("action"),
            "result": compact_value(brief_result, string_limit=350, list_limit=6),
        }
    return compact_value(event, string_limit=500, list_limit=6)


def public_resume_context(task: dict[str, Any]) -> str:
    events = task.get("events") if isinstance(task.get("events"), list) else []
    useful = [event for event in events if isinstance(event, dict) and str(event.get("type") or "") in USEFUL_CONTEXT_TYPES]
    selected = useful[:1] + useful[-24:]
    context = {
        "source": "public_task_snapshot",
        "rule": (
            "This snapshot is authoritative. Continue from these journal facts and current files only. "
            "Do not use Archive/focus memory as the active task state."
        ),
        "task_id": task.get("task_id"),
        "running": task.get("running"),
        "final": compact_value(task.get("final"), string_limit=700, list_limit=6),
        "events": [compact_watchdog_event(event) for event in selected],
    }
    return json.dumps(context, ensure_ascii=False, indent=2)


def start_resume(base_url: str, api_key: str, task_id: str, max_auto_cycles: int) -> tuple[int, dict[str, Any], str]:
    payload = {
        "task": DEFAULT_CONTINUE_TASK,
        "task_id": task_id,
        "resume_task_id": task_id,
        "technical": True,
        "shell_enabled": False,
        "wait_for_slot": False,
        "auto_continue": True,
        "auto_continue_max_cycles": max_auto_cycles,
    }
    status, result = request_json(base_url, "POST", "/start", api_key, payload)
    if status != 401:
        return status, result, "resume_task_id"

    snapshot_status, snapshot = task_snapshot(base_url, api_key, task_id)
    snapshot_text = public_resume_context(snapshot) if snapshot_status == 200 else json.dumps(
        {"source": "public_task_snapshot", "error": "snapshot unavailable", "status": snapshot_status, "response": snapshot},
        ensure_ascii=False,
        indent=2,
    )
    public_payload = {
        "task": PUBLIC_CONTINUE_TASK + "\n\nAuthoritative task snapshot:\n" + snapshot_text,
        "task_id": f"mobile-watchdog-{int(time.time())}",
        "technical": True,
        "shell_enabled": False,
        "wait_for_slot": False,
        "auto_continue": True,
        "auto_continue_max_cycles": max_auto_cycles,
        "task_memory": False,
        "archive_task": False,
        "skip_previous_task_context": True,
    }
    status, result = request_json(base_url, "POST", "/start", api_key, public_payload)
    return status, result, "public_start"


def run_once(args: argparse.Namespace) -> int:
    base_url = args.base_url.rstrip("/")
    api_key = args.api_key
    task_id = target_task_id(base_url, api_key, args.task_id)
    if not task_id:
        log("task-watchdog: no task to watch")
        return 0

    status, task = task_snapshot(base_url, api_key, task_id)
    if status != 200:
        log("task-watchdog: task snapshot unavailable", task_id=task_id, status=status, response=task)
        return 1

    final = task.get("final") if isinstance(task.get("final"), dict) else None
    state = load_state()
    reset_attempts_on_progress(task_id, state, final)
    now = time.time()
    allowed, reason = should_resume(task, state, now, args.max_attempts, args.cooldown_sec)
    if not allowed:
        save_state(state)
        log(
            "task-watchdog: no resume",
            task_id=task_id,
            reason=reason,
            running=task.get("running"),
            final_ok=final.get("ok") if final else None,
        )
        return 0

    if args.dry_run:
        log("task-watchdog: dry-run resume", task_id=task_id, reason=reason)
        return 0

    remember_resume_attempt(task_id, state, now, final)
    save_state(state)
    start_status, response, mode = start_resume(base_url, api_key, task_id, args.auto_continue_max_cycles)
    ok = 200 <= start_status < 300 and bool(response.get("ok", True))
    log("task-watchdog: resume requested", task_id=task_id, status=start_status, ok=ok, mode=mode, response=response)
    return 0 if ok or start_status in {409, 429} else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor one ShushunyaAgent task and resume continuable failures.")
    parser.add_argument("--base-url", default=os.environ.get("SHUSHUNYA_AGENT_WATCH_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key", default=os.environ.get("SHUSHUNYA_AGENT_API_KEY", ""))
    parser.add_argument("--task-id", default=os.environ.get("SHUSHUNYA_AGENT_WATCH_TASK_ID", ""))
    parser.add_argument("--once", action="store_true", default=os.environ.get("SHUSHUNYA_AGENT_TASK_WATCH_ONCE", "1").lower() not in {"0", "false", "no", "off"})
    parser.add_argument("--interval-sec", type=int, default=int(os.environ.get("SHUSHUNYA_AGENT_TASK_WATCH_INTERVAL_SEC", "900")))
    parser.add_argument("--cooldown-sec", type=int, default=int(os.environ.get("SHUSHUNYA_AGENT_TASK_WATCH_COOLDOWN_SEC", "300")))
    parser.add_argument("--max-attempts", type=int, default=int(os.environ.get("SHUSHUNYA_AGENT_TASK_WATCH_MAX_ATTEMPTS", "8")))
    parser.add_argument("--auto-continue-max-cycles", type=int, default=int(os.environ.get("SHUSHUNYA_AGENT_TASK_WATCH_AUTO_CONTINUE_MAX_CYCLES", "3")))
    parser.add_argument("--dry-run", action="store_true", default=os.environ.get("SHUSHUNYA_AGENT_TASK_WATCH_DRY_RUN", "").lower() in {"1", "true", "yes", "on"})
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    while True:
        code = run_once(args)
        if args.once:
            return code
        time.sleep(max(5, int(args.interval_sec)))


if __name__ == "__main__":
    raise SystemExit(main())
