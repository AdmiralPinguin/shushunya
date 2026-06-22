from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from ..schemas import JobSpec

ProgressCallback = Callable[[float, str], None]


class EngineError(RuntimeError):
    pass


class BaseEngine(ABC):
    name: str

    @abstractmethod
    def generate_txt2img(self, spec: JobSpec, progress: ProgressCallback) -> list[object]:
        raise NotImplementedError
