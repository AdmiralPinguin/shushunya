#!/usr/bin/env python3
"""Focused CAS/catalog/effect/HTTP smoke without the full EyeOfTerror tree."""
from __future__ import annotations

import gc
import json
import multiprocessing
import sqlite3
import sys
import tempfile
import threading
import types
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _install_administratum_import_stub():
    eye = types.ModuleType("EyeOfTerror")
    eye.__path__ = []
    administratum = types.ModuleType("EyeOfTerror.Administratum")
    administratum.__path__ = []
    parser = types.ModuleType("EyeOfTerror.Administratum.intent_parser")
    parser.administratum_payload_from_intent = lambda *_args, **_kwargs: ("task", {})
    parser.build_intent_detection_request = lambda *_args, **_kwargs: {}
    parser.normalize_intent = lambda value: value if isinstance(value, dict) else {}
    sys.modules.setdefault("EyeOfTerror", eye)
    sys.modules.setdefault("EyeOfTerror.Administratum", administratum)
    sys.modules.setdefault("EyeOfTerror.Administratum.intent_parser", parser)


_install_administratum_import_stub()

import archive_handler
import archive_httpio
import archive_ops
import artifact_store
from archive_handler import ArchiveHandler
from turn_protocol import turn_capability_manifest


def _process_import_worker(db_path, cas_root, start_event, result_queue):
    import artifact_store as worker_store

    worker_store.SQLITE_PATH = Path(db_path)
    worker_store.ARTIFACTS_ROOT = Path(cas_root)
    worker_store.ARTIFACT_MAX_BYTES = 1024 * 1024
    worker_store.ARTIFACT_TOTAL_QUOTA_BYTES = 16 * 1024 * 1024
    worker_store.ARTIFACT_STREAM_CHUNK_BYTES = 4096
    start_event.wait(10)
    try:
        item = worker_store.trusted_import_bytes(
            b"cross-process-content",
            filename="cross-process.bin",
            source="self-test",
            session_id="shushunya-main",
            audience_source="app",
            dedupe_key="cross-process-publication",
        )
        result_queue.put({"ok": True, "artifact_id": item["artifact_id"]})
    except Exception as exc:
        result_queue.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def _init_chat_tables(db_path: Path):
    with closing(sqlite3.connect(db_path)) as db:
        db.execute(
            """
            CREATE TABLE mobile_chat_sessions (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE mobile_chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                asset_id TEXT,
                artifact_id TEXT,
                client_request_id TEXT,
                source TEXT NOT NULL DEFAULT 'unknown',
                dedupe_key TEXT
            )
            """
        )
        db.commit()
        db.execute(
            "CREATE UNIQUE INDEX idx_mobile_chat_messages_dedupe ON mobile_chat_messages(dedupe_key) WHERE dedupe_key IS NOT NULL"
        )
        db.execute(
            """
            CREATE TABLE core_effect_receipts (
                effect_id TEXT PRIMARY KEY,
                request_sha256 TEXT NOT NULL,
                intent_json TEXT,
                state TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.commit()


class QuietArchiveHandler(ArchiveHandler):
    def log_message(self, _fmt, *_args):
        return


def _http_smoke(artifact, hidden, expected):
    old_keys = (
        archive_httpio.ARCHIVE_API_KEY,
        archive_httpio.ARCHIVE_CLIENT_API_KEY,
        archive_httpio.ARCHIVE_MOBILE_API_KEY,
        archive_httpio.SHUSHUNYA_CORE_ARCHIVE_KEY,
    )
    archive_httpio.ARCHIVE_API_KEY = "operator-self-test"
    archive_httpio.ARCHIVE_CLIENT_API_KEY = "artifact-self-test"
    archive_httpio.ARCHIVE_MOBILE_API_KEY = ""
    archive_httpio.SHUSHUNYA_CORE_ARCHIVE_KEY = "core-self-test-key-0123456789abcdef"
    assert archive_httpio._matches_secret("é", "not-the-same") is False
    assert archive_httpio._matches_secret("ключ-секрет", "ключ-секрет") is True
    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietArchiveHandler)
    server.daemon_threads = False
    server.block_on_close = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    path = f"/archive/client/artifacts/{artifact['artifact_id']}"
    headers = {"Authorization": "Bearer artifact-self-test"}
    operator_headers = {"Authorization": "Bearer operator-self-test"}
    try:
        internal_body = json.dumps(
            {
                "effect_id": "effect-http-auth-self-test",
                "payload": {
                    "artifact_id": artifact["artifact_id"],
                    "session_id": "shushunya-main",
                    "source": "app",
                    "client_request_id": "http-auth-self-test",
                },
            }
        ).encode("utf-8")
        internal_url = base + "/archive/internal/core/artifact-effect"
        for core_headers in (
            {"Content-Type": "application/json"},
            {"Content-Type": "application/json", "X-Shushunya-Core-Key": "wrong"},
        ):
            try:
                urlopen(Request(internal_url, data=internal_body, headers=core_headers), timeout=5)
            except HTTPError as exc:
                assert exc.code == 401
            else:
                raise AssertionError("internal Core adapter accepted a missing/wrong secret")
        with urlopen(
            Request(
                internal_url,
                data=internal_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Shushunya-Core-Key": "core-self-test-key-0123456789abcdef",
                },
            ),
            timeout=5,
        ) as response:
            assert response.status == 200
            assert json.loads(response.read().decode("utf-8"))["artifact_id"] == artifact["artifact_id"]

        client_context = types.SimpleNamespace(headers=headers)
        assert archive_httpio.authorized(client_context, allow_mobile=True)
        assert archive_httpio.authenticated_artifact_audience(
            client_context,
            {"client_source": "telegram"},
        ) == "app"
        operator_context = types.SimpleNamespace(headers=operator_headers)
        assert archive_httpio.authorized(operator_context, allow_mobile=True)
        assert archive_httpio.authenticated_artifact_audience(
            operator_context,
            {"client_source": "telegram"},
        ) == "telegram"

        with urlopen(Request(base + path, headers=headers), timeout=5) as response:
            metadata = json.loads(response.read().decode("utf-8"))["artifact"]
            assert metadata["sha256"] == artifact["sha256"]
            assert metadata["content_url"].endswith("/content")

        with urlopen(Request(base + path + "/content", headers=headers, method="HEAD"), timeout=5) as response:
            assert response.status == 200
            assert int(response.headers["Content-Length"]) == len(expected)
            etag = response.headers["ETag"]

        ranged_headers = {**headers, "Range": "bytes=2-5", "If-Range": etag}
        with urlopen(Request(base + path + "/content", headers=ranged_headers), timeout=5) as response:
            assert response.status == 206
            assert response.read() == expected[2:6]
            assert response.headers["Content-Range"] == f"bytes 2-5/{len(expected)}"

        try:
            urlopen(Request(base + path + "/content", headers={**headers, "Range": "bytes=999-1000"}), timeout=5)
        except HTTPError as exc:
            assert exc.code == 416
            assert exc.headers["Content-Range"] == f"bytes */{len(expected)}"
        else:
            raise AssertionError("unsatisfiable range was accepted")

        try:
            urlopen(Request(base + path + "/content"), timeout=5)
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("unauthenticated artifact download was accepted")

        hidden_path = f"/archive/client/artifacts/{hidden['artifact_id']}"
        try:
            urlopen(Request(base + hidden_path, headers=headers), timeout=5)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("app key accessed a telegram-only artifact")
        with urlopen(Request(base + hidden_path, headers=operator_headers), timeout=5) as response:
            assert json.loads(response.read().decode("utf-8"))["artifact"]["artifact_id"] == hidden["artifact_id"]

        history_path = "/archive/client/chat/messages?session_id=shushunya-main&limit=100"
        with urlopen(Request(base + history_path, headers=headers), timeout=5) as response:
            app_messages = json.loads(response.read().decode("utf-8"))["messages"]
        hidden_app = next(item for item in app_messages if item["content"] == "Файл приложен: hidden.txt")
        assert hidden_app["artifact_id"] is None and hidden_app["artifact"] is None
        with urlopen(Request(base + history_path, headers=operator_headers), timeout=5) as response:
            operator_messages = json.loads(response.read().decode("utf-8"))["messages"]
        hidden_operator = next(item for item in operator_messages if item["content"] == "Файл приложен: hidden.txt")
        assert hidden_operator["artifact_id"] == hidden["artifact_id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        (
            archive_httpio.ARCHIVE_API_KEY,
            archive_httpio.ARCHIVE_CLIENT_API_KEY,
            archive_httpio.ARCHIVE_MOBILE_API_KEY,
            archive_httpio.SHUSHUNYA_CORE_ARCHIVE_KEY,
        ) = old_keys


def main():
    with tempfile.TemporaryDirectory(prefix="archive-artifact-self-test-") as temporary:
        root = Path(temporary)
        db_path = root / "archive.sqlite3"
        cas_root = root / "artifacts"
        source_root = root / "producer"
        source_root.mkdir(mode=0o700)
        sample = b"arbitrary\x00file\nbytes"
        source_file = source_root / "report.bin"
        source_file.write_bytes(sample)

        artifact_store.SQLITE_PATH = db_path
        artifact_store.ARTIFACTS_ROOT = cas_root
        artifact_store.ARTIFACT_MAX_BYTES = 1024
        artifact_store.ARTIFACT_TOTAL_QUOTA_BYTES = 4096
        artifact_store.ARTIFACT_STREAM_CHUNK_BYTES = 4
        archive_ops.SQLITE_PATH = db_path
        artifact_store.init_artifact_storage()
        _init_chat_tables(db_path)

        fsynced_files = []
        fsynced_directories = []
        original_fsync_file = artifact_store._fsync_regular_file
        original_fsync_directory = artifact_store._fsync_directory

        def track_fsync_file(path):
            fsynced_files.append(Path(path))
            return original_fsync_file(path)

        def track_fsync_directory(path):
            fsynced_directories.append(Path(path))
            return original_fsync_directory(path)

        artifact_store._fsync_regular_file = track_fsync_file
        artifact_store._fsync_directory = track_fsync_directory
        try:
            artifact = artifact_store.trusted_import_path(
                source_file,
                allowed_roots=[source_root],
                filename="evidence.bin",
                media_type="application/octet-stream",
                source="iskandar",
                session_id="shushunya-main",
                audience_source="app",
                mission_id="mission-self-test",
                dedupe_key="mission-self-test:evidence",
            )
        finally:
            artifact_store._fsync_regular_file = original_fsync_file
            artifact_store._fsync_directory = original_fsync_directory
        assert any(path.name == artifact["sha256"] for path in fsynced_files)
        assert cas_root in fsynced_directories
        duplicate = artifact_store.trusted_import_path(
            source_file,
            allowed_roots=[source_root],
            filename="evidence.bin",
            media_type="application/octet-stream",
            source="iskandar",
            session_id="shushunya-main",
            audience_source="app",
            mission_id="mission-self-test",
            dedupe_key="mission-self-test:evidence",
        )
        assert duplicate["artifact_id"] == artifact["artifact_id"]
        assert artifact_store.artifact_store_stats()["blobs"] == 1

        process_context = multiprocessing.get_context("spawn")
        process_start = process_context.Event()
        process_results = process_context.Queue()
        processes = [
            process_context.Process(
                target=_process_import_worker,
                args=(str(db_path), str(cas_root), process_start, process_results),
            )
            for _index in range(2)
        ]
        for process in processes:
            process.start()
        process_start.set()
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
        imported = [process_results.get(timeout=5) for _index in processes]
        assert all(item.get("ok") for item in imported), imported
        assert len({item["artifact_id"] for item in imported}) == 1
        process_results.close()
        process_results.join_thread()
        for process in processes:
            process.close()

        hidden = artifact_store.trusted_import_bytes(
            b"telegram-only",
            filename="hidden.txt",
            media_type="text/plain",
            source="operator",
            session_id="shushunya-main",
            audience_source="telegram",
            dedupe_key="telegram-only",
        )
        alternate = artifact_store.trusted_import_bytes(
            b"alternate-app-artifact",
            filename="alternate.txt",
            media_type="text/plain",
            source="operator",
            session_id="shushunya-main",
            audience_source="app",
            dedupe_key="alternate-app-artifact",
        )
        multibyte_name = ("😀" * 180) + "\u202ecod.exe.pdf"
        sanitized_name = artifact_store.trusted_import_bytes(
            b"filename-hygiene",
            filename=multibyte_name,
            media_type="application/pdf",
            source="operator",
            session_id="shushunya-main",
            audience_source="app",
            dedupe_key="multibyte-filename",
        )["filename"]
        assert len(sanitized_name.encode("utf-8")) <= 240
        assert "\u202e" not in sanitized_name and sanitized_name.endswith(".pdf")
        old_relevant = artifact_store.trusted_import_bytes(
            b"old relevant report",
            filename="ancient-iskandar-audit.pdf",
            media_type="application/pdf",
            source="iskandar",
            session_id="shushunya-main",
            audience_source="app",
            mission_id="ancient-iskandar-audit",
            dedupe_key="ancient-iskandar-audit",
        )
        for index in range(12):
            artifact_store.trusted_import_bytes(
                f"noise-{index}".encode("ascii"),
                filename=f"noise-{index}.txt",
                media_type="text/plain",
                source="operator",
                session_id="shushunya-main",
                audience_source="app",
                dedupe_key=f"recent-noise-{index}",
            )
        catalog = artifact_store.recent_artifact_catalog("shushunya-main", audience_source="app", limit=10)
        assert len(catalog) == 10
        assert hidden["artifact_id"] not in {item["artifact_id"] for item in catalog}
        assert old_relevant["artifact_id"] not in {item["artifact_id"] for item in catalog}
        relevant_catalog = artifact_store.artifact_catalog_for_query(
            "shushunya-main",
            audience_source="app",
            query="пришли ancient iskandar audit",
            limit=10,
        )
        assert old_relevant["artifact_id"] in {item["artifact_id"] for item in relevant_catalog}
        exact_id_catalog = artifact_store.artifact_catalog_for_query(
            "shushunya-main",
            audience_source="app",
            query=f"send {old_relevant['artifact_id']}",
            limit=10,
        )
        assert exact_id_catalog[0]["artifact_id"] == old_relevant["artifact_id"]
        capability = next(
            item for item in turn_capability_manifest(artifacts=catalog)["capabilities"]
            if item["action"] == "deliver_artifact"
        )
        assert capability["available"] is True and len(capability["artifacts"]) <= 12

        old_max = artifact_store.ARTIFACT_MAX_BYTES
        artifact_store.ARTIFACT_MAX_BYTES = 3
        try:
            artifact_store.trusted_import_bytes(
                b"four",
                filename="too-large.bin",
                source="operator",
                session_id="shushunya-main",
            )
        except artifact_store.ArtifactTooLarge as exc:
            assert "single-file limit" in str(exc)
        else:
            raise AssertionError("single artifact limit was not enforced")
        finally:
            artifact_store.ARTIFACT_MAX_BYTES = old_max

        old_quota = artifact_store.ARTIFACT_TOTAL_QUOTA_BYTES
        artifact_store.ARTIFACT_TOTAL_QUOTA_BYTES = artifact_store.artifact_store_stats()["used_bytes"]
        try:
            artifact_store.trusted_import_bytes(
                b"new-unique-content",
                filename="quota.bin",
                source="operator",
                session_id="shushunya-main",
            )
        except artifact_store.ArtifactQuotaExceeded as exc:
            assert "quota" in str(exc)
        else:
            raise AssertionError("total artifact quota was not enforced")
        finally:
            artifact_store.ARTIFACT_TOTAL_QUOTA_BYTES = old_quota

        effect_payload = {
            "artifact_id": artifact["artifact_id"],
            "caption": "MODEL MUST NOT CONTROL THIS CAPTION",
            "session_id": "shushunya-main",
            "source": "app",
            "client_request_id": "android-self-test",
        }
        first = archive_ops.run_core_artifact_effect("effect-artifact-self-test", effect_payload)
        second = archive_ops.run_core_artifact_effect(
            "effect-artifact-self-test",
            {**effect_payload, "caption": "a different model caption on retry"},
        )
        assert first["ok"] is True and second["delegate_ref"] == first["delegate_ref"]
        assert first["caption"] == "Файл приложен: evidence.bin"
        history = archive_ops.chat_history("shushunya-main", limit=100, audience_source="app")
        delivered = [item for item in history if item.get("artifact_id") == artifact["artifact_id"]]
        assert len(delivered) == 1
        assert delivered[0]["content"] == first["caption"]
        assert delivered[0]["artifact"]["content_url"].endswith("/content")
        assert delivered[0]["client_request_id"] == "android-self-test"

        hidden_delivery = archive_ops.run_core_artifact_effect(
            "effect-artifact-hidden",
            {
                "artifact_id": hidden["artifact_id"],
                "caption": "model leak",
                "session_id": "shushunya-main",
                "source": "telegram",
                "client_request_id": "telegram-self-test",
            },
        )
        assert hidden_delivery["ok"] is True and hidden_delivery["caption"] == "Файл приложен: hidden.txt"
        app_scoped_history = archive_ops.chat_history(
            "shushunya-main",
            limit=100,
            audience_source="app",
        )
        hidden_for_app = next(item for item in app_scoped_history if item["content"] == hidden_delivery["caption"])
        assert hidden_for_app["artifact_id"] is None and hidden_for_app["artifact"] is None
        operator_history = archive_ops.chat_history(
            "shushunya-main",
            limit=100,
            audience_source="*",
        )
        hidden_for_operator = next(item for item in operator_history if item["content"] == hidden_delivery["caption"])
        assert hidden_for_operator["artifact_id"] == hidden["artifact_id"]

        barrier = threading.Barrier(2)
        collision_results = []
        collision_lock = threading.Lock()

        def collide(candidate):
            barrier.wait(timeout=5)
            try:
                value = archive_ops.run_core_artifact_effect(
                    "effect-artifact-collision",
                    {
                        "artifact_id": candidate["artifact_id"],
                        "caption": "collision",
                        "session_id": "shushunya-main",
                        "source": "app",
                        "client_request_id": "android-collision",
                    },
                )
            except Exception as exc:  # the losing mismatched request must be refused
                value = exc
            with collision_lock:
                collision_results.append(value)

        threads = [
            threading.Thread(target=collide, args=(artifact,)),
            threading.Thread(target=collide, args=(alternate,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert len(collision_results) == 2
        successes = [item for item in collision_results if isinstance(item, dict) and item.get("ok")]
        conflicts = [item for item in collision_results if isinstance(item, ValueError)]
        assert len(successes) == 1 and len(conflicts) == 1
        with closing(sqlite3.connect(db_path)) as db:
            rows = db.execute(
                "SELECT artifact_id FROM mobile_chat_messages WHERE dedupe_key=?",
                ("core-effect:effect-artifact-collision:artifact",),
            ).fetchall()
        assert rows == [(successes[0]["artifact_id"],)]

        wrong_source = archive_ops.run_core_artifact_effect(
            "effect-artifact-wrong-source",
            {**effect_payload, "source": "telegram"},
        )
        assert wrong_source["ok"] is False and wrong_source["code"] == "artifact_not_available"

        corrupt = artifact_store.trusted_import_bytes(
            b"integrity-check",
            filename="corrupt.bin",
            source="operator",
            session_id="shushunya-main",
            audience_source="app",
            dedupe_key="corrupt-self-test",
        )
        corrupt_path = cas_root / artifact_store._blob_relpath(corrupt["sha256"])
        corrupt_path.write_bytes(b"short")
        integrity_failure = archive_ops.run_core_artifact_effect(
            "effect-artifact-corrupt",
            {
                "artifact_id": corrupt["artifact_id"],
                "session_id": "shushunya-main",
                "source": "app",
            },
        )
        assert integrity_failure["ok"] is False and integrity_failure["retryable"] is True
        with closing(sqlite3.connect(db_path)) as db:
            assert db.execute(
                "SELECT 1 FROM mobile_chat_messages WHERE artifact_id=?",
                (corrupt["artifact_id"],),
            ).fetchone() is None

        cleanup = artifact_store.cleanup_unreferenced_artifacts(older_than_days=0, dry_run=True)
        assert artifact["artifact_id"] not in {item["artifact_id"] for item in cleanup["artifacts"]}

        orphan_sha = "f" * 64
        orphan_path = cas_root / "blobs" / "sha256" / orphan_sha[:2] / orphan_sha
        orphan_path.parent.mkdir(parents=True, exist_ok=True)
        orphan_path.write_bytes(b"abandoned-transaction")
        cleanup = artifact_store.cleanup_unreferenced_artifacts(older_than_days=36_500, dry_run=True)
        assert orphan_sha in cleanup["orphan_blobs"] and orphan_path.exists()
        cleanup = artifact_store.cleanup_unreferenced_artifacts(older_than_days=36_500, dry_run=False)
        assert cleanup["orphan_blob_count"] == 1 and not orphan_path.exists()
        _http_smoke(artifact, hidden, sample)
        gc.collect()

    print("[ok] artifact CAS/catalog + scoped Core delivery + authenticated Range HTTP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
