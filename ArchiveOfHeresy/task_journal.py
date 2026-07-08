"""Brigade task journal: everything Shushunya's departments do is remembered.

Polls Warmaster runs and, on lifecycle transitions (task started, task finished
with success/failure), writes an entry into memory: a labeled vector chunk in
the shared namespace plus a deterministic wiki journal page. Completed final
answers are also delivered to the shared chat once, while brigade progress stays
out of the chat and remains available through Warmaster activity endpoints.
"""
import json
import threading
import time
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import archive_state
from archive_config import WARMASTER_BASE_URL
from archive_httpio import proxy_json_url
from archive_util import shared_memory_namespace, wiki_bookshelf_for_namespace
from pending_reports import enqueue_report

TASK_JOURNAL_ENABLED = os.environ.get("ARCHIVE_TASK_JOURNAL_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
TASK_JOURNAL_INTERVAL_SEC = max(15.0, float(os.environ.get("ARCHIVE_TASK_JOURNAL_INTERVAL_SEC", "60")))
TASK_JOURNAL_RUNS_LIMIT = int(os.environ.get("ARCHIVE_TASK_JOURNAL_RUNS_LIMIT", "30"))
TASK_JOURNAL_MAX_LINES = int(os.environ.get("ARCHIVE_TASK_JOURNAL_MAX_LINES", "300"))
TASK_ESCALATION_TO_CHAT = os.environ.get("ARCHIVE_TASK_ESCALATION_TO_CHAT_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
STATE_PATH = Path(__file__).resolve().parent / "archive" / "task_journal_state.json"
JOURNAL_PAGE_TITLE = "Brigade Task Journal"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
SHARED_CHAT_SESSION_ID = os.environ.get("ARCHIVE_SHARED_CHAT_SESSION_ID", "shushunya-main").strip() or "shushunya-main"


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


def fetch_orchestration(task_id):
    _status, response = proxy_json_url(
        "GET",
        f"{WARMASTER_BASE_URL}/runs/{quote(task_id, safe='')}/orchestration?event_limit=0&events_after=0&max_bytes=20000",
        timeout=30,
    )
    return response if isinstance(response, dict) else {}


def _orchestration_summary(orchestration):
    summary = orchestration.get("summary") if isinstance(orchestration.get("summary"), dict) else {}
    if summary:
        return summary
    snapshot = orchestration.get("snapshot") if isinstance(orchestration.get("snapshot"), dict) else {}
    return snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}


def _latest_acceptance_review(protocol):
    review = protocol.get("acceptance_review") if isinstance(protocol.get("acceptance_review"), dict) else {}
    reviews = protocol.get("acceptance_reviews") if isinstance(protocol.get("acceptance_reviews"), list) else []
    valid_reviews = [item for item in reviews if isinstance(item, dict)]
    if valid_reviews:
        review = valid_reviews[-1]
    return review if isinstance(review, dict) else {}


def _warmaster_accepted_protocol_final(protocol):
    review = _latest_acceptance_review(protocol)
    return (
        str(review.get("type") or "") == "acceptance_review"
        and str(review.get("reviewer") or "") == "Warmaster"
        and review.get("accepted") is True
    )


def final_response_message_from_orchestration(orchestration):
    status = str(orchestration.get("status") or "").strip().lower()
    summary = _orchestration_summary(orchestration)
    if not status:
        status = str(summary.get("status") or "").strip().lower()
    if status != "completed":
        return ""
    protocol = summary.get("mission_protocol") if isinstance(summary.get("mission_protocol"), dict) else {}
    if not _warmaster_accepted_protocol_final(protocol):
        return ""
    final_response = protocol.get("final_response") if isinstance(protocol.get("final_response"), dict) else {}
    if str(final_response.get("type") or "") != "final_response":
        return ""
    return str(final_response.get("answer") or "").strip()


def final_message_from_orchestration(orchestration):
    return final_response_message_from_orchestration(orchestration)


def deliver_final_to_chat(task_id, run=None):
    """Queue the accepted final answer; the owner releases it via the report
    button or by asking for news (pending-reports outbox)."""
    try:
        final_message = final_message_from_orchestration(fetch_orchestration(task_id))
    except Exception as exc:  # noqa: BLE001 - final delivery must not break the journal loop
        print(f"Task journal final fetch failed for {task_id}: {exc}", flush=True)
        return False
    if not final_message:
        return False
    goal = " ".join(str((run or {}).get("goal") or "").split())[:120] or task_id
    body = f"Задача бригады выполнена и принята Warmaster'ом.\ntask: {goal}\nfinal ответ:\n{final_message[:4000]}"
    report_id = enqueue_report("warmaster", "task_completed", f"готово: {goal}", body, dedupe_key=f"warmaster:{task_id}:final")
    return bool(report_id)


def escalation_facts(task_id, run):
    """Collect Warmaster's own verdict about a stuck/failed run: acceptance
    reason, revision order, manifest blockers. Data packaging only — the
    judgement already happened in Warmaster's acceptance review."""
    facts = {
        "task_id": task_id,
        "goal": " ".join(str(run.get("goal") or "").split())[:300],
        "governor": str(run.get("governor") or ""),
        "status": str(run.get("status") or "").lower(),
    }
    try:
        orchestration = fetch_orchestration(task_id)
    except Exception as exc:  # noqa: BLE001 - escalation must survive Warmaster hiccups
        facts["detail_error"] = str(exc)
        return facts
    summary = _orchestration_summary(orchestration)
    protocol = summary.get("mission_protocol") if isinstance(summary.get("mission_protocol"), dict) else {}
    review = _latest_acceptance_review(protocol)
    if review:
        facts["warmaster_reason"] = str(review.get("reason") or "")
        facts["escalate_to_user"] = bool(review.get("escalate_to_user"))
        required = review.get("required_revision") if isinstance(review.get("required_revision"), dict) else {}
        if required.get("order"):
            facts["required_order"] = str(required.get("order") or "")
    manifest = summary.get("final_manifest_summary") if isinstance(summary.get("final_manifest_summary"), dict) else {}
    blockers = manifest.get("blockers") if isinstance(manifest.get("blockers"), list) else []
    if blockers:
        facts["blockers"] = [str(item)[:200] for item in blockers[:5]]
    return facts


def deliver_escalation_to_chat(task_id, run, event_kind):
    """Queue a Warmaster escalation report; it reaches the chat only when the
    owner presses the report button or asks for news (pending-reports outbox)."""
    if not TASK_ESCALATION_TO_CHAT:
        return False
    facts = escalation_facts(task_id, run)
    if event_kind == "task_blocked":
        lines = ["Задача бригады остановлена и ждёт решения владельца."]
    else:
        lines = ["Задача бригады провалена."]
    lines.append(f"task: {facts.get('goal')}")
    lines.append(f"губернатор: {facts.get('governor')}; task_id: {task_id}")
    if facts.get("warmaster_reason"):
        lines.append(f"вердикт Warmaster'а: {facts['warmaster_reason']}")
    if facts.get("required_order"):
        lines.append(f"что требуется: {facts['required_order']}")
    for blocker in facts.get("blockers") or []:
        lines.append(f"блокер: {blocker}")
    topic = ("нужно решение: " if event_kind == "task_blocked" else "провал задачи: ") + str(facts.get("goal") or task_id)[:120]
    report_id = enqueue_report("warmaster", event_kind, topic, "\n".join(lines), dedupe_key=f"warmaster:{task_id}:{event_kind}")
    return bool(report_id)


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
        return f"Шушуня начал задачу бригады {task_id} (губернатор {governor}): {goal}"
    if event == "blocked":
        return f"Задача бригады {task_id} остановлена и ждёт решения владельца (губернатор {governor}{step_note}): {goal}"
    outcome = {"completed": "успешно выполнил", "failed": "провалил", "cancelled": "отменил"}.get(status, f"завершил со статусом {status}")
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
        if previous is None and status not in TERMINAL_STATUSES and status != "blocked":
            remember_entry(run_entry_text(run, "started"), task_id, "started")
        elif status == "blocked":
            remember_entry(run_entry_text(run, "blocked"), task_id, "blocked")
            deliver_escalation_to_chat(task_id, run, "task_blocked")
        elif status in TERMINAL_STATUSES:
            remember_entry(run_entry_text(run, "finished"), task_id, f"finished-{status}")
            if status == "completed":
                deliver_final_to_chat(task_id, run)
            elif status == "failed":
                deliver_escalation_to_chat(task_id, run, "task_failed")
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
