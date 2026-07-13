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
from pending_reports import enqueue_report, mark_delivered
from decision_requests import (
    clear_pending as clear_pending_decision,
    normalize_decision_request,
    render_decision_request,
    render_internal_stall,
    upsert_pending as upsert_pending_decision,
)


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
CONVERSATION_DELIVERIES_STATE_KEY = "_conversation_deliveries_v1"
STATE_METADATA_KEYS = {
    ARTIFACT_PUBLICATIONS_STATE_KEY,
    CONVERSATION_DELIVERIES_STATE_KEY,
}
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


def _deliver_final_event(task_id, run=None):
    """Publish one accepted result to chat and Vox through idempotent keys.

    Chat is the durable conversation record.  Vox owns the independently durable
    FCM outbox, so marking the already-visible report conveyed must not cancel a
    push that was owed while the owner was away.
    """
    try:
        final_message = final_message_from_orchestration(
            fetch_orchestration(task_id),
            task_id,
        )
    except Exception as exc:  # noqa: BLE001 - final delivery must not break the journal loop
        print(f"Task journal final fetch failed for {task_id}: {exc}", flush=True)
        return {
            "ok": False,
            "chat": False,
            "vox": False,
            "conveyed": False,
            "report_id": None,
            "error": str(exc),
        }
    if not final_message:
        return {
            "ok": False,
            "chat": False,
            "vox": False,
            "conveyed": False,
            "report_id": None,
            "error": "accepted final response is missing",
        }
    goal = human_goal(run or {})[:120] or task_id
    body = f"Я закончил задачу «{goal}».\nРезультат:\n{final_message[:4000]}"
    report_id = enqueue_report("warmaster", "task_completed", f"готово: {goal}", body, dedupe_key=f"warmaster:{task_id}:final")
    try:
        from archive_ops import append_chat_message  # local import avoids module-init cycle

        message_id = append_chat_message(
            SHARED_CHAT_SESSION_ID,
            "assistant",
            body,
            source="shushunya",
            dedupe_key=f"warmaster:{task_id}:final:chat",
        )
    except Exception as exc:  # noqa: BLE001 - Vox can still notify; the journal retries chat
        print(f"Task journal final chat delivery failed for {task_id}: {exc}", flush=True)
        message_id = None
    conveyed = bool(mark_delivered([report_id])) if report_id and message_id else False
    return {
        "ok": bool(report_id and message_id and conveyed),
        "chat": bool(message_id),
        "vox": bool(report_id),
        "conveyed": conveyed,
        "report_id": int(report_id) if report_id else None,
    }


def deliver_final_to_chat(task_id, run=None):
    """Compatibility wrapper used by direct final-delivery callers/tests."""
    return bool(_deliver_final_event(task_id, run).get("ok"))


def _current_decision_request(result, mission_state, *, task_id, fallback_problem):
    """Read only the authoritative current result/state, never protocol history."""
    def direct_request(value):
        explicit = value.get("decision_request")
        if isinstance(explicit, dict):
            return explicit
        question = str(
            value.get("question")
            or value.get("clarification_question")
            or value.get("exact_question")
            or ""
        ).strip()
        return value if question else None

    current_needs_user = (
        result.get("needs_user") is True
        or mission_state.get("needs_user") is True
        or str(mission_state.get("user_visible_state") or "") == "needs_user_decision"
        or str(mission_state.get("status") or "") == "needs_user"
    )
    if not current_needs_user:
        return None
    raw_decision = direct_request(result) or direct_request(mission_state)
    if not isinstance(raw_decision, dict):
        return None
    exact_question = str(
        raw_decision.get("question")
        or raw_decision.get("clarification_question")
        or raw_decision.get("exact_question")
        or ""
    ).strip()
    if not exact_question:
        return None
    return normalize_decision_request(
        raw_decision,
        task_id=task_id,
        fallback_problem=fallback_problem,
        fallback_question=exact_question,
    )


def _decision_fingerprint_payload(value):
    if isinstance(value, dict):
        return {
            str(key): _decision_fingerprint_payload(item)
            for key, item in value.items()
            if str(key) not in {"stored_at", "vox_intent_id"}
        }
    if isinstance(value, list):
        return [_decision_fingerprint_payload(item) for item in value]
    return value


