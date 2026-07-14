from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from ShushunyaCore.ledger import MIGRATION_1, canonical_json, sha256_json
from ShushunyaCore.repair_recursive_commitment import (
    EventExpectation,
    RepairExpectation,
    RepairError,
    RepairRefused,
    repair_database,
)


class RecursiveCommitmentRepairTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "forensic.sqlite3"
        self.output = self.root / "repaired.sqlite3"
        self.expectation = self._create_source()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _create_source(self) -> RepairExpectation:
        commitment_id = "commitment-aedceca910194789ae0c7d4137a68015"
        delegate_ref = "core-aedceca910194789ae0c"
        goal = "synthetic Galaga goal"
        honest_status = "lineage conflict remains externally repairable"
        spec = {"message": "build the game", "task_id": delegate_ref}
        diagnostic = {
            "code": "task_memory_lineage_repair_required",
            "evidence": {"previous_attempt": {"task_id": delegate_ref}},
        }
        result = {
            "previous_attempt": {"task_id": delegate_ref, "status": "failed"},
            "recovery_generation": 0,
        }
        encoded_diagnostic = canonical_json(diagnostic)
        encoded_result = canonical_json(result)
        expectations: list[EventExpectation] = []

        with closing(sqlite3.connect(self.source)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(MIGRATION_1)
            for version in range(1, 18):
                if version == 1:
                    kind = "commitment.opened"
                elif version in {2, 3}:
                    kind = "commitment.retry_wait"
                elif version == 4:
                    kind = "commitment.failed"
                else:
                    kind = "commitment.waiting_external"
                payload = {
                    "delegate_ref": delegate_ref,
                    "from": "failed" if version == 5 else "waiting_external",
                    "nested": {"synthetic": "x" * (version * 31)},
                    "to": "waiting_external",
                    "version": version,
                }
                encoded = canonical_json(payload)
                digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                event_id = f"evt-test-{version:02d}"
                cursor = db.execute(
                    """
                    INSERT INTO events(
                        event_id,aggregate_type,aggregate_id,aggregate_version,kind,
                        occurred_at,actor,correlation_id,causation_event_id,
                        payload_json,payload_sha256
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event_id,
                        "commitment",
                        commitment_id,
                        version,
                        kind,
                        f"2026-07-14T00:{version:02d}:00+00:00",
                        "test",
                        commitment_id,
                        None,
                        encoded,
                        digest,
                    ),
                )
                expectations.append(
                    EventExpectation(
                        version=version,
                        seq=int(cursor.lastrowid),
                        event_id=event_id,
                        kind=kind,
                        payload_sha256=digest,
                        payload_bytes=len(encoded.encode("utf-8")),
                    )
                )

            other_payload = canonical_json({"other": True})
            other_event = db.execute(
                """
                INSERT INTO events(
                    event_id,aggregate_type,aggregate_id,aggregate_version,kind,
                    occurred_at,actor,correlation_id,causation_event_id,
                    payload_json,payload_sha256
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "evt-other",
                    "commitment",
                    "commitment-other",
                    1,
                    "commitment.opened",
                    "2026-07-14T01:00:00+00:00",
                    "test",
                    "commitment-other",
                    None,
                    other_payload,
                    hashlib.sha256(other_payload.encode("utf-8")).hexdigest(),
                ),
            )
            now = "2026-07-14T02:00:00+00:00"
            db.execute(
                """
                INSERT INTO commitments(
                    id,kind,owner,goal,spec_json,spec_sha256,state,version,priority,
                    due_at,next_attempt_at,attempt_count,max_attempts,delegate_kind,
                    delegate_ref,honest_status,diagnostic_json,result_json,last_event_seq,
                    created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    commitment_id,
                    "abaddon_mission",
                    "shushunya",
                    goal,
                    canonical_json(spec),
                    sha256_json(spec),
                    "waiting_external",
                    17,
                    50,
                    None,
                    None,
                    3,
                    3,
                    "abaddon",
                    delegate_ref,
                    honest_status,
                    encoded_diagnostic,
                    encoded_result,
                    expectations[-1].seq,
                    now,
                    now,
                ),
            )
            other_spec = {"other": True}
            db.execute(
                """
                INSERT INTO commitments(
                    id,kind,owner,goal,spec_json,spec_sha256,state,version,priority,
                    due_at,next_attempt_at,attempt_count,max_attempts,delegate_kind,
                    delegate_ref,honest_status,diagnostic_json,result_json,last_event_seq,
                    created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "commitment-other",
                    "task",
                    "shushunya",
                    "untouched",
                    canonical_json(other_spec),
                    sha256_json(other_spec),
                    "succeeded",
                    1,
                    0,
                    None,
                    None,
                    0,
                    3,
                    None,
                    None,
                    "untouched",
                    None,
                    canonical_json({"proof": "kept"}),
                    int(other_event.lastrowid),
                    now,
                    now,
                ),
            )
            effect_payload = canonical_json({"task_id": delegate_ref})
            effect_hash = hashlib.sha256(effect_payload.encode("utf-8")).hexdigest()
            db.execute(
                """
                INSERT INTO effects(
                    id,turn_id,commitment_id,kind,destination,payload_json,
                    payload_sha256,state,result_json,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "effect-test",
                    "turn-test",
                    commitment_id,
                    "request_warmaster_mission",
                    "abaddon",
                    effect_payload,
                    effect_hash,
                    "dead_letter",
                    canonical_json({"status": "failed"}),
                    now,
                    now,
                ),
            )
            db.execute(
                """
                INSERT INTO outbox(
                    message_id,event_seq,destination,operation,idempotency_key,
                    payload_json,payload_sha256,state,attempt_count,max_attempts,
                    created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "effect-test",
                    int(other_event.lastrowid),
                    "abaddon",
                    "request_warmaster_mission",
                    "effect-test",
                    effect_payload,
                    effect_hash,
                    "dead_letter",
                    3,
                    3,
                    now,
                ),
            )
            db.commit()

        return RepairExpectation(
            commitment_id=commitment_id,
            delegate_ref=delegate_ref,
            goal=goal,
            honest_status=honest_status,
            spec_sha256=sha256_json(spec),
            state="waiting_external",
            commitment_version=17,
            last_event_seq=expectations[-1].seq,
            diagnostic_bytes=len(encoded_diagnostic.encode("utf-8")),
            diagnostic_sha256=hashlib.sha256(encoded_diagnostic.encode("utf-8")).hexdigest(),
            result_bytes=len(encoded_result.encode("utf-8")),
            result_sha256=hashlib.sha256(encoded_result.encode("utf-8")).hexdigest(),
            attempt_count=3,
            max_attempts=3,
            events=tuple(expectations),
            database_event_count=18,
            database_max_event_seq=18,
        )

    @staticmethod
    def _sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_creates_verified_compact_copy_without_touching_forensic_source(self) -> None:
        source_sha = self._sha(self.source)
        with closing(sqlite3.connect(self.source)) as source_db:
            preserved = {
                int(row[0]): str(row[1])
                for row in source_db.execute(
                    "SELECT aggregate_version,payload_json FROM events "
                    "WHERE aggregate_id=? AND aggregate_version<=4",
                    (self.expectation.commitment_id,),
                )
            }
            untouched = tuple(
                source_db.execute(
                    "SELECT * FROM commitments WHERE id='commitment-other'"
                ).fetchone()
            )

        report = repair_database(self.source, self.output, expectation=self.expectation)

        self.assertEqual(self._sha(self.source), source_sha)
        self.assertEqual(report["forensic_source_sha256"], source_sha)
        self.assertEqual(report["repair_event_seq"], 19)
        self.assertTrue(self.output.is_file())
        self.assertEqual(report["output_bytes"], self.output.stat().st_size)

        with closing(sqlite3.connect(self.output)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(list(db.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(db.execute("PRAGMA freelist_count").fetchone()[0], 0)
            versions = [
                int(row[0])
                for row in db.execute(
                    "SELECT aggregate_version FROM events WHERE aggregate_id=? "
                    "ORDER BY aggregate_version",
                    (self.expectation.commitment_id,),
                )
            ]
            self.assertEqual(versions, list(range(1, 19)))
            for version, payload in preserved.items():
                current = db.execute(
                    "SELECT payload_json FROM events WHERE aggregate_id=? AND aggregate_version=?",
                    (self.expectation.commitment_id, version),
                ).fetchone()[0]
                self.assertEqual(current, payload)
            compacted = json.loads(
                db.execute(
                    "SELECT payload_json FROM events WHERE aggregate_id=? AND aggregate_version=5",
                    (self.expectation.commitment_id,),
                ).fetchone()[0]
            )
            proof = compacted["compaction"]["original_event"]
            self.assertEqual(proof["payload_sha256"], self.expectation.events[4].payload_sha256)
            self.assertEqual(proof["payload_bytes"], self.expectation.events[4].payload_bytes)
            self.assertEqual(proof["forensic_db_sha256"], source_sha)
            row = db.execute(
                "SELECT version,last_event_seq,length(CAST(diagnostic_json AS BLOB)),"
                "length(CAST(result_json AS BLOB)),diagnostic_json,result_json "
                "FROM commitments WHERE id=?",
                (self.expectation.commitment_id,),
            ).fetchone()
            self.assertEqual((row[0], row[1]), (18, 19))
            self.assertLess(row[2], 64 * 1024)
            self.assertLess(row[3], 64 * 1024)
            self.assertIn(source_sha, row[4])
            self.assertIn(source_sha, row[5])
            self.assertEqual(
                tuple(db.execute("SELECT * FROM commitments WHERE id='commitment-other'").fetchone()),
                untouched,
            )
            with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only"):
                db.execute("UPDATE events SET actor=actor WHERE seq=1")

    def test_refuses_in_place_existing_output_and_hot_wal(self) -> None:
        with self.assertRaisesRegex(RepairRefused, "in-place"):
            repair_database(self.source, self.source, expectation=self.expectation)

        existing = self.root / "existing.sqlite3"
        existing.write_bytes(b"do not overwrite")
        with self.assertRaisesRegex(RepairRefused, "must not already exist"):
            repair_database(self.source, existing, expectation=self.expectation)
        self.assertEqual(existing.read_bytes(), b"do not overwrite")

        wal = Path(str(self.source) + "-wal")
        wal.write_bytes(b"hot")
        with self.assertRaisesRegex(RepairRefused, "non-empty wal"):
            repair_database(self.source, self.output, expectation=self.expectation)
        self.assertFalse(self.output.exists())

    def test_refuses_same_length_payload_tampering_before_compaction(self) -> None:
        with closing(sqlite3.connect(self.source)) as db:
            trigger_sql = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='events_no_update'"
            ).fetchone()[0]
            db.execute("DROP TRIGGER events_no_update")
            raw = db.execute(
                "SELECT payload_json FROM events WHERE aggregate_id=? AND aggregate_version=5",
                (self.expectation.commitment_id,),
            ).fetchone()[0]
            tampered = raw.replace("x", "y", 1)
            self.assertEqual(len(tampered.encode("utf-8")), len(raw.encode("utf-8")))
            db.execute(
                "UPDATE events SET payload_json=? WHERE aggregate_id=? AND aggregate_version=5",
                (tampered, self.expectation.commitment_id),
            )
            db.execute(trigger_sql)
            db.commit()

        with self.assertRaisesRegex(RepairError, "payload bytes differ"):
            repair_database(self.source, self.output, expectation=self.expectation)
        self.assertFalse(self.output.exists())

    def test_refuses_conditional_append_only_trigger(self) -> None:
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("DROP TRIGGER events_no_delete")
            db.execute(
                """
                CREATE TRIGGER events_no_delete BEFORE DELETE ON events
                WHEN OLD.seq=1 BEGIN
                    SELECT RAISE(ABORT, 'events are append-only');
                END
                """
            )
            db.commit()

        with self.assertRaisesRegex(RepairError, "not the audited append-only guard"):
            repair_database(self.source, self.output, expectation=self.expectation)
        self.assertFalse(self.output.exists())

    def test_refuses_any_extra_event_trigger(self) -> None:
        with closing(sqlite3.connect(self.source)) as db:
            db.execute(
                """
                CREATE TRIGGER events_payload_side_effect AFTER UPDATE OF payload_json ON "EvEnTs"
                BEGIN
                    UPDATE events SET kind='tampered.kind' WHERE seq=NEW.seq;
                END
                """
            )
            db.commit()

        with self.assertRaisesRegex(RepairError, "append-only event triggers"):
            repair_database(self.source, self.output, expectation=self.expectation)
        self.assertFalse(self.output.exists())

    def test_accepts_read_only_forensic_copy_but_keeps_it_read_only(self) -> None:
        self.source.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        try:
            source_sha = self._sha(self.source)
            repair_database(self.source, self.output, expectation=self.expectation)
            self.assertEqual(self._sha(self.source), source_sha)
            self.assertFalse(self.source.stat().st_mode & stat.S_IWUSR)
            self.assertTrue(self.output.stat().st_mode & stat.S_IWUSR)
        finally:
            self.source.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_refuses_live_database_derived_from_runtime_directory(self) -> None:
        runtime = self.root / "custom-core-runtime"
        runtime.mkdir()
        live_source = runtime / "store.sqlite3"
        self.source.replace(live_source)
        self.source = live_source

        with mock.patch.dict(
            os.environ,
            {
                "SHUSHUNYA_CORE_RUNTIME_DIR": str(runtime),
                "SHUSHUNYA_CORE_DB": "",
            },
        ):
            with self.assertRaisesRegex(RepairRefused, "live Core database"):
                repair_database(self.source, self.output, expectation=self.expectation)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
