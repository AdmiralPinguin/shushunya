from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from EyeOfTerror.Pictorium.Moriana.forge_runtime.schemas import JobSpec

ProgressCallback = Callable[[float, str], None]


class EngineError(RuntimeError):
    pass


class BaseEngine(ABC):
    name: str

    @abstractmethod
    def generate_txt2img(self, spec: JobSpec, progress: ProgressCallback) -> list[object]:
        raise NotImplementedError

    def generate_img2img(self, spec: JobSpec, source_image: Path, progress: ProgressCallback) -> list[object]:
        raise EngineError(f"{self.name} does not support img2img")

    def generate_inpaint(
        self,
        spec: JobSpec,
        source_image: Path,
        mask_image: Path,
        progress: ProgressCallback,
    ) -> list[object]:
        raise EngineError(f"{self.name} does not support inpaint")