def escalation_fingerprint(facts, event_kind):
    """Stable identity for one user-visible lifecycle/decision version."""
    decision_request = (
        facts.get("decision_request")
        if isinstance(facts.get("decision_request"), dict)
        else {}
    )
    if decision_request:
        # A live question keeps one identity while the coarse run wrapper moves
        # through running/needs_user/blocked.  Only a changed question/options/
        # resume contract is a new interruption worth another notification.
        payload = {
            "event_kind": "decision_required",
            "decision_request": _decision_fingerprint_payload(decision_request),
        }
    else:
        payload = {
            "event_kind": str(event_kind or ""),
            "status": str(facts.get("status") or ""),
            "needs_user": False,
        }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
    result = summary.get("result") if isinstance(summary.get("result"), dict) else {}
    mission_state = summary.get("mission_state") if isinstance(summary.get("mission_state"), dict) else {}
    facts["result_needs_user"] = result.get("needs_user") is True
    facts["mission_state"] = mission_state
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
    fallback_problem = (
        facts.get("warmaster_reason")
        or "; ".join(facts.get("blockers") or [])
        or str(result.get("reason") or result.get("summary") or "")
    )
    decision_request = _current_decision_request(
        result,
        mission_state,
        task_id=task_id,
        fallback_problem=fallback_problem,
    )
    facts["decision_request"] = decision_request
    # A blocked status or next_owner label is not authority to interrupt the
    # user. Only a valid contract containing an exact question is.
    facts["needs_user"] = decision_request is not None
    return facts


def deliver_escalation_to_chat(task_id, run, event_kind, *, facts=None, delivery_token=""):
    """Project a technical stop into one idempotent conversational event."""
    if not TASK_ESCALATION_TO_CHAT:
        return {"ok": False, "chat": False, "vox": False, "report_id": None}
    facts = facts if isinstance(facts, dict) else escalation_facts(task_id, run)
    fingerprint = escalation_fingerprint(facts, event_kind)
    if not delivery_token:
        delivery_token = hashlib.sha256(
            f"{task_id}|{event_kind}|{fingerprint}".encode("utf-8")
        ).hexdigest()[:24]
    decision_request = facts.get("decision_request")
    if isinstance(decision_request, dict):
        message = render_decision_request(decision_request)
        topic = "мне нужен твой выбор: " + str(facts.get("goal") or "задача")[:120]
        report_id = enqueue_report(
            "warmaster",
            "decision_required",
            topic,
            message,
            dedupe_key=f"decision:{delivery_token}",
        )
        if report_id:
            decision_request["vox_intent_id"] = report_id
        upsert_pending_decision(decision_request)
        dedupe_key = f"decision:{delivery_token}:chat"
    else:
        clear_pending_decision(task_id)
        reason = (
            facts.get("warmaster_reason")
            or "; ".join(facts.get("blockers") or [])
            or facts.get("required_order")
            or "внутренняя проверка не пропустила текущий результат"
        )
        message = render_internal_stall(
            str(facts.get("goal") or "эта задача"),
            str(reason),
            failed=event_kind == "task_failed",
        )
        report_id = enqueue_report(
            "warmaster",
            "task_failed" if event_kind == "task_failed" else "task_stalled_internal",
            "работа остановилась: " + str(facts.get("goal") or "задача")[:120],
            message,
            dedupe_key=f"warmaster:{delivery_token}",
        )
        dedupe_key = f"warmaster:{delivery_token}:chat"
    try:
        from archive_ops import append_chat_message  # local import avoids module-init cycle

        message_id = append_chat_message(
            SHARED_CHAT_SESSION_ID,
            "assistant",
            message,
            source="shushunya",
            dedupe_key=dedupe_key,
        )
    except Exception as exc:  # noqa: BLE001 - Vox still carries the notification
        print(f"Task journal proactive chat delivery failed for {task_id}: {exc}", flush=True)
        message_id = None
    conveyed = bool(mark_delivered([report_id])) if report_id and message_id else False
    return {
        "ok": bool(report_id and message_id and conveyed),
        "chat": bool(message_id),
        "vox": bool(report_id),
        "conveyed": conveyed,
        "report_id": int(report_id) if report_id else None,
        "fingerprint": fingerprint,
        "delivery_token": delivery_token,
    }


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
    match = re.search(r"Исходный запрос (?:пользователя|владельца):\s*(.+?)(?:\n\n|$)", request, re.S)
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
        return f"Шушуня начал задачу {task_id} (исполнитель {governor}): {goal}"
    if event == "blocked":
        return f"Задача {task_id} остановилась на проверке (исполнитель {governor}{step_note}): {goal}"
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


def _delivery_token(task_id, version, fingerprint):
    return hashlib.sha256(
        f"{task_id}|{int(version)}|{fingerprint}".encode("utf-8")
    ).hexdigest()[:24]


