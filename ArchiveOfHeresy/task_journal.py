"""Brigade task journal: everything Shushunya's departments do is remembered.

Polls Warmaster runs and, on lifecycle transitions (task started, task finished
with success/failure), writes an entry into memory: a labeled vector chunk in
the shared namespace plus a deterministic wiki journal page. Completed final
answers are also delivered to the shared chat once, while brigade progress stays
out of the chat and remains available through Warmaster activity endpoints.
"""
import hashlib
import json
import mimetypes
import os
import re
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import archive_state
from archive_config import ARTIFACT_MAX_BYTES, SHARED_CHAT_SESSION_ID, WARMASTER_BASE_URL
from archive_httpio import proxy_json_url
from archive_util import shared_memory_namespace, wiki_bookshelf_for_namespace
from artifact_store import trusted_import_stream
from pending_reports import enqueue_report


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


_PRIVATE_BINARY_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirect(),
)

TASK_JOURNAL_ENABLED = os.environ.get("ARCHIVE_TASK_JOURNAL_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
TASK_JOURNAL_INTERVAL_SEC = max(15.0, float(os.environ.get("ARCHIVE_TASK_JOURNAL_INTERVAL_SEC", "60")))
TASK_JOURNAL_RUNS_LIMIT = int(os.environ.get("ARCHIVE_TASK_JOURNAL_RUNS_LIMIT", "30"))
TASK_JOURNAL_MAX_LINES = int(os.environ.get("ARCHIVE_TASK_JOURNAL_MAX_LINES", "300"))
TASK_JOURNAL_ARTIFACTS_PER_RUN_LIMIT = max(
    1,
    min(
        int(os.environ.get("ARCHIVE_TASK_JOURNAL_ARTIFACTS_PER_RUN_LIMIT", "32")),
        256,
    ),
)
TASK_JOURNAL_ARTIFACTS_PER_POLL_LIMIT = max(
    1,
    min(
        int(os.environ.get("ARCHIVE_TASK_JOURNAL_ARTIFACTS_PER_POLL_LIMIT", "16")),
        512,
    ),
)
TASK_JOURNAL_ARTIFACT_BYTES_PER_POLL = max(
    1,
    int(
        os.environ.get(
            "ARCHIVE_TASK_JOURNAL_ARTIFACT_BYTES_PER_POLL",
            str(256 * 1024 * 1024),
        )
    ),
)
TASK_ESCALATION_TO_CHAT = os.environ.get("ARCHIVE_TASK_ESCALATION_TO_CHAT_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
STATE_PATH = Path(__file__).resolve().parent / "archive" / "task_journal_state.json"
JOURNAL_PAGE_TITLE = "Brigade Task Journal"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ARTIFACT_PUBLICATIONS_STATE_KEY = "_artifact_publications_v1"
MAX_PUBLICATION_ERRORS_PER_RUN = 128


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_state():
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
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


def fetch_artifacts(task_id):
    status, response = proxy_json_url(
        "GET",
        f"{WARMASTER_BASE_URL}/runs/{quote(task_id, safe='')}/artifacts",
        timeout=30,
    )
    if status != 200 or not isinstance(response, dict) or response.get("ok") is not True:
        raise ValueError("Warmaster artifact listing did not return a successful catalog")
    catalog = response.get("artifact_catalog")
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1:
        raise ValueError("Warmaster artifact listing has no bounded catalog protocol")
    if catalog.get("complete") is not True or catalog.get("truncated") is not False:
        detail = "; ".join(
            " ".join(str(value).split())[:240]
            for value in (catalog.get("errors") or [])[:3]
        )
        raise ValueError(
            "Warmaster artifact catalog is incomplete or truncated"
            + (f": {detail}" if detail else "")
        )
    artifacts = response.get("artifacts") if isinstance(response, dict) else None
    if not isinstance(artifacts, list):
        raise ValueError("Warmaster artifact listing is not a list")
    if any(not isinstance(item, dict) for item in artifacts):
        raise ValueError("Warmaster artifact listing contains a non-object entry")
    returned = catalog.get("returned")
    if isinstance(returned, bool) or not isinstance(returned, int) or returned != len(artifacts):
        raise ValueError("Warmaster artifact catalog count does not match its payload")
    limit = catalog.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < returned:
        raise ValueError("Warmaster artifact catalog limit is invalid")
    return artifacts


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
        and str(review.get("status") or "") == "accepted"
        and review.get("accepted") is True
    )


def _one_identity(values):
    identities = {str(value).strip() for value in values if str(value or "").strip()}
    return next(iter(identities)) if len(identities) == 1 else ""


def _accepted_completed_orchestration(orchestration, expected_task_id=""):
    if not isinstance(orchestration, dict):
        return False, {}, {}
    summary = _orchestration_summary(orchestration)
    top_status = str(orchestration.get("status") or "").strip().lower()
    summary_status = str(summary.get("status") or "").strip().lower()
    protocol = summary.get("mission_protocol") if isinstance(summary.get("mission_protocol"), dict) else {}
    mission = protocol.get("mission") if isinstance(protocol.get("mission"), dict) else {}
    commander_order = (
        protocol.get("commander_order")
        if isinstance(protocol.get("commander_order"), dict)
        else {}
    )
    review = _latest_acceptance_review(protocol)
    final_response = protocol.get("final_response") if isinstance(protocol.get("final_response"), dict) else {}
    mission_ref = summary.get("mission_ref") if isinstance(summary.get("mission_ref"), dict) else {}
    result = summary.get("result") if isinstance(summary.get("result"), dict) else {}

    expected_task_id = str(expected_task_id or "").strip()
    top_task_id = str(orchestration.get("task_id") or "").strip()
    summary_task_id = str(summary.get("task_id") or "").strip()
    task_id = _one_identity(
        (
            expected_task_id,
            top_task_id,
            summary_task_id,
            result.get("task_id"),
        )
    )
    mission_id = _one_identity(
        (
            mission_ref.get("mission_id"),
            mission.get("mission_id"),
            commander_order.get("mission_id"),
            review.get("mission_id"),
            final_response.get("mission_id"),
        )
    )
    accepted = (
        top_status == "completed"
        and summary_status == "completed"
        and bool(task_id)
        and top_task_id == task_id
        and summary_task_id == task_id
        and (not expected_task_id or task_id == expected_task_id)
        and bool(mission_id)
        and _warmaster_accepted_protocol_final(protocol)
        and str(final_response.get("type") or "") == "final_response"
        and str(final_response.get("status") or "").strip().lower() == "completed"
        and str(final_response.get("accepted_by") or "") == "Warmaster"
        and str(review.get("mission_id") or "") == mission_id
        and str(final_response.get("mission_id") or "") == mission_id
        and str(mission_ref.get("mission_id") or "") == mission_id
    )
    return accepted, summary, protocol


def final_response_message_from_orchestration(orchestration, expected_task_id=""):
    expected = str(
        expected_task_id
        or (orchestration.get("task_id") if isinstance(orchestration, dict) else "")
        or ""
    ).strip()
    accepted, _summary, protocol = _accepted_completed_orchestration(
        orchestration,
        expected,
    )
    if not accepted:
        return ""
    final_response = protocol.get("final_response") if isinstance(protocol.get("final_response"), dict) else {}
    return str(final_response.get("answer") or "").strip()


def final_message_from_orchestration(orchestration, expected_task_id=""):
    return final_response_message_from_orchestration(orchestration, expected_task_id)


def _catalog_logical_path(recorded_path):
    raw = str(recorded_path or "").replace("\\", "/")
    if raw.startswith("/work/"):
        raw = raw[1:]
    path = PurePosixPath(raw)
    if (
        not raw
        or "\x00" in raw
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0].endswith(":")
    ):
        raise ValueError("recorded artifact path is not a safe logical path")
    normalized = path.as_posix()
    if len(normalized) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise ValueError("recorded artifact logical path is invalid or longer than 512 characters")
    return normalized


