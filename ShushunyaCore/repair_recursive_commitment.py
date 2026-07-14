from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


MAX_PROJECTED_JSON_BYTES = 64 * 1024
COMPACT_FROM_VERSION = 5
COMPACT_TO_VERSION = 17
REPAIR_AGGREGATE_VERSION = 18
TRIGGER_NAMES = ("events_no_update", "events_no_delete")


class RepairError(RuntimeError):
    """The forensic source is not the exact database this tool can repair."""


class RepairRefused(RepairError):
    """The requested paths could mutate a live, existing, or ambiguous file."""


@dataclass(frozen=True)
class EventExpectation:
    version: int
    seq: int
    event_id: str
    kind: str
    payload_sha256: str
    payload_bytes: int


@dataclass(frozen=True)
class RepairExpectation:
    commitment_id: str
    delegate_ref: str
    goal: str
    honest_status: str
    spec_sha256: str
    state: str
    commitment_version: int
    last_event_seq: int
    diagnostic_bytes: int
    diagnostic_sha256: str
    result_bytes: int
    result_sha256: str
    attempt_count: int
    max_attempts: int
    events: tuple[EventExpectation, ...]
    database_event_count: int | None = None
    database_max_event_seq: int | None = None


DEFAULT_EXPECTATION = RepairExpectation(
    commitment_id="commitment-aedceca910194789ae0c7d4137a68015",
    delegate_ref="core-aedceca910194789ae0c",
    goal="Рабочий установочный файл (.apk) игры Galaga для Android.",
    honest_status=(
        "Abaddon rejected the recovery attempt because its immutable task-memory "
        "ancestry is inconsistent. The goal and prior evidence remain preserved; "
        "generating more child ids cannot repair provenance."
    ),
    spec_sha256="e389987ba08478de5102af3d6684bbd20da01ec9eb8c711c6c0458b550850756",
    state="waiting_external",
    commitment_version=17,
    last_event_seq=90,
    diagnostic_bytes=444_289_007,
    diagnostic_sha256="250beada99f8d1c1799526e8f6b49b6ef029fe82d363c58abfb2559e0d000abf",
    result_bytes=444_288_102,
    result_sha256="e478bc73a29e9b8f07a435949886eefba1be67ac00fffaf1fec3745d88f52114",
    attempt_count=3,
    max_attempts=3,
    database_event_count=90,
    database_max_event_seq=90,
    events=(
        EventExpectation(1, 42, "evt-44cd8a1de1d5405cba1939f2eb29da94", "commitment.opened", "23e938cf6f95718e0c663bb42b7e3df4ad188fc085b6fb7b253ef7135ed19032", 3_839),
        EventExpectation(2, 44, "evt-cd7cb0eb0802470bb4cf297b4cfc2835", "commitment.retry_wait", "fadaf407a7ea63dfa444b328800a55605269774cd8b896c7bdff1bf621537831", 84_510),
        EventExpectation(3, 46, "evt-a4deecb2d5104176b79d9f81022f6db0", "commitment.retry_wait", "2bdbb275d56774a5a2908261e59dd6d39b51365be67e08576c19a88820f29c17", 84_582),
        EventExpectation(4, 50, "evt-0a88e1d3091a4e3fbde603cac3d49cac", "commitment.failed", "ea0da30067b87e723b164815bef046663db83dbda7c339ab7d6640aef44f8e33", 85_334),
        EventExpectation(5, 77, "evt-3ecbe5b8b4a04c908211b5945365883b", "commitment.waiting_external", "1579548952066cb0c4887d2d45e51c88b8761cda26df5e4f039bbd7e8c5ac2f6", 194_433),
        EventExpectation(6, 79, "evt-b53deb86a2ee42a68dbe1474bdd7be5c", "commitment.waiting_external", "8187ccf64916875bd2b87e0543e2f8ec327bfc0dcd75503c5a4240c42431c676", 411_573),
        EventExpectation(7, 80, "evt-bf1f695b82a9403f91f063f28ed9e345", "commitment.waiting_external", "2ff5e0a86c9fc28dabd5fde5f5a56fcbab959770f2ceb18419ac2a0ea2688c4f", 845_493),
        EventExpectation(8, 81, "evt-a7728e3e60124b27b2115c505daa38a1", "commitment.waiting_external", "68d6ea0bc9b4590ce5287e9da5aa7f8a248dc10acb958caf98e440322f22d138", 1_713_321),
        EventExpectation(9, 82, "evt-0240c9217bf347c188551967d12471c8", "commitment.waiting_external", "dbc6370734655ec9ef8c140a22a3df093294b84d13a85de67543dec1f891a2b8", 3_448_973),
        EventExpectation(10, 83, "evt-37369a3e416443ac96d94e10bc46dd13", "commitment.waiting_external", "4603061a2a711c64a4ad32d1112e8336faf547c0203acfd5eeda31050a0c2308", 6_920_249),
        EventExpectation(11, 84, "evt-c4da45824f42448aa09b0642bb288bf4", "commitment.waiting_external", "af4a485277d9970a781e0a695dac29831f46b5fc59480b8611c23271c0ad98c1", 13_862_733),
        EventExpectation(12, 85, "evt-a9b6227fb5c94ffcb59b134039748ca7", "commitment.waiting_external", "1cfec16c335eb1f602666f7f1f12548e28afd73ce2df17516e1b27f3cd0a24f7", 27_747_491),
        EventExpectation(13, 86, "evt-69e01ea554704f36abe2017da203fa3b", "commitment.waiting_external", "d57d6fb2d0885bdf535db179cde1e71a6d90dbce2408028adf0b12786b4145ae", 55_516_515),
        EventExpectation(14, 87, "evt-954af950c9a44d07bbc0ea252a3b0078", "commitment.waiting_external", "3ac79953e99941c64bcf92a1b1abca4be9a3ba119c305abbc0c0f9630193699a", 111_054_065),
        EventExpectation(15, 88, "evt-977dac95c1bc44389c904b9b76043f61", "commitment.waiting_external", "019defb75f3278db931fd2af8d5f24b0f00c60603b074f4c9bf4f666ca148ed9", 222_128_833),
        EventExpectation(16, 89, "evt-a7c0a71b635c462cad814ec11beea32b", "commitment.waiting_external", "4cdf863b95f70d17c3d15d65bbbcfe955d4995b9b7c891e4c4adcf7e106d26d3", 444_278_369),
        EventExpectation(17, 90, "evt-2621b8427e0c48e396219cfc67870dda", "commitment.waiting_external", "2a000f23d3317b91e48a6e31fd4f2d0032c29fde4b50e1eb01b3d0ddbe6e739b", 888_577_441),
    ),
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    before = path.stat()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise RepairRefused("forensic source changed while it was being hashed")
    return digest.hexdigest()


def _known_live_paths() -> set[Path]:
    project_root = Path(__file__).resolve().parents[1]
    paths = {(project_root / "runtime" / "shushunya-core" / "store.sqlite3").resolve()}
    runtime = os.environ.get("SHUSHUNYA_CORE_RUNTIME_DIR", "").strip()
    if runtime:
        paths.add((Path(runtime).expanduser() / "store.sqlite3").resolve())
    configured = os.environ.get("SHUSHUNYA_CORE_DB", "").strip()
    if configured:
        paths.add(Path(configured).expanduser().resolve())
    return paths


def _validate_paths(source_arg: Path, output_arg: Path) -> tuple[Path, Path]:
    expanded_source = source_arg.expanduser()
    expanded_output = output_arg.expanduser()
    if expanded_source.is_symlink():
        raise RepairRefused("--source must be a regular forensic file, not a symlink")
    if expanded_output.is_symlink():
        raise RepairRefused("--output must not be a symlink")
    source = expanded_source.resolve(strict=True)
    output = expanded_output.resolve(strict=False)
    if not stat.S_ISREG(source.stat().st_mode):
        raise RepairRefused("--source is not a regular file")
    if source.stat().st_nlink != 1:
        raise RepairRefused("--source must not be a hard-link alias of another database")
    if source == output:
        raise RepairRefused("in-place repair is forbidden")
    if output.exists() or output.is_symlink():
        raise RepairRefused("--output must not already exist")
    if not output.parent.is_dir():
        raise RepairRefused("--output parent directory must already exist")
    live_paths = _known_live_paths()
    if source in live_paths:
        raise RepairRefused("--source resolves to the configured/default live Core database")
    if output in live_paths:
        raise RepairRefused("--output resolves to the configured/default live Core database")
    for suffix in ("-wal", "-journal"):
        journal = Path(str(source) + suffix)
        if journal.exists() and journal.stat().st_size:
            raise RepairRefused(
                f"forensic source has a non-empty {suffix[1:]} sidecar; checkpoint a copy first"
            )
    return source, output


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = path.as_uri() + "?mode=ro&immutable=1"
    db = sqlite3.connect(uri, uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA query_only=ON")
    return db


def _connect_work(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA synchronous=FULL")
    mode = str(db.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
    if mode != "delete":
        db.close()
        raise RepairError(f"working copy refused DELETE journal mode: {mode}")
    return db


def _verify_database_integrity(db: sqlite3.Connection, *, full: bool) -> None:
    pragma = "integrity_check" if full else "quick_check"
    rows = [str(row[0]) for row in db.execute(f"PRAGMA {pragma}")]
    if rows != ["ok"]:
        raise RepairError(f"PRAGMA {pragma} failed: {rows[:10]}")
    violations = list(db.execute("PRAGMA foreign_key_check"))
    if violations:
        raise RepairError(f"foreign key violations: {[tuple(row) for row in violations[:10]]}")


def _event_trigger_sql(db: sqlite3.Connection) -> dict[str, str]:
    rows = db.execute(
        "SELECT name,tbl_name,sql FROM sqlite_master "
        "WHERE type='trigger' AND lower(tbl_name)=lower('events') ORDER BY name",
    ).fetchall()
    found = {str(row["name"]): str(row["sql"] or "") for row in rows}
    if set(found) != set(TRIGGER_NAMES) or any(not sql for sql in found.values()):
        raise RepairError("expected append-only event triggers are missing or attached elsewhere")
    normalized = {name: " ".join(sql.lower().split()) for name, sql in found.items()}
    expected_sql = {
        "events_no_update": (
            "create trigger events_no_update before update on events begin "
            "select raise(abort, 'events are append-only'); end"
        ),
        "events_no_delete": (
            "create trigger events_no_delete before delete on events begin "
            "select raise(abort, 'events are append-only'); end"
        ),
    }
    for name, expected in expected_sql.items():
        if normalized[name] != expected:
            raise RepairError(f"{name} is not the audited append-only guard")
    return found


def _blob_sha256(
    db: sqlite3.Connection,
    table: str,
    column: str,
    rowid: int,
    expected_bytes: int,
) -> str:
    digest = hashlib.sha256()
    counted = 0
    with db.blobopen(table, column, rowid, readonly=True) as blob:
        if len(blob) != expected_bytes:
            raise RepairError(
                f"{table}.{column} rowid={rowid} changed size during forensic hashing"
            )
        while True:
            chunk = blob.read(8 * 1024 * 1024)
            if not chunk:
                break
            counted += len(chunk)
            digest.update(chunk)
    if counted != expected_bytes:
        raise RepairError(f"short forensic read of {table}.{column} rowid={rowid}")
    return digest.hexdigest()


def _row_digest(rows: Iterable[sqlite3.Row]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(_canonical_json(list(row)).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _unaffected_fingerprints(db: sqlite3.Connection, expectation: RepairExpectation) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    tables = [
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        if table == "events":
            rows = db.execute(
                f"SELECT * FROM {quoted} WHERE NOT (aggregate_type='commitment' AND aggregate_id=?) ORDER BY rowid",
                (expectation.commitment_id,),
            )
        elif table == "commitments":
            rows = db.execute(
                f"SELECT * FROM {quoted} WHERE id<>? ORDER BY rowid",
                (expectation.commitment_id,),
            )
        else:
            rows = db.execute(f"SELECT * FROM {quoted} ORDER BY rowid")
        fingerprints[table] = _row_digest(rows)
    return fingerprints


def _target_invariant(db: sqlite3.Connection, commitment_id: str) -> tuple[Any, ...]:
    row = db.execute(
        """
        SELECT id,kind,owner,goal,spec_json,spec_sha256,state,priority,due_at,
               next_attempt_at,attempt_count,max_attempts,delegate_kind,delegate_ref,
               honest_status,created_at
        FROM commitments WHERE id=?
        """,
        (commitment_id,),
    ).fetchone()
    if not row:
        raise RepairError("target commitment is missing")
    return tuple(row)


@dataclass(frozen=True)
class SourceSnapshot:
    triggers: dict[str, str]
    unaffected: dict[str, str]
    target_invariant: tuple[Any, ...]
    target_event_metadata: dict[int, tuple[Any, ...]]


def _event_metadata(row: sqlite3.Row) -> tuple[Any, ...]:
    return tuple(
        row[field]
        for field in (
            "seq",
            "event_id",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "kind",
            "occurred_at",
            "actor",
            "correlation_id",
            "causation_event_id",
        )
    )


def _verify_exact_source(db: sqlite3.Connection, expectation: RepairExpectation) -> SourceSnapshot:
    _verify_database_integrity(db, full=True)
    triggers = _event_trigger_sql(db)
    row = db.execute(
        """
        SELECT rowid AS storage_rowid,id,goal,state,version,last_event_seq,delegate_ref,honest_status,
               spec_sha256,attempt_count,max_attempts,
               length(CAST(diagnostic_json AS BLOB)) AS diagnostic_bytes,
               length(CAST(result_json AS BLOB)) AS result_bytes
        FROM commitments WHERE id=?
        """,
        (expectation.commitment_id,),
    ).fetchone()
    if not row:
        raise RepairError("exact target commitment is absent")
    expected_fields = {
        "id": expectation.commitment_id,
        "goal": expectation.goal,
        "state": expectation.state,
        "version": expectation.commitment_version,
        "last_event_seq": expectation.last_event_seq,
        "delegate_ref": expectation.delegate_ref,
        "honest_status": expectation.honest_status,
        "spec_sha256": expectation.spec_sha256,
        "attempt_count": expectation.attempt_count,
        "max_attempts": expectation.max_attempts,
        "diagnostic_bytes": expectation.diagnostic_bytes,
        "result_bytes": expectation.result_bytes,
    }
    actual_fields = dict(row)
    mismatches = {
        key: {"expected": value, "actual": actual_fields.get(key)}
        for key, value in expected_fields.items()
        if actual_fields.get(key) != value
    }
    if mismatches:
        raise RepairError(f"target commitment does not match forensic expectation: {mismatches}")
    storage_rowid = int(row["storage_rowid"])
    if _blob_sha256(
        db, "commitments", "spec_json", storage_rowid,
        int(db.execute("SELECT length(CAST(spec_json AS BLOB)) FROM commitments WHERE rowid=?", (storage_rowid,)).fetchone()[0]),
    ) != expectation.spec_sha256:
        raise RepairError("target spec_json bytes do not match spec_sha256")
    if _blob_sha256(
        db, "commitments", "diagnostic_json", storage_rowid, expectation.diagnostic_bytes
    ) != expectation.diagnostic_sha256:
        raise RepairError("target diagnostic_json hash differs from the forensic audit")
    if _blob_sha256(
        db, "commitments", "result_json", storage_rowid, expectation.result_bytes
    ) != expectation.result_sha256:
        raise RepairError("target result_json hash differs from the forensic audit")

    event_rows = db.execute(
        """
        SELECT seq,event_id,aggregate_type,aggregate_id,aggregate_version,kind,
               occurred_at,actor,correlation_id,causation_event_id,payload_sha256,
               length(CAST(payload_json AS BLOB)) AS payload_bytes
        FROM events WHERE aggregate_type='commitment' AND aggregate_id=?
        ORDER BY aggregate_version
        """,
        (expectation.commitment_id,),
    ).fetchall()
    actual_events = [
        (
            row["seq"], row["event_id"], row["aggregate_version"], row["kind"],
            row["payload_sha256"], row["payload_bytes"],
        )
        for row in event_rows
    ]
    expected_events = [
        (event.seq, event.event_id, event.version, event.kind, event.payload_sha256, event.payload_bytes)
        for event in expectation.events
    ]
    if actual_events != expected_events:
        raise RepairError("target event history differs from the audited v1..v17 sequence")
    for event in expectation.events:
        actual_sha256 = _blob_sha256(
            db, "events", "payload_json", event.seq, event.payload_bytes
        )
        if actual_sha256 != event.payload_sha256:
            raise RepairError(
                f"event v{event.version} payload bytes differ from its audited SHA-256"
            )
    if [event.version for event in expectation.events] != list(range(1, 18)):
        raise RepairError("repair expectation itself must describe contiguous v1..v17")
    if expectation.database_event_count is not None:
        count = int(db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        if count != expectation.database_event_count:
            raise RepairError(f"database event count changed: expected {expectation.database_event_count}, got {count}")
    if expectation.database_max_event_seq is not None:
        maximum = int(db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0])
        if maximum != expectation.database_max_event_seq:
            raise RepairError(f"database max event seq changed: expected {expectation.database_max_event_seq}, got {maximum}")
    return SourceSnapshot(
        triggers=triggers,
        unaffected=_unaffected_fingerprints(db, expectation),
        target_invariant=_target_invariant(db, expectation.commitment_id),
        target_event_metadata={
            int(row["aggregate_version"]): _event_metadata(row) for row in event_rows
        },
    )


def _original_event_ref(event: EventExpectation, forensic_sha256: str) -> dict[str, Any]:
    return {
        "aggregate_version": event.version,
        "event_id": event.event_id,
        "forensic_db_sha256": forensic_sha256,
        "payload_bytes": event.payload_bytes,
        "payload_sha256": event.payload_sha256,
        "seq": event.seq,
    }


def _bounded_attempts(expectation: RepairExpectation) -> tuple[dict[str, Any], dict[str, Any]]:
    previous = {
        "phase": "delegation_not_created",
        "status": "failed",
        "task_id": expectation.delegate_ref,
    }
    recovery = {
        "continuation_of": expectation.delegate_ref,
        "goal_id": expectation.delegate_ref,
        "http_status": 409,
        "idempotency_key": "recovery-e662c2da3904cb1b6c89270b",
        "outcome_type": "rejected",
        "parent_task_id": expectation.delegate_ref,
        "recovery_generation": 0,
        "recovery_of": expectation.delegate_ref,
        "root_task_id": expectation.delegate_ref,
        "task_id": "core-recovery-e662c2da3904cb1b6c89270b",
        "task_memory_id": expectation.delegate_ref,
        "technical_error_code": "task_memory_parent_conflict",
    }
    return previous, recovery


def _bounded_diagnostic(
    expectation: RepairExpectation,
    evidence_reference: dict[str, Any],
) -> dict[str, Any]:
    previous, recovery = _bounded_attempts(expectation)
    return {
        "code": "task_memory_lineage_repair_required",
        "evidence": {
            "compacted_history": evidence_reference,
            "downstream_error_code": "task_memory_parent_conflict",
            "previous_attempt": previous,
            "recovery_attempt": recovery,
        },
        "explanation": expectation.honest_status,
        "external_dependency": "internal task-memory lineage reconciliation",
        "required_action": (
            "Reconcile the existing parent run, mission record, and Archive page "
            "identity without rebinding or deleting their evidence."
        ),
        "requires_user": False,
        "resume_condition": (
            "The parent task_memory.json, mission lineage, and Archive root identity "
            "agree, after which this same recovery can be retried."
        ),
    }


def _bounded_result(
    expectation: RepairExpectation,
    evidence_reference: dict[str, Any],
) -> dict[str, Any]:
    previous, recovery = _bounded_attempts(expectation)
    return {
        "evidence_reference": evidence_reference,
        "previous_attempt": previous,
        "recovery": recovery,
        "recovery_generation": 0,
    }


def _encode_bounded(value: dict[str, Any], label: str) -> tuple[str, str]:
    encoded = _canonical_json(value)
    size = len(encoded.encode("utf-8"))
    if size >= MAX_PROJECTED_JSON_BYTES:
        raise RepairError(f"{label} projection is {size} bytes, expected < {MAX_PROJECTED_JSON_BYTES}")
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _repair_working_copy(
    db: sqlite3.Connection,
    expectation: RepairExpectation,
    source_sha256: str,
    source_snapshot: SourceSnapshot,
) -> tuple[int, str]:
    compact_events = [
        event for event in expectation.events
        if COMPACT_FROM_VERSION <= event.version <= COMPACT_TO_VERSION
    ]
    history = [_original_event_ref(event, source_sha256) for event in compact_events]
    latest_ref = {
        "commitment_id": expectation.commitment_id,
        "forensic_db_sha256": source_sha256,
        "original_current": {
            "diagnostic_bytes": expectation.diagnostic_bytes,
            "diagnostic_sha256": expectation.diagnostic_sha256,
            "result_bytes": expectation.result_bytes,
            "result_sha256": expectation.result_sha256,
        },
        "original_events": history,
        "projection_schema": "shushunya-core/recursive-evidence-ref/v1",
        "spec_sha256": expectation.spec_sha256,
    }
    current_diagnostic = _bounded_diagnostic(expectation, latest_ref)
    current_result = _bounded_result(expectation, latest_ref)
    diagnostic_json, _ = _encode_bounded(current_diagnostic, "current diagnostic")
    result_json, _ = _encode_bounded(current_result, "current result")
    repaired_at = datetime.now(UTC).isoformat()

    db.execute("BEGIN EXCLUSIVE")
    try:
        for name in TRIGGER_NAMES:
            db.execute(f'DROP TRIGGER "{name}"')
        for event in compact_events:
            event_ref = {
                "commitment_id": expectation.commitment_id,
                "forensic_db_sha256": source_sha256,
                "original_event": _original_event_ref(event, source_sha256),
                "projection_schema": "shushunya-core/recursive-evidence-ref/v1",
                "spec_sha256": expectation.spec_sha256,
            }
            payload = {
                "compaction": {
                    "reason": "recursive_commitment_snapshot_amplification",
                    **event_ref,
                },
                "delegate_ref": expectation.delegate_ref,
                "diagnostic": _bounded_diagnostic(expectation, event_ref),
                "from": "failed" if event.version == COMPACT_FROM_VERSION else "waiting_external",
                "honest_status": expectation.honest_status,
                "result": _bounded_result(expectation, event_ref),
                "to": "waiting_external",
            }
            payload_json, payload_sha256 = _encode_bounded(payload, f"event v{event.version}")
            changed = db.execute(
                """
                UPDATE events SET payload_json=?,payload_sha256=?
                WHERE seq=? AND event_id=? AND aggregate_type='commitment'
                  AND aggregate_id=? AND aggregate_version=? AND payload_sha256=?
                """,
                (
                    payload_json,
                    payload_sha256,
                    event.seq,
                    event.event_id,
                    expectation.commitment_id,
                    event.version,
                    event.payload_sha256,
                ),
            )
            if changed.rowcount != 1:
                raise RepairError(f"event v{event.version} changed during repair")

        repair_event_id = "evt-repair-" + hashlib.sha256(
            f"{expectation.commitment_id}:{source_sha256}:v18".encode("utf-8")
        ).hexdigest()[:32]
        repair_payload = {
            "compaction": {
                "commitment_id": expectation.commitment_id,
                "forensic_db_sha256": source_sha256,
                "kind": "recursive_commitment_snapshot_repair",
                "original_current_diagnostic_bytes": expectation.diagnostic_bytes,
                "original_current_diagnostic_sha256": expectation.diagnostic_sha256,
                "original_current_result_bytes": expectation.result_bytes,
                "original_current_result_sha256": expectation.result_sha256,
                "original_current_container_payload_sha256": expectation.events[-1].payload_sha256,
                "original_event_count": len(compact_events),
                "original_event_payload_bytes": sum(event.payload_bytes for event in compact_events),
                "original_events": history,
                "projection_schema": "shushunya-core/recursive-evidence-ref/v1",
                "repaired_at": repaired_at,
                "spec_sha256": expectation.spec_sha256,
            },
            "delegate_ref": expectation.delegate_ref,
            "diagnostic": current_diagnostic,
            "from": expectation.state,
            "honest_status": expectation.honest_status,
            "result": current_result,
            "to": expectation.state,
        }
        repair_payload_json, repair_payload_sha256 = _encode_bounded(repair_payload, "v18 repair event")
        cursor = db.execute(
            """
            INSERT INTO events(
                event_id,aggregate_type,aggregate_id,aggregate_version,kind,
                occurred_at,actor,correlation_id,causation_event_id,
                payload_json,payload_sha256
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                repair_event_id,
                "commitment",
                expectation.commitment_id,
                REPAIR_AGGREGATE_VERSION,
                "commitment.waiting_external",
                repaired_at,
                "shushunya-core-repair",
                expectation.commitment_id,
                expectation.events[-1].event_id,
                repair_payload_json,
                repair_payload_sha256,
            ),
        )
        repair_seq = int(cursor.lastrowid)
        changed = db.execute(
            """
            UPDATE commitments
            SET version=?,diagnostic_json=?,result_json=?,last_event_seq=?,updated_at=?
            WHERE id=? AND version=? AND last_event_seq=? AND state=?
            """,
            (
                REPAIR_AGGREGATE_VERSION,
                diagnostic_json,
                result_json,
                repair_seq,
                repaired_at,
                expectation.commitment_id,
                expectation.commitment_version,
                expectation.last_event_seq,
                expectation.state,
            ),
        )
        if changed.rowcount != 1:
            raise RepairError("target commitment changed during repair")
        for name in sorted(source_snapshot.triggers):
            db.execute(source_snapshot.triggers[name])
    except Exception:
        db.rollback()
        raise
    else:
        db.commit()
    return repair_seq, repair_event_id


def _assert_append_only(db: sqlite3.Connection, seq: int) -> None:
    statements = (
        ("UPDATE events SET actor=actor WHERE seq=?", "update"),
        ("DELETE FROM events WHERE seq=?", "delete"),
    )
    for index, (statement, operation) in enumerate(statements):
        savepoint = f"verify_append_only_{index}"
        db.execute(f"SAVEPOINT {savepoint}")
        try:
            db.execute(statement, (seq,))
        except sqlite3.DatabaseError as exc:
            db.execute(f"ROLLBACK TO {savepoint}")
            db.execute(f"RELEASE {savepoint}")
            if "append-only" not in str(exc):
                raise RepairError(
                    f"event {operation} failed for an unexpected reason: {exc}"
                ) from exc
        else:
            db.execute(f"ROLLBACK TO {savepoint}")
            db.execute(f"RELEASE {savepoint}")
            raise RepairError(f"append-only {operation} trigger was not restored")


def _verify_repaired(
    db: sqlite3.Connection,
    expectation: RepairExpectation,
    source_sha256: str,
    source_snapshot: SourceSnapshot,
) -> tuple[int, str]:
    _verify_database_integrity(db, full=False)
    _event_trigger_sql(db)
    if _unaffected_fingerprints(db, expectation) != source_snapshot.unaffected:
        raise RepairError("a row outside the audited commitment changed")
    if _target_invariant(db, expectation.commitment_id) != source_snapshot.target_invariant:
        raise RepairError("an immutable target commitment field changed")

    rows = db.execute(
        """
        SELECT seq,event_id,aggregate_type,aggregate_id,aggregate_version,kind,
               occurred_at,actor,correlation_id,causation_event_id,
               payload_json,payload_sha256,
               length(CAST(payload_json AS BLOB)) AS payload_bytes
        FROM events WHERE aggregate_type='commitment' AND aggregate_id=?
        ORDER BY aggregate_version
        """,
        (expectation.commitment_id,),
    ).fetchall()
    versions = [int(row["aggregate_version"]) for row in rows]
    if versions != list(range(1, REPAIR_AGGREGATE_VERSION + 1)):
        raise RepairError(f"target event versions are not contiguous v1..v18: {versions}")

    expected_by_version = {event.version: event for event in expectation.events}
    expected_history = [
        _original_event_ref(expected_by_version[version], source_sha256)
        for version in range(COMPACT_FROM_VERSION, COMPACT_TO_VERSION + 1)
    ]
    repair_seq = 0
    repair_event_id = ""
    for row in rows:
        version = int(row["aggregate_version"])
        if version <= COMPACT_TO_VERSION:
            if _event_metadata(row) != source_snapshot.target_event_metadata.get(version):
                raise RepairError(f"event v{version} non-payload metadata changed")
        else:
            repair_compaction = json.loads(str(row["payload_json"])).get("compaction", {})
            expected_v18_metadata = (
                int(row["seq"]),
                str(row["event_id"]),
                "commitment",
                expectation.commitment_id,
                REPAIR_AGGREGATE_VERSION,
                "commitment.waiting_external",
                repair_compaction.get("repaired_at"),
                "shushunya-core-repair",
                expectation.commitment_id,
                expectation.events[-1].event_id,
            )
            if _event_metadata(row) != expected_v18_metadata:
                raise RepairError("v18 repair event metadata is invalid")
        payload_json = str(row["payload_json"])
        payload_bytes = len(payload_json.encode("utf-8"))
        if payload_bytes != int(row["payload_bytes"]):
            raise RepairError(f"event v{version} byte length query disagrees with Python")
        calculated = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        if calculated != row["payload_sha256"]:
            raise RepairError(f"event v{version} payload hash is invalid")
        payload = json.loads(payload_json)
        if version <= 4:
            original = expected_by_version[version]
            if (
                row["seq"], row["event_id"], row["kind"], row["payload_sha256"], payload_bytes
            ) != (
                original.seq, original.event_id, original.kind,
                original.payload_sha256, original.payload_bytes,
            ):
                raise RepairError(f"preserved event v{version} changed")
        elif version <= COMPACT_TO_VERSION:
            if payload_bytes >= MAX_PROJECTED_JSON_BYTES:
                raise RepairError(f"compacted event v{version} remains oversized")
            original = expected_by_version[version]
            proof = payload.get("compaction", {}).get("original_event", {})
            if proof != _original_event_ref(original, source_sha256):
                raise RepairError(f"compacted event v{version} lost its forensic proof")
        else:
            repair_seq = int(row["seq"])
            repair_event_id = str(row["event_id"])
            if payload_bytes >= MAX_PROJECTED_JSON_BYTES:
                raise RepairError("v18 repair event is oversized")
            compaction = payload.get("compaction", {})
            if (
                compaction.get("forensic_db_sha256") != source_sha256
                or compaction.get("commitment_id") != expectation.commitment_id
                or compaction.get("spec_sha256") != expectation.spec_sha256
                or compaction.get("original_events") != expected_history
                or compaction.get("original_current_diagnostic_bytes") != expectation.diagnostic_bytes
                or compaction.get("original_current_diagnostic_sha256")
                != expectation.diagnostic_sha256
                or compaction.get("original_current_result_bytes") != expectation.result_bytes
                or compaction.get("original_current_result_sha256") != expectation.result_sha256
                or compaction.get("original_current_container_payload_sha256")
                != expectation.events[-1].payload_sha256
            ):
                raise RepairError("v18 repair event lost the forensic database hash")

    commitment = db.execute(
        """
        SELECT version,last_event_seq,diagnostic_json,result_json,
               length(CAST(diagnostic_json AS BLOB)),length(CAST(result_json AS BLOB))
        FROM commitments WHERE id=?
        """,
        (expectation.commitment_id,),
    ).fetchone()
    if not commitment or tuple(commitment[:2]) != (REPAIR_AGGREGATE_VERSION, repair_seq):
        raise RepairError("target commitment does not point at the v18 repair event")
    for label, raw, size in (
        ("diagnostic", commitment["diagnostic_json"], commitment[4]),
        ("result", commitment["result_json"], commitment[5]),
    ):
        if int(size) >= MAX_PROJECTED_JSON_BYTES:
            raise RepairError(f"current {label} remains oversized")
        value = json.loads(str(raw))
        encoded_size = len(str(raw).encode("utf-8"))
        if encoded_size != int(size):
            raise RepairError(f"current {label} byte length disagrees")
        serialized = _canonical_json(value)
        if serialized != raw:
            raise RepairError(f"current {label} is not canonical JSON")
        if source_sha256 not in raw:
            raise RepairError(f"current {label} lost the forensic database hash")
        evidence_reference = (
            value.get("evidence", {}).get("compacted_history", {})
            if label == "diagnostic"
            else value.get("evidence_reference", {})
        )
        if (
            evidence_reference.get("forensic_db_sha256") != source_sha256
            or evidence_reference.get("commitment_id") != expectation.commitment_id
            or evidence_reference.get("spec_sha256") != expectation.spec_sha256
            or evidence_reference.get("original_events") != expected_history
            or evidence_reference.get("original_current")
            != {
                "diagnostic_bytes": expectation.diagnostic_bytes,
                "diagnostic_sha256": expectation.diagnostic_sha256,
                "result_bytes": expectation.result_bytes,
                "result_sha256": expectation.result_sha256,
            }
        ):
            raise RepairError(f"current {label} lost original event hashes or lengths")
    _assert_append_only(db, expectation.events[0].seq)
    return repair_seq, repair_event_id


def _fsync_file(path: Path) -> None:
    # Windows rejects FlushFileBuffers on a read-only handle.  The files passed
    # here are staged/output files owned by this tool, never the forensic input.
    descriptor = os.open(path, os.O_RDWR)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_no_clobber(
    final: Path,
    output: Path,
    expected_parent_identity: tuple[int, int],
) -> None:
    """Publish a staged file without replacing an existing directory entry."""
    if os.name == "nt":
        # Python exposes no portable directory-relative link/unlink API on
        # Windows. Recheck immediately before the no-clobber hard-link; the
        # kernel still guarantees that os.link cannot replace an existing path.
        current = output.parent.stat()
        if (current.st_dev, current.st_ino) != expected_parent_identity:
            raise RepairRefused("--output parent changed during repair")
        if output.exists() or output.is_symlink():
            raise RepairRefused("--output appeared during repair; refusing to overwrite it")
        published = False
        try:
            os.link(final, output)
            published = True
            _fsync_file(output)
        except Exception:
            if published:
                output.unlink(missing_ok=True)
            raise
        return

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    parent_fd = os.open(output.parent, flags)
    published = False
    try:
        current = os.fstat(parent_fd)
        if (current.st_dev, current.st_ino) != expected_parent_identity:
            raise RepairRefused("--output parent changed during repair")
        try:
            os.link(final, output.name, dst_dir_fd=parent_fd)
            published = True
            os.fsync(parent_fd)
        except Exception:
            if published:
                os.unlink(output.name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            raise
    finally:
        os.close(parent_fd)


def repair_database(
    source_arg: Path,
    output_arg: Path,
    *,
    expectation: RepairExpectation = DEFAULT_EXPECTATION,
) -> dict[str, Any]:
    source, output = _validate_paths(Path(source_arg), Path(output_arg))
    parent_stat = output.parent.stat()
    output_parent_identity = (parent_stat.st_dev, parent_stat.st_ino)
    source_sha256 = _file_sha256(source)
    source_size = source.stat().st_size
    with closing(_connect_readonly(source)) as source_db:
        source_snapshot = _verify_exact_source(source_db, expectation)

    with tempfile.TemporaryDirectory(prefix=f".{output.name}.repair-", dir=output.parent) as temp:
        temp_root = Path(temp)
        work = temp_root / "working.sqlite3"
        final = temp_root / "final.sqlite3"
        shutil.copy2(source, work)
        work_mode = (
            stat.S_IMODE(source.stat().st_mode) & 0o666
        ) | stat.S_IRUSR | stat.S_IWUSR
        os.chmod(work, work_mode)
        if _file_sha256(work) != source_sha256:
            raise RepairRefused("working copy does not match the hashed forensic source")

        with closing(_connect_work(work)) as work_db:
            source_again = _verify_exact_source(work_db, expectation)
            if source_again.unaffected != source_snapshot.unaffected:
                raise RepairRefused("working copy changed before repair")
            repair_seq, repair_event_id = _repair_working_copy(
                work_db, expectation, source_sha256, source_snapshot
            )
            verified_seq, verified_event_id = _verify_repaired(
                work_db, expectation, source_sha256, source_snapshot
            )
            if (repair_seq, repair_event_id) != (verified_seq, verified_event_id):
                raise RepairError("repair event identity changed before compaction")
            work_db.execute("VACUUM INTO ?", (str(final),))

        output_mode = (
            stat.S_IMODE(source.stat().st_mode) & 0o666
        ) | stat.S_IRUSR | stat.S_IWUSR
        os.chmod(final, output_mode)
        with closing(_connect_work(final)) as final_db:
            final_seq, final_event_id = _verify_repaired(
                final_db, expectation, source_sha256, source_snapshot
            )
            _verify_database_integrity(final_db, full=True)
        if (final_seq, final_event_id) != (repair_seq, repair_event_id):
            raise RepairError("VACUUM output changed repair event identity")
        output_sha256 = _file_sha256(final)
        output_size = final.stat().st_size
        _fsync_file(final)
        _publish_no_clobber(final, output, output_parent_identity)

    return {
        "compacted_event_versions": [COMPACT_FROM_VERSION, COMPACT_TO_VERSION],
        "forensic_source_bytes": source_size,
        "forensic_source_sha256": source_sha256,
        "output": str(output),
        "output_bytes": output_size,
        "output_sha256": output_sha256,
        "repair_event_id": repair_event_id,
        "repair_event_seq": repair_seq,
        "status": "repaired_offline_copy",
        "target_commitment_id": expectation.commitment_id,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a verified compact Core database from the exact immutable forensic "
            "copy audited for recursive commitment amplification. Never edits source/live."
        )
    )
    parser.add_argument("--source", type=Path, required=True, help="immutable checkpointed forensic DB copy")
    parser.add_argument("--output", type=Path, required=True, help="new staged DB path; must not exist")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = repair_database(args.source, args.output)
    except RepairError as exc:
        print(f"repair refused: {exc}", file=sys.stderr)
        return 2
    print(_canonical_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