def _new_delivery_checkpoint(task_id, status, event_kind, fingerprint, previous=None):
    previous = previous if isinstance(previous, dict) else {}
    version = max(0, int(previous.get("version") or 0)) + 1
    return {
        "version": version,
        "fingerprint": fingerprint,
        "delivery_token": _delivery_token(task_id, version, fingerprint),
        "status": status,
        "event_kind": event_kind,
        "active": True,
        "chat": False,
        "vox": False,
        "conveyed": False,
        "report_id": None,
        "complete": False,
        "attempts": 0,
        "updated_at": now_iso(),
    }


def _delivery_checkpoint_changed(before, after):
    return json.dumps(before, ensure_ascii=False, sort_keys=True) != json.dumps(
        after,
        ensure_ascii=False,
        sort_keys=True,
    )


def _attempt_escalation_delivery(task_id, run, event_kind, facts, checkpoint):
    """Merge one idempotent chat/Vox attempt into its durable checkpoint."""
    before = dict(checkpoint)
    checkpoint["attempts"] = int(checkpoint.get("attempts") or 0) + 1
    checkpoint["last_attempt_at"] = now_iso()
    try:
        outcome = deliver_escalation_to_chat(
            task_id,
            run,
            event_kind,
            facts=facts,
            delivery_token=str(checkpoint.get("delivery_token") or ""),
        )
    except Exception as exc:  # noqa: BLE001 - the checkpoint makes this retryable
        outcome = {
            "ok": False,
            "chat": False,
            "vox": False,
            "conveyed": False,
            "report_id": None,
            "error": str(exc),
        }
    checkpoint["chat"] = bool(checkpoint.get("chat") or outcome.get("chat"))
    checkpoint["vox"] = bool(checkpoint.get("vox") or outcome.get("vox"))
    checkpoint["conveyed"] = bool(
        checkpoint.get("conveyed") or outcome.get("conveyed")
    )
    if outcome.get("report_id"):
        checkpoint["report_id"] = int(outcome["report_id"])
    report_id = checkpoint.get("report_id")
    if checkpoint["chat"] and checkpoint["vox"] and not checkpoint["conveyed"] and report_id:
        checkpoint["conveyed"] = bool(mark_delivered([report_id]))
    checkpoint["complete"] = bool(
        checkpoint["chat"] and checkpoint["vox"] and checkpoint["conveyed"]
    )
    checkpoint["last_error"] = (
        ""
        if checkpoint["complete"]
        else str(outcome.get("error") or "chat/Vox delivery is incomplete")[:500]
    )
    checkpoint["updated_at"] = now_iso()
    return _delivery_checkpoint_changed(before, checkpoint)


