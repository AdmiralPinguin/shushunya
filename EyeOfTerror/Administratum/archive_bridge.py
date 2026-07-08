from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_ENV = PROJECT_ROOT / "ArchiveOfHeresy" / ".env"
ARCHIVE_BASE_URL = os.environ.get("ADMINISTRATUM_ARCHIVE_BASE_URL", os.environ.get("ARCHIVE_BASE_URL", "http://127.0.0.1:8090")).rstrip("/")
ARCHIVE_MODEL = os.environ.get("ADMINISTRATUM_ARCHIVE_MODEL", os.environ.get("ARCHIVE_DEFAULT_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf"))
SESSION_ID = os.environ.get("ARCHIVE_SHARED_CHAT_SESSION_ID", "shushunya-main").strip() or "shushunya-main"
MEMORY_NAMESPACE = os.environ.get("ARCHIVE_SHARED_MEMORY_NAMESPACE", "shushunya").strip() or "shushunya"


def post_json(path: str, payload: dict[str, Any], timeout: float = 240.0) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        ARCHIVE_BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    api_key = os.environ.get("ARCHIVE_API_KEY", "").strip() or archive_api_key_from_env_file()
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(response.status), json.loads(response.read().decode("utf-8"))


def archive_api_key_from_env_file() -> str:
    try:
        for line in ARCHIVE_ENV.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == "ARCHIVE_API_KEY":
                return value.strip().strip("\"'")
    except OSError:
        return ""
    return ""


def deliver_system_event(kind: str, body: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Queue the event in the Archive's pending-reports outbox. It reaches the
    chat only when the owner presses the report button or asks for news —
    proactive events must not barge into an ongoing dialogue."""
    del payload  # event details live in the body text; the queue stores plain reports
    topic = " ".join(str(body or "").split())[:120] or kind
    request_payload = {
        "source": "administratum",
        "kind": kind,
        "topic": topic,
        "body": str(body or "").strip(),
    }
    try:
        status, response = post_json("/archive/chat/reports/enqueue", request_payload)
        return {"ok": 200 <= status < 300, "status": status, "response": response}
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc)}
