from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from . import config


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobType(str, Enum):
    txt2img = "txt2img"
    img2img = "img2img"
    inpaint = "inpaint"
    outpaint = "outpaint"
    upscale = "upscale"
    variation = "variation"
    prompt_enhance = "prompt-enhance"
    metadata_read = "metadata-read"
    asset_download = "asset-download"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class LoraRef(BaseModel):
    name: str
    weight: float = 1.0


class AssetRequest(BaseModel):
    name: str
    asset_type: Literal["model", "lora", "embedding", "control_asset", "ip_adapter"]
    candidate_source: str | None = None
    license_note: str | None = None
    risks: list[str] = Field(default_factory=list)
    requires_user_approval: bool = True


class AssetDownloadSpec(BaseModel):
    name: str
    asset_type: Literal["model", "lora", "embedding", "control_asset", "ip_adapter"]
    source_url: str
    sha256: str | None = None
    license_note: str | None = None
    target_dir: str | None = None
    approved: bool = False


class JobSpec(BaseModel):
    type: JobType = JobType.txt2img
    engine: str | None = None
    model: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    width: int = 1024
    height: int = 1024
    aspect_preset: str | None = None
    steps: int = 20
    cfg: float | None = None
    guidance: float | None = None
    sampler: str | None = None
    scheduler: str | None = None
    seed: int | None = None
    strength: float = 0.75
    upscale_factor: int = 2
    batch_size: int = 1
    loras: list[LoraRef] = Field(default_factory=list)
    embeddings: list[str] = Field(default_factory=list)
    source_images: list[str] = Field(default_factory=list)
    mask_image: str | None = None
    control: dict[str, Any] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
    asset_request: AssetRequest | None = None
    asset_download: AssetDownloadSpec | None = None

    @field_validator("width", "height")
    @classmethod
    def validate_dimensions(cls, value: int, info: ValidationInfo) -> int:
        if value < 64 or value % 8 != 0:
            raise ValueError("dimensions must be >=64 and divisible by 8")
        max_value = config.MAX_HEIGHT if info.field_name == "height" else config.MAX_WIDTH
        if value > max_value:
            raise ValueError(f"{info.field_name} must be <= {max_value}")
        return value

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, value: int) -> int:
        if value < 1 or value > config.MAX_STEPS:
            raise ValueError(f"steps must be between 1 and {config.MAX_STEPS}")
        return value

    @field_validator("batch_size")
    @classmethod
    def validate_batch(cls, value: int) -> int:
        if value < 1 or value > config.MAX_BATCH:
            raise ValueError(f"batch_size must be between 1 and {config.MAX_BATCH}")
        return value

    @field_validator("strength")
    @classmethod
    def validate_strength(cls, value: float) -> float:
        if value < 0.0 or value > 1.0:
            raise ValueError("strength must be between 0.0 and 1.0")
        return value

    @field_validator("upscale_factor")
    @classmethod
    def validate_upscale_factor(cls, value: int) -> int:
        if value not in {2, 3, 4}:
            raise ValueError("upscale_factor must be 2, 3, or 4")
        return value


class JobRecord(BaseModel):
    id: str
    spec: JobSpec
    status: JobStatus
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    progress: float = 0.0
    logs: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    error: str | None = None


class JobCloneRequest(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)
    reuse_seed: bool = True


class PlanRequest(BaseModel):
    request: str
    preferred_engine: str | None = None


class MemoryProposal(BaseModel):
    proposal: str = Field(min_length=1)
    evidence: str | None = None
    target: Literal["auto", "focus", "wiki", "vector", "graph"] = "auto"
    importance: int = Field(default=3, ge=1, le=5)

    @field_validator("proposal")
    @classmethod
    def validate_proposal(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("proposal must not be empty")
        return text

    @field_validator("evidence")
    @classmethod
    def validate_evidence(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class ArtifactRecord(BaseModel):
    id: str
    job_id: str
    kind: Literal["image", "metadata", "asset"]
    path: str
    metadata_path: str
    created_at: str = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssetDownloadRecord(BaseModel):
    id: str
    name: str
    asset_type: str
    source_url: str
    sha256: str | None = None
    license_note: str | None = None
    target_dir: str
    status: Literal["queued", "running", "downloaded", "failed", "rejected"] = "queued"
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    error: str | None = None
