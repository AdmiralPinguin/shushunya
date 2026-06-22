from __future__ import annotations

import json
import queue
import random
import threading
import time
import uuid
from pathlib import Path

import psutil
from PIL import Image

from . import config
from .downloader import DownloadError, download_asset, target_dir_for, validate_download_spec
from .engines.diffusers_adapter import DiffusersEngine
from .registries import ENGINE_MODELS, write_json
from .schemas import ArtifactRecord, AssetDownloadRecord, JobRecord, JobSpec, JobStatus, JobType, utc_now
from .storage import ForgeStore


class ForgeQueue:
    def __init__(self, store: ForgeStore):
        self.store = store
        self._queue: queue.Queue[str] = queue.Queue()
        self._cancel = set[str]()
        self._engines: dict[str, DiffusersEngine] = {}
        self._worker = threading.Thread(target=self._run, name="forge-worker", daemon=True)
        self._worker.start()
        self._maintenance = threading.Thread(
            target=self._maintain,
            name="forge-maintenance",
            daemon=True,
        )
        self._maintenance.start()

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
        elif spec.type == JobType.prompt_enhance:
            if not spec.prompt or not spec.prompt.strip():
                raise RuntimeError("prompt-enhance requires prompt")
        elif spec.type == JobType.metadata_read:
            if not spec.source_images:
                raise RuntimeError("metadata-read requires source_images")
        elif spec.type == JobType.img2img:
            if not spec.source_images:
                raise RuntimeError("img2img requires source_images")
            if not spec.prompt or not spec.prompt.strip():
                raise RuntimeError("img2img requires prompt")
            if (spec.engine or "sdxl") != "sdxl":
                raise RuntimeError("img2img is currently implemented for sdxl only")
            self._resolve_input_path(spec.source_images[0])
        elif spec.type == JobType.inpaint:
            if not spec.source_images:
                raise RuntimeError("inpaint requires source_images")
            if not spec.prompt or not spec.prompt.strip():
                raise RuntimeError("inpaint requires prompt")
            if not spec.mask_image:
                raise RuntimeError("inpaint requires mask_image")
            if (spec.engine or "sdxl") != "sdxl":
                raise RuntimeError("inpaint is currently implemented for sdxl only")
            self._resolve_input_path(spec.source_images[0])
            self._resolve_input_path(spec.mask_image)
        elif spec.type == JobType.upscale:
            if not spec.source_images:
                raise RuntimeError("upscale requires source_images")
            self._resolve_input_path(spec.source_images[0])
        elif spec.type == JobType.asset_download:
            if spec.asset_download is None:
                raise RuntimeError("asset-download requires asset_download payload")
            validate_download_spec(spec.asset_download)
        else:
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

    def _maintain(self) -> None:
        while True:
            time.sleep(60)
            for engine in list(self._engines.values()):
                if engine.unload_if_idle(config.MODEL_IDLE_SECONDS):
                    self.store.append_log("system", f"unloaded idle engine {engine.name}")

    def runtime_state(self) -> dict[str, object]:
        mem = psutil.virtual_memory()
        return {
            "queue_depth": self._queue.qsize(),
            "canceled_jobs": len(self._cancel),
            "loaded_engines": [engine.runtime_state() for engine in self._engines.values()],
            "cpu_only": True,
            "cpu_threads": config.CPU_THREADS,
            "model_idle_seconds": config.MODEL_IDLE_SECONDS,
            "db_schema_version": self.store.schema_version(),
            "ram": {
                "total_gb": round(mem.total / 1024**3, 2),
                "available_gb": round(mem.available / 1024**3, 2),
                "percent": mem.percent,
            },
        }

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
            elif spec.type == JobType.img2img:
                self._execute_img2img(job_id, spec)
            elif spec.type == JobType.inpaint:
                self._execute_inpaint(job_id, spec)
            elif spec.type == JobType.upscale:
                self._execute_upscale(job_id, spec)
            elif spec.type == JobType.prompt_enhance:
                self._execute_prompt_enhance(job_id, spec)
            elif spec.type == JobType.metadata_read:
                self._execute_metadata_read(job_id, spec)
            else:
                raise RuntimeError(f"job type is not supported by any registered backend yet: {spec.type.value}")
            self.store.update_job(job_id, status=JobStatus.succeeded, progress=1.0)
            self.store.append_log(job_id, "job succeeded")
        except Exception as exc:
            status = JobStatus.canceled if str(exc) == "job canceled" else JobStatus.failed
            self.store.update_job(job_id, status=status, error=str(exc))
            self.store.append_log(job_id, f"job {status.value}: {exc}")

    def _resolve_input_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = config.ROOT / path
        resolved = path.resolve()
        root = config.ROOT.resolve()
        if resolved != root and root not in resolved.parents:
            raise RuntimeError("input path must stay inside DemonsForge")
        if not resolved.exists():
            raise RuntimeError(f"input path does not exist: {resolved}")
        return resolved

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
            self._save_image_artifact(job_id, spec, image, index)

    def _execute_img2img(self, job_id: str, spec: JobSpec) -> None:
        if not spec.prompt or not spec.prompt.strip():
            raise RuntimeError("img2img requires prompt")
        if not spec.source_images:
            raise RuntimeError("img2img requires source_images")
        engine_name = spec.engine or "sdxl"
        if engine_name != "sdxl":
            raise RuntimeError("img2img is currently implemented for sdxl only")
        self._resource_check(spec)
        source_image = self._resolve_input_path(spec.source_images[0])
        images = self._engine(engine_name).generate_img2img(
            spec,
            source_image,
            lambda value, message: self._progress(job_id, value, message),
        )
        for index, image in enumerate(images[: spec.batch_size]):
            self._save_image_artifact(job_id, spec, image, index)

    def _execute_inpaint(self, job_id: str, spec: JobSpec) -> None:
        if not spec.prompt or not spec.prompt.strip():
            raise RuntimeError("inpaint requires prompt")
        if not spec.source_images or not spec.mask_image:
            raise RuntimeError("inpaint requires source_images and mask_image")
        engine_name = spec.engine or "sdxl"
        if engine_name != "sdxl":
            raise RuntimeError("inpaint is currently implemented for sdxl only")
        self._resource_check(spec)
        source_image = self._resolve_input_path(spec.source_images[0])
        mask_image = self._resolve_input_path(spec.mask_image)
        images = self._engine(engine_name).generate_inpaint(
            spec,
            source_image,
            mask_image,
            lambda value, message: self._progress(job_id, value, message),
        )
        for index, image in enumerate(images[: spec.batch_size]):
            self._save_image_artifact(job_id, spec, image, index)

    def _execute_upscale(self, job_id: str, spec: JobSpec) -> None:
        if not spec.source_images:
            raise RuntimeError("upscale requires source_images")
        source_image = self._resolve_input_path(spec.source_images[0])
        with Image.open(source_image) as image:
            image = image.convert("RGB")
            new_size = (image.width * spec.upscale_factor, image.height * spec.upscale_factor)
            resampling = getattr(Image.Resampling, "LANCZOS", Image.LANCZOS)
            upscaled = image.resize(new_size, resampling)
        self._save_image_artifact(job_id, spec, upscaled, 0)

    def _execute_asset_download(self, job_id: str, spec: JobSpec) -> None:
        if spec.asset_download is None:
            raise RuntimeError("asset-download requires asset_download payload")
        download_record = AssetDownloadRecord(
            id=job_id,
            name=spec.asset_download.name,
            asset_type=spec.asset_download.asset_type,
            source_url=spec.asset_download.source_url,
            sha256=spec.asset_download.sha256,
            license_note=spec.asset_download.license_note,
            target_dir=str(target_dir_for(spec.asset_download)),
            status="running",
        )
        self.store.create_asset_download(download_record)
        try:
            result = download_asset(spec.asset_download)
        except DownloadError as exc:
            self.store.update_asset_download(job_id, status="rejected", error=str(exc))
            raise
        except Exception as exc:
            self.store.update_asset_download(job_id, status="failed", error=str(exc))
            raise
        self.store.update_asset_download(
            job_id,
            status="downloaded",
            sha256=result["sha256"],
            target_dir=str(Path(result["path"]).parent),
        )
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

    def _execute_prompt_enhance(self, job_id: str, spec: JobSpec) -> None:
        if not spec.prompt or not spec.prompt.strip():
            raise RuntimeError("prompt-enhance requires prompt")
        artifact_id = uuid.uuid4().hex
        artifact_dir = config.ARTIFACTS_DIR / job_id
        metadata_path = artifact_dir / f"{artifact_id}.json"
        prompt = spec.prompt.strip()
        additions = []
        lowered = prompt.lower()
        if "portrait" in lowered or "портрет" in lowered:
            additions.extend(["detailed face", "balanced composition"])
        if "cinematic" in lowered or "кинематограф" in lowered:
            additions.extend(["cinematic lighting", "high dynamic range"])
        if "anime" in lowered or "аниме" in lowered:
            additions.extend(["clean linework", "expressive character design"])
        if not additions:
            additions.extend(["high detail", "coherent composition", "clean lighting"])
        enhanced = f"{prompt}, {', '.join(dict.fromkeys(additions))}"
        metadata = {
            "job_id": job_id,
            "type": "prompt-enhance",
            "created_at": utc_now(),
            "prompt": prompt,
            "enhanced_prompt": enhanced,
            "negative_prompt": spec.negative_prompt or "low quality, blurry, distorted",
            "raw_spec": json.loads(spec.model_dump_json()),
        }
        write_json(metadata_path, metadata)
        self.store.add_artifact(
            ArtifactRecord(
                id=artifact_id,
                job_id=job_id,
                kind="metadata",
                path=str(metadata_path),
                metadata_path=str(metadata_path),
                metadata=metadata,
            )
        )

    def _execute_metadata_read(self, job_id: str, spec: JobSpec) -> None:
        if not spec.source_images:
            raise RuntimeError("metadata-read requires source_images")
        artifact_id = uuid.uuid4().hex
        artifact_dir = config.ARTIFACTS_DIR / job_id
        metadata_path = artifact_dir / f"{artifact_id}.json"
        entries = []
        for source in spec.source_images:
            source_path = Path(source)
            if not source_path.is_absolute():
                source_path = config.ROOT / source_path
            entry: dict[str, object] = {"source": str(source_path), "exists": source_path.exists()}
            if source_path.exists() and source_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                with Image.open(source_path) as image:
                    entry["image"] = {
                        "format": image.format,
                        "mode": image.mode,
                        "width": image.width,
                        "height": image.height,
                        "info": {k: str(v) for k, v in image.info.items()},
                    }
            sidecar = source_path.with_suffix(".json")
            if sidecar.exists():
                try:
                    entry["sidecar_json"] = json.loads(sidecar.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    entry["sidecar_error"] = str(exc)
            entries.append(entry)
        metadata = {
            "job_id": job_id,
            "type": "metadata-read",
            "created_at": utc_now(),
            "entries": entries,
            "raw_spec": json.loads(spec.model_dump_json()),
        }
        write_json(metadata_path, metadata)
        self.store.add_artifact(
            ArtifactRecord(
                id=artifact_id,
                job_id=job_id,
                kind="metadata",
                path=str(metadata_path),
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
            "strength": spec.strength,
            "upscale_factor": spec.upscale_factor,
            "dimensions": {"width": spec.width, "height": spec.height},
            "sampler": spec.sampler,
            "scheduler": spec.scheduler,
            "steps": spec.steps,
            "cfg": spec.cfg,
            "guidance": spec.guidance,
            "source_images": spec.source_images,
            "mask_image": spec.mask_image,
            "control": spec.control,
            "raw_spec": json.loads(spec.model_dump_json()),
        }

    def _write_thumbnail(self, image_path: Path, thumbnail_path: Path) -> None:
        with Image.open(image_path) as image:
            image.thumbnail((256, 256))
            image.save(thumbnail_path)

    def _save_image_artifact(self, job_id: str, spec: JobSpec, image: object, index: int) -> None:
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
