from __future__ import annotations

import os
import json
import socket
import urllib.request
from pathlib import Path

from .config import PROJECT_ROOT, Settings


def main() -> int:
    settings = Settings.from_env()
    if not PROJECT_ROOT.is_dir():
        raise SystemExit(f"project root is missing: {PROJECT_ROOT}")
    if settings.db_path.exists() and settings.db_path.is_symlink():
        raise SystemExit("SHUSHUNYA_CORE_DB must not be a symlink")
    parent = settings.db_path.parent
    if not parent.is_dir():
        raise SystemExit(f"runtime directory is missing: {parent}")
    resolved_parent = parent.resolve()
    runtime_root = (PROJECT_ROOT / "runtime").resolve()
    if runtime_root not in {resolved_parent, *resolved_parent.parents}:
        raise SystemExit(f"runtime DB must stay below {runtime_root}")
    if not os.access(parent, os.W_OK):
        raise SystemExit(f"runtime directory is not writable: {parent}")
    try:
        with urllib.request.urlopen(f"{settings.llm_base_url}/models", timeout=5) as response:
            models_payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise SystemExit(f"LLM dispatcher is not ready: {exc}") from exc
    models = models_payload.get("data") if isinstance(models_payload, dict) else []
    selected = next(
        (item for item in models if isinstance(item, dict) and str(item.get("id") or "") == settings.llm_model),
        None,
    )
    if not selected:
        available = sorted(str(item.get("id") or "") for item in models if isinstance(item, dict))
        raise SystemExit(f"required LLM model is absent: {settings.llm_model}; available={available}")
    max_model_len = int(selected.get("max_model_len") or 0)
    if max_model_len and max_model_len <= 6_144 and settings.context_char_budget > 3_000:
        raise SystemExit(
            f"context char budget {settings.context_char_budget} is unsafe for max_model_len={max_model_len}; "
            "temporary 31B profile permits at most 3000 chars with the 1200-token response reserve"
        )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex((settings.host, settings.port)) == 0:
            raise SystemExit(f"port {settings.port} is already occupied")
    print(f"ShushunyaCore preflight OK: {settings.host}:{settings.port}, db={settings.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
