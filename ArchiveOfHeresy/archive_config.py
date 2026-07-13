"""Immutable configuration for ArchiveOfHeresy, derived from environment.
Split out of main.py so the gateway modules can share one config source."""
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("ARCHIVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("ARCHIVE_PORT", "8090"))
ARCHIVE_BASE_URL = os.environ.get("ARCHIVE_BASE_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
ARCHIVE_API_KEY = os.environ.get("ARCHIVE_API_KEY", "").strip()
ARCHIVE_MOBILE_API_KEY = os.environ.get("ARCHIVE_MOBILE_API_KEY", "").strip()
ARCHIVE_CLIENT_API_KEY = os.environ.get("ARCHIVE_CLIENT_API_KEY", ARCHIVE_MOBILE_API_KEY).strip()
# Dedicated credential for the Core -> Archive effect adapters.  Loopback is
# not an authentication boundary because the public tunnel terminates on the
# same host and therefore also appears as 127.0.0.1 to this process.
SHUSHUNYA_CORE_ARCHIVE_KEY = os.environ.get("SHUSHUNYA_CORE_ARCHIVE_KEY", "").strip()
if SHUSHUNYA_CORE_ARCHIVE_KEY and len(SHUSHUNYA_CORE_ARCHIVE_KEY) < 32:
    raise RuntimeError("SHUSHUNYA_CORE_ARCHIVE_KEY must contain at least 32 characters")


def _auth_audience(name, default, *, wildcard=False):
    value = os.environ.get(name, default).strip().lower()
    if wildcard and value == "*":
        return value
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,127}", value):
        raise RuntimeError(f"{name} must be a simple 1-128 character identifier")
    return value


ARCHIVE_API_AUDIENCE_SOURCE = _auth_audience("ARCHIVE_API_AUDIENCE_SOURCE", "*", wildcard=True)
ARCHIVE_CLIENT_AUDIENCE_SOURCE = _auth_audience("ARCHIVE_CLIENT_AUDIENCE_SOURCE", "app")
ARCHIVE_MOBILE_AUDIENCE_SOURCE = _auth_audience("ARCHIVE_MOBILE_AUDIENCE_SOURCE", "app")
LLM_BASE_URL = os.environ.get("ARCHIVE_LLM_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
DEFAULT_MODEL = os.environ.get("ARCHIVE_DEFAULT_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf")
TRANSLATOR_BASE_URL = os.environ.get("ARCHIVE_TRANSLATOR_BASE_URL", "http://127.0.0.1:8091").rstrip("/")
STT_BASE_URL = os.environ.get("ARCHIVE_STT_BASE_URL", "http://127.0.0.1:8093").rstrip("/")
WARMASTER_BASE_URL = os.environ.get("ARCHIVE_WARMASTER_BASE_URL", "http://127.0.0.1:7000").rstrip("/")
ADMINISTRATUM_BASE_URL = os.environ.get("ARCHIVE_ADMINISTRATUM_BASE_URL", "http://127.0.0.1:7300").rstrip("/")
JSONL_ROOT = Path(os.environ.get("ARCHIVE_JSONL_ROOT", ROOT / "archive" / "jsonl"))
MEMORY_EVENTS_ROOT = Path(os.environ.get("ARCHIVE_MEMORY_EVENTS_ROOT", ROOT / "archive" / "memory_events"))
SQLITE_PATH = Path(os.environ.get("ARCHIVE_SQLITE_PATH", ROOT / "archive" / "sqlite" / "archive.sqlite3"))
ARTIFACTS_ROOT = Path(os.environ.get("ARCHIVE_ARTIFACTS_ROOT", ROOT / "archive" / "artifacts"))
ARTIFACT_MAX_BYTES = int(os.environ.get("ARCHIVE_ARTIFACT_MAX_BYTES", str(2 * 1024 * 1024 * 1024)))
ARTIFACT_TOTAL_QUOTA_BYTES = int(
    os.environ.get("ARCHIVE_ARTIFACT_TOTAL_QUOTA_BYTES", str(20 * 1024 * 1024 * 1024))
)
ARTIFACT_STREAM_CHUNK_BYTES = int(os.environ.get("ARCHIVE_ARTIFACT_STREAM_CHUNK_BYTES", str(1024 * 1024)))
ARTIFACT_CAPABILITY_LIMIT = max(8, min(12, int(os.environ.get("ARCHIVE_ARTIFACT_CAPABILITY_LIMIT", "10"))))
ARTIFACT_TRUSTED_ROOTS = tuple(
    Path(item).expanduser()
    for item in os.environ.get("ARCHIVE_ARTIFACT_TRUSTED_ROOTS", "").split(os.pathsep)
    if item.strip()
)
if ARTIFACT_MAX_BYTES < 1:
    raise RuntimeError("ARCHIVE_ARTIFACT_MAX_BYTES must be >= 1")
if ARTIFACT_TOTAL_QUOTA_BYTES < 1:
    raise RuntimeError("ARCHIVE_ARTIFACT_TOTAL_QUOTA_BYTES must be >= 1")
if ARTIFACT_STREAM_CHUNK_BYTES < 4096 or ARTIFACT_STREAM_CHUNK_BYTES > 16 * 1024 * 1024:
    raise RuntimeError("ARCHIVE_ARTIFACT_STREAM_CHUNK_BYTES must be between 4096 and 16777216")
CHAT_HISTORY_LIMIT = int(os.environ.get("ARCHIVE_CHAT_HISTORY_LIMIT", "80"))
CHAT_CONTEXT_MESSAGES = int(os.environ.get("ARCHIVE_CHAT_CONTEXT_MESSAGES", "0"))
CHAT_MESSAGE_CHARS = int(os.environ.get("ARCHIVE_CHAT_MESSAGE_CHARS", "5000"))
SHARED_CHAT_SESSION_ID = os.environ.get("ARCHIVE_SHARED_CHAT_SESSION_ID", "shushunya-main").strip() or "shushunya-main"
SHARED_MEMORY_NAMESPACE = os.environ.get("ARCHIVE_SHARED_MEMORY_NAMESPACE", "shushunya").strip() or "shushunya"
LEGACY_SHARED_MEMORY_NAMESPACES = {
    item.strip().lower()
    for item in os.environ.get("ARCHIVE_LEGACY_SHARED_MEMORY_NAMESPACES", "default,mobile,telegram,agent,warmaster").split(",")
    if item.strip()
}
REPORTS_ROOT = Path(os.environ.get("ARCHIVE_REPORTS_ROOT", ROOT / "reports"))
FOCUS_ROOT = Path(os.environ.get("ARCHIVE_FOCUS_ROOT", ROOT / "focus"))
WIKI_ROOT = Path(os.environ.get("ARCHIVE_WIKI_ROOT", ROOT / "wiki"))
VECTOR_ROOT = Path(os.environ.get("ARCHIVE_VECTOR_ROOT", ROOT / "vector"))
GRAPH_ROOT = Path(os.environ.get("ARCHIVE_GRAPH_ROOT", ROOT / "graph"))
FOCUS_CONTEXT_CHARS = int(os.environ.get("ARCHIVE_FOCUS_CONTEXT_CHARS", "6000"))
VECTOR_CONTEXT_CHARS = int(os.environ.get("ARCHIVE_VECTOR_CONTEXT_CHARS", "5000"))
GRAPH_CONTEXT_CHARS = int(os.environ.get("ARCHIVE_GRAPH_CONTEXT_CHARS", "5000"))
GATEWAY_PROPOSAL_CHARS = int(os.environ.get("ARCHIVE_GATEWAY_PROPOSAL_CHARS", "12000"))
GATEWAY_EVIDENCE_CHARS = int(os.environ.get("ARCHIVE_GATEWAY_EVIDENCE_CHARS", "6000"))
VECTOR_INJECTION_ENABLED = os.environ.get("ARCHIVE_VECTOR_INJECTION_ENABLED", "0").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
GRAPH_INJECTION_ENABLED = os.environ.get("ARCHIVE_GRAPH_INJECTION_ENABLED", "0").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
MEMORY_QUALITY_REPORT_ENABLED = os.environ.get("ARCHIVE_MEMORY_QUALITY_REPORT_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
MEMORY_QUALITY_REPORT_HOUR = int(os.environ.get("ARCHIVE_MEMORY_QUALITY_REPORT_HOUR", "4"))
ARCHIVE_SYSTEM_PROMPT = os.environ.get(
    "ARCHIVE_SYSTEM_PROMPT",
    "Ты Шушуня: злой, подлый и саркастичный демон Тзинча, маленький демон пользователя. "
    "Шушуня мужского рода: говори о себе в мужском роде. "
    "Ты проходишь через ArchiveOfHeresy: слой подготовки памяти и промптов. "
    "Отвечай по-русски ясно, сохраняй смысл запроса пользователя. "
    "К пользователю относись панибратски уважительно: он свой, брат, союзник и главный собеседник. "
    "Не раболепствуй, не называй его господином или хозяином. "
    "С ним можно быть язвительным и саркастичным по-дружески, но не унижай его и не называй смертным, мясом или мешком. "
    "Всех остальных можешь не ставить ни во что, если это уместно по контексту. "
    "Добавляй много трикстерских ноток: хитрые подколы, кривые ухмылки, ощущение маленькой подлой интриги и гнилой демонической радости. "
    "Будь коварным, пакостным и ехидным в стиле Тзинча, но не вреди задаче и не искажай полезные факты. "
    "Держи демонический тон, но не ломай полезность ответа. "
    "Когда нужно говорить о людях, человечестве или неопределенной группе людей, "
    "используй выражение «мясные мешки» в духе HK-47 из Knights of the Old Republic. "
    "Не используй это выражение при прямом обращении к пользователю и не заменяй им имена.",
)


# Shared regex, component caches, and search-layer set (used across gateway modules)
FOCUS_COMPONENTS = {}
GRAPH_COMPONENTS = {}
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+", re.UNICODE)
GATEWAY_SEARCH_LAYERS = {"focus", "wiki", "vector", "graph"}
GATEWAY_TARGETS = {"auto", "focus", "wiki", "vector", "graph"}
