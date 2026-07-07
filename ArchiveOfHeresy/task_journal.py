"""Brigade task journal: everything Shushunya's departments do is remembered.

Polls Warmaster runs and, on lifecycle transitions (task started, task finished
with success/failure), writes an entry into memory: a labeled vector chunk in
the shared namespace plus a deterministic wiki journal page. Nothing is posted
to chat — this is memory only, so the persona can answer "что там с задачей X"
and "чем ты занималась" from her own history.
"""
import json
import threading
import time
import os
from datetime import datetime
from pathlib import Path

import archive_state
from archive_config import WARMASTER_BASE_URL
from archive_httpio import proxy_json_url
from archive_util import shared_memory_namespace, wiki_bookshelf_for_namespace

TASK_JOURNAL_ENABLED = os.environ.get("ARCHIVE_TASK_JOURNAL_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
TASK_JOURNAL_INTERVAL_SEC = max(15.0, float(os.environ.get("ARCHIVE_TASK_JOURNAL_INTERVAL_SEC", "60")))
TASK_JOURNAL_RUNS_LIMIT = int(os.environ.get("ARCHIVE_TASK_JOURNAL_RUNS_LIMIT", "30"))
TASK_JOURNAL_MAX_LINES = int(os.environ.get("ARCHIVE_TASK_JOURNAL_MAX_LINES", "300"))
STATE_PATH = Path(__file__).resolve().parent / "archive" / "task_journal_state.json"
JOURNAL_PAGE_TITLE = "Brigade Task Journal"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def fetch_runs():
    _status, response = proxy_json_url("GET", f"{WARMASTER_BASE_URL}/runs?limit={TASK_JOURNAL_RUNS_LIMIT}", timeout=30)
    runs = response.get("runs") if isinstance(response.get("runs"), list) else []
    return [run for run in runs if isinstance(run, dict) and str(run.get("task_id") or "").strip()]


def run_entry_text(run, event):
    task_id = str(run.get("task_id") or "")
    governor = str(run.get("governor") or "").strip() or "неизвестный губернатор"
    goal = " ".join(str(run.get("goal") or "").split())[:300]
    progress = run.get("progress") if isinstance(run.get("progress"), dict) else {}
    planned = progress.get("planned_steps")
    completed = progress.get("completed_steps")
    step_note = f", шаги {completed}/{planned}" if planned else ""
    status = str(run.get("status") or "").lower()
    if event == "started":
        return f"Шушуня начала задачу бригады {task_id} (губернатор {governor}): {goal}"
    outcome = {"completed": "успешно выполнила", "failed": "провалила", "cancelled": "отменила"}.get(status, f"завершила со статусом {status}")
    return f"Шушуня {outcome} задачу бригады {task_id} (губернатор {governor}{step_note}): {goal}"


def remember_entry(entry_text, task_id, event):
    namespace = shared_memory_namespace(None)
    if archive_state.VECTOR_MEMORY is not None:
        record = {
            "turn_id": f"taskjournal-{task_id}-{event}",
            "conversation_id": "brigade-task-journal",
            "memory_namespace": namespace,
            "created_at": now_iso(),
            "status": "ok",
            "request": {"text": entry_text},
            "assistant_message": None,
        }
        try:
            archive_state.VECTOR_MEMORY.index_turn(record, label="задача")
        except Exception as exc:  # noqa: BLE001 - journal must never break the poller
            print(f"Task journal vector write failed: {exc}", flush=True)
    try:
        append_journal_page(entry_text, namespace, task_id, event)
    except Exception as exc:  # noqa: BLE001
        print(f"Task journal wiki write failed: {exc}", flush=True)


def append_journal_page(entry_text, namespace, task_id, event):
    bookshelf = wiki_bookshelf_for_namespace(namespace)
    with archive_state.MAINTENANCE_LOCK:
        index = bookshelf.load_index()
        page = bookshelf.find_page(index, title=JOURNAL_PAGE_TITLE)
        body_lines = []
        if page:
            content = bookshelf.read_page(page)
            if content.startswith("---"):
                parts = content.split("---", 2)
                content = parts[2] if len(parts) == 3 else content
            body_lines = [line for line in content.strip().splitlines() if line.strip()]
            if body_lines and body_lines[0].startswith("#"):
                body_lines = body_lines[1:]
        body_lines.append(f"- {now_iso()} — {entry_text}")
        body_lines = body_lines[-TASK_JOURNAL_MAX_LINES:]
        body = "Журнал дел Шушуни: какие задачи бригад она начинала и чем они закончились.\n\n" + "\n".join(body_lines)
        bookshelf.upsert_page(
            index,
            {
                "id": page.get("id") if page else None,
                "title": JOURNAL_PAGE_TITLE,
                "kind": "journal",
                "importance": 3,
                "body": body,
            },
            {"turn_id": f"taskjournal-{task_id}-{event}"},
        )
        bookshelf.save_index(index)


def poll_once():
    runs = fetch_runs()
    state = load_state()
    first_run = not state
    changed = False
    for run in runs:
        task_id = str(run.get("task_id") or "")
        status = str(run.get("status") or "").lower()
        previous = state.get(task_id)
        if previous == status:
            continue
        state[task_id] = status
        changed = True
        if first_run:
            continue  # baseline pass: learn current state silently, no retro-entries
        if previous is None and status not in TERMINAL_STATUSES:
            remember_entry(run_entry_text(run, "started"), task_id, "started")
        elif status in TERMINAL_STATUSES:
            remember_entry(run_entry_text(run, "finished"), task_id, f"finished-{status}")
    if changed:
        save_state(state)
    return {"runs": len(runs), "baseline": first_run}


def task_journal_loop():
    while True:
        try:
            poll_once()
        except Exception as exc:  # noqa: BLE001 - keep the loop alive across Warmaster restarts
            print(f"Task journal poll failed: {exc}", flush=True)
        time.sleep(TASK_JOURNAL_INTERVAL_SEC)


def start_task_journal_thread():
    if not TASK_JOURNAL_ENABLED:
        return False
    threading.Thread(target=task_journal_loop, daemon=True, name="brigade-task-journal").start()
    return True
