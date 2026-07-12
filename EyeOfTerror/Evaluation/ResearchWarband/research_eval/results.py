"""Fail-closed result serialization."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class ResultWriteError(RuntimeError):
    pass


def render_result(result: dict[str, Any]) -> bytes:
    return (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def result_sha256(result: dict[str, Any]) -> str:
    return hashlib.sha256(render_result(result)).hexdigest()


def write_result_atomic(result: dict[str, Any], target: str | Path) -> None:
    if result.get("run_valid") is not True:
        raise ResultWriteError("refusing to replace a result with run_valid=false")
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(render_result(result))
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
