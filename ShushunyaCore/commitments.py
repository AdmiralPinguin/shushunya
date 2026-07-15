from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator

from .ledger import (
    COMMITMENT_STATES,
    UNHAPPY_STATES,
    InvariantViolation,
    Ledger,
    canonical_json,
    sha256_json,
    utc_now,
)
from .organs import OrganError, Organs


# A failed delegate run is evidence about one attempt, not the death of the
# durable goal. Only success or an explicit cancellation closes a commitment.
TERMINAL_STATES = {"succeeded", "cancelled"}
WORKING_STATES = {
    "running",
    "started",
    "accepted",
    "queued",
    "pending",
    "routing",
    "planning",
    "executing",
    "ready",
    "ready_to_preflight",
    "apply_intent",
    "applied_unverified",
    "publishing",
    "push_pending",
    "protocol_finalize_pending",
    "cancelling",
}
REVISION_STATES = {"revising", "revision", "needs_revision", "revision_required"}
TASK_MEMORY_LINEAGE_ERRORS = {
    "invalid_abaddon_continuation",
    "invalid_task_memory_identity",
    "legacy_mission_lineage_migration_required",
    "mission_identity_conflict",
    "task_memory_auth_invalid",
    "task_memory_identity_conflict",
    "task_memory_identity_invalid",
    "task_memory_mission_missing",
    "task_memory_parent_conflict",
    "task_memory_read_rejected",
    "task_memory_reference_invalid",
    "task_memory_reference_missing",
    "task_memory_rejected",
}

# Commitment rows and their events are a compact index over evidence owned by
# Abaddon/Archive, not another evidence store.  In particular, a failed run's
# snapshot must never absorb the previous commitment diagnostic/result and be
# written back recursively on every steward pass.
MAX_PERSISTED_EVIDENCE_BYTES = 24 * 1024
MAX_PERSISTED_EVENT_BYTES = 64 * 1024
MAX_PERSISTED_EVIDENCE_DEPTH = 5
MAX_PERSISTED_COLLECTION_ITEMS = 24
MAX_PERSISTED_TEXT_CHARS = 2_000

_HISTORICAL_BLOB_KEYS = {
    "diagnostic",
    "last_diagnostic",
    "last_result",
    "previous_attempt",
    "previous_diagnostic",
    "previous_result",
    "prior_diagnostic",
    "prior_result",
    "recovery_payload",
    "rejected_recovery",
    "snapshot",
}
_REFERENCE_FIELDS = (
    "task_id",
    "run_id",
    "mission_id",
    "delegate_ref",
    "task_memory_id",
    "root_task_id",
    "parent_task_id",
    "goal_id",
    "artifact_id",
    "event_id",
    "idempotency_key",
    "recovery_generation",
    "snapshot_sha256",
    "payload_sha256",
)
_STATUS_FIELDS = (
    "status",
    "phase",
    "state",
    "code",
    "error_code",
    "kind",
    "method",
    "path",
    "reason",
    "error",
    "explanation",
    "question",
    "required_action",
    "resume_condition",
    "http_status",
    "status_code",
    "retryable",
)
_REFERENCE_ENVELOPE_FIELDS = {
    "_evidence_ref",
    "evidence_kind",
    "sha256",
    "source_type",
    "source_items",
    "byte_length",
    *_REFERENCE_FIELDS,
    *_STATUS_FIELDS,
}


def _json_digest_and_size(value: Any) -> tuple[str, int]:
    """Hash canonical JSON incrementally so a legacy blob is not duplicated in RAM."""
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256()
    size = 0
    for chunk in encoder.iterencode(value):
        encoded = chunk.encode("utf-8")
        digest.update(encoded)
        size += len(encoded)
    return digest.hexdigest(), size


