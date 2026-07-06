from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from .archive_bridge import deliver_system_event
from .storage import (
    add_journal,
    claim_due_tasks,
    claim_due_watches,
    complete_due_task,
    complete_watch_check,
    create_task,
    init_db,
    list_journal,
    list_tasks,
    list_watches,
)
from .timeutil import DEFAULT_TZ, next_run_after, now_iso, parse_datetime


MORNING_SUMMARY_DEDUPE = "routine:administratum-morning-summary"

ARCHIVE_HEALTH_URL = os.environ.get("ADMINISTRATUM_ARCHIVE_HEALTH_URL", "http://127.0.0.1:8090/health")
WARMASTER_HEALTH_URL = os.environ.get("ADMINISTRATUM_WARMASTER_HEALTH_URL", "http://127.0.0.1:7000/health")
LLM_HEALTH_URL = os.environ.get("ADMINISTRATUM_LLM_HEALTH_URL", "http://127.0.0.1:8080/health")


def ensure_default_routines(db_path: Path) -> None:
    existing = [task for task in list_tasks(db_path=db_path) if task.get("dedupe_key") == MORNING_SUMMARY_DEDUPE]
    if existing:
        return
    now = parse_datetime(now_iso(DEFAULT_TZ), DEFAULT_TZ)
    first_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if first_run <= now:
        first_run = first_run + timedelta(days=1)
    create_task(
        {
            "kind": "routine",
            "title": "Утренняя сводка",
            "body": "Собрать задачи на сегодня, просроченные задачи, ночной журнал, watches и здоровье сервисов.",
            "interval": "1d",
            "next_run": first_run.astimezone(ZoneInfo(DEFAULT_TZ)).isoformat(),
            "timezone": DEFAULT_TZ,
            "created_by": "AshurKai",
            "dedupe_key": MORNING_SUMMARY_DEDUPE,
            "payload": {"system": True, "routine": "morning_summary"},
        },
        db_path=db_path,
    )


def morning_summary_payload(db_path: Path) -> dict[str, Any]:
    tasks = list_tasks(db_path=db_path)
    watches = list_watches(db_path=db_path)
    journal = list_journal(limit=20, db_path=db_path)
    return {
        "active_tasks": [task for task in tasks if task.get("status") == "active"][:20],
        "running_tasks": [task for task in tasks if task.get("status") == "running"][:20],
        "watches": watches[:20],
        "recent_journal": journal[:20],
        "service_health": service_health_payload(),
    }


def check_http_health(name: str, url: str, timeout: float = 5.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = response.read(8192).decode("utf-8", errors="replace")
            payload = {}
            try:
                payload = json.loads(data) if data else {}
            except json.JSONDecodeError:
                payload = {"raw": data[:500]}
            return {"name": name, "ok": 200 <= int(response.status) < 300, "status": int(response.status), "url": url, "payload": payload}
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "ok": False, "url": url, "error": str(exc)}


def service_health_payload() -> dict[str, Any]:
    return {
        "archive": check_http_health("ArchiveOfHeresy", ARCHIVE_HEALTH_URL),
        "warmaster": check_http_health("Warmaster", WARMASTER_HEALTH_URL),
        "llm": check_http_health("LLM", LLM_HEALTH_URL),
        "administratum": {"name": "Administratum", "ok": True, "service": "AshurKai heartbeat"},
    }


def task_event_body(task: dict[str, Any], db_path: Path) -> tuple[str, dict[str, Any]]:
    if task.get("dedupe_key") == MORNING_SUMMARY_DEDUPE or task.get("kind") == "routine":
        payload = morning_summary_payload(db_path)
        return (
            "Утренняя сводка Администратума. Расскажи владельцу, что на сегодня, что просрочено, что делал ночью, "
            "и какие watches/сервисы требуют внимания.",
            payload,
        )
    return (f"Сработало напоминание: {task.get('title')}. {task.get('body') or ''}".strip(), {"task": task})