def _artifact_dedupe_key(task_id, recorded_path):
    identity = f"{task_id}\x00{recorded_path}".encode("utf-8")
    return f"warmaster-artifact:{hashlib.sha256(identity).hexdigest()}"


def _publication_state(publications, task_id):
    current = publications.get(task_id)
    if not isinstance(current, dict):
        current = {}
        publications[task_id] = current
    for key in ("published", "skipped", "errors"):
        if not isinstance(current.get(key), dict):
            current[key] = {}
    current["complete"] = current.get("complete") is True
    return current


def _publication_fingerprint(publication):
    durable = {
        key: value
        for key, value in publication.items()
        if key != "updated_at"
    }
    return json.dumps(durable, ensure_ascii=False, sort_keys=True)


def _remember_publication_error(publication, path, error):
    errors = publication["errors"]
    key = str(path or "<artifact>")[:512]
    message = " ".join(str(error).split())[:500]
    previous = errors.get(key)
    if key not in errors and len(errors) >= MAX_PUBLICATION_ERRORS_PER_RUN:
        errors.pop(next(iter(errors)))
    errors[key] = message
    return previous != message


def _open_warmaster_artifact(task_id, recorded_path, expected_size):
    target = (
        f"{WARMASTER_BASE_URL}/runs/{quote(task_id, safe='')}/artifact"
        f"?path={quote(recorded_path, safe='')}"
    )
    request = urllib.request.Request(
        target,
        headers={"Accept": "application/octet-stream"},
        method="GET",
    )
    response = _PRIVATE_BINARY_OPENER.open(request, timeout=300)
    try:
        if response.geturl() != target:
            raise ValueError("Warmaster artifact response changed origin or redirected")
        if int(getattr(response, "status", 200) or 200) != 200:
            raise ValueError("Warmaster artifact endpoint did not return HTTP 200")
        if response.headers.get("Transfer-Encoding"):
            raise ValueError("Warmaster artifact response must use a fixed Content-Length")
        raw_length = response.headers.get("Content-Length")
        try:
            content_length = int(raw_length)
        except (TypeError, ValueError) as exc:
            raise ValueError("Warmaster artifact response has no valid Content-Length") from exc
        if content_length != expected_size:
            raise ValueError(
                f"Warmaster artifact size changed: listed {expected_size}, response {content_length}"
            )
        media_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip()
        return response, media_type
    except Exception:
        response.close()
        raise


