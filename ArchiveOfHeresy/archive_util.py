"""Pure namespace, memory-text, and small utility helpers for ArchiveOfHeresy."""
import re
from pathlib import Path

from archive_config import *  # noqa: F401,F403
from archivist_agent.agent import WikiBookshelf


def safe_memory_namespace(value):
    raw = str(value or "default").strip().lower()
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in raw).strip("-_")
    return safe[:64] or "default"


def shared_memory_namespace(value=None):
    namespace = safe_memory_namespace(value or SHARED_MEMORY_NAMESPACE)
    if namespace in LEGACY_SHARED_MEMORY_NAMESPACES:
        return safe_memory_namespace(SHARED_MEMORY_NAMESPACE)
    return namespace


def shared_chat_session_id(_value=None):
    return safe_chat_session_id(SHARED_CHAT_SESSION_ID)


def focus_root_for_namespace(namespace):
    namespace = safe_memory_namespace(namespace)
    if namespace == "default":
        return FOCUS_ROOT
    return FOCUS_ROOT / "namespaces" / namespace


def graph_root_for_namespace(namespace):
    namespace = safe_memory_namespace(namespace)
    if namespace == "default":
        return GRAPH_ROOT
    return GRAPH_ROOT / "namespaces" / namespace


def wiki_root_for_namespace(namespace):
    namespace = safe_memory_namespace(namespace)
    if namespace == "default":
        return WIKI_ROOT
    return WIKI_ROOT / "namespaces" / namespace


def existing_child_namespaces(root):
    namespace_root = Path(root) / "namespaces"
    if not namespace_root.exists():
        return set()
    return {
        safe_memory_namespace(path.name)
        for path in namespace_root.iterdir()
        if path.is_dir() and safe_memory_namespace(path.name) != "default"
    }


def known_memory_namespaces():
    namespaces = {"default"}
    namespaces.update(FOCUS_COMPONENTS.keys())
    namespaces.update(GRAPH_COMPONENTS.keys())
    namespaces.update(existing_child_namespaces(FOCUS_ROOT))
    namespaces.update(existing_child_namespaces(WIKI_ROOT))
    namespaces.update(existing_child_namespaces(GRAPH_ROOT))
    return sorted(namespaces)


def memory_namespace_exists(namespace):
    return safe_memory_namespace(namespace) in set(known_memory_namespaces())


def wiki_bookshelf_for_namespace(namespace):
    return WikiBookshelf(wiki_root_for_namespace(namespace))


def find_focus(index, focus_id=None, active=False):
    target_id = index.get("active_id") if active else focus_id
    for focus in index.get("files", []):
        if focus.get("id") == target_id:
            return focus
    return None


def memory_tokens(value):
    return {token.lower() for token in TOKEN_RE.findall(str(value or "")) if len(token) > 1}


def memory_chargrams(value, size=3):
    grams = set()
    for token in memory_tokens(value):
        if len(token) < size + 1:
            continue
        for index in range(0, len(token) - size + 1):
            grams.add(token[index : index + size])
    return grams


def memory_overlap_score(query_tokens, value):
    if not query_tokens:
        return 0.0
    target = memory_tokens(value)
    token_score = 0.0
    if target:
        token_score = len(query_tokens & target) / max(1, min(len(query_tokens), len(target)))
    query_grams = set()
    for token in query_tokens:
        if len(token) < 4:
            continue
        for index in range(0, len(token) - 2):
            query_grams.add(token[index : index + 3])
    target_grams = memory_chargrams(value)
    gram_score = 0.0
    if query_grams and target_grams:
        gram_score = len(query_grams & target_grams) / max(1, min(len(query_grams), len(target_grams)))
    return max(token_score, gram_score * 0.75)


def trim_memory_text(value, limit=1200):
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n..."


def parse_max_chars(value, default=12000, upper=50000):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1000, min(parsed, upper))


def gateway_book_payload(content, max_chars):
    content = str(content or "")
    if len(content) <= max_chars:
        return {"content": content, "content_chars": len(content), "truncated": False, "max_chars": max_chars}
    return {
        "content": content[:max_chars].rstrip() + "\n...",
        "content_chars": len(content),
        "truncated": True,
        "max_chars": max_chars,
    }


def focus_summary_text(content):
    text = str(content or "").strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2].strip()
    if "## Last Exchange" in text:
        text = text.split("## Last Exchange", 1)[0].strip()
    return text


def compact_vector_matches(matches, include_content=False):
    compacted = []
    for match in matches:
        item = dict(match)
        content = str(item.pop("content", "") or "")
        if include_content:
            item["content"] = trim_memory_text(content, 1200)
        else:
            item["excerpt"] = trim_memory_text(content, 360)
        compacted.append(item)
    return compacted


def parse_search_layers(value):
    raw = str(value or "").strip().lower()
    if not raw or raw == "all":
        return sorted(GATEWAY_SEARCH_LAYERS)
    layers = []
    for item in raw.split(","):
        layer = item.strip()
        if not layer:
            continue
        if layer not in GATEWAY_SEARCH_LAYERS:
            raise ValueError(f"unsupported memory search layer: {layer}")
        if layer not in layers:
            layers.append(layer)
    if not layers:
        return sorted(GATEWAY_SEARCH_LAYERS)
    return layers


def internal_flag(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def text_from_content(content):
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return str(content or "").strip()

    parts = []
    image_count = 0
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
        elif item_type == "image_url":
            image_count += 1
    if image_count:
        parts.append(f"[Пользователь приложил изображение: {image_count} шт. Содержимое изображения доступно Шушуне, но не анализируется Magos/памятью.]")
    return "\n".join(parts).strip()


def trim_chat_text(text, limit=CHAT_MESSAGE_CHARS):
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def safe_chat_session_id(value):
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_.:@-]+", "-", str(value or "default").strip())
    cleaned = cleaned.strip(".-:_@")
    return cleaned[:120] or "default"
