from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    try:
        value = default if raw is None or not raw.strip() else float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    return value


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None or not raw.strip() else int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    db_path: Path
    lock_path: Path
    llm_base_url: str
    llm_model: str
    llm_timeout_sec: float
    abaddon_base_url: str
    archive_base_url: str
    administratum_base_url: str
    vox_base_url: str
    warpwails_base_url: str
    steward_interval_sec: float
    organ_health_ttl_sec: float
    effect_lease_sec: float
    context_char_budget: int

    @classmethod
    def from_env(cls) -> "Settings":
        runtime = Path(
            os.environ.get(
                "SHUSHUNYA_CORE_RUNTIME_DIR",
                str(PROJECT_ROOT / "runtime" / "shushunya-core"),
            )
        ).resolve()
        return cls(
            host=os.environ.get("SHUSHUNYA_CORE_HOST", "127.0.0.1"),
            port=_int("SHUSHUNYA_CORE_PORT", 7600, 1),
            db_path=Path(os.environ.get("SHUSHUNYA_CORE_DB", str(runtime / "store.sqlite3"))).resolve(),
            lock_path=Path(os.environ.get("SHUSHUNYA_CORE_LOCK", str(runtime / "core.lock"))).resolve(),
            llm_base_url=os.environ.get("SHUSHUNYA_CORE_LLM_BASE_URL", "http://127.0.0.1:8079/v1").rstrip("/"),
            llm_model=os.environ.get(
                "SHUSHUNYA_CORE_LLM_MODEL",
                "google/gemma-4-31B-it-qat-w4a16-ct",
            ),
            llm_timeout_sec=_float("SHUSHUNYA_CORE_LLM_TIMEOUT_SEC", 240.0, 1.0),
            abaddon_base_url=os.environ.get("SHUSHUNYA_CORE_ABADDON_URL", "http://127.0.0.1:7000").rstrip("/"),
            archive_base_url=os.environ.get("SHUSHUNYA_CORE_ARCHIVE_URL", "http://127.0.0.1:8090").rstrip("/"),
            administratum_base_url=os.environ.get("SHUSHUNYA_CORE_ADMINISTRATUM_URL", "http://127.0.0.1:7300").rstrip("/"),
            vox_base_url=os.environ.get("SHUSHUNYA_CORE_VOX_URL", "http://127.0.0.1:7400").rstrip("/"),
            warpwails_base_url=os.environ.get("SHUSHUNYA_CORE_WARPWAILS_URL", "http://127.0.0.1:7500").rstrip("/"),
            steward_interval_sec=_float("SHUSHUNYA_CORE_STEWARD_INTERVAL_SEC", 15.0, 1.0),
            organ_health_ttl_sec=_float("SHUSHUNYA_CORE_ORGAN_HEALTH_TTL_SEC", 15.0, 1.0),
            effect_lease_sec=_float("SHUSHUNYA_CORE_EFFECT_LEASE_SEC", 300.0, 10.0),
            # The temporary one-GPU 31B profile is served with 6144 tokens.
            # Measurements through its tokenizer leave a safe repair-pass
            # margin at roughly 2800 serialized situation chars + 1200 output.
            context_char_budget=_int("SHUSHUNYA_CORE_CONTEXT_CHAR_BUDGET", 2_800, 1_800),
        )
