from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .config import DB_PATH, LOGS_DIR, ensure_dirs
from .schemas import ArtifactRecord, AssetDownloadRecord, JobRecord, JobStatus, utc_now


SCHEMA_VERSION = 1


class ForgeStore:
    def __init__(self, db_path: Path = DB_PATH):
        ensure_dirs()
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    spec_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    progress REAL NOT NULL,
                    logs_json TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    metadata_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_downloads (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    sha256 TEXT,
                    license_note TEXT,
                    target_dir TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT
                )
                """
            )
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def schema_version(self) -> int:
        with self._lock, self._connect() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    def create_job(self, record: JobRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.spec.model_dump_json(),
                    record.status.value,
                    record.created_at,
                    record.updated_at,
                    record.progress,
                    json.dumps(record.logs),
                    json.dumps(record.artifacts),
                    record.error,
                ),
            )

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return JobRecord(
            id=row["id"],
            spec=json.loads(row["spec_json"]),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            progress=row["progress"],
            logs=json.loads(row["logs_json"]),
            artifacts=json.loads(row["artifacts_json"]),
            error=row["error"],
        )

    def list_jobs(self, status: str | None = None, limit: int = 100) -> list[JobRecord]:
        query = "SELECT * FROM jobs"
        params: list[object] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            JobRecord(
                id=row["id"],
                spec=json.loads(row["spec_json"]),
                status=row["status"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                progress=row["progress"],
                logs=json.loads(row["logs_json"]),
                artifacts=json.loads(row["artifacts_json"]),
                error=row["error"],
            )
            for row in rows
        ]

    def update_job(self, job_id: str, **fields: Any) -> JobRecord:
        record = self.get_job(job_id)
        if record is None:
            raise KeyError(job_id)
        data = record.model_dump()
        data.update(fields)
        data["updated_at"] = utc_now()
        updated = JobRecord(**data)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET spec_json=?, status=?, updated_at=?, progress=?, logs_json=?, artifacts_json=?, error=?
                WHERE id=?
                """,
                (
                    updated.spec.model_dump_json(),
                    updated.status.value,
                    updated.updated_at,
                    updated.progress,
                    json.dumps(updated.logs),
                    json.dumps(updated.artifacts),
                    updated.error,
                    job_id,
                ),
            )
        return updated

    def append_log(self, job_id: str, message: str) -> None:
        record = self.get_job(job_id)
        if record is None:
            return
        logs = [*record.logs, f"{utc_now()} {message}"]
        self.update_job(job_id, logs=logs)
        event = {
            "ts": utc_now(),
            "job_id": job_id,
            "status": record.status.value,
            "message": message,
        }
        with self._lock:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with (LOGS_DIR / "jobs.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def add_artifact(self, artifact: ArtifactRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact.id,
                    artifact.job_id,
                    artifact.kind,
                    artifact.path,
                    artifact.metadata_path,
                    artifact.created_at,
                    json.dumps(artifact.metadata),
                ),
            )
        job = self.get_job(artifact.job_id)
        if job:
            self.update_job(artifact.job_id, artifacts=[*job.artifacts, artifact.id])

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if row is None:
            return None
        return ArtifactRecord(
            id=row["id"],
            job_id=row["job_id"],
            kind=row["kind"],
            path=row["path"],
            metadata_path=row["metadata_path"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    def list_gallery(self, limit: int = 100) -> list[ArtifactRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            ArtifactRecord(
                id=row["id"],
                job_id=row["job_id"],
                kind=row["kind"],
                path=row["path"],
                metadata_path=row["metadata_path"],
                created_at=row["created_at"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        ]

    def create_asset_download(self, record: AssetDownloadRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO asset_downloads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.name,
                    record.asset_type,
                    record.source_url,
                    record.sha256,
                    record.license_note,
                    record.target_dir,
                    record.status,
                    record.created_at,
                    record.updated_at,
                    record.error,
                ),
            )

    def update_asset_download(self, record_id: str, **fields: object) -> AssetDownloadRecord:
        record = self.get_asset_download(record_id)
        if record is None:
            raise KeyError(record_id)
        data = record.model_dump()
        data.update(fields)
        data["updated_at"] = utc_now()
        updated = AssetDownloadRecord(**data)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE asset_downloads
                SET sha256=?, license_note=?, target_dir=?, status=?, updated_at=?, error=?
                WHERE id=?
                """,
                (
                    updated.sha256,
                    updated.license_note,
                    updated.target_dir,
                    updated.status,
                    updated.updated_at,
                    updated.error,
                    record_id,
                ),
            )
        return updated

    def get_asset_download(self, record_id: str) -> AssetDownloadRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM asset_downloads WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            return None
        return AssetDownloadRecord(
            id=row["id"],
            name=row["name"],
            asset_type=row["asset_type"],
            source_url=row["source_url"],
            sha256=row["sha256"],
            license_note=row["license_note"],
            target_dir=row["target_dir"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            error=row["error"],
        )

    def list_asset_downloads(self, limit: int = 100) -> list[AssetDownloadRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM asset_downloads ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            AssetDownloadRecord(
                id=row["id"],
                name=row["name"],
                asset_type=row["asset_type"],
                source_url=row["source_url"],
                sha256=row["sha256"],
                license_note=row["license_note"],
                target_dir=row["target_dir"],
                status=row["status"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                error=row["error"],
            )
            for row in rows
        ]
