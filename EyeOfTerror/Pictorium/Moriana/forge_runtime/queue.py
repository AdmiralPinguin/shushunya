from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import random
import sys
import threading
import time
import uuid
from pathlib import Path

import psutil
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from . import config
from .archive_memory import ArchiveMemoryClient, asset_memory_proposal
from EyeOfTerror.Pictorium.Moriana.moriana_core.asset_catalog import (
    ENGINE_MODELS,
    SAMPLERS,
    SCHEDULERS,
    clear_registry_caches,
    find_lora,
    peft_available,
    write_json,
)
from EyeOfTerror.Pictorium.Moriana.moriana_core.asset_downloader import (
    DownloadError,
    download_asset,
    target_dir_for,
    validate_download_spec,
)
from DemonsForge.forge_service.engines.diffusers_adapter import DiffusersEngine
from .schemas import ArtifactRecord, AssetDownloadRecord, JobRecord, JobSpec, JobStatus, JobType, utc_now
from .storage import ForgeStore


class ForgeQueue:
    def __init__(self, store: ForgeStore, start_worker: bool = True):
        self.store = store
        self._queue: queue.Queue[str] = queue.Queue()
        self._cancel = set[str]()
        self._engines: dict[str, DiffusersEngine] = {}
        self.memory = ArchiveMemoryClient.from_config()
        self._embedded_worker = start_worker
        self._worker = None
        if start_worker:
            self._worker = threading.Thread(target=self._run, name="forge-worker", daemon=True)
            self._worker.start()
        self._maintenance = threading.Thread(
            target=self._maintain,
            name="forge-maintenance",
            daemon=True,
        )
        self._maintenance.start()

    def submit(self, spec: JobSpec) -> JobRecord:
        self.validate(spec)
        job_id = uuid.uuid4().hex
        if spec.seed is None and spec.type in {JobType.txt2img, JobType.img2img, JobType.inpaint, JobType.variation}:
            spec.seed = random.randint(0, 2**32 - 1)
        record = JobRecord(id=job_id, spec=spec, status=JobStatus.queued)
        self.store.create_job(record)
        if self._embedded_worker:
            self._queue.put(job_id)
        return record

    def validate(self, spec: JobSpec) -> dict[str, object]:
        estimate = resource_estimate(spec)
        if (
            spec.type != JobType.asset_download
            and spec.asset_request is not None
            and spec.asset_request.requires_user_approval
        ):
            raise RuntimeError("job has unresolved asset_request and requires user approval")
        if spec.type == JobType.txt2img:
            engine_name = spec.engine or "sdxl"
            self._validate_engine_options(spec, engine_name)
            if not spec.prompt or not spec.prompt.strip():
                raise RuntimeError("txt2img requires prompt")
        elif spec.type == JobType.prompt_enhance:
            if not spec.prompt or not spec.prompt.strip():
                raise RuntimeError("prompt-enhance requires prompt")
        elif spec.type == JobType.metadata_read:
            if not spec.source_images:
                raise RuntimeError("metadata-read requires source_images")
            for source in spec.source_images:
                self._resolve_input_path(source)
        elif spec.type == JobType.img2img:
            if not spec.source_images:
                raise RuntimeError("img2img requires source_images")
            if not spec.prompt or not spec.prompt.strip():
                raise RuntimeError("img2img requires prompt")
            engine_name = spec.engine or "sdxl"
            self._validate_engine_options(spec, engine_name)
            if engine_name != "sdxl":
                raise RuntimeError("img2img is currently implemented for sdxl only")
            self._resolve_input_path(spec.source_images[0])
        elif spec.type == JobType.inpaint:
            if not spec.source_images:
                raise RuntimeError("inpaint requires source_images")
            if not spec.prompt or not spec.prompt.strip():
                raise RuntimeError("inpaint requires prompt")
            if not spec.mask_image:
                raise RuntimeError("inpaint requires mask_image")
            engine_name = spec.engine or "sdxl"
            self._validate_engine_options(spec, engine_name)
            if engine_name != "sdxl":
                raise RuntimeError("inpaint is currently implemented for sdxl only")
            self._resolve_input_path(spec.source_images[0])
            self._resolve_input_path(spec.mask_image)
        elif spec.type == JobType.upscale:
            if not spec.source_images:
                raise RuntimeError("upscale requires source_images")
            source_path = self._resolve_input_path(spec.source_images[0])
            estimate["upscale"] = self._upscale_estimate(source_path, spec.upscale_factor)
        elif spec.type == JobType.asset_download:
            if spec.asset_download is None:
                raise RuntimeError("asset-download requires asset_download payload")
            validate_download_spec(spec.asset_download)
        else:
            raise RuntimeError(f"job type is not supported by any registered backend yet: {spec.type.value}")
        estimate["loaded_engines"] = [engine.runtime_state() for engine in self._engines.values()]
        return {"valid": True, "resource_estimate": estimate}

    def _validate_engine_options(self, spec: JobSpec, engine_name: str) -> None:
        if engine_name not in ENGINE_MODELS:
            raise RuntimeError(f"unknown engine: {engine_name}")
        meta = ENGINE_MODELS[engine_name]
        if spec.type.value not in meta["job_types"]:
            raise RuntimeError(f"{engine_name} does not support {spec.type.value}")
        model_name = spec.model or str(meta["default_model"])
        if not (config.MODELS_DIR / model_name / "model_index.json").exists():
            raise RuntimeError(f"model is not available locally: {model_name}")
        if spec.sampler and spec.sampler not in SAMPLERS:
            raise RuntimeError(f"unsupported sampler: {spec.sampler}")
        scheduler_names = {str(item["name"]) for item in SCHEDULERS if item.get("available")}
        if spec.scheduler and spec.scheduler not in scheduler_names:
            raise RuntimeError(f"unsupported scheduler: {spec.scheduler}")
        if spec.negative_prompt and not meta.get("supports_negative_prompt"):
            raise RuntimeError(f"{engine_name} does not support negative_prompt")
        if engine_name == "flux":
            if spec.guidance not in {None, 0, 0.0} or spec.cfg not in {None, 0, 0.0}:
                raise RuntimeError("flux adapter currently runs with guidance/cfg fixed at 0.0")
        if spec.loras and not meta.get("supports_lora"):
            raise RuntimeError(f"{engine_name} does not support LoRA")
        if spec.loras and not peft_available():
            raise RuntimeError("LoRA support requires the peft package in the DemonsForge runtime")
        for lora in spec.loras:
            if not find_lora(lora.name):
                raise RuntimeError(f"LoRA is not available locally: {lora.name}")
        if spec.embeddings:
            raise RuntimeError("textual inversion embeddings are not implemented for active backends yet")
        if spec.control and not meta.get("supports_control"):
            raise RuntimeError(f"{engine_name} does not support control assets yet")

    def cancel(self, job_id: str) -> JobRecord:
        record = self.store.get_job(job_id)
        if record is None:
            raise KeyError(job_id)
        if record.status in {JobStatus.succeeded, JobStatus.failed, JobStatus.canceled}:
            return record
        self._cancel.add(job_id)
        updated = self.store.update_job(job_id, status=JobStatus.canceled, progress=record.progress)
        if record.status == JobStatus.queued:
            self._cancel.discard(job_id)
        return updated

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                if job_id in self._cancel:
                    self._cancel.discard(job_id)
                    continue
                while self.store.get_runtime_flag("queue_paused", default=False):
                    time.sleep(0.5)
                self._execute(job_id)
            finally:
                self._queue.task_done()

    def run_pending_once(self) -> bool:
        if self.store.get_runtime_flag("queue_paused", default=False):
            return False
        queued = self.store.list_jobs(status=JobStatus.queued.value, limit=1)
        if not queued:
            return False
        job_id = queued[0].id
        if job_id in self._cancel:
            self._cancel.discard(job_id)
            return True
        self._execute(job_id)
        return True

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
            "pid": os.getpid(),
            "paused": self.store.get_runtime_flag("queue_paused", default=False),
            "embedded_worker": self._embedded_worker,
            "canceled_jobs": len(self._cancel),
            "loaded_engines": [engine.runtime_state() for engine in self._engines.values()],
            "cpu_only": True,
            "cpu_threads": config.CPU_THREADS,
            "thread_policy": config.thread_policy(),
            "model_idle_seconds": config.MODEL_IDLE_SECONDS,
            "db_schema_version": self.store.schema_version(),
            "memory": self.memory.status(),
            "ram": {
                "total_gb": round(mem.total / 1024**3, 2),
                "available_gb": round(mem.available / 1024**3, 2),
                "percent": mem.percent,
            },
        }

    def queue_state(self) -> dict[str, object]:
        counts = {status.value: 0 for status in JobStatus}
        counts.update(self.store.job_status_counts())
        return {
            "queue_depth": self._queue.qsize(),
            "pid": os.getpid(),
            "paused": self.store.get_runtime_flag("queue_paused", default=False),
            "embedded_worker": self._embedded_worker,
            "canceled_jobs": len(self._cancel),
            "status_counts": counts,
        }

    def pause(self) -> dict[str, object]:
        self.store.set_runtime_flag("queue_paused", True)
        return {"ok": True, "paused": True, "runtime": self.runtime_state()}

    def resume(self) -> dict[str, object]:
        self.store.set_runtime_flag("queue_paused", False)
        return {"ok": True, "paused": False, "runtime": self.runtime_state()}

    def unload_engines(self, engine_name: str | None = None) -> dict[str, object]:
        unloaded = []
        for name, engine in list(self._engines.items()):
            if engine_name and name != engine_name:
                continue
            if engine.unload():
                unloaded.append(name)
        return {"ok": True, "engine": engine_name, "unloaded": unloaded, "runtime": self.runtime_state()}

    def _unload_other_engines(self, target_engine: str) -> list[str]:
        unloaded = []
        for name, engine in list(self._engines.items()):
            if name == target_engine:
                continue
            if engine.unload():
                unloaded.append(name)
        return unloaded

    def _progress(self, job_id: str, value: float, message: str) -> None:
        if job_id in self._cancel:
            raise RuntimeError("job canceled")
        record = self.store.get_job(job_id)
        if not record:
            return
        self.store.update_job(job_id, progress=value)
        self.store.append_log(job_id, message)

    def _resource_check(self, spec: JobSpec, engine_name: str | None = None) -> None:
        if spec.type.value in {"txt2img", "img2img", "inpaint"} and engine_name:
            unloaded = self._unload_other_engines(engine_name)
            for unloaded_engine in unloaded:
                self.store.append_log("system", f"unloaded {unloaded_engine} before {engine_name} job")
        mem = psutil.virtual_memory()
        estimate = resource_estimate(spec)
        min_free_gb = float(estimate["min_free_ram_gb"])
        if engine_name and engine_name in self._engines and self._engines[engine_name].runtime_state()["loaded"]:
            min_free_gb = max(4.0, float(estimate["estimated_working_ram_gb"]))
        if mem.available < min_free_gb * 1024**3:
            raise RuntimeError(
                f"not enough available RAM for generation queue: "
                f"{round(mem.available / 1024**3, 2)}GB available, {min_free_gb}GB required"
            )
        if spec.width * spec.height * spec.batch_size > 1536 * 1536:
            raise RuntimeError("job exceeds conservative pixel budget")
        if spec.engine == "sdxl" and spec.type.value in {"txt2img", "img2img", "inpaint"}:
            if spec.width < 512 or spec.height < 512:
                raise RuntimeError("sdxl diffusion jobs require width and height >= 512")
        if spec.type.value in {"img2img", "inpaint"} and spec.steps * spec.strength < 1:
            raise RuntimeError("img2img/inpaint require steps * strength >= 1")

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
            if spec.type in {JobType.txt2img, JobType.img2img, JobType.inpaint}:
                config.boost_torch_threads()
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
        finally:
            config.cooldown_torch_threads()
            self._cancel.discard(job_id)

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

    def _upscale_estimate(self, source_image: Path, upscale_factor: int) -> dict[str, object]:
        with Image.open(source_image) as image:
            source_width, source_height = image.size
        output_width = source_width * upscale_factor
        output_height = source_height * upscale_factor
        output_pixels = output_width * output_height
        pixel_budget = config.MAX_WIDTH * config.MAX_HEIGHT
        if output_pixels > pixel_budget:
            raise RuntimeError("upscale output exceeds conservative pixel budget")
        return {
            "source_dimensions": {"width": source_width, "height": source_height},
            "output_dimensions": {"width": output_width, "height": output_height},
            "output_pixel_count": output_pixels,
            "pixel_budget": pixel_budget,
        }

    def _execute_txt2img(self, job_id: str, spec: JobSpec) -> None:
        if not spec.prompt or not spec.prompt.strip():
            raise RuntimeError("txt2img requires prompt")
        engine_name = spec.engine or "sdxl"
        if engine_name not in ENGINE_MODELS:
            raise RuntimeError(f"unknown engine: {engine_name}")
        self._resource_check(spec, engine_name)
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
        self._resource_check(spec, engine_name)
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
        self._resource_check(spec, engine_name)
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
        self._upscale_estimate(source_image, spec.upscale_factor)
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
            self._remember_asset_status(spec, "rejected", error=str(exc))
            raise
        except Exception as exc:
            self.store.update_asset_download(job_id, status="failed", error=str(exc))
            self._remember_asset_status(spec, "failed", error=str(exc))
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
        clear_registry_caches()
        self._remember_asset_status(spec, "downloaded", result=result)

    def _remember_asset_status(
        self,
        spec: JobSpec,
        status: str,
        result: dict[str, object] | None = None,
        error: str | None = None,
    ) -> None:
        if spec.asset_download is None or not self.memory.enabled:
            return
        asset = spec.asset_download.model_dump()
        proposal, evidence, importance = asset_memory_proposal(status, asset, result, error)

        def propose() -> None:
            response = self.memory.propose(
                proposal=proposal,
                evidence=evidence,
                target="auto",
                importance=importance,
            )
            if response.get("ok") is False:
                self.store.append_event_log(
                    "system",
                    "memory",
                    f"archive memory proposal failed: {response.get('error')}",
                )
            else:
                self.store.append_event_log(
                    "system",
                    "memory",
                    f"archive memory proposal accepted for asset status {status}",
                )

        threading.Thread(target=propose, name="forge-memory-proposal", daemon=True).start()

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
            source_path = self._resolve_input_path(source)
            entry: dict[str, object] = {
                "source": str(source_path),
                "exists": True,
                "size_bytes": source_path.stat().st_size,
                "sha256": self._sha256_file(source_path),
                "mime_type": mimetypes.guess_type(source_path.name)[0],
            }
            if source_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
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
                    entry["sidecar_path"] = str(sidecar)
                    entry["sidecar_size_bytes"] = sidecar.stat().st_size
                    if sidecar.stat().st_size <= 1_000_000:
                        entry["sidecar_json"] = json.loads(sidecar.read_text(encoding="utf-8"))
                    else:
                        entry["sidecar_skipped"] = "sidecar is larger than 1MB"
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

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _metadata(self, job_id: str, spec: JobSpec, path: Path, index: int) -> dict[str, object]:
        engine_name = spec.engine
        if engine_name is None and spec.type in {JobType.txt2img, JobType.img2img, JobType.inpaint}:
            engine_name = "sdxl"
        model_name = spec.model
        if model_name is None and engine_name in ENGINE_MODELS:
            model_name = str(ENGINE_MODELS[engine_name]["default_model"])
        return {
            "job_id": job_id,
            "artifact_index": index,
            "path": str(path),
            "created_at": utc_now(),
            "prompt": spec.prompt,
            "negative_prompt": spec.negative_prompt,
            "engine": engine_name,
            "model": model_name,
            "quality_preset": spec.quality_preset,
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
        metadata["image_sha256"] = self._sha256_file(image_path)
        metadata["image_size_bytes"] = image_path.stat().st_size
        metadata["thumbnail_path"] = str(thumbnail_path)
        metadata["thumbnail_sha256"] = self._sha256_file(thumbnail_path)
        metadata["thumbnail_size_bytes"] = thumbnail_path.stat().st_size
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
    megapixels = pixel_count / 1_000_000
    model_ram_gb = {
        "flux": 60,
        "stable_diffusion": 44,
        "sdxl": 30,
    }.get(spec.engine or "sdxl", 24)
    if spec.type.value in {"upscale", "metadata-read", "prompt-enhance", "asset-download"}:
        model_ram_gb = 0.5
    working_ram_gb = max(1.0, megapixels * max(spec.steps, 1) * 0.08)
    estimated_min_ram_gb = round(model_ram_gb + working_ram_gb, 2)
    pixel_budget = 1536 * 1536
    warnings = []
    if spec.type.value in {"txt2img", "img2img", "inpaint"}:
        warnings.append("CPU-only diffusion generation can be slow; use low steps for smoke tests")
    if pixel_count > pixel_budget:
        warnings.append("job exceeds conservative pixel budget and will be rejected")
    return {
        "pixel_count": pixel_count,
        "pixel_budget": pixel_budget,
        "pixel_budget_ratio": round(pixel_count / pixel_budget, 3),
        "megapixels": round(megapixels, 3),
        "steps": spec.steps,
        "batch_size": spec.batch_size,
        "engine": spec.engine,
        "job_type": spec.type.value,
        "cpu_only": True,
        "estimated_min_ram_gb": estimated_min_ram_gb,
        "estimated_model_ram_gb": model_ram_gb,
        "estimated_working_ram_gb": round(working_ram_gb, 2),
        "min_free_ram_gb": max(4, min(estimated_min_ram_gb, 96)),
        "current_free_ram_gb": round(psutil.virtual_memory().available / 1024**3, 2),
        "warnings": warnings,
    }