def _text_digest_and_size(value: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for offset in range(0, len(value), 64 * 1024):
        encoded = value[offset : offset + 64 * 1024].encode("utf-8")
        digest.update(encoded)
        size += len(encoded)
    return digest.hexdigest(), size


def _bounded_text(value: str) -> str:
    if len(value) <= MAX_PERSISTED_TEXT_CHARS:
        return value
    digest = _text_digest_and_size(value)[0]
    suffix = f"… [text_sha256:{digest}]"
    return value[: MAX_PERSISTED_TEXT_CHARS - len(suffix)] + suffix


class _InternalEvidenceReference(dict):
    """Process-local proof that a reference was derived from full evidence.

    JSON loaded from an organ or the database is always a plain ``dict`` and
    therefore cannot self-assert a trusted digest with ``_evidence_ref``.
    """


def _is_evidence_reference(value: Any) -> bool:
    return (
        isinstance(value, _InternalEvidenceReference)
        and value.get("_evidence_ref") is True
        and isinstance(value.get("sha256"), str)
        and len(value.get("sha256")) == 64
    )


def _restore_stored_references(value: Any) -> Any:
    """Trust only strict references that have already crossed our DB boundary."""
    if isinstance(value, dict):
        sha = value.get("sha256")
        if (
            value.get("_evidence_ref") is True
            and isinstance(sha, str)
            and len(sha) == 64
            and all(char in "0123456789abcdef" for char in sha)
            and set(value) <= _REFERENCE_ENVELOPE_FIELDS
            and all(not isinstance(item, (dict, list)) for item in value.values())
        ):
            return _InternalEvidenceReference(value)
        return {key: _restore_stored_references(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_restore_stored_references(child) for child in value]
    return value


def _reference_values(value: Any) -> dict[str, Any]:
    """Extract a few useful scalar facts without copying the evidence tree."""
    found: dict[str, Any] = {}
    queue: list[tuple[Any, int]] = [(value, 0)]
    visited: set[int] = set()
    inspected = 0
    wanted = {*_REFERENCE_FIELDS, *_STATUS_FIELDS}
    while queue and inspected < 96:
        node, depth = queue.pop(0)
        if isinstance(node, (dict, list)):
            identity = id(node)
            if identity in visited:
                continue
            visited.add(identity)
        inspected += 1
        if isinstance(node, dict):
            for key in (*_REFERENCE_FIELDS, *_STATUS_FIELDS):
                candidate = node.get(key)
                if key in found or candidate in (None, "") or isinstance(candidate, (dict, list)):
                    continue
                if isinstance(candidate, str):
                    candidate = _bounded_text(candidate)[:512]
                elif not isinstance(candidate, (bool, int, float)):
                    continue
                found[key] = candidate
            if depth < 3:
                for key in sorted(node, key=str):
                    child = node[key]
                    if isinstance(child, (dict, list)):
                        queue.append((child, depth + 1))
        elif isinstance(node, list) and depth < 3:
            queue.extend((child, depth + 1) for child in node[:16] if isinstance(child, (dict, list)))
    return found


def _evidence_reference(value: Any, *, kind: str) -> dict[str, Any]:
    if _is_evidence_reference(value):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key not in _REFERENCE_ENVELOPE_FIELDS or isinstance(item, (dict, list)):
                continue
            if isinstance(item, str):
                maximum = 80 if key in {"evidence_kind", "source_type"} else 512
                item = _bounded_text(item)[:maximum]
            elif item is not None and not isinstance(item, (bool, int, float)):
                continue
            normalized[key] = item
        return _InternalEvidenceReference(normalized)
    digest, byte_length = _json_digest_and_size(value)
    reference: dict[str, Any] = {
        "_evidence_ref": True,
        "evidence_kind": str(kind or "evidence")[:80],
        "sha256": digest,
        "source_type": type(value).__name__,
        "byte_length": byte_length,
    }
    if isinstance(value, (dict, list)):
        reference["source_items"] = len(value)
    # Add useful facts in a deterministic order only while the complete
    # reference remains inside the byte budget.  Unicode can consume four
    # bytes per character, so a character limit alone is not a hard bound.
    for key, item in _reference_values(value).items():
        candidate = {**reference, key: item}
        if len(canonical_json(candidate).encode("utf-8")) <= MAX_PERSISTED_EVIDENCE_BYTES:
            reference[key] = item
    return _InternalEvidenceReference(reference)


def _bounded_evidence(value: Any, *, depth: int = 0, key_hint: str = "evidence") -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded_text(value)
    if _is_evidence_reference(value):
        return _evidence_reference(value, kind=key_hint)
    if isinstance(value, dict) and value.get("_evidence_ref") is True:
        # Wire data cannot appoint its own digest.  Hash the complete claimed
        # envelope, including any hidden payload, and replace the claim with a
        # reference derived inside this process.
        return _evidence_reference(value, kind=f"untrusted_{key_hint}")
    if depth >= MAX_PERSISTED_EVIDENCE_DEPTH - 1 and isinstance(value, (dict, list)):
        return _evidence_reference(value, kind=key_hint)
    if isinstance(value, dict):
        keys = sorted(value, key=str)
        priority = [key for key in (*_REFERENCE_FIELDS, *_STATUS_FIELDS) if key in value]
        ordered = priority + [key for key in keys if key not in priority]
        kept = ordered[:MAX_PERSISTED_COLLECTION_ITEMS]
        projected: dict[str, Any] = {}
        for key in kept:
            child = value[key]
            name = str(key)
            if name.lower() in _HISTORICAL_BLOB_KEYS and child is not None:
                projected[name] = _evidence_reference(child, kind=name)
            else:
                projected[name] = _bounded_evidence(
                    child,
                    depth=depth + 1,
                    key_hint=name,
                )
        omitted = ordered[MAX_PERSISTED_COLLECTION_ITEMS:]
        if omitted:
            omitted_value = {str(key): value[key] for key in omitted}
            projected["_omitted_fields"] = len(omitted)
            projected["_omitted_fields_sha256"] = _json_digest_and_size(omitted_value)[0]
        return projected
    if isinstance(value, list):
        kept = value[:MAX_PERSISTED_COLLECTION_ITEMS]
        projected = [
            _bounded_evidence(child, depth=depth + 1, key_hint=key_hint)
            for child in kept
        ]
        omitted = value[MAX_PERSISTED_COLLECTION_ITEMS:]
        if omitted:
            projected.append(
                {
                    "_evidence_ref": True,
                    "evidence_kind": f"{key_hint}_omitted_items"[:80],
                    "sha256": _json_digest_and_size(omitted)[0],
                    "source_type": "list",
                    "source_items": len(omitted),
                }
            )
        return projected
    # Commitment evidence is required to be JSON. Preserve the old fail-closed
    # behavior for unsupported values instead of persisting an unstable repr.
    canonical_json(value)
    return value


def _bounded_document(value: dict[str, Any], *, kind: str) -> dict[str, Any]:
    projected = _bounded_evidence(value, key_hint=kind)
    if not isinstance(projected, dict):
        return _evidence_reference(value, kind=kind)
    if len(canonical_json(projected).encode("utf-8")) > MAX_PERSISTED_EVIDENCE_BYTES:
        projected = _evidence_reference(value, kind=kind)
    if len(canonical_json(projected).encode("utf-8")) > MAX_PERSISTED_EVIDENCE_BYTES:
        raise InvariantViolation(f"{kind} reference exceeds persistence budget")
    return projected


def _bounded_stored_json(raw: str | None, *, kind: str) -> tuple[dict[str, Any] | None, str | None]:
    """Compact an old row on its next transition without parsing giant JSON."""
    if not raw:
        return None, None
    if len(raw) > MAX_PERSISTED_EVIDENCE_BYTES:
        digest, byte_length = _text_digest_and_size(raw)
        reference = {
            "_evidence_ref": True,
            "evidence_kind": f"legacy_{kind}_json",
            "sha256": digest,
            "source_type": "canonical_json",
            "byte_length": byte_length,
        }
        return reference, canonical_json(reference)
    raw_bytes = raw.encode("utf-8")
    if len(raw_bytes) > MAX_PERSISTED_EVIDENCE_BYTES:
        reference = {
            "_evidence_ref": True,
            "evidence_kind": f"legacy_{kind}_json",
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "source_type": "canonical_json",
            "byte_length": len(raw_bytes),
        }
        return reference, canonical_json(reference)
    parsed = _restore_stored_references(json.loads(raw))
    if not isinstance(parsed, dict):
        reference = _evidence_reference(parsed, kind=kind)
        return reference, canonical_json(reference)
    bounded = _bounded_document(parsed, kind=kind)
    return bounded, canonical_json(bounded)


def _snapshot_record(snapshot: dict[str, Any]) -> dict[str, Any]:
    bounded = _bounded_document(snapshot, kind="abaddon_snapshot")
    if _is_evidence_reference(bounded):
        return bounded
    bounded["_snapshot_sha256"] = _json_digest_and_size(snapshot)[0]
    if len(canonical_json(bounded).encode("utf-8")) > MAX_PERSISTED_EVIDENCE_BYTES:
        return _evidence_reference(snapshot, kind="abaddon_snapshot")
    return bounded


def _merge_worker_activity(prior_result: Any, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Accumulate the fighter's plain-language steps across reconcile polls.

    Each /orchestration poll only carries the most recent worker steps; keep a
    rolling, deduplicated tail on the commitment so the app can show the full
    feed of what the worker is actually doing, not just its final verdict.
    """
    prior: list[dict[str, Any]] = []
    if isinstance(prior_result, dict) and isinstance(prior_result.get("activity_steps"), list):
        prior = [s for s in prior_result["activity_steps"] if isinstance(s, dict)]
    seen = {(str(s.get("at") or ""), str(s.get("text") or "")) for s in prior}
    merged = list(prior)
    for step in snapshot.get("worker_steps", []) or []:
        if not isinstance(step, dict):
            continue
        text = str(step.get("text") or "").strip()
        if not text:
            continue
        key = (str(step.get("at") or ""), text)
        if key in seen:
            continue
        seen.add(key)
        merged.append({"text": text[:1000], "at": str(step.get("at") or "")})
    return merged[-30:]


def _recovery_record(
    payload: dict[str, Any],
    response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        key: payload[key]
        for key in (
            "task_id",
            "task_memory_id",
            "root_task_id",
            "parent_task_id",
            "idempotency_key",
            "recovery_generation",
        )
        if key in payload
    }
    record["payload_sha256"] = _json_digest_and_size(payload)[0]
    if response is not None:
        record["response"] = _evidence_reference(response, kind="recovery_response")
    return record


def _retry_at(seconds: int = 30) -> str:
    return (datetime.now(UTC) + timedelta(seconds=max(1, seconds))).isoformat()


def _dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _dicts(child)


def _task_memory_lineage_error(evidence: Any) -> str:
    """Return a durable-lineage rejection hidden in a downstream envelope."""
    for node in _dicts(evidence):
        for field in ("error_code", "code"):
            code = str(node.get(field) or "").strip().lower()
            if code in TASK_MEMORY_LINEAGE_ERRORS:
                return code
    return ""


class Commitments:
    def __init__(self, ledger: Ledger, organs: Organs):
        self.ledger = ledger
        self.organs = organs

    def transition(
        self,
        commitment_id: str,
        state: str,
        *,
        honest_status: str,
        diagnostic: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        delegate_ref: str = "",
        next_attempt_at: str | None = None,
        increment_attempt: bool = False,
    ) -> dict[str, Any]:
        if state not in COMMITMENT_STATES:
            raise InvariantViolation(f"unknown commitment state: {state}")
        if state in UNHAPPY_STATES and not diagnostic:
            raise InvariantViolation(f"{state} requires an actionable diagnostic")
        bounded_honest_status = _bounded_text(str(honest_status or ""))
        bounded_diagnostic = (
            _bounded_document(diagnostic, kind="commitment_diagnostic")
            if diagnostic is not None
            else None
        )
        with self.ledger.write() as db:
            row = db.execute("SELECT * FROM commitments WHERE id=?", (commitment_id,)).fetchone()
            if not row:
                raise KeyError("commitment not found")
            current = str(row["state"])
            if current in TERMINAL_STATES:
                return self._row(row)
            encoded_diagnostic = canonical_json(bounded_diagnostic) if bounded_diagnostic else None
            if result is not None:
                bounded_result = _bounded_document(result, kind="commitment_result")
                encoded_result = canonical_json(bounded_result)
            else:
                bounded_result, encoded_result = _bounded_stored_json(
                    row["result_json"],
                    kind="commitment_result",
                )
            result_was_compacted = row["result_json"] != encoded_result
            if (
                current == state
                and str(row["honest_status"] or "") == bounded_honest_status
                and (not delegate_ref or str(row["delegate_ref"] or "") == delegate_ref)
                and row["diagnostic_json"] == encoded_diagnostic
                and row["result_json"] == encoded_result
                and row["next_attempt_at"] == next_attempt_at
                and not increment_attempt
            ):
                return self._row(row)
            version = int(row["version"])
            event_payload = {
                "from": current,
                "to": state,
                "honest_status": bounded_honest_status,
                "diagnostic": bounded_diagnostic,
                "result": (
                    bounded_result
                    if result is not None or result_was_compacted
                    else None
                ),
                "delegate_ref": delegate_ref or row["delegate_ref"],
            }
            if len(canonical_json(event_payload).encode("utf-8")) > MAX_PERSISTED_EVENT_BYTES:
                raise InvariantViolation("commitment event exceeds persistence budget")
            event = self.ledger._append_event(
                db,
                aggregate_type="commitment",
                aggregate_id=commitment_id,
                kind=f"commitment.{state}",
                actor="shushunya-steward",
                correlation_id=commitment_id,
                causation_event_id=None,
                payload=event_payload,
            )
            updated = db.execute(
                """
                UPDATE commitments SET state=?,version=version+1,next_attempt_at=?,
                    attempt_count=attempt_count+?,delegate_ref=?,honest_status=?,diagnostic_json=?,
                    result_json=?,last_event_seq=?,updated_at=?
                WHERE id=? AND version=?
                """,
                (
                    state,
                    next_attempt_at,
                    1 if increment_attempt else 0,
                    delegate_ref or row["delegate_ref"],
                    bounded_honest_status,
                    encoded_diagnostic,
                    encoded_result,
                    int(event["seq"]),
                    utc_now(),
                    commitment_id,
                    version,
                ),
            )
            if updated.rowcount != 1:
                raise InvariantViolation("stale commitment writer")
            updated_row = db.execute("SELECT * FROM commitments WHERE id=?", (commitment_id,)).fetchone()
            self.ledger.enqueue_quarantine_notification(
                db,
                commitment_row=updated_row,
                previous_state=current,
                diagnostic=bounded_diagnostic or {},
                event_seq=int(event["seq"]),
                delegate_ref=delegate_ref,
            )
            return self._row(updated_row)

    @staticmethod
    def _row(row) -> dict[str, Any]:
        import json

        item = dict(row)
        item["spec"] = json.loads(item.pop("spec_json"))
        item["diagnostic"] = json.loads(item.pop("diagnostic_json")) if item.get("diagnostic_json") else None
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
        return item

    @staticmethod
    def _needs_user_diagnostic(snapshot: dict[str, Any]) -> dict[str, Any] | None:
        for node in _dicts(snapshot):
            status = str(node.get("status") or node.get("phase") or "").lower()
            needs_user = node.get("needs_user") is True or status in {"needs_user", "waiting_user"}
            decision_request = (
                node.get("decision_request")
                if isinstance(node.get("decision_request"), dict)
                else {}
            )
            question = str(
                decision_request.get("question")
                or node.get("question")
                or node.get("user_question")
                or node.get("clarification_question")
                or ""
            ).strip()
            if needs_user and question:
                return {
                    "code": "abaddon_needs_user",
                    "explanation": question,
                    "evidence": {
                        "task_id": snapshot.get("task_id"),
                        "detail": _evidence_reference(node, kind="needs_user_detail"),
                    },
                    "required_action": question,
                    "resume_condition": "Ответ будет передан в ту же ожидающую миссию.",
                }
        return None

    @staticmethod
    def _external_diagnostic(snapshot: dict[str, Any]) -> dict[str, Any] | None:
        for node in _dicts(snapshot):
            code = str(node.get("code") or node.get("status") or node.get("phase") or "").lower()
            blocker = (
                node.get("external_blocker")
                or node.get("external_dependency")
                or node.get("dependency")
                or node.get("blocked_by")
            )
            resume = str(node.get("resume_condition") or node.get("required_action") or "").strip()
            explicitly_external = bool(blocker) or code in {"external_dependency", "waiting_external"}
            if not explicitly_external or not resume:
                continue
            return {
                "code": "external_dependency",
                "explanation": str(node.get("explanation") or blocker or "Миссия ждёт внешнюю зависимость."),
                "evidence": {
                    "task_id": snapshot.get("task_id"),
                    "detail": _evidence_reference(node, kind="external_dependency_detail"),
                },
                "required_action": str(node.get("required_action") or "Восстановить внешнюю зависимость."),
                "resume_condition": resume,
            }
        return None

    @staticmethod
    def _nested_state(snapshot: dict[str, Any]) -> str:
        summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
        result = summary.get("result") if isinstance(summary.get("result"), dict) else {}
        return str(result.get("phase") or result.get("status") or "").lower()

    @staticmethod
    def _recovery_payload(
        item: dict[str, Any],
        snapshot: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        """Build one crash-stable child run for a failed immutable parent."""
        spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
        parent_task_id = str(item.get("delegate_ref") or "").strip()
        task_memory_id = str(
            spec.get("task_memory_id")
            or spec.get("goal_id")
            or spec.get("root_task_id")
            or spec.get("task_id")
            or item.get("id")
        ).strip()
        root_task_id = str(
            spec.get("root_task_id") or task_memory_id or spec.get("task_id")
        ).strip()
        snapshot_reference = _evidence_reference(snapshot, kind="abaddon_snapshot")
        prior_result = item.get("result") if isinstance(item.get("result"), dict) else {}
        recovery_generation = max(0, int(prior_result.get("recovery_generation") or 0))
        # Do not include attempt_count: a lost acknowledgement increments the
        # transport counter, but retry must reattach to this exact child run.
        recovery_digest = sha256_json(
            {
                "commitment_id": item.get("id"),
                "parent_task_id": parent_task_id,
                "task_memory_id": task_memory_id,
                "root_task_id": root_task_id,
                "recovery_generation": recovery_generation,
            }
        )[:24]
        task_id = f"core-recovery-{recovery_digest}"
        failure_guidance = {
            "code": f"abaddon_{status}",
            "explanation": (
                f"Неизменяемая попытка {parent_task_id} завершилась в состоянии {status}."
            ),
            "required_action": (
                "Разобрать доказательства провала и выбрать отличающуюся стратегию; "
                "не повторять stale action или тот же план."
            ),
            "resume_condition": (
                "Новая связанная попытка реализует изменённый план и заново проверяет исходные критерии успеха."
            ),
            "snapshot_sha256": snapshot_reference["sha256"],
        }
        original_message = str(spec.get("message") or "").strip()
        original_goal = str(item.get("goal") or "").strip()
        evidence_summary = canonical_json(snapshot_reference)
        message_parts = [
            "Автономная recovery-миссия для всё ещё активной цели Шушуни.",
            f"Неизменяемая родительская попытка: {parent_task_id}",
            f"Корневая задача: {root_task_id}",
            f"Поколение recovery-стратегии: {recovery_generation}",
            f"Исходная цель: {original_goal}",
            failure_guidance["explanation"],
            f"Обязательное изменение стратегии: {failure_guidance['required_action']}",
        ]
        if original_message:
            message_parts.append("Исходная спецификация:\n" + original_message)
        if evidence_summary:
            message_parts.append("Краткие доказательства предыдущей попытки:\n" + evidence_summary)
        payload = {
            "message": "\n\n".join(message_parts),
            "task_id": task_id,
            "goal_id": task_memory_id,
            "task_memory_id": task_memory_id,
            "root_task_id": root_task_id,
            "parent_task_id": parent_task_id,
            "continuation_of": parent_task_id,
            "recovery_of": parent_task_id,
            "failure_guidance": failure_guidance,
            "recovery_generation": recovery_generation,
            "idempotency_key": f"recovery-{recovery_digest}",
        }
        if isinstance(spec.get("warmaster_request"), dict):
            payload["warmaster_request"] = dict(spec["warmaster_request"])
        return payload

    def _bounded_retry(
        self,
        item: dict[str, Any],
        *,
        diagnostic: dict[str, Any],
        result: dict[str, Any] | None = None,
        seconds: int = 30,
    ) -> dict[str, Any]:
        attempt = int(item.get("attempt_count") or 0) + 1
        maximum = max(1, int(item.get("max_attempts") or 3))
        delay = min(3_600, max(1, int(seconds)) * (2 ** min(max(0, attempt - 1), 7)))
        if attempt >= maximum:
            existing_action = str(diagnostic.get("required_action") or "").strip()
            strategy_action = (
                "Не повторять ту же транспортную попытку или тот же план. "
                "Сформировать отличающуюся проверяемую стратегию продолжения и новую попытку."
            )
            diagnostic = {
                **diagnostic,
                "strategy_review_required": True,
                "requires_user": False,
                "required_action": (
                    f"{existing_action} {strategy_action}".strip()
                    if existing_action
                    else strategy_action
                ),
                "resume_condition": diagnostic.get("resume_condition")
                or "Будет опубликована новая стратегия с отличающимся планом или исправленный action contract.",
            }
        return self.transition(
            item["id"],
            "retry_wait",
            honest_status=str(diagnostic["explanation"]),
            diagnostic=diagnostic,
            result=result,
            next_attempt_at=_retry_at(delay),
            increment_attempt=True,
        )

    async def _dispatch_recovery_attempt(
        self,
        item: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        status: str,
        diagnostic: dict[str, Any],
    ) -> dict[str, Any]:
        """Replace one inert immutable run with a linked autonomous attempt."""
        recovery_payload = self._recovery_payload(item, snapshot, status)
        snapshot_reference = _evidence_reference(snapshot, kind="abaddon_snapshot")
        recovery_reference = _recovery_record(recovery_payload)
        try:
            dispatched = await self.organs.dispatch_abaddon(recovery_payload)
        except OrganError as exc:
            dispatch_error_reference = _evidence_reference(
                exc.evidence,
                kind="recovery_dispatch_error",
            )
            decision_request = (
                exc.evidence.get("decision_request")
                if isinstance(exc.evidence, dict)
                and isinstance(exc.evidence.get("decision_request"), dict)
                else {}
            )
            question = str(decision_request.get("question") or "").strip()
            if exc.code == "clarification_required" and question:
                needs_user = {
                    "code": "abaddon_needs_user",
                    "explanation": question,
                    "evidence": {
                        "previous_attempt": snapshot_reference,
                        "recovery_task_id": recovery_payload["task_id"],
                        "detail": dispatch_error_reference,
                    },
                    "required_action": question,
                    "resume_condition": "Ответ будет передан в связанную recovery-миссию.",
                }
                return self.transition(
                    item["id"],
                    "waiting_user",
                    honest_status=question,
                    diagnostic=needs_user,
                    result={
                        "previous_attempt": snapshot_reference,
                        "recovery": recovery_reference,
                    },
                )
            lineage_error = _task_memory_lineage_error(exc.evidence)
            if lineage_error:
                # A broken task-memory ancestry (missing/legacy parent run, absent
                # task_memory.json, or disagreeing identity) cannot be repaired by
                # retrying or minting more child ids — the diagnostic itself says so.
                # Parking it in waiting_external forever is a silent dead-end: nothing
                # reconciles it, so the goal waits mutely for a condition that will
                # never arrive. Escalate to the owner with a concrete decision instead
                # of stalling — reason on failure, do not block.
                lineage_question = (
                    "Не могу автоматически пересобрать эту цель: её родословная в "
                    "памяти задачи повреждена (родительский прогон или его "
                    "task_memory отсутствуют/расходятся), и провенанс так не "
                    "восстановить. Начать эту цель заново с чистой задачи?"
                )
                lineage_repair = {
                    "code": "task_memory_lineage_broken_needs_owner",
                    "explanation": lineage_question,
                    "evidence": {
                        "downstream_error_code": lineage_error,
                        "previous_attempt": snapshot_reference,
                        "recovery_payload": recovery_reference,
                        "dispatch_error": dispatch_error_reference,
                    },
                    "required_action": (
                        "Owner decision required: restart this goal as a fresh root "
                        "task, or abandon it. Provenance cannot be auto-repaired."
                    ),
                    "resume_condition": (
                        "The owner starts a fresh root task for this goal, or cancels it."
                    ),
                    "requires_user": True,
                }
                return self.transition(
                    item["id"],
                    "waiting_user",
                    honest_status=lineage_repair["explanation"],
                    diagnostic=lineage_repair,
                    result={
                        "previous_attempt": snapshot_reference,
                        "recovery": recovery_reference,
                    },
                )
            external = self._external_diagnostic(exc.evidence)
            if external:
                return self.transition(
                    item["id"],
                    "waiting_external",
                    honest_status=external["explanation"],
                    diagnostic=external,
                    result={
                        "previous_attempt": snapshot_reference,
                        "recovery": recovery_reference,
                    },
                )
            recovery_error = {
                **diagnostic,
                "code": exc.code,
                "explanation": (
                    f"Новая recovery-попытка {recovery_payload['task_id']} пока не подтверждена: "
                    f"{exc.explanation} Цель остаётся активной."
                ),
                "evidence": {
                    "previous_attempt": snapshot_reference,
                    "recovery_payload": recovery_reference,
                    "dispatch_error": dispatch_error_reference,
                },
            }
            if not exc.retryable:
                next_generation = int(recovery_payload.get("recovery_generation") or 0) + 1
                recovery_error.update(
                    {
                        "strategy_review_required": True,
                        "requires_user": False,
                        "required_action": (
                            "Доказанно отвергнутую recovery-попытку не повторять. "
                            "Сформировать следующее поколение стратегии с новым task_id."
                        ),
                        "resume_condition": (
                            "Абаддон примет новое поколение recovery-стратегии либо вернёт "
                            "конкретную внешнюю зависимость/решение владельца."
                        ),
                    }
                )
                return self._bounded_retry(
                    item,
                    diagnostic=recovery_error,
                    result={
                        "previous_attempt": snapshot_reference,
                        "rejected_recovery": recovery_reference,
                        "recovery_generation": next_generation,
                    },
                    seconds=30,
                )
            return self._bounded_retry(
                item,
                diagnostic=recovery_error,
                result={
                    "previous_attempt": snapshot_reference,
                    "recovery": recovery_reference,
                    "recovery_generation": int(
                        recovery_payload.get("recovery_generation") or 0
                    ),
                },
                seconds=30,
            )
        new_ref = str(
            dispatched.get("delegate_ref")
            or dispatched.get("task_id")
            or recovery_payload["task_id"]
        ).strip()
        return self.transition(
            item["id"],
            "working",
            honest_status=(
                f"Абаддон принял новую recovery-попытку {new_ref}; Core продолжает исходную цель."
            ),
            result={
                "previous_attempt": snapshot_reference,
                "recovery": _recovery_record(recovery_payload, dispatched),
            },
            delegate_ref=new_ref,
            increment_attempt=True,
        )

    async def _execute_continuation(
        self,
        item: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any] | None:
        action = self.organs.executable_action(snapshot)
        kind, method, path, _payload = self.organs._normalize_action(str(item["delegate_ref"]), action)
        if method != "POST" or not path:
            return None
        joined = f"{kind} {path}".lower()
        actionable = any(
            marker in joined
            for marker in ("revision", "resume", "apply_patch", "apply_verified", "retry_publish", "reconcile_mission", "reprepare")
        )
        phase = str(snapshot.get("phase") or "").lower()
        if "/start_http" in path and phase == "ready_to_start":
            actionable = True
        if not actionable:
            return None
        snapshot_hash = _json_digest_and_size(snapshot)[0]
        prior = item.get("result") if isinstance(item.get("result"), dict) else {}
        prior_continuation = prior.get("continuation") if isinstance(prior.get("continuation"), dict) else {}
        if prior_continuation.get("snapshot_sha256") == snapshot_hash:
            return item
        if int(item.get("attempt_count") or 0) >= int(item.get("max_attempts") or 3):
            diagnostic = {
                "code": "continuation_budget_exhausted",
                "explanation": "Лимит повторов одной continuation-стратегии исчерпан; цель остаётся активной и ждёт внутреннего пересмотра стратегии.",
                "evidence": {
                    "snapshot": _evidence_reference(snapshot, kind="abaddon_snapshot"),
                    "action": _evidence_reference(action, kind="continuation_action"),
                },
                "required_action": "Абаддон должен выбрать новую проверяемую стратегию, а не повторить тот же action.",
                "resume_condition": "Появится новая внутренняя стратегия/миссия с отличающимся проверяемым планом.",
            }
            return await self._dispatch_recovery_attempt(
                item,
                snapshot,
                status="continuation_budget_exhausted",
                diagnostic=diagnostic,
            )
        try:
            dispatched = await self.organs.execute_abaddon_action(str(item["delegate_ref"]), snapshot)
        except OrganError as exc:
            diagnostic = {
                "code": exc.code,
                "explanation": exc.explanation,
                "evidence": {
                    "dispatch_error": _evidence_reference(
                        exc.evidence,
                        kind="continuation_dispatch_error",
                    ),
                    "snapshot": _evidence_reference(snapshot, kind="abaddon_snapshot"),
                },
                "required_action": "Исправить опубликованную continuation-команду или повторить её после восстановления органа.",
                "resume_condition": "Абаддон опубликует и примет однозначный POST action.",
            }
            return self._bounded_retry(
                item,
                diagnostic=diagnostic,
                result=_snapshot_record(snapshot),
            )
        new_ref = str(dispatched.get("task_id") or item["delegate_ref"])
        revision = "revision" in joined or "reprepare" in joined or self._nested_state(snapshot) in REVISION_STATES
        return self.transition(
            item["id"],
            "revising" if revision else "working",
            honest_status=(
                "Абаддон фактически принял команду ревизии; Core продолжает сверять результат."
                if revision
                else "Абаддон фактически принял continuation-команду; Core продолжает сверять результат."
            ),
            result={
                "snapshot": _evidence_reference(snapshot, kind="abaddon_snapshot"),
                "continuation": {
                    "snapshot_sha256": snapshot_hash,
                    "kind": kind,
                    "path": path,
                    "response": _evidence_reference(
                        dispatched,
                        kind="continuation_response",
                    ),
                },
            },
            delegate_ref=new_ref,
            increment_attempt=True,
        )

    async def reconcile_one(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("delegate_kind") != "abaddon" or not item.get("delegate_ref"):
            return item
        try:
            snapshot = await self.organs.inspect_abaddon(str(item["delegate_ref"]))
        except OrganError as exc:
            if exc.code == "abaddon_run_not_found":
                missing_snapshot = {
                    "task_id": str(item["delegate_ref"]),
                    "status": "failed",
                    "phase": "delegation_not_created",
                    "summary": {
                        "commitment_id": str(item.get("id") or ""),
                        "commitment_spec_sha256": str(
                            item.get("spec_sha256") or ""
                        ),
                        "inspection": _evidence_reference(
                            exc.evidence,
                            kind="missing_run_inspection",
                        ),
                    },
                }
                diagnostic = {
                    "code": "abaddon_attempt_missing",
                    "explanation": exc.explanation,
                    "evidence": missing_snapshot,
                    "strategy_review_required": True,
                    "requires_user": False,
                    "required_action": "Создать новую связанную попытку той же задачи; не ждать статус несуществующего run.",
                    "resume_condition": "Абаддон подтвердит новую попытку с той же страницей задачи.",
                }
                return await self._dispatch_recovery_attempt(
                    item,
                    missing_snapshot,
                    status="delegation_not_created",
                    diagnostic=diagnostic,
                )
            diagnostic = {
                "code": exc.code,
                "explanation": exc.explanation,
                "evidence": _evidence_reference(
                    exc.evidence,
                    kind="abaddon_inspection_error",
                ),
                "required_action": "Повторить сверку, не объявляя работу завершённой.",
                "resume_condition": "Абаддон снова отвечает на orchestration snapshot.",
            }
            return self._bounded_retry(item, diagnostic=diagnostic, seconds=30)

        status = str(snapshot.get("status") or "unknown").lower()
        phase = str(snapshot.get("phase") or "").lower()
        nested = self._nested_state(snapshot)
        needs_user = self._needs_user_diagnostic(snapshot)
        external = self._external_diagnostic(snapshot)
        revision_required = status in REVISION_STATES or phase in REVISION_STATES or nested in REVISION_STATES
        failure_states = {"failed", "corrupt", "preflight_failed"}
        failure_status = (
            status
            if status in failure_states
            else phase
            if phase in failure_states
            else nested
            if nested in failure_states
            else ""
        )

        # The outer Abaddon run may be mechanically complete while its native
        # warband result still requires owner input, an external dependency or
        # another revision. Those nested facts outrank the outer wrapper state.
        if (
            (status in {"completed", "succeeded", "done"} or phase == "completed")
            and needs_user is None
            and external is None
            and not revision_required
            and not failure_status
        ):
            return self.transition(
                item["id"],
                "succeeded",
                honest_status="Абаддон подтвердил терминальное завершение; итог сохранён как факт.",
                result=_snapshot_record(snapshot),
            )
        if status == "cancelled" or phase == "cancelled":
            return self.transition(
                item["id"],
                "cancelled",
                honest_status="Миссия отменена.",
                result=_snapshot_record(snapshot),
            )

        if needs_user:
            return self.transition(
                item["id"],
                "waiting_user",
                honest_status=needs_user["explanation"],
                diagnostic=needs_user,
                result=_snapshot_record(snapshot),
            )
        if external:
            return self.transition(
                item["id"],
                "waiting_external",
                honest_status=external["explanation"],
                diagnostic=external,
                result=_snapshot_record(snapshot),
            )

        # Publication phases mean the action already happened and verification
        # is ongoing; never resend apply merely because its idempotent action is
        # still visible in the snapshot.
        if status in {"apply_intent", "applied_unverified", "publishing", "push_pending", "protocol_finalize_pending", "cancelling"}:
            return self.transition(
                item["id"],
                "working",
                honest_status=f"Абаддон подтверждает фазу {status}; терминальный результат ещё не доказан.",
                result=_snapshot_record(snapshot),
            )

        # A failed Abaddon run is an immutable failed attempt, not a failed
        # durable goal. Never replay its stale start affordance; schedule an
        # internal strategy review that must produce a different attempt.
        if failure_status:
            diagnostic = {
                "code": f"abaddon_{failure_status}",
                "explanation": (
                    f"Попытка Абаддона завершилась в состоянии {failure_status}; цель остаётся активной и переведена на внутренний пересмотр стратегии."
                ),
                "evidence": _evidence_reference(snapshot, kind="failed_abaddon_snapshot"),
                "strategy_review_required": True,
                "requires_user": False,
                "required_action": "Не запускать stale action этого run. Сформировать отличающуюся стратегию и создать новую связанную попытку с теми же критериями цели.",
                "resume_condition": "Core или Абаддон опубликует новую связанную попытку с исправленной стратегией и явными критериями.",
            }
            return await self._dispatch_recovery_attempt(
                item,
                snapshot,
                status=failure_status,
                diagnostic=diagnostic,
            )

        continuation = await self._execute_continuation(item, snapshot)
        if continuation is not None:
            return continuation

        if revision_required:
            return self.transition(
                item["id"],
                "revising",
                honest_status=f"Абаддон подтверждает внутреннюю ревизию ({nested or phase or status}); завершение ещё не доказано.",
                result=_snapshot_record(snapshot),
            )

        if status in {"blocked", "interrupted", "resume_required"} or phase in {"blocked", "resume_required", "inspect", "needs_attention"}:
            diagnostic = {
                "code": "abaddon_continuation_not_executable",
                "explanation": "Абаддон остановил попытку и не дал исполнимую команду продолжения; Core сохраняет её диагностику и меняет стратегию в новой попытке.",
                "evidence": _evidence_reference(snapshot, kind="blocked_abaddon_snapshot"),
                "strategy_review_required": True,
                "requires_user": False,
                "required_action": "Не ждать пустого status-блока и не повторять stale action. Создать новую связанную попытку с исправленной стратегией и конкретным исполнимым планом.",
                "resume_condition": "Новая связанная попытка принята Абаддоном и продолжает ту же долговечную цель.",
            }
            return await self._dispatch_recovery_attempt(
                item,
                snapshot,
                status="blocked_no_action",
                diagnostic=diagnostic,
            )

        if status not in WORKING_STATES and phase not in WORKING_STATES:
            diagnostic = {
                "code": "unknown_abaddon_status",
                "explanation": f"Абаддон вернул неизвестное состояние {status or '<empty>'}/{phase or '<empty>'}; Core не будет выдавать его за живую работу.",
                "evidence": _evidence_reference(snapshot, kind="unknown_abaddon_snapshot"),
                "required_action": "Исправить orchestration snapshot или вернуть документированное состояние.",
                "resume_condition": "Абаддон вернёт однозначный status и фактические доказательства прогресса.",
            }
            return self._bounded_retry(
                item,
                diagnostic=diagnostic,
                result=_snapshot_record(snapshot),
                seconds=30,
            )

        activity_steps = _merge_worker_activity(item.get("result"), snapshot)
        working_result = {**_snapshot_record(snapshot), "activity_steps": activity_steps}
        latest_step = activity_steps[-1]["text"] if activity_steps else ""
        honest = (
            f"Боец: {latest_step[:180]}"
            if latest_step
            else f"Абаддон сообщает состояние {phase or status}; завершение ещё не подтверждено."
        )
        return self.transition(
            item["id"],
            "working",
            honest_status=honest,
            result=working_result,
        )

    async def reconcile_all(self) -> dict[str, int]:
        items = self.ledger.list_commitments(include_terminal=False, limit=100)
        checked = 0
        changed = 0
        now = utc_now()
        for item in items:
            if item.get("delegate_kind") != "abaddon" or not item.get("delegate_ref"):
                continue
            # A waiting-user mission must remain observable: after Archive
            # delivers the answer directly to Abaddon, its next snapshot is
            # the durable evidence that this commitment resumed. Legacy
            # quarantined rows are reconciled too: quarantine must not make a
            # still-authorized durable goal disappear from the steward.
            if item.get("state") == "retry_wait" and str(item.get("next_attempt_at") or "") > now:
                continue
            checked += 1
            before = (item["state"], item.get("honest_status"), item.get("version"))
            current = await self.reconcile_one(item)
            after = (current["state"], current.get("honest_status"), current.get("version"))
            changed += int(before != after)
        return {"checked": checked, "changed": changed}
