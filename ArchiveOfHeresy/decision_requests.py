"""Durable, typed questions that genuinely require the user's decision.

Warmaster anatomy stays in the Warbands/debug surfaces. This module stores the
small conversation contract needed by ordinary chat and renders it in
Shushunya's voice. A coarse blocked status is deliberately not enough to
create one of these requests.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


STORE_PATH = Path(
    os.environ.get(
        "ARCHIVE_PENDING_DECISIONS_PATH",
        Path(__file__).resolve().parent / "archive" / "pending_decisions.json",
    )
)
_LOCK = threading.RLock()
_MAX_TEXT = 1200
_MAX_ANSWER_RECEIPTS = 256


def _text(value: Any, limit: int = _MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _list(value: Any, *, limit: int = 5) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [_text(item, 500) for item in value[:limit] if _text(item, 500)]


def _option(value: Any) -> dict[str, str] | None:
    if isinstance(value, str):
        label = _text(value, 300)
        return {"id": label, "label": label, "effect": ""} if label else None
    if not isinstance(value, dict):
        return None
    label = _text(
        value.get("label")
        or value.get("title")
        or value.get("option")
        or value.get("value"),
        300,
    )
    if not label:
        return None
    return {
        "id": _text(value.get("id") or value.get("value") or label, 160),
        "label": label,
        "effect": _text(
            value.get("effect")
            or value.get("description")
            or value.get("tradeoff")
            or value.get("impact"),
            500,
        ),
    }


def normalize_decision_request(
    raw: Any,
    *,
    task_id: str = "",
    fallback_problem: str = "",
    fallback_question: str = "",
) -> dict[str, Any] | None:
    """Normalize the cross-service contract; a real question is mandatory."""
    raw = raw if isinstance(raw, dict) else {}
    question = _text(
        raw.get("question")
        or raw.get("exact_question")
        or raw.get("clarification_question")
        or fallback_question,
        700,
    )
    if not question:
        return None
    options = []
    for item in (raw.get("options") if isinstance(raw.get("options"), list) else []):
        normalized = _option(item)
        if normalized:
            options.append(normalized)
    tried = _list(
        raw.get("what_tried")
        or raw.get("tried")
        or raw.get("attempts")
        or raw.get("what_i_tried"),
        limit=5,
    )
    resume = raw.get("resume") if isinstance(raw.get("resume"), dict) else {}
    resume_body_raw = resume.get("body") if isinstance(resume.get("body"), dict) else {}
    resume_body: dict[str, Any] = {}
    if resume_body_raw:
        message = str(resume_body_raw.get("message") or "").strip()[:20_000]
        if message:
            resume_body["message"] = message
        body_task_id = _text(resume_body_raw.get("task_id") or raw.get("task_id") or task_id, 200)
        if body_task_id:
            resume_body["task_id"] = body_task_id
    request = {
        "schema_version": 1,
        "kind": "decision_request",
        "task_id": _text(raw.get("task_id") or task_id, 200),
        "problem": _text(
            raw.get("problem")
            or raw.get("reason")
            or raw.get("summary")
            or fallback_problem,
            900,
        ),
        "what_tried": tried,
        "options": options[:3],
        "recommendation": _text(
            raw.get("recommendation")
            or raw.get("recommended_option")
            or raw.get("default"),
            500,
        ),
        "question": question,
        "resume": {
            "kind": _text(resume.get("kind"), 80),
            "method": _text(resume.get("method") or "POST", 20).upper(),
            "path": _text(resume.get("path"), 500),
            "body": resume_body,
            "condition": _text(
                resume.get("condition")
                or raw.get("resume_condition")
                or "после твоего ответа продолжу ту же задачу",
                500,
            ),
        },
    }
    request["decision_id"] = decision_version(request)
    return request


def decision_version(request: Any) -> str:
    """Stable version of one exact question and its continuation contract."""
    request = request if isinstance(request, dict) else {}
    payload = {
        key: value
        for key, value in request.items()
        if key not in {"decision_id", "stored_at", "vox_intent_id"}
        and not str(key).startswith("_")
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def decision_prompt_version(request: Any) -> str:
    """Stable identity of the choice itself, excluding its transport route."""
    request = request if isinstance(request, dict) else {}
    request = normalize_decision_request(
        request,
        task_id=_text(request.get("task_id"), 200),
    ) or request
    payload = {
        key: value
        for key, value in request.items()
        if key not in {"decision_id", "stored_at", "vox_intent_id", "resume"}
        and not str(key).startswith("_")
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def extract_decision_request(payload: Any) -> dict[str, Any] | None:
    """Find an explicit decision contract in a bounded service response.

    Legacy services sometimes put the question next to ``needs_user`` instead
    of under ``decision_request``.  We accept that only when needs_user is the
    literal boolean true; a blocked/error string alone never qualifies.
    """
    queue: list[Any] = [payload]
    visited = 0
    while queue and visited < 120:
        current = queue.pop(0)
        visited += 1
        if isinstance(current, dict):
            explicit = current.get("decision_request")
            if isinstance(explicit, dict):
                return explicit
            question = (
                current.get("question")
                or current.get("clarification_question")
                or current.get("exact_question")
            )
            if current.get("needs_user") is True and _text(question):
                return current
            queue.extend(value for value in current.values() if isinstance(value, (dict, list)))
        elif isinstance(current, list):
            queue.extend(value for value in current[:30] if isinstance(value, (dict, list)))
    return None


_IDENTIFIER_RE = re.compile(
    r"\b(?:task|mission|run|commitment|effect)_id\b(?:\s*[=:]\s*[\w.:-]+)?",
    re.I,
)
_IDEMPOTENCY_RE = re.compile(
    r"\bidempotency(?:\s+key)?\b(?:\s*[=:]\s*[\w.:-]+)?",
    re.I,
)
_HTTP_RE = re.compile(r"\bHTTP(?:\s+status)?\s*\d{3}\b", re.I)
_INTERNAL_ENTITY_RE = re.compile(
    r"\b(?:"
    r"Core|Warmaster|Abaddon|Skitarii|Ceraxia|Iskandar|Archive(?:OfHeresy)?|EyeOfTerror|Vox|Administratum|"
    r"Абаддон\w*|Вармастер\w*|Скитари\w*|Церакси\w*|Искандар\w*|Архив\w*|Администратум\w*"
    r")\b",
    re.I,
)
_INTERNAL_ROLE_RE = re.compile(
    r"\b(?:бригад\w*|варбанд\w*|губернатор\w*|бригадир\w*|исполнител\w*)\b",
    re.I,
)
_PROTOCOL_RE = re.compile(
    r"\b(?:gateway|preflight|orchestration|client_action|next_action)\b|"
    r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(?:action|/[\w{}./:-]*)",
    re.I,
)
_INTERNAL_PATTERNS = (
    (_IDENTIFIER_RE, ""),
    (_IDEMPOTENCY_RE, ""),
    (_HTTP_RE, "внутренняя ошибка связи"),
    (_INTERNAL_ENTITY_RE, "внутренняя часть моей работы"),
    (_INTERNAL_ROLE_RE, "моя внутренняя работа"),
    (_PROTOCOL_RE, "внутренняя проверка"),
)


def contains_internal_anatomy(value: Any) -> bool:
    raw = str(value or "")
    return any(pattern.search(raw) for pattern, _replacement in _INTERNAL_PATTERNS)


def _first_person_detail(value: Any, *, fallback: str) -> str:
    """Keep human facts, but never narrate named organs as another actor."""
    raw = _text(value, 1600)
    if not raw or contains_internal_anatomy(raw):
        return fallback
    return conversational_text(raw, fallback=fallback)


def conversational_text(value: Any, *, fallback: str = "") -> str:
    """Remove implementation anatomy from ordinary-chat prose."""
    result = _text(value, 1600) or _text(fallback, 1600)
    for pattern, replacement in _INTERNAL_PATTERNS:
        result = pattern.sub(replacement, result)
    result = re.sub(r"\s+([,.;:!?])", r"\1", result)
    result = re.sub(r"\s{2,}", " ", result).strip(" -;,")
    return result


def conversational_document(value: Any, *, fallback: str = "", limit: int = 5000) -> str:
    """Line-preserving variant for final reports and other structured prose."""
    result = str(value or fallback or "").strip()[:limit]
    for pattern, replacement in _INTERNAL_PATTERNS:
        result = pattern.sub(replacement, result)
    lines = []
    for line in result.splitlines():
        line = re.sub(r"\s+([,.;:!?])", r"\1", line)
        lines.append(re.sub(r"[ \t]{2,}", " ", line).rstrip())
    return "\n".join(lines).strip(" \n-;,")


def render_decision_request(request: dict[str, Any]) -> str:
    problem = _first_person_detail(
        request.get("problem"),
        fallback="без твоего выбора я не могу честно продолжить эту задачу",
    )
    lines = ["Мне нужен твой выбор, чтобы продолжить ту же задачу.", f"У меня возникла проблема: {problem}."]
    raw_tried = [item for item in request.get("what_tried") or [] if _text(item, 500)]
    if raw_tried:
        if any(contains_internal_anatomy(item) for item in raw_tried):
            lines.append("Я уже попробовал продолжить без твоего участия, но этого оказалось недостаточно.")
        else:
            tried = [conversational_text(item) for item in raw_tried]
            tried = [item for item in tried if item]
            if tried:
                lines.append("Я уже попробовал: " + "; ".join(tried) + ".")
    options = request.get("options") if isinstance(request.get("options"), list) else []
    if options:
        lines.append("Варианты:")
        for index, option in enumerate(options[:3], 1):
            label = _first_person_detail(option.get("label"), fallback=f"Вариант {index}")
            effect = _first_person_detail(option.get("effect"), fallback="")
            lines.append(f"{index}. {label}" + (f" — {effect}" if effect else ""))
    recommendation = _first_person_detail(request.get("recommendation"), fallback="")
    if recommendation:
        lines.append(f"Я бы выбрал: {recommendation}.")
    lines.append(_first_person_detail(request.get("question"), fallback="Какой вариант выбираешь?"))
    return "\n".join(lines)


def render_internal_stall(goal: str, reason: str, *, failed: bool = False) -> str:
    goal = _first_person_detail(goal, fallback="эта задача")
    reason = _first_person_detail(
        reason,
        fallback="внутренняя проверка не пропустила текущий результат",
    )
    if failed:
        return f"Я не смог довести задачу «{goal}» до результата. Причина: {reason}. Твой выбор сейчас не нужен."
    return (
        f"Я пока не могу продолжить задачу «{goal}»: {reason}. "
        "От тебя сейчас ничего не требуется."
    )


def render_dispatch_retry(explanation: Any = "") -> str:
    explanation = _first_person_detail(
        explanation,
        fallback="внутренняя проверка не подтвердила запуск",
    )
    return (
        f"С первого раза я не смог запустить задачу: {explanation}. "
        "Я не считаю её начатой. Если понадобится именно твой выбор — спрошу отдельно."
    )


def _load_store_unlocked() -> dict[str, Any]:
    try:
        payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    pending = payload.get("pending") if isinstance(payload.get("pending"), dict) else {}
    receipts = payload.get("answer_receipts") if isinstance(payload.get("answer_receipts"), dict) else {}
    return {"schema_version": 2, "pending": pending, "answer_receipts": receipts}


def _load_unlocked() -> dict[str, dict[str, Any]]:
    return _load_store_unlocked()["pending"]


def _save_store_unlocked(store: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    target = STORE_PATH.with_suffix(STORE_PATH.suffix + ".tmp")
    target.write_text(
        json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    target.replace(STORE_PATH)


def _save_unlocked(values: dict[str, dict[str, Any]]) -> None:
    store = _load_store_unlocked()
    store["pending"] = values
    _save_store_unlocked(store)


def _prune_answer_receipts(
    receipts: dict[str, Any],
    *,
    keep_id: str = "",
    limit: int = _MAX_ANSWER_RECEIPTS,
) -> None:
    """Bound completed history without ever dropping an in-flight reservation."""
    limit = max(0, int(limit))
    if len(receipts) <= limit:
        return
    completed = [
        (receipt_id, receipt)
        for receipt_id, receipt in receipts.items()
        if receipt_id != keep_id
        and isinstance(receipt, dict)
        and (
            str(receipt.get("state") or "") == "completed"
            or (not receipt.get("state") and isinstance(receipt.get("result"), dict) and receipt.get("result"))
        )
    ]
    completed.sort(key=lambda item: str(item[1].get("stored_at") or ""))
    for receipt_id, _receipt in completed:
        if len(receipts) <= limit:
            break
        receipts.pop(receipt_id, None)


def upsert_pending(request: dict[str, Any]) -> bool:
    task_id = _text(request.get("task_id"), 200)
    if not task_id:
        return False
    with _LOCK:
        values = _load_unlocked()
        previous = values.get(task_id)
        candidate = dict(request)
        candidate["decision_id"] = decision_version(candidate)
        comparable_previous = {key: value for key, value in (previous or {}).items() if key != "stored_at"}
        if comparable_previous == candidate:
            return True
        candidate["stored_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        values[task_id] = candidate
        if previous != values[task_id]:
            _save_unlocked(values)
    return True


def clear_pending(task_id: str) -> bool:
    task_id = _text(task_id, 200)
    if not task_id:
        return False
    with _LOCK:
        values = _load_unlocked()
        removed = values.pop(task_id, None)
        if removed is not None:
            _save_unlocked(values)
    return removed is not None


def find_answer_receipt(request_id: str, task_id: str = "") -> dict[str, Any] | None:
    request_id = _text(request_id, 200)
    task_id = _text(task_id, 200)
    if not request_id:
        return None
    with _LOCK:
        receipt = _load_store_unlocked()["answer_receipts"].get(request_id)
    if not isinstance(receipt, dict):
        return None
    if task_id and _text(receipt.get("task_id"), 200) != task_id:
        return None
    return dict(receipt)


def reserve_answer_attempt(
    request_id: str,
    *,
    task_id: str,
    answer: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Durably claim one answer transport identity before any backend call.

    A mobile job can be replayed after its POST reached the backend but before
    Archive persisted the HTTP result.  The reservation is therefore written
    first.  An existing matching reservation means "reconcile, never POST";
    reusing the same transport id for another task/question/answer is a hard
    conflict rather than permission to overwrite the original receipt.
    """
    request_id = _text(request_id, 200)
    task_id = _text(task_id, 200)
    request = request if isinstance(request, dict) else {}
    decision_id = str(request.get("decision_id") or decision_version(request))
    prompt_id = decision_prompt_version(request)
    answer_sha256 = hashlib.sha256(str(answer or "").encode("utf-8")).hexdigest()
    if not request_id or not task_id or not decision_id:
        return {"ok": False, "created": False, "error": "invalid_answer_reservation"}
    with _LOCK:
        store = _load_store_unlocked()
        receipts = store["answer_receipts"]
        existing = receipts.get(request_id)
        if isinstance(existing, dict):
            matches = (
                _text(existing.get("task_id"), 200) == task_id
                and str(existing.get("decision_id") or "") == decision_id
                and str(existing.get("answer_sha256") or "") == answer_sha256
            )
            return {
                "ok": matches,
                "created": False,
                "conflict": not matches,
                "receipt": dict(existing),
            }
        _prune_answer_receipts(receipts, limit=_MAX_ANSWER_RECEIPTS - 1)
        if len(receipts) >= _MAX_ANSWER_RECEIPTS:
            return {
                "ok": False,
                "created": False,
                "conflict": False,
                "error": "answer_reservation_capacity",
            }
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        receipt = {
            "task_id": task_id,
            "decision_id": decision_id,
            "prompt_id": prompt_id,
            "answer_sha256": answer_sha256,
            "request": dict(request),
            "state": "reserved",
            "result": {},
            "stored_at": now,
            "updated_at": now,
        }
        receipts[request_id] = receipt
        _prune_answer_receipts(receipts, keep_id=request_id)
        _save_store_unlocked(store)
    return {"ok": True, "created": True, "conflict": False, "receipt": dict(receipt)}


