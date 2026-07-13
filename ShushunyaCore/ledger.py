from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1
UNHAPPY_STATES = {
    "waiting_user",
    "waiting_external",
    "retry_wait",
    "failed",
    "quarantined",
}
COMMITMENT_STATES = {
    "queued",
    "working",
    "revising",
    *UNHAPPY_STATES,
    "succeeded",
    "cancelled",
}


class LedgerError(RuntimeError):
    pass


class IdempotencyConflict(LedgerError):
    pass


class InvariantViolation(LedgerError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


MIGRATION_1 = r"""
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_event_id TEXT,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    UNIQUE(aggregate_type, aggregate_id, aggregate_version)
);
CREATE INDEX IF NOT EXISTS events_aggregate ON events(aggregate_type, aggregate_id, seq);
CREATE INDEX IF NOT EXISTS events_correlation ON events(correlation_id, seq);

CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

CREATE TABLE IF NOT EXISTS idempotency (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    response_json TEXT NOT NULL CHECK(json_valid(response_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(scope, key)
);

CREATE TABLE IF NOT EXISTS commitments (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    owner TEXT NOT NULL,
    goal TEXT NOT NULL,
    spec_json TEXT NOT NULL CHECK(json_valid(spec_json)),
    spec_sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'queued','working','revising','waiting_user','waiting_external',
        'retry_wait','succeeded','failed','cancelled','quarantined'
    )),
    version INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    due_at TEXT,
    next_attempt_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    delegate_kind TEXT,
    delegate_ref TEXT,
    honest_status TEXT NOT NULL,
    diagnostic_json TEXT CHECK(diagnostic_json IS NULL OR json_valid(diagnostic_json)),
    result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
    last_event_seq INTEGER NOT NULL REFERENCES events(seq),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        state NOT IN ('waiting_user','waiting_external','retry_wait','failed','quarantined')
        OR diagnostic_json IS NOT NULL
    )
);
CREATE INDEX IF NOT EXISTS commitments_reconcile
ON commitments(state, next_attempt_at, priority DESC, created_at);

CREATE TABLE IF NOT EXISTS effects (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    commitment_id TEXT REFERENCES commitments(id),
    kind TEXT NOT NULL,
    destination TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','leased','retry_wait','delivered','dead_letter')),
    result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE REFERENCES effects(id),
    event_seq INTEGER NOT NULL REFERENCES events(seq),
    destination TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','leased','retry_wait','delivered','dead_letter')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TEXT,
    lease_owner TEXT,
    lease_token TEXT,
    lease_until TEXT,
    last_error_code TEXT,
    last_error_detail TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    UNIQUE(destination, idempotency_key),
    CHECK (
        (state = 'leased' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
        OR
        (state <> 'leased' AND lease_owner IS NULL AND lease_token IS NULL AND lease_until IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS outbox_dispatch ON outbox(state, next_attempt_at, id);

CREATE TABLE IF NOT EXISTS state_projection (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL CHECK(json_valid(value_json)),
    value_sha256 TEXT NOT NULL,
    version INTEGER NOT NULL,
    last_event_seq INTEGER NOT NULL REFERENCES events(seq),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(namespace, key)
);

CREATE TABLE IF NOT EXISTS preference_evidence (
    id TEXT PRIMARY KEY,
    action_kind TEXT NOT NULL,
    target_scope TEXT NOT NULL,
    context_scope TEXT NOT NULL,
    verdict TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preference_rules (
    id TEXT PRIMARY KEY,
    action_kind TEXT NOT NULL,
    target_scope TEXT NOT NULL,
    context_scope TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK(verdict IN ('auto','ask','never_auto')),
    confidence REAL NOT NULL,
    source TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(action_kind, target_scope, context_scope)
);

CREATE TABLE IF NOT EXISTS preference_candidates (
    id TEXT PRIMARY KEY,
    action_kind TEXT NOT NULL,
    target_scope TEXT NOT NULL,
    context_scope TEXT NOT NULL,
    proposed_verdict TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('proposed','approved','rejected')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(action_kind, target_scope, context_scope, proposed_verdict)
);

CREATE TABLE IF NOT EXISTS identity_proposals (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL CHECK(json_valid(value_json)),
    rationale TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    state TEXT NOT NULL CHECK(state IN ('proposed','approved','rejected')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agenda_items (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    state TEXT NOT NULL CHECK(state IN ('queued','working','done','failed','cancelled')),
    value REAL NOT NULL,
    confidence REAL NOT NULL,
    urgency REAL NOT NULL,
    cost REAL NOT NULL,
    risk REAL NOT NULL,
    score REAL NOT NULL,
    stop_condition TEXT NOT NULL,
    budget_seconds INTEGER NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    next_eligible_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS agenda_pick ON agenda_items(state, next_eligible_at, score DESC, created_at);
"""


class Ledger:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self.ready = False
        self.integrity_error = ""

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 5000")
        db.execute("PRAGMA synchronous = FULL")
        return db

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except Exception:
                db.rollback()
                raise
            else:
                db.commit()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            checksum = hashlib.sha256(MIGRATION_1.encode("utf-8")).hexdigest()
            try:
                # sqlite3.executescript commits any transaction that existed
                # before the call.  Put BEGIN inside the script so the schema
                # and its checksum are protected by the same exclusive lock.
                db.executescript("BEGIN EXCLUSIVE;\n" + MIGRATION_1)
                row = db.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = ?",
                    (SCHEMA_VERSION,),
                ).fetchone()
                if row and row["checksum"] != checksum:
                    raise InvariantViolation("schema migration checksum mismatch")
                db.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
                    (SCHEMA_VERSION, checksum, utc_now()),
                )
            except Exception:
                db.rollback()
                raise
            else:
                db.commit()
            check = str(db.execute("PRAGMA quick_check").fetchone()[0])
        self.ready = check == "ok"
        self.integrity_error = "" if self.ready else check

    def _append_event(
        self,
        db: sqlite3.Connection,
        *,
        aggregate_type: str,
        aggregate_id: str,
        kind: str,
        actor: str,
        correlation_id: str,
        causation_event_id: str | None,
        payload: dict[str, Any],
    ) -> sqlite3.Row:
        version = int(
            db.execute(
                "SELECT COALESCE(MAX(aggregate_version), 0) + 1 FROM events WHERE aggregate_type=? AND aggregate_id=?",
                (aggregate_type, aggregate_id),
            ).fetchone()[0]
        )
        event_id = new_id("evt")
        encoded = canonical_json(payload)
        cur = db.execute(
            """
            INSERT INTO events(
                event_id, aggregate_type, aggregate_id, aggregate_version, kind,
                occurred_at, actor, correlation_id, causation_event_id,
                payload_json, payload_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                aggregate_type,
                aggregate_id,
                version,
                kind,
                utc_now(),
                actor,
                correlation_id or aggregate_id,
                causation_event_id,
                encoded,
                hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            ),
        )
        return db.execute("SELECT * FROM events WHERE seq=?", (cur.lastrowid,)).fetchone()

    def enqueue_quarantine_notification(
        self,
        db: sqlite3.Connection,
        *,
        commitment_row: sqlite3.Row,
        previous_state: str,
        diagnostic: dict[str, Any],
        event_seq: int,
        delegate_ref: str = "",
    ) -> str | None:
        """Atomically enqueue one owner notice for one new quarantine event.

        Callers pass their already-open write transaction and the exact event
        which established quarantine.  Existing quarantines are deliberately
        not scanned or backfilled; repairing one historical case is an explicit
        operation, not a startup side effect.
        """
        if str(previous_state or "") == "quarantined" or str(commitment_row["state"] or "") != "quarantined":
            return None
        diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
        code = str(diagnostic.get("code") or "commitment_quarantined").strip()
        if "continuation" in code or code in {"abaddon_rejected", "abaddon_repair_required"}:
            explanation = (
                "Я попытался автоматически продолжить остановившуюся работу, но опубликованная "
                "команда продолжения оказалась неисполнимой. Без новой стратегии безопасно "
                "повторять её нельзя."
            )
            required_action = (
                "Сформировать новую стратегию и запустить свежую проверяемую попытку, "
                "а не повторять прежнюю команду."
            )
        elif code in {"abaddon_status_unavailable", "unknown_abaddon_status"}:
            explanation = (
                "После нескольких проверок я так и не получил однозначного подтверждаемого "
                "состояния работы, поэтому перестал выдавать её за живую."
            )
            required_action = (
                "Восстановить однозначный статус работы или запустить свежую проверяемую попытку."
            )
        elif "ack" in code or "unreachable" in code:
            explanation = (
                "Я не получил однозначного подтверждения, было ли действие выполнено. "
                "Безопасные повторы исчерпаны: считать его выполненным или повторять вслепую нельзя."
            )
            required_action = str(
                diagnostic.get("required_action")
                or "Сверить результат по стабильному идентификатору и выбрать доказуемый следующий шаг."
            )
        else:
            explanation = (
                "Безопасные автоматические попытки продолжить остановившуюся работу исчерпаны, "
                "а подтверждаемого пути продолжения нет."
            )
            required_action = str(
                diagnostic.get("required_action")
                or "Сформировать новую проверяемую стратегию продолжения."
            )
        payload = {
            "kind": "commitment_stalled",
            "commitment_id": str(commitment_row["id"]),
            "task_id": str(delegate_ref or commitment_row["delegate_ref"] or ""),
            "goal": str(commitment_row["goal"] or "эта задача"),
            "state": "quarantined",
            "diagnostic_code": code,
            "explanation": explanation,
            "required_action": required_action,
            "needs_user": False,
        }
        commitment_id = str(commitment_row["id"])
        effect_id = "effect-stall-" + hashlib.sha256(
            f"{commitment_id}:{int(event_seq)}:quarantined".encode("utf-8")
        ).hexdigest()[:32]
        now = utc_now()
        encoded_payload = canonical_json(payload)
        payload_sha256 = sha256_json(payload)
        db.execute(
            """
            INSERT OR IGNORE INTO effects(
                id,turn_id,commitment_id,kind,destination,payload_json,
                payload_sha256,state,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                effect_id,
                commitment_id,
                # Delivery of the notice must not advance or reopen the work
                # whose terminal evidence the notice describes.
                None,
                "notify_commitment_stalled",
                "archive_notification_adapter",
                encoded_payload,
                payload_sha256,
                "pending",
                now,
                now,
            ),
        )
        db.execute(
            """
            INSERT OR IGNORE INTO outbox(
                message_id,event_seq,destination,operation,idempotency_key,
                payload_json,payload_sha256,state,attempt_count,max_attempts,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                effect_id,
                int(event_seq),
                "archive_notification_adapter",
                "notify_commitment_stalled",
                effect_id,
                encoded_payload,
                payload_sha256,
                "pending",
                0,
                10,
                now,
            ),
        )
        return effect_id

    def accept_turn(self, idempotency_key: str, request: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        digest = sha256_json(request)
        with self.write() as db:
            found = db.execute(
                "SELECT * FROM idempotency WHERE scope='turn' AND key=?",
                (idempotency_key,),
            ).fetchone()
            if found:
                if found["request_sha256"] != digest:
                    raise IdempotencyConflict("same turn idempotency key was used with a different request")
                response = json.loads(found["response_json"])
                return str(found["aggregate_id"]), response or None
            turn_id = new_id("turn")
            self._append_event(
                db,
                aggregate_type="turn",
                aggregate_id=turn_id,
                kind="turn.received",
                actor=str(request.get("source") or "unknown"),
                correlation_id=str(request.get("correlation_id") or turn_id),
                causation_event_id=None,
                payload=request,
            )
            now = utc_now()
            db.execute(
                """
                INSERT INTO idempotency(scope,key,request_sha256,aggregate_id,response_json,created_at,updated_at)
                VALUES ('turn',?,?,?,?,?,?)
                """,
                (idempotency_key, digest, turn_id, "{}", now, now),
            )
            return turn_id, None

    def save_turn_resolution(
        self,
        *,
        idempotency_key: str,
        turn_id: str,
        resolution: dict[str, Any],
        commitment: dict[str, Any] | None = None,
        effect: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.write() as db:
            existing = json.loads(
                db.execute(
                    "SELECT response_json FROM idempotency WHERE scope='turn' AND key=? AND aggregate_id=?",
                    (idempotency_key, turn_id),
                ).fetchone()[0]
            )
            if existing:
                return existing
            event = self._append_event(
                db,
                aggregate_type="turn",
                aggregate_id=turn_id,
                kind="turn.resolved",
                actor="shushunya-core",
                correlation_id=turn_id,
                causation_event_id=None,
                payload=resolution,
            )
            if commitment:
                spec = dict(commitment.get("spec") or {})
                state = str(commitment.get("state") or "queued")
                if state not in COMMITMENT_STATES:
                    raise InvariantViolation(f"invalid commitment state: {state}")
                diagnostic = commitment.get("diagnostic")
                if state in UNHAPPY_STATES and not isinstance(diagnostic, dict):
                    raise InvariantViolation(f"{state} requires a diagnostic")
                commit_event = self._append_event(
                    db,
                    aggregate_type="commitment",
                    aggregate_id=str(commitment["id"]),
                    kind="commitment.opened",
                    actor="shushunya-core",
                    correlation_id=turn_id,
                    causation_event_id=str(event["event_id"]),
                    payload=commitment,
                )
                now = utc_now()
                db.execute(
                    """
                    INSERT INTO commitments(
                        id,kind,owner,goal,spec_json,spec_sha256,state,version,priority,
                        due_at,next_attempt_at,attempt_count,max_attempts,delegate_kind,delegate_ref,
                        honest_status,diagnostic_json,result_json,last_event_seq,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        commitment["id"],
                        commitment.get("kind") or "task",
                        commitment.get("owner") or "shushunya",
                        commitment.get("goal") or "",
                        canonical_json(spec),
                        sha256_json(spec),
                        state,
                        1,
                        int(commitment.get("priority") or 0),
                        commitment.get("due_at"),
                        commitment.get("next_attempt_at"),
                        0,
                        int(commitment.get("max_attempts") or 3),
                        commitment.get("delegate_kind"),
                        commitment.get("delegate_ref"),
                        commitment.get("honest_status") or "Принял обязательство и готовлю исполнение.",
                        canonical_json(diagnostic) if diagnostic else None,
                        None,
                        int(commit_event["seq"]),
                        now,
                        now,
                    ),
                )
            if effect:
                payload = dict(effect.get("payload") or {})
                now = utc_now()
                db.execute(
                    """
                    INSERT INTO effects(id,turn_id,commitment_id,kind,destination,payload_json,payload_sha256,state,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        effect["id"],
                        turn_id,
                        effect.get("commitment_id"),
                        effect["kind"],
                        effect["destination"],
                        canonical_json(payload),
                        sha256_json(payload),
                        "pending",
                        now,
                        now,
                    ),
                )
                db.execute(
                    """
                    INSERT INTO outbox(
                        message_id,event_seq,destination,operation,idempotency_key,payload_json,payload_sha256,
                        state,attempt_count,max_attempts,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        effect["id"],
                        int(event["seq"]),
                        effect["destination"],
                        effect["kind"],
                        effect.get("idempotency_key") or effect["id"],
                        canonical_json(payload),
                        sha256_json(payload),
                        "pending",
                        0,
                        int(effect.get("max_attempts") or 3),
                        now,
                    ),
                )
            encoded = canonical_json(resolution)
            db.execute(
                "UPDATE idempotency SET response_json=?,updated_at=? WHERE scope='turn' AND key=? AND aggregate_id=?",
                (encoded, utc_now(), idempotency_key, turn_id),
            )
            return resolution

    def get_effect(self, effect_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM effects WHERE id=?", (effect_id,)).fetchone()
        return self._effect_row(row) if row else None

    @staticmethod
    def _effect_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
        return item

    def claim_outbox(
        self,
        worker: str,
        lease_seconds: float,
        message_id: str = "",
        destination: str = "",
    ) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        now_s = now.isoformat()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.write() as db:
            if message_id:
                row = db.execute("SELECT * FROM outbox WHERE message_id=?", (message_id,)).fetchone()
            else:
                if destination:
                    row = db.execute(
                        """
                        SELECT * FROM outbox
                        WHERE destination=? AND (
                            (state IN ('pending','retry_wait') AND (next_attempt_at IS NULL OR next_attempt_at <= ?))
                            OR (state='leased' AND lease_until <= ?)
                        )
                        ORDER BY id LIMIT 1
                        """,
                        (destination, now_s, now_s),
                    ).fetchone()
                else:
                    row = db.execute(
                        """
                        SELECT * FROM outbox
                        WHERE
                            (state IN ('pending','retry_wait') AND (next_attempt_at IS NULL OR next_attempt_at <= ?))
                            OR (state='leased' AND lease_until <= ?)
                        ORDER BY id LIMIT 1
                        """,
                        (now_s, now_s),
                    ).fetchone()
            if not row or row["state"] in {"delivered", "dead_letter"}:
                return None
            if row["state"] == "retry_wait" and str(row["next_attempt_at"] or "") > now_s:
                # Foreground dispatch obeys the same durable backoff as the
                # steward. Otherwise repeated explicit calls can exhaust the
                # retry budget before the scheduled attempt becomes eligible.
                return None
            if row["state"] == "leased" and str(row["lease_until"] or "") > now_s:
                return None
            token = uuid.uuid4().hex
            updated = db.execute(
                """
                UPDATE outbox SET state='leased',lease_owner=?,lease_token=?,lease_until=?,attempt_count=attempt_count+1
                WHERE id=? AND (
                    state IN ('pending','retry_wait') OR (state='leased' AND lease_until <= ?)
                )
                """,
                (worker, token, lease_until, row["id"], now_s),
            )
            if updated.rowcount != 1:
                return None
            # Keep the durable effect projection in lock-step with the outbox.
            # Foreground dispatch uses this state to wait for the fenced owner;
            # leaving it as ``pending`` made a concurrent caller report a false
            # failure while delivery was still in flight.
            db.execute(
                "UPDATE effects SET state='leased',updated_at=? WHERE id=?",
                (now_s, str(row["message_id"])),
            )
            claimed = db.execute("SELECT * FROM outbox WHERE id=?", (row["id"],)).fetchone()
            item = dict(claimed)
            item["payload"] = json.loads(item.pop("payload_json"))
            return item

    def finish_effect(
        self,
        *,
        effect_id: str,
        lease_token: str | None,
        ok: bool,
        result: dict[str, Any],
        retryable: bool = True,
    ) -> dict[str, Any]:
        with self.write() as db:
            outbox = db.execute("SELECT * FROM outbox WHERE message_id=?", (effect_id,)).fetchone()
            effect = db.execute("SELECT * FROM effects WHERE id=?", (effect_id,)).fetchone()
            if not outbox or not effect:
                raise LedgerError("effect not found")
            if outbox["state"] == "delivered":
                return self._effect_row(effect)
            if outbox["state"] == "dead_letter":
                raise InvariantViolation("dead-letter effect is fenced; reconcile its stable delegate instead")
            if outbox["state"] == "leased":
                if not lease_token or outbox["lease_token"] != lease_token:
                    raise InvariantViolation("leased effect requires its current lease token")
            elif lease_token is not None:
                raise InvariantViolation("stale effect lease cannot be finalized")
            if outbox["state"] != "leased":
                raise InvariantViolation("effect completion requires a claimed delivery lease")
            now = utc_now()
            attempts = int(outbox["attempt_count"] or 0)
            max_attempts = int(outbox["max_attempts"] or 3)
            commitment_id = str(effect["commitment_id"] or "")
            if ok:
                outbox_state = "delivered"
                effect_state = "delivered"
                commitment_state = "working" if effect["destination"] == "abaddon" else "succeeded"
                diagnostic = None
                honest = str(result.get("explanation") or "Действие подтверждено фактическим результатом.")
                next_attempt = None
            else:
                can_retry = retryable and attempts < max_attempts
                outbox_state = "retry_wait" if can_retry else "dead_letter"
                effect_state = outbox_state
                code = str(result.get("code") or "effect_failed")
                clarification = code in {
                    "administratum_needs_clarification",
                    "clarification_required",
                    "confirmation_required",
                }
                acknowledgement_unknown = code in {
                    "delivery_ack_unknown_after_restart",
                    "abaddon_unreachable",
                    "archive_adapter_unreachable",
                    "archive_artifact_adapter_unreachable",
                    "request_timeout",
                }
                if clarification:
                    commitment_state = "waiting_user"
                elif can_retry:
                    commitment_state = "retry_wait"
                elif acknowledgement_unknown:
                    # An exhausted transport retry budget is not proof that the
                    # downstream action failed. Quarantine the delivery and keep
                    # the stable delegate reconcilable instead of manufacturing
                    # a terminal failure.
                    commitment_state = "quarantined"
                else:
                    commitment_state = "failed"
                delay = min(300, max(5, 5 * (2 ** max(0, attempts - 1))))
                next_attempt = (
                    (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
                    if can_retry and not clarification
                    else None
                )
                diagnostic = {
                    "code": code,
                    "explanation": str(result.get("explanation") or "Орган не подтвердил действие."),
                    "evidence": result.get("evidence") if isinstance(result.get("evidence"), dict) else {},
                    "required_action": str(
                        result.get("required_action")
                        or (
                            "Уточнить недостающие параметры или дать требуемое подтверждение."
                            if clarification
                            else "Повторить после паузы."
                            if can_retry
                            else "Сверить стабильный идентификатор с органом и выбрать доказуемый следующий шаг."
                        )
                    ),
                    "resume_condition": str(
                        result.get("resume_condition")
                        or (
                            "Владелец даст недостающие параметры или подтверждение."
                            if clarification
                            else f"Автоповтор {next_attempt}"
                            if can_retry
                            else "Орган даст однозначный фактический статус по стабильному идентификатору."
                        )
                    ),
                }
                honest = diagnostic["explanation"]
            db.execute(
                """
                UPDATE outbox SET state=?,next_attempt_at=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL,
                    last_error_code=?,last_error_detail=?,delivered_at=? WHERE message_id=?
                """,
                (
                    outbox_state,
                    next_attempt,
                    None if ok else str(result.get("code") or "effect_failed"),
                    None if ok else str(result.get("explanation") or ""),
                    now if ok else None,
                    effect_id,
                ),
            )
            db.execute(
                "UPDATE effects SET state=?,result_json=?,updated_at=? WHERE id=?",
                (effect_state, canonical_json(result), now, effect_id),
            )
            self._append_event(
                db,
                aggregate_type="effect",
                aggregate_id=effect_id,
                kind="effect.delivered" if ok else "effect.retry_scheduled" if outbox_state == "retry_wait" else "effect.failed",
                actor="shushunya-core",
                correlation_id=str(effect["turn_id"]),
                causation_event_id=None,
                payload=result,
            )
            if commitment_id:
                row = db.execute("SELECT * FROM commitments WHERE id=?", (commitment_id,)).fetchone()
                if row and row["state"] not in {"succeeded", "failed", "cancelled"}:
                    previous_state = str(row["state"] or "")
                    effect_payload = json.loads(effect["payload_json"] or "{}")
                    stable_ref = effect_payload.get("task_id") if effect["destination"] == "abaddon" else None
                    delegate_ref = str(result.get("delegate_ref") or row["delegate_ref"] or stable_ref or "") or None
                    commit_event = self._append_event(
                        db,
                        aggregate_type="commitment",
                        aggregate_id=commitment_id,
                        kind=f"commitment.{commitment_state}",
                        actor="shushunya-core",
                        correlation_id=str(effect["turn_id"]),
                        causation_event_id=None,
                        payload={"state": commitment_state, "result": result, "diagnostic": diagnostic},
                    )
                    db.execute(
                        """
                        UPDATE commitments SET state=?,version=version+1,attempt_count=?,next_attempt_at=?,
                            delegate_ref=?,honest_status=?,diagnostic_json=?,result_json=?,last_event_seq=?,updated_at=?
                        WHERE id=?
                        """,
                        (
                            commitment_state,
                            attempts,
                            next_attempt,
                            delegate_ref,
                            honest,
                            canonical_json(diagnostic) if diagnostic else None,
                            canonical_json(result),
                            int(commit_event["seq"]),
                            now,
                            commitment_id,
                        ),
                    )
                    updated_commitment = db.execute(
                        "SELECT * FROM commitments WHERE id=?",
                        (commitment_id,),
                    ).fetchone()
                    self.enqueue_quarantine_notification(
                        db,
                        commitment_row=updated_commitment,
                        previous_state=previous_state,
                        diagnostic=diagnostic or {},
                        event_seq=int(commit_event["seq"]),
                        delegate_ref=str(delegate_ref or ""),
                    )
            updated = db.execute("SELECT * FROM effects WHERE id=?", (effect_id,)).fetchone()
            return self._effect_row(updated)

    def list_commitments(self, include_terminal: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM commitments"
        params: list[Any] = []
        if not include_terminal:
            sql += " WHERE state NOT IN ('succeeded','failed','cancelled')"
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["spec"] = json.loads(item.pop("spec_json"))
            item["diagnostic"] = json.loads(item.pop("diagnostic_json")) if item.get("diagnostic_json") else None
            item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
            result.append(item)
        return result

    def find_commitment_by_delegate_ref(self, delegate_ref: str) -> dict[str, Any] | None:
        """Return the newest durable commitment bound to an exact organ task id."""
        delegate_ref = str(delegate_ref or "").strip()
        if not delegate_ref:
            return None
        with self.connect() as db:
            row = db.execute(
                """
                SELECT * FROM commitments
                WHERE delegate_ref=?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (delegate_ref,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["spec"] = json.loads(item.pop("spec_json"))
        item["diagnostic"] = json.loads(item.pop("diagnostic_json")) if item.get("diagnostic_json") else None
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
        return item

    def find_open_continuation(self, parent_task_id: str) -> dict[str, Any] | None:
        """Find the one newest nonterminal child already linked to a parent."""
        parent_task_id = str(parent_task_id or "").strip()
        if not parent_task_id:
            return None
        with self.connect() as db:
            row = db.execute(
                """
                SELECT * FROM commitments
                WHERE kind='abaddon_mission'
                  AND state IN (
                    'queued','working','revising','waiting_user',
                    'waiting_external','retry_wait'
                  )
                  AND json_extract(spec_json, '$.parent_task_id')=?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (parent_task_id,),
            ).fetchone()
            if not row:
                return None
            effect_row = db.execute(
                """
                SELECT * FROM effects
                WHERE commitment_id=?
                  AND kind='continue_warmaster_mission'
                  AND destination='abaddon'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (str(row["id"]),),
            ).fetchone()
        commitment = dict(row)
        commitment["spec"] = json.loads(commitment.pop("spec_json"))
        commitment["diagnostic"] = (
            json.loads(commitment.pop("diagnostic_json"))
            if commitment.get("diagnostic_json")
            else None
        )
        commitment["result"] = (
            json.loads(commitment.pop("result_json"))
            if commitment.get("result_json")
            else None
        )
        return {
            "commitment": commitment,
            "effect": self._effect_row(effect_row) if effect_row else None,
        }

    def list_events(self, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?",
                (max(0, int(after)), max(1, min(int(limit), 500))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def projection_get(self, namespace: str, key: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM state_projection WHERE namespace=? AND key=?",
                (namespace, key),
            ).fetchone()
        if not row:
            return None
        return {**dict(row), "value": json.loads(row["value_json"])}

    def projection_put(self, namespace: str, key: str, value: Any, actor: str = "system") -> dict[str, Any]:
        with self.write() as db:
            current = db.execute(
                "SELECT * FROM state_projection WHERE namespace=? AND key=?",
                (namespace, key),
            ).fetchone()
            version = int(current["version"] if current else 0) + 1
            event = self._append_event(
                db,
                aggregate_type=namespace,
                aggregate_id=key,
                kind=f"{namespace}.updated",
                actor=actor,
                correlation_id=f"{namespace}:{key}",
                causation_event_id=None,
                payload={"key": key, "value": value, "version": version},
            )
            encoded = canonical_json(value)
            now = utc_now()
            db.execute(
                """
                INSERT INTO state_projection(namespace,key,value_json,value_sha256,version,last_event_seq,updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(namespace,key) DO UPDATE SET
                    value_json=excluded.value_json,value_sha256=excluded.value_sha256,
                    version=excluded.version,last_event_seq=excluded.last_event_seq,updated_at=excluded.updated_at
                """,
                (namespace, key, encoded, hashlib.sha256(encoded.encode()).hexdigest(), version, int(event["seq"]), now),
            )
        return {"namespace": namespace, "key": key, "value": value, "version": version, "updated_at": now}

    def recover_after_restart(self) -> dict[str, int]:
        recovered_outbox = 0
        recovered_commitments = 0
        now = utc_now()
        # A crashed delivery has an unknown acknowledgement. Finalize it through
        # the normal fenced path so retry limits, effect events and commitment
        # truth all advance together.
        with self.connect() as db:
            leased = [dict(row) for row in db.execute("SELECT * FROM outbox WHERE state='leased'").fetchall()]
        for row in leased:
            self.finish_effect(
                effect_id=str(row["message_id"]),
                lease_token=str(row["lease_token"]),
                ok=False,
                result={
                    "code": "delivery_ack_unknown_after_restart",
                    "explanation": "Core перезапустился до фиксации ответа внешнего органа.",
                    "evidence": {"previous_worker": row.get("lease_owner"), "attempt_count": row.get("attempt_count")},
                    "required_action": "Повторить доставку с тем же idempotency key, если лимит попыток не исчерпан.",
                    "resume_condition": "Орган даст однозначное подтверждение приёма или отказа.",
                },
                retryable=True,
            )
            recovered_outbox += 1
        with self.write() as db:
            orphaned = db.execute(
                "SELECT * FROM commitments WHERE state='working' AND (delegate_ref IS NULL OR delegate_ref='')"
            ).fetchall()
            for row in orphaned:
                diagnostic = {
                    "code": "execution_ack_unknown_after_restart",
                    "explanation": "Core перезапустился до фиксации ссылки на исполнителя.",
                    "evidence": {"commitment_id": row["id"]},
                    "required_action": "Повторно сверить outbox и реестр органов.",
                    "resume_condition": "Появится подтверждённый delegate_ref или будет выбран новый способ.",
                }
                event = self._append_event(
                    db,
                    aggregate_type="commitment",
                    aggregate_id=row["id"],
                    kind="commitment.retry_wait",
                    actor="boot-recovery",
                    correlation_id=row["id"],
                    causation_event_id=None,
                    payload=diagnostic,
                )
                db.execute(
                    """
                    UPDATE commitments SET state='retry_wait',version=version+1,next_attempt_at=?,
                        honest_status=?,diagnostic_json=?,last_event_seq=?,updated_at=? WHERE id=?
                    """,
                    (
                        now,
                        diagnostic["explanation"],
                        canonical_json(diagnostic),
                        int(event["seq"]),
                        now,
                        row["id"],
                    ),
                )
                recovered_commitments += 1
        return {"outbox": recovered_outbox, "commitments": recovered_commitments}

    def status(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        if self.db_path.exists():
            with self.connect() as db:
                for row in db.execute("SELECT state,COUNT(*) AS n FROM commitments GROUP BY state"):
                    counts[str(row["state"])] = int(row["n"])
                pending = int(
                    db.execute("SELECT COUNT(*) FROM outbox WHERE state IN ('pending','leased','retry_wait')").fetchone()[0]
                )
                last_seq = int(db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0])
        else:
            pending = 0
            last_seq = 0
        return {
            "ready": self.ready,
            "integrity_error": self.integrity_error,
            "db_path": str(self.db_path),
            "commitments": counts,
            "pending_effects": pending,
            "last_event_seq": last_seq,
        }