def json_value(raw: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def fetch_watch_value(watch: dict[str, Any]) -> dict[str, Any]:
    target = str(watch.get("target") or "").strip()
    if not target:
        raise ValueError("watch target is empty")
    if not target.startswith(("http://", "https://")):
        return {
            "kind": "literal",
            "target": target,
            "fingerprint": hashlib.sha256(target.encode("utf-8")).hexdigest(),
            "preview": target[:500],
            "fetched_at": now_iso(),
        }
    request = urllib.request.Request(target, headers={"User-Agent": "Shushunya-Administratum/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(1024 * 1024)
        status = int(getattr(response, "status", 200))
        content_type = response.headers.get("Content-Type", "")
    text = data.decode("utf-8", errors="replace")
    return {
        "kind": "http",
        "target": target,
        "status": status,
        "content_type": content_type,
        "length": len(data),
        "fingerprint": hashlib.sha256(data).hexdigest(),
        "preview": text[:1000],
        "fetched_at": now_iso(),
    }


def evaluate_watch(watch: dict[str, Any], current: dict[str, Any]) -> tuple[bool, str]:
    condition = json_value(watch.get("condition_json"))
    previous = json_value(watch.get("last_value_json"))
    mode = str(condition.get("mode") or condition.get("type") or "changed").strip().lower()
    interval = str(condition.get("interval") or condition.get("check_interval") or "15m").strip() or "15m"
    current["next_interval"] = interval
    if not previous or not previous.get("fingerprint"):
        return False, "baseline"
    if mode in {"always", "heartbeat"}:
        return True, "watch_check"
    if mode in {"contains", "text_contains"}:
        needle = str(condition.get("text") or condition.get("contains") or "").strip()
        if not needle:
            return False, "missing_contains_text"
        matched = needle.lower() in str(current.get("preview") or "").lower()
        current["matched"] = matched
        previous_matched = bool(previous.get("matched"))
        return matched and not previous_matched, "watch_matched" if matched else "watch_not_matched"
    changed = current.get("fingerprint") != previous.get("fingerprint")
    return changed, "watch_changed" if changed else "watch_unchanged"


def process_due_watches(db_path: Path, limit: int = 20) -> list[dict[str, Any]]:
    due_watches = claim_due_watches(limit=limit, db_path=db_path)
    results = []
    for watch in due_watches:
        watch_id = str(watch.get("id") or "")
        try:
            current = fetch_watch_value(watch)
            should_deliver, event_kind = evaluate_watch(watch, current)
            interval = current.pop("next_interval", "15m")
            next_check = next_run_after(now_iso(), interval, DEFAULT_TZ) or now_iso()
            complete_watch_check(watch, current, next_check, db_path=db_path)
            add_journal(event_kind, f"Watch checked: {watch.get('title')}", db_path=db_path, watch_id=watch_id, payload=current)
            delivery = {"ok": True, "skipped": True, "reason": event_kind}
            if should_deliver:
                body = f"Сработало наблюдение: {watch.get('title')}. Цель: {watch.get('target')}. Причина: {event_kind}."
                delivery = deliver_system_event(event_kind, body, payload={"watch": watch, "value": current})
                add_journal("watch_delivered" if delivery.get("ok") else "watch_delivery_failed", body, db_path=db_path, watch_id=watch_id, payload=delivery)
            results.append({"watch_id": watch_id, "event_kind": event_kind, "delivery": delivery})
        except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
            next_check = next_run_after(now_iso(), "15m", DEFAULT_TZ) or now_iso()
            complete_watch_check(watch, {}, next_check, db_path=db_path, status="active", error=str(exc))
            add_journal("watch_check_failed", str(exc), db_path=db_path, watch_id=watch_id)
            results.append({"watch_id": watch_id, "error": str(exc)})
    return results


def run_once(db_path: Path, limit: int = 20) -> dict[str, Any]:
    init_db(db_path)
    ensure_default_routines(db_path)
    due_tasks = claim_due_tasks(limit=limit, db_path=db_path)
    results = []
    for task in due_tasks:
        task_id = str(task.get("id") or "")
        body, payload = task_event_body(task, db_path)
        add_journal("task_triggered", body, db_path=db_path, task_id=task_id, payload=payload)
        delivery = deliver_system_event(f"{task.get('kind')}_triggered", body, payload={"task": task, **payload})
        if delivery.get("ok"):
            complete_due_task(task, db_path=db_path)
            add_journal("task_delivered", f"Delivered task event to Archive: {task.get('title')}", db_path=db_path, task_id=task_id, payload=delivery)
        else:
            error = str(delivery.get("error") or delivery)
            complete_due_task(task, db_path=db_path, error=error)
            add_journal("task_delivery_failed", error, db_path=db_path, task_id=task_id, payload=delivery)
        results.append({"task_id": task_id, "delivery": delivery})
    watch_results = process_due_watches(db_path, limit=limit)
    return {
        "ok": True,
        "checked_at": now_iso(),
        "due_count": len(due_tasks),
        "watch_due_count": len(watch_results),
        "results": results,
        "watch_results": watch_results,
    }


def loop(db_path: Path, interval_sec: float = 60.0) -> None:
    init_db(db_path)
    ensure_default_routines(db_path)
    while True:
        try:
            result = run_once(db_path)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
        time.sleep(max(1.0, float(interval_sec)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Administratum AshurKai heartbeat.")
    parser.add_argument("--db", default="")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=60.0)
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else Path(__file__).resolve().parent / "runtime" / "administratum.sqlite3"
    if args.once:
        print(json.dumps(run_once(db_path), ensure_ascii=False, indent=2))
        return 0
    loop(db_path, interval_sec=args.interval_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