def publish_completed_artifacts(task_id, publications, *, byte_budget, file_budget):
    """Publish one accepted run's recorded outputs without exposing host paths.

    The per-task journal state makes ordinary polls cheap.  The catalog dedupe
    key makes a retry safe even if the process died after the CAS transaction
    committed but before the journal state was replaced.
    """
    publication = _publication_state(publications, task_id)
    before = _publication_fingerprint(publication)
    if publication["complete"]:
        return {
            "changed": False,
            "bytes": 0,
            "attempted": 0,
            "published": 0,
            "complete": True,
        }

    notices = []
    try:
        orchestration = fetch_orchestration(task_id)
        accepted, summary, protocol = _accepted_completed_orchestration(
            orchestration,
            task_id,
        )
        if not accepted:
            raise ValueError("completed run has no final Warmaster acceptance")
        artifacts = fetch_artifacts(task_id)
    except Exception as exc:  # noqa: BLE001 - producer failure must not break journal delivery
        if _remember_publication_error(publication, "<run>", exc):
            notices.append(str(exc))
        publication["complete"] = False
        changed = before != _publication_fingerprint(publication)
        if changed:
            publication["updated_at"] = now_iso()
        return {
            "changed": changed,
            "bytes": 0,
            "attempted": 0,
            "published": 0,
            "complete": False,
            "error": str(exc),
            "notices": notices,
        }

    publication["errors"].pop("<run>", None)
    mission_ref = summary.get("mission_ref") if isinstance(summary.get("mission_ref"), dict) else {}
    mission_id = str(
        mission_ref.get("mission_id")
        or protocol.get("mission_id")
        or ""
    ).strip()
    published_now = 0
    attempted = 0
    spent = 0
    listed_paths = []

    for index, item in enumerate(artifacts):
        recorded_path = str(item.get("path") or "")
        state_key = recorded_path or f"<invalid:{index}>"
        if state_key not in listed_paths:
            listed_paths.append(state_key)
        if state_key in publication["published"] or state_key in publication["skipped"]:
            continue
        if item.get("exists") is not True:
            detail = item.get("errors") if isinstance(item.get("errors"), list) else []
            reason = (
                "; ".join(str(value) for value in detail[:3])
                or "recorded artifact is unavailable"
            )
            if _remember_publication_error(
                publication,
                state_key,
                reason,
            ):
                notices.append(f"{state_key}: {reason}")
            continue
        size = item.get("bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            reason = "invalid recorded artifact size"
            if publication["skipped"].get(state_key) != reason:
                notices.append(f"{state_key}: {reason}; skipped")
            publication["skipped"][state_key] = reason
            publication["errors"].pop(state_key, None)
            continue
        if size > ARTIFACT_MAX_BYTES:
            reason = f"artifact exceeds configured single-file limit of {ARTIFACT_MAX_BYTES} bytes"
            if publication["skipped"].get(state_key) != reason:
                notices.append(f"{state_key}: {reason}; skipped")
            publication["skipped"][state_key] = reason
            publication["errors"].pop(state_key, None)
            continue
        if size > TASK_JOURNAL_ARTIFACT_BYTES_PER_POLL:
            reason = (
                f"artifact is {size} bytes and exceeds the configured per-poll byte budget "
                f"of {TASK_JOURNAL_ARTIFACT_BYTES_PER_POLL}; raise "
                "ARCHIVE_TASK_JOURNAL_ARTIFACT_BYTES_PER_POLL to import it"
            )
            if _remember_publication_error(publication, state_key, reason):
                notices.append(f"{state_key}: {reason}; left pending")
            continue
        if (
            attempted >= TASK_JOURNAL_ARTIFACTS_PER_RUN_LIMIT
            or attempted >= max(0, int(file_budget))
        ):
            continue
        if size > max(0, int(byte_budget) - spent):
            continue
        try:
            logical_path = _catalog_logical_path(recorded_path)
        except ValueError as exc:
            reason = str(exc)
            if publication["skipped"].get(state_key) != reason:
                notices.append(f"{state_key}: {reason}; skipped")
            publication["skipped"][state_key] = reason
            publication["errors"].pop(state_key, None)
            continue

        attempted += 1
        spent += size
        response = None
        try:
            response, response_type = _open_warmaster_artifact(
                task_id,
                recorded_path,
                size,
            )
            guessed_type = mimetypes.guess_type(PurePosixPath(logical_path).name)[0]
            with response:
                stored = trusted_import_stream(
                    response,
                    expected_size=size,
                    filename=PurePosixPath(logical_path).name,
                    media_type=response_type or guessed_type or "application/octet-stream",
                    source="warmaster",
                    session_id=SHARED_CHAT_SESSION_ID,
                    audience_source="*",
                    task_id=task_id,
                    mission_id=mission_id or None,
                    logical_path=logical_path,
                    dedupe_key=_artifact_dedupe_key(task_id, recorded_path),
                    metadata={
                        "producer": "warmaster",
                        "recorded_path": recorded_path,
                        "recorded_source": " ".join(
                            str(item.get("source") or "result").split()
                        )[:80],
                        "accepted_by": "Warmaster",
                    },
                )
            artifact_id = str(stored.get("artifact_id") or "")
            if not artifact_id:
                raise ValueError("artifact store returned no artifact_id")
            publication["published"][state_key] = artifact_id
            publication["errors"].pop(state_key, None)
            published_now += 1
        except Exception as exc:  # noqa: BLE001 - one bad artifact cannot starve the journal
            if response is not None:
                response.close()
            if _remember_publication_error(publication, state_key, exc):
                notices.append(f"{state_key}: {exc}; will retry")

    pending = [
        path
        for path in listed_paths
        if path not in publication["published"] and path not in publication["skipped"]
    ]
    publication["complete"] = not pending
    changed = before != _publication_fingerprint(publication)
    if changed:
        publication["updated_at"] = now_iso()
    return {
        "changed": changed,
        "bytes": spent,
        "attempted": attempted,
        "published": published_now,
        "complete": publication["complete"],
        "pending": len(pending),
        "errors": len(publication["errors"]),
        "notices": notices,
    }


def deliver_final_to_chat(task_id, run=None):
    """Queue the accepted final answer; the owner releases it via the report
    button or by asking for news (pending-reports outbox)."""
    try:
        final_message = final_message_from_orchestration(
            fetch_orchestration(task_id),
            task_id,
        )
    except Exception as exc:  # noqa: BLE001 - final delivery must not break the journal loop
        print(f"Task journal final fetch failed for {task_id}: {exc}", flush=True)
        return False
    if not final_message:
        return False
    goal = human_goal(run or {})[:120] or task_id
    body = f"Задача бригады выполнена и принята Абаддоном.\ntask: {goal}\nfinal ответ:\n{final_message[:4000]}"
    report_id = enqueue_report("warmaster", "task_completed", f"готово: {goal}", body, dedupe_key=f"warmaster:{task_id}:final")
    return bool(report_id)


def escalation_facts(task_id, run):
    """Collect Warmaster's own verdict about a stuck/failed run: acceptance
    reason, revision order, manifest blockers. Data packaging only — the
    judgement already happened in Warmaster's acceptance review."""
    facts = {
        "task_id": task_id,
        "goal": human_goal(run),
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
    facts["goal"] = human_goal(run, protocol)
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
        lines.append(f"вердикт Абаддона: {facts['warmaster_reason']}")
    if facts.get("required_order"):
        lines.append(f"что требуется: {facts['required_order']}")
    for blocker in facts.get("blockers") or []:
        lines.append(f"блокер: {blocker}")
    topic = ("нужно решение: " if event_kind == "task_blocked" else "провал задачи: ") + str(facts.get("goal") or task_id)[:120]
    report_id = enqueue_report("warmaster", event_kind, topic, "\n".join(lines), dedupe_key=f"warmaster:{task_id}:{event_kind}")
    return bool(report_id)


def _user_request_from_protocol(protocol):
    order = protocol.get("commander_order") if isinstance(protocol.get("commander_order"), dict) else {}
    request = str(order.get("user_request") or "").strip()
    if request:
        return request
    intake = protocol.get("mission_intake") if isinstance(protocol.get("mission_intake"), dict) else {}
    return str(intake.get("user_request") or "").strip()


def human_goal(run, protocol=None):
    """The owner's own request, not the protocol wrapper: run goals (and even
    commander_order.user_request) carry the 'Запрос Шушуни к EyeOfTerror
    Warmaster...' boilerplate, and quoting it back at the owner reads like
    machine garbage, so the wrapper is unwrapped wherever it comes from."""
    request = _user_request_from_protocol(protocol or {}) or str(run.get("goal") or "")
    match = re.search(r"Исходный запрос пользователя:\s*(.+?)(?:\n\n|$)", request, re.S)
    if match:
        request = match.group(1)
    return " ".join(request.split())[:300]


def run_entry_text(run, event):
    task_id = str(run.get("task_id") or "")
    governor = str(run.get("governor") or "").strip() or "неизвестный губернатор"
    goal = human_goal(run)
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
        body = "Журнал дел Шушуни: какие задачи бригад он начинал и чем они закончились.\n\n" + "\n".join(body_lines)
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
    first_run = not any(key != ARTIFACT_PUBLICATIONS_STATE_KEY for key in state)
    publications = state.get(ARTIFACT_PUBLICATIONS_STATE_KEY)
    changed = False
    if not isinstance(publications, dict):
        publications = {}
        state[ARTIFACT_PUBLICATIONS_STATE_KEY] = publications
        changed = True
    # A just-completed run is never starved behind legacy baseline backfill.
    # Python's stable sort preserves Warmaster's order within both groups.
    def artifact_priority(run):
        task_id = str(run.get("task_id") or "")
        if str(run.get("status") or "").lower() != "completed":
            return 3
        if state.get(task_id) != "completed":
            return 0
        checkpoint = publications.get(task_id)
        if isinstance(checkpoint, dict) and checkpoint.get("complete") is not True:
            return 1
        return 2

    runs = sorted(runs, key=artifact_priority)
    artifact_budget_remaining = TASK_JOURNAL_ARTIFACT_BYTES_PER_POLL
    artifact_files_remaining = TASK_JOURNAL_ARTIFACTS_PER_POLL_LIMIT
    artifacts_published = 0
    artifacts_attempted = 0
    artifact_errors = 0
    for run in runs:
        task_id = str(run.get("task_id") or "")
        status = str(run.get("status") or "").lower()
        previous = state.get(task_id)
        if previous != status:
            state[task_id] = status
            changed = True
            if not first_run:
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

        # Artifact publication is deliberately independent of lifecycle event
        # replay.  This backfills accepted runs on the very first baseline poll
        # and retries partial imports without duplicating journal/chat reports.
        if status == "completed":
            try:
                result = publish_completed_artifacts(
                    task_id,
                    publications,
                    byte_budget=artifact_budget_remaining,
                    file_budget=artifact_files_remaining,
                )
            except Exception as exc:  # noqa: BLE001 - producer must never stop the poller
                artifact_errors += 1
                print(f"Task journal artifact producer failed for {task_id}: {exc}", flush=True)
            else:
                changed = changed or bool(result.get("changed"))
                spent = max(0, int(result.get("bytes") or 0))
                attempted = max(0, int(result.get("attempted") or 0))
                artifact_budget_remaining = max(0, artifact_budget_remaining - spent)
                artifact_files_remaining = max(0, artifact_files_remaining - attempted)
                artifacts_attempted += attempted
                artifacts_published += max(0, int(result.get("published") or 0))
                artifact_errors += max(0, int(result.get("errors") or bool(result.get("error"))))
                for notice in result.get("notices") or []:
                    print(
                        f"Task journal artifact producer for {task_id}: {notice}",
                        flush=True,
                    )
    if changed:
        save_state(state)
    return {
        "runs": len(runs),
        "baseline": first_run,
        "artifacts_published": artifacts_published,
        "artifacts_attempted": artifacts_attempted,
        "artifact_errors": artifact_errors,
        "artifact_bytes": TASK_JOURNAL_ARTIFACT_BYTES_PER_POLL - artifact_budget_remaining,
    }


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