def mark_answer_reconcile_pending(request_id: str, task_id: str, error: str = "") -> bool:
    """Keep an ambiguous pre-sent answer durable without making it replayable."""
    request_id = _text(request_id, 200)
    task_id = _text(task_id, 200)
    if not request_id or not task_id:
        return False
    with _LOCK:
        store = _load_store_unlocked()
        receipt = store["answer_receipts"].get(request_id)
        if not isinstance(receipt, dict) or _text(receipt.get("task_id"), 200) != task_id:
            return False
        if str(receipt.get("state") or "") == "completed":
            return True
        receipt["state"] = "reconcile_pending"
        receipt["last_error"] = _text(error, 800)
        receipt["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _save_store_unlocked(store)
    return True


def commit_answer_result(
    request_id: str,
    *,
    task_id: str,
    answer: str,
    request: dict[str, Any],
    result: dict[str, Any],
    pending_request: dict[str, Any] | None = None,
    clear_pending: bool = False,
) -> bool:
    """Atomically checkpoint an answer and the resulting question transition.

    If a mobile job is replayed after A created question B, the receipt wins
    before B can consume A again. Without a request id the pending transition
    is still durable, but no cross-process replay claim is made.
    """
    task_id = _text(task_id, 200)
    request_id = _text(request_id, 200)
    if not task_id:
        return False
    with _LOCK:
        store = _load_store_unlocked()
        pending = store["pending"]
        answer_sha256 = hashlib.sha256(str(answer or "").encode("utf-8")).hexdigest()
        decision_id = str(request.get("decision_id") or decision_version(request))
        prompt_id = decision_prompt_version(request)
        receipts = store["answer_receipts"]
        existing = receipts.get(request_id) if request_id else None
        if isinstance(existing, dict):
            if (
                _text(existing.get("task_id"), 200) != task_id
                or str(existing.get("decision_id") or "") != decision_id
                or str(existing.get("answer_sha256") or "") != answer_sha256
            ):
                return False
            if str(existing.get("state") or "") == "completed":
                return True
        if pending_request is not None:
            candidate = dict(pending_request)
            candidate["task_id"] = task_id
            candidate["decision_id"] = decision_version(candidate)
            candidate["stored_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            pending[task_id] = candidate
        elif clear_pending:
            pending.pop(task_id, None)
        if request_id:
            replay_result = {
                key: value
                for key, value in (result if isinstance(result, dict) else {}).items()
                if key in {"ok", "status", "message", "task_id"}
            }
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            receipts[request_id] = {
                "task_id": task_id,
                "decision_id": decision_id,
                "prompt_id": str((existing or {}).get("prompt_id") or prompt_id),
                "answer_sha256": answer_sha256,
                "request": dict((existing or {}).get("request") or request),
                "state": "completed",
                "result": replay_result,
                "stored_at": str((existing or {}).get("stored_at") or now),
                "updated_at": now,
            }
            _prune_answer_receipts(receipts, keep_id=request_id)
        _save_store_unlocked(store)
    return True


def pending_decisions(limit: int = 3) -> list[dict[str, Any]]:
    with _LOCK:
        values = list(_load_unlocked().values())
    values.sort(key=lambda item: str(item.get("stored_at") or ""))
    return [dict(item) for item in values[-max(1, min(int(limit or 3), 12)) :]]


def find_pending(task_id: str = "") -> dict[str, Any] | None:
    task_id = _text(task_id, 200)
    decisions = pending_decisions(limit=12)
    if task_id:
        return next((item for item in decisions if item.get("task_id") == task_id), None)
    return decisions[-1] if len(decisions) == 1 else None


def core_context() -> list[dict[str, Any]]:
    """Machine context for Core; never rendered to ordinary chat."""
    return pending_decisions(limit=3)