def _final_delivery_fingerprint(task_id, run):
    payload = {
        "event_kind": "task_completed",
        "task_id": str(task_id or ""),
        "goal": human_goal(run or {}),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _attempt_final_delivery(task_id, run, checkpoint):
    """Merge an idempotent final chat/Vox publication into its checkpoint."""
    before = dict(checkpoint)
    checkpoint["attempts"] = int(checkpoint.get("attempts") or 0) + 1
    checkpoint["last_attempt_at"] = now_iso()
    try:
        outcome = _deliver_final_event(task_id, run)
    except Exception as exc:  # noqa: BLE001 - checkpointed and retried next poll
        outcome = {
            "ok": False,
            "chat": False,
            "vox": False,
            "conveyed": False,
            "report_id": None,
            "error": str(exc),
        }
    checkpoint["chat"] = bool(checkpoint.get("chat") or outcome.get("chat"))
    checkpoint["vox"] = bool(checkpoint.get("vox") or outcome.get("vox"))
    checkpoint["conveyed"] = bool(
        checkpoint.get("conveyed") or outcome.get("conveyed")
    )
    if outcome.get("report_id"):
        checkpoint["report_id"] = int(outcome["report_id"])
    report_id = checkpoint.get("report_id")
    if checkpoint["chat"] and checkpoint["vox"] and not checkpoint["conveyed"] and report_id:
        checkpoint["conveyed"] = bool(mark_delivered([report_id]))
    checkpoint["complete"] = bool(
        checkpoint["chat"] and checkpoint["vox"] and checkpoint["conveyed"]
    )
    checkpoint["last_error"] = (
        ""
        if checkpoint["complete"]
        else str(outcome.get("error") or "final chat/Vox delivery is incomplete")[:500]
    )
    checkpoint["updated_at"] = now_iso()
    return _delivery_checkpoint_changed(before, checkpoint)


def poll_once():
    runs = fetch_runs()
    state = load_state()
    first_run = not any(key not in STATE_METADATA_KEYS for key in state)
    publications = state.get(ARTIFACT_PUBLICATIONS_STATE_KEY)
    deliveries = state.get(CONVERSATION_DELIVERIES_STATE_KEY)
    delivery_baseline = not isinstance(deliveries, dict)
    changed = False
    if not isinstance(publications, dict):
        publications = {}
        state[ARTIFACT_PUBLICATIONS_STATE_KEY] = publications
        changed = True
    if not isinstance(deliveries, dict):
        deliveries = {}
        state[CONVERSATION_DELIVERIES_STATE_KEY] = deliveries
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
                if previous is None and status not in TERMINAL_STATUSES and status not in {"blocked", "needs_user"}:
                    remember_entry(run_entry_text(run, "started"), task_id, "started")
                elif status in {"blocked", "needs_user"}:
                    remember_entry(run_entry_text(run, "blocked"), task_id, "blocked")
                elif status in TERMINAL_STATUSES:
                    remember_entry(run_entry_text(run, "finished"), task_id, f"finished-{status}")

        # Native warbands remain top-level `running` while their durable
        # mission result carries a typed needs_user question.  Read that current
        # truth independently from the wrapper status; otherwise the normal live
        # clarification path never reaches chat or the phone.
        facts = None
        if status == "failed" or status not in TERMINAL_STATUSES:
            facts = escalation_facts(task_id, run)
        has_current_decision = bool(
            isinstance(facts, dict)
            and isinstance(facts.get("decision_request"), dict)
        )
        escalation_event = (
            "decision_required"
            if has_current_decision
            else "task_failed"
            if status == "failed"
            else "task_blocked"
            if status in {"blocked", "needs_user"}
            else ""
        )
        delivery_event = "task_completed" if status == "completed" else escalation_event
        checkpoint = deliveries.get(task_id)
        if delivery_event:
            if delivery_event == "task_completed":
                clear_pending_decision(task_id)
                fingerprint = _final_delivery_fingerprint(task_id, run)
            else:
                facts = facts if isinstance(facts, dict) else escalation_facts(task_id, run)
                # A failed orchestration read is not authority to discard the
                # last durable question, even when the coarse run says blocked.
                if (
                    not isinstance(facts.get("decision_request"), dict)
                    and not facts.get("detail_error")
                ):
                    clear_pending_decision(task_id)
                fingerprint = escalation_fingerprint(facts, delivery_event)
            checkpoint_changed = (
                not isinstance(checkpoint, dict)
                or checkpoint.get("fingerprint") != fingerprint
                or checkpoint.get("event_kind") != delivery_event
                or checkpoint.get("active") is not True
            )
            if checkpoint_changed:
                checkpoint = _new_delivery_checkpoint(
                    task_id,
                    status,
                    delivery_event,
                    fingerprint,
                    previous=checkpoint,
                )
                deliveries[task_id] = checkpoint
                changed = True

            # Do not flood the user with every historical internal block at
            # service bootstrap. A real current decision request is different:
            # it must be asked even on the first baseline poll.
            if (
                delivery_baseline
                and (first_run or previous == status)
                and not (
                    delivery_event == "decision_required"
                    and isinstance(facts, dict)
                    and facts.get("needs_user") is True
                )
                and not checkpoint.get("attempts")
            ):
                before = dict(checkpoint)
                checkpoint["complete"] = True
                checkpoint["baseline_suppressed"] = True
                checkpoint["updated_at"] = now_iso()
                changed = changed or _delivery_checkpoint_changed(before, checkpoint)
            elif isinstance(facts, dict) and facts.get("detail_error"):
                detail_error = str(facts["detail_error"])[:500]
                if checkpoint.get("last_error") != detail_error:
                    checkpoint["last_error"] = detail_error
                    checkpoint["updated_at"] = now_iso()
                    changed = True
            elif checkpoint.get("complete") is not True:
                if delivery_event == "task_completed":
                    changed = _attempt_final_delivery(task_id, run, checkpoint) or changed
                else:
                    changed = (
                        _attempt_escalation_delivery(
                            task_id,
                            run,
                            delivery_event,
                            facts,
                            checkpoint,
                        )
                        or changed
                    )
        else:
            # Clear a stale question only after an authoritative current read.
            # A transient orchestration error must leave it durable for retry.
            if status in TERMINAL_STATUSES or (
                isinstance(facts, dict) and not facts.get("detail_error")
            ):
                clear_pending_decision(task_id)
            if isinstance(checkpoint, dict) and checkpoint.get("active") is True:
                checkpoint["active"] = False
                checkpoint["updated_at"] = now_iso()
                changed = True

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
        "conversation_deliveries_pending": sum(
            1
            for checkpoint in deliveries.values()
            if isinstance(checkpoint, dict)
            and checkpoint.get("active") is True
            and checkpoint.get("complete") is not True
        ),
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
