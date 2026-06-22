from __future__ import annotations

import json
import queue
import random
import threading
import uuid
from pathlib import Path

import psutil
from PIL import Image

from . import config
from .downloader import DownloadError, download_asset
from .engines.diffusers_adapter import DiffusersEngine
from .registries import ENGINE_MODELS, write_json
from .schemas import ArtifactRecord, JobRecord, JobSpec, JobStatus, JobType, utc_now
from .storage import ForgeStore


class ForgeQueue:
    def __init__(self, store: ForgeStore):
        self.store = store
        self._queue: queue.Queue[str] = queue.Queue()
        self._cancel = set[str]()
        self._engines: dict[str, DiffusersEngine] = {}
        self._worker = threading.Thread(target=self._run, name="forge-worker", daemon=True)
        self._worker.start()

    def submit(self, spec: JobSpec) -> JobRecord:
        job_id = uuid.uuid4().hex
        if spec.seed is None and spec.type == JobType.txt2img:
            spec.seed = random.randint(0, 2**32 - 1)
        record = JobRecord(id=job_id, spec=spec, status=JobStatus.queued)
        self.store.create_job(record)
        self._queue.put(job_id)
        return record

    def validate(self, spec: JobSpec) -> dict[str, object]:
        estimate = resource_estimate(spec)
        if spec.type == JobType.txt2img:
            engine_name = spec.engine or "sdxl"
            if engine_name not in ENGINE_MODELS:
                raise RuntimeError(f"unknown engine: {engine_name}")
            if not spec.prompt or not spec.prompt.strip():
                raise RuntimeError("txt2img requires prompt")
        elif spec.type != JobType.asset_download:
            raise RuntimeError(f"job type is not supported by any registered backend yet: {spec.type.value}")
        return {"valid": True, "resource_estimate": estimate}

    def cancel(self, job_id: str) -> JobRecord:
        record = self.store.get_job(job_id)
        if record is None:
            raise KeyError(job_id)
        if record.status in {JobStatus.succeeded, JobStatus.failed, JobStatus.canceled}:
            return record
        self._cancel.add(job_id)
        return self.store.update_job(job_id, status=JobStatus.canceled, progress=record.progress)

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                if job_id in self._cancel:
                    continue
                self._execute(job_id)
            finally:
                self._queue.task_done()

    def _progress(self, job_id: str, value: float, message: str) -> None:
        if job_id in self._cancel:
            raise RuntimeError("job canceled")
        record = self.store.get_job(job_id)
        if not record:
            return
        self.store.update_job(job_id, progress=value)
        self.store.append_log(job_id, message)

    def _resource_check(self, spec: JobSpec) -> None:
        mem = psutil.virtual_memory()
        if mem.available < 4 * 1024**3:
            raise RuntimeError("not enough available RAM for generation queue")
        if spec.width * spec.height * spec.batch_size > 1536 * 1536:
            raise RuntimeError("job exceeds conservative pixel budget")

    def _engine(self, name: str) -> DiffusersEngine:
        if name not in self._engines:
            self._engines[name] = DiffusersEngine(name)
        return self._engines[name]

    def _execute(self, job_id: str) -> None:
        record = self.store.get_job(job_id)
        if record is None or record.status == JobStatus.canceled:
            return
        self.store.update_job(job_id, status=JobStatus.running, progress=0.0)
        try:
            spec = record.spec
            if spec.type == JobType.asset_download:
                self._execute_asset_download(job_id, spec)
            elif spec.type == JobType.txt2img:
                self._execute_txt2img(job_id, spec)
            else:
                raise RuntimeError(f"job type is not supported by any registered backend yet: {spec.type.value}")
            self.store.update_job(job_id, status=JobStatus.succeeded, progress=1.0)
            self.store.append_log(job_id, "job succeeded")
        except Exception as exc:
            status = JobStatus.canceled if str(exc) == "job canceled" else JobStatus.failed
            self.store.update_job(job_id, status=status, error=str(exc))
            self.store.append_log(job_id, f"job {status.value}: {exc}")

    def _execute_txt2img(self, job_id: str, spec: JobSpec) -> None:
        if not spec.prompt or not spec.prompt.strip():
            raise RuntimeError("txt2img requires prompt")
        engine_name = spec.engine or "sdxl"
        if engine_name not in ENGINE_MODELS:
            raise RuntimeError(f"unknown engine: {engine_name}")
        self._resource_check(spec)
        images = self._engine(engine_name).generate_txt2img(
            spec,
            lambda value, message: self._progress(job_id, value, message),
        )
        for index, image in enumerate(images[: spec.batch_size]):
            artifact_id = uuid.uuid4().hex
            artifact_dir = config.ARTIFACTS_DIR / job_id
            artifact_dir.mkdir(parents=True, exist_ok=True)
            image_path = artifact_dir / f"{artifact_id}.png"
            metadata_path = artifact_dir / f"{artifact_id}.json"
            image.save(image_path)
            thumbnail_path = artifact_dir / f"{artifact_id}.thumb.png"
            self._write_thumbnail(image_path, thumbnail_path)
            metadata = self._metadata(job_id, spec, image_path, index)
            metadata["thumbnail_path"] = str(thumbnail_path)
            write_json(metadata_path, metadata)
            self.store.add_artifact(
                ArtifactRecord(
                    id=artifact_id,
                    job_id=job_id,
                    kind="image",
                    path=str(image_path),
                    metadata_path=str(metadata_path),
                    metadata=metadata,
                )
            )

    def _execute_asset_download(self, job_id: str, spec: JobSpec) -> None:
        if spec.asset_download is None:
            raise RuntimeError("asset-download requires asset_download payload")
        try:
            result = download_asset(spec.asset_download)
        except DownloadError:
            raise
        artifact_id = uuid.uuid4().hex
        metadata_path = config.ARTIFACTS_DIR / job_id / f"{artifact_id}.json"
        metadata = {
            "job_id": job_id,
            "type": "asset-download",
            "asset": spec.asset_download.model_dump(),
            "result": result,
            "created_at": utc_now(),
        }
        write_json(metadata_path, metadata)
        self.store.add_artifact(
            ArtifactRecord(
                id=artifact_id,
                job_id=job_id,
                kind="asset",
                path=result["path"],
                metadata_path=str(metadata_path),
                metadata=metadata,
            )
        )

    def _metadata(self, job_id: str, spec: JobSpec, path: Path, index: int) -> dict[str, object]:
        return {
            "job_id": job_id,
            "artifact_index": index,
            "path": str(path),
            "created_at": utc_now(),
            "prompt": spec.prompt,
            "negative_prompt": spec.negative_prompt,
            "engine": spec.engine,
            "model": spec.model,
            "loras": [item.model_dump() for item in spec.loras],
            "embeddings": spec.embeddings,
            "seed": spec.seed,
            "dimensions": {"width": spec.width, "height": spec.height},
            "sampler": spec.sampler,
            "scheduler": spec.scheduler,
            "steps": spec.steps,
            "cfg": spec.cfg,
            "guidance": spec.guidance,
            "source_images": spec.source_images,
            "control": spec.control,
            "raw_spec": json.loads(spec.model_dump_json()),
        }

    def _write_thumbnail(self, image_path: Path, thumbnail_path: Path) -> None:
        with Image.open(image_path) as image:
            image.thumbnail((256, 256))
            image.save(thumbnail_path)


def resource_estimate(spec: JobSpec) -> dict[str, object]:
    pixel_count = spec.width * spec.height * spec.batch_size
    return {
        "pixel_count": pixel_count,
        "megapixels": round(pixel_count / 1_000_000, 3),
        "steps": spec.steps,
        "batch_size": spec.batch_size,
        "cpu_only": True,
        "min_free_ram_gb": 4,
        "current_free_ram_gb": round(psutil.virtual_memory().available / 1024**3, 2),
    }
