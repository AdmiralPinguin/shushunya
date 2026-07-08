"""ArchiveOfHeresy operations: memory search/context, chat, storage, mobile,
and maintenance. Uses shared singletons via archive_state."""
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import archive_state
from archive_config import *  # noqa: F401,F403
from archive_httpio import *  # noqa: F401,F403
from archive_util import *  # noqa: F401,F403
from semantic_memory import SEMANTIC_MIN_SCORE, semantic_scores
from archive_state import (ARCHIVE_LOCK, CHAT_QUEUE_LOCK, CHAT_QUEUE_WAIT_TIMEOUT_SEC, ChatQueueBusy,
    MAINTENANCE_LOCK, MOBILE_JOB_LOCK, TimedChatQueueLock)
from archivist_agent import Librarian
from archivist_agent.agent import FocusBookshelf, WikiBookshelf
from archivist_agent.graph_memory import GRAPH_TOP_K, GraphMemory
from archivist_agent.magos_agent import MAGOS_CONTEXT_LAYERS, MAGOS_EXTRA_NAMESPACES, Magos
from archivist_agent.quality_report import generate_quality_report
from archivist_agent.vector_memory import VECTOR_TOP_K, VectorMemory, latest_user_message
from turn_protocol import (
    build_turn_decision_request,
    capability_contract_message,
    normalize_turn_decision,
    turn_capability_manifest,
    warmaster_request_to_message,
)
from pending_reports import (
    enqueue_report,
    mark_delivered,
    pending_reports,
    pending_summary,
    task_roster_note,
    phone_announce,
    register_push_token,
    judge_conveyed,
    pending_topics_note,
    reports_event_text,
)

try:
    from EyeOfTerror.Administratum.intent_parser import (
        administratum_payload_from_intent,
        build_intent_detection_request,
        normalize_intent,
    )
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from EyeOfTerror.Administratum.intent_parser import (
        administratum_payload_from_intent,
        build_intent_detection_request,
        normalize_intent,
    )


PERSONA_PAGE_ORDER = [
    ("persona-core", "Persona Core"),
    ("voice-style", "Voice Style"),
    ("master-profile", "Master Profile"),
    ("relationship-journal", "Relationship Journal"),
    ("standing-rules", "Standing Rules"),
]


def allow_gateway_namespace(handler, namespace, create=False):
    namespace = safe_memory_namespace(namespace)
    if create or memory_namespace_exists(namespace):
        return True
    write_json(
        handler,
        404,
        {
            "error": "Memory namespace not found",
            "memory_namespace": namespace,
            "known_namespaces": known_memory_namespaces(),
            "hint": "Use create=1 only when intentionally opening a new read namespace, or submit a proposal/chat turn to create memory through the librarian.",
        },
    )
    return False


def vector_stats(memory_namespace):
    if archive_state.VECTOR_MEMORY is None or not archive_state.VECTOR_MEMORY.db_path.exists():
        return {"chunks": 0, "turns": 0, "embedding": {}}
    with sqlite3.connect(archive_state.VECTOR_MEMORY.db_path) as db:
        row = db.execute(
            """
            SELECT count(*) AS chunks, count(DISTINCT turn_id) AS turns
            FROM vector_chunks
            WHERE memory_namespace = ?
            """,
            (memory_namespace,),
        ).fetchone()
    return {
        "chunks": int(row[0] or 0),
        "turns": int(row[1] or 0),
        "embedding": archive_state.VECTOR_MEMORY.embedding_status(),
    }


def graph_stats(memory_namespace):
    graph_memory = graph_memory_for_namespace(memory_namespace)
    if graph_memory is None or not graph_memory.db_path.exists():
        return {"nodes": 0, "edges": 0}
    with sqlite3.connect(graph_memory.db_path) as db:
        nodes = int(db.execute("SELECT count(*) FROM graph_nodes").fetchone()[0] or 0)
        edges = int(db.execute("SELECT count(*) FROM graph_edges").fetchone()[0] or 0)
    return {"nodes": nodes, "edges": edges}


def wiki_search(memory_namespace, query, limit=5):
    query_tokens = memory_tokens(query)
    if not query_tokens:
        return []
    bookshelf = wiki_bookshelf_for_namespace(memory_namespace)
    index = bookshelf.load_index()
    candidates = []
    for page in index.get("pages", []):
        content = bookshelf.read_page(page)
        text = " ".join([str(page.get("title") or ""), str(page.get("kind") or ""), content])
        candidates.append((page, content, text))
    semantic = semantic_scores(query, [(str(page.get("id")), text[:600]) for page, _c, text in candidates])
    matches = []
    for page, content, text in candidates:
        lexical = memory_overlap_score(query_tokens, text)
        if lexical > 0:
            score = lexical + 1.0  # lexical match: precise, always ranks above pure-semantic recall
        elif semantic is not None and (semantic.get(str(page.get("id"))) or 0.0) >= SEMANTIC_MIN_SCORE:
            score = semantic[str(page.get("id"))] - SEMANTIC_MIN_SCORE  # pure paraphrase recall, below any lexical hit
        else:
            continue
        matches.append(
            {
                "score": score,
                "id": page.get("id"),
                "title": page.get("title"),
                "kind": page.get("kind"),
                "importance": page.get("importance"),
                "updated_at": page.get("updated_at"),
                "excerpt": trim_memory_text(content, 1400),
            }
        )
    matches.sort(key=lambda item: (-item["score"], -int(item.get("importance") or 0), item.get("updated_at") or ""))
    return matches[:limit]


def focus_search(memory_namespace, query, limit=5):
    query_tokens = memory_tokens(query)
    if not query_tokens:
        return []
    bookshelf = focus_components(memory_namespace)["bookshelf"]
    index = bookshelf.load_index()
    candidates = []
    for focus in index.get("files", []):
        content = bookshelf.read_focus(focus)
        summary = focus_summary_text(content)
        text = " ".join([str(focus.get("title") or ""), str(focus.get("status") or ""), summary])
        candidates.append((focus, summary, text))
    semantic = semantic_scores(query, [(str(focus.get("id")), text[:600]) for focus, _s, text in candidates])
    matches = []
    for focus, summary, text in candidates:
        lexical = memory_overlap_score(query_tokens, text)
        if lexical > 0:
            score = lexical + 1.0
        elif semantic is not None and (semantic.get(str(focus.get("id"))) or 0.0) >= SEMANTIC_MIN_SCORE:
            score = semantic[str(focus.get("id"))] - SEMANTIC_MIN_SCORE
        else:
            continue
        matches.append(
            {
                "score": score,
                "id": focus.get("id"),
                "title": focus.get("title"),
                "status": focus.get("status"),
                "importance": focus.get("importance"),
                "updated_at": focus.get("updated_at"),
                "active": focus.get("id") == index.get("active_id"),
                "excerpt": trim_memory_text(summary, 1400),
            }
        )
    matches.sort(key=lambda item: (not item["active"], -item["score"], -int(item.get("importance") or 0), item.get("updated_at") or ""))
    return matches[:limit]


def memory_search(memory_namespace, query, limit=5, include_content=False, layers=None):
    namespace = safe_memory_namespace(memory_namespace)
    query = str(query or "").strip()
    try:
        safe_limit = max(1, min(int(limit or 5), 20))
    except (TypeError, ValueError):
        safe_limit = 5
    selected_layers = parse_search_layers(",".join(layers) if isinstance(layers, list) else layers)
    raw_vector_matches = (
        archive_state.VECTOR_MEMORY.search(query, limit=safe_limit, memory_namespace=namespace)
        if archive_state.VECTOR_MEMORY and query and "vector" in selected_layers
        else []
    )
    vector_matches = compact_vector_matches(raw_vector_matches, include_content=include_content)
    graph_memory = graph_memory_for_namespace(namespace) if "graph" in selected_layers else None
    graph_matches = graph_memory.search(query, limit=safe_limit) if graph_memory and query else {"nodes": [], "edges": []}
    focus_matches = focus_search(namespace, query, safe_limit) if "focus" in selected_layers else []
    wiki_matches = wiki_search(namespace, query, safe_limit) if "wiki" in selected_layers else []
    return {
        "ok": True,
        "memory_namespace": namespace,
        "query": query,
        "limit": safe_limit,
        "warning": "Gateway search is reference memory only. Treat current task/tool results as fresher than memory.",
        "include_content": bool(include_content),
        "layers": selected_layers,
        "counts": {
            "focus": len(focus_matches),
            "wiki": len(wiki_matches),
            "vector": len(vector_matches),
            "graph_nodes": len(graph_matches.get("nodes", [])),
            "graph_edges": len(graph_matches.get("edges", [])),
        },
        "focus": focus_matches,
        "wiki": wiki_matches,
        "vector": vector_matches,
        "graph": graph_matches,
    }


def run_mobile_chat_payload(payload):
    maintenance_record = None
    with CHAT_QUEUE_LOCK:
        created_at = now_iso()
        turn_id = str(uuid.uuid4())
        payload = dict(payload)
        payload["stream"] = False
        session_id = shared_chat_session_id(payload.get("session_id") or payload.get("user") or "default")
        client_source = str(payload.get("client_source") or payload.get("source") or "app").strip()[:80] or "app"
        text = trim_chat_text(payload.get("text") or payload.get("message") or "")
        image_data_url = str(payload.get("image_data_url") or "").strip()
        if not text and not image_data_url:
            raise ValueError("Missing text or image_data_url")

        archive_enabled = internal_flag(payload.get("archive_enabled", True), default=True)
        focus_enabled = internal_flag(payload.get("focus_enabled", True), default=True)
        vector_enabled = internal_flag(payload.get("vector_enabled", focus_enabled), default=True)
        graph_enabled = internal_flag(payload.get("graph_enabled", focus_enabled), default=True)
        archive_system_prompt_enabled = internal_flag(payload.get("archive_system_prompt_enabled", True), default=True)
        memory_namespace = shared_memory_namespace(payload.get("memory_namespace"))
        model = payload.get("model") or DEFAULT_MODEL
        system_prompt = ""
        max_tokens = int(payload.get("max_tokens") or 2048)
        temperature = float(payload.get("temperature") or 0.4)
        turn_capabilities = payload.get("turn_capabilities") if isinstance(payload.get("turn_capabilities"), dict) else turn_capability_manifest(image_attached=bool(image_data_url))
        turn_decision = payload.get("turn_decision") if isinstance(payload.get("turn_decision"), dict) else {"action": "answer_in_chat"}
        forced_chat_reply = trim_chat_text(payload.get("forced_chat_reply") or "")

        request_messages = messages_for_chat_context(session_id, system_prompt, text, image_data_url=image_data_url)
        request_messages.insert(0, capability_contract_message(turn_capabilities, turn_decision))
        append_chat_message(
            session_id,
            "user",
            text if not image_data_url else f"{text}\n[image attached server-side]",
            created_at=created_at,
            source=client_source,
        )
        if forced_chat_reply:
            assistant = {"role": "assistant", "content": forced_chat_reply}
            append_chat_message(session_id, "assistant", forced_chat_reply, source=client_source)
            response = {
                "object": "chat.completion",
                "model": "archive-turn-protocol",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "turn_protocol_reply",
                        "message": assistant,
                    }
                ],
            }
            record = {
                "turn_id": turn_id,
                "created_at": created_at,
                "source": f"{client_source}-chat-session",
                "conversation_id": session_id,
                "memory_namespace": memory_namespace,
                "archive_enabled": archive_enabled,
                "focus_enabled": focus_enabled,
                "vector_enabled": vector_enabled,
                "graph_enabled": graph_enabled,
                "archive_system_prompt_enabled": archive_system_prompt_enabled,
                "magos_enabled": False,
                "magos_result": None,
                "administratum_intent": None,
                "administratum_result": None,
                "turn_decision": turn_decision,
                "turn_capabilities": turn_capabilities,
                "prompt_diagnostics": {},
                "model": "archive-turn-protocol",
                "request": {
                    "session_id": session_id,
                    "client_source": client_source,
                    "text": text,
                    "has_image": bool(image_data_url),
                    "stream": False,
                },
                "prepared_messages": request_messages,
                "status": "ok",
                "http_status": 200,
                "response": response,
                "assistant_message": assistant,
                "error": None,
            }
            maybe_write_archives(record)
            return {"ok": True, "session_id": session_id, "response": response, "message": forced_chat_reply}
        administratum_intent = None
        administratum_result = None
        administratum_message = None
        # One decision point: the turn controller gates task creation. The intent
        # parser runs only to STRUCTURE the task the controller already ordered —
        # not as a second brain on every message (that both doubled latency and
        # let the two models disagree about whether a task was created).
        if str(turn_decision.get("action") or "") == "create_administratum_task" and should_detect_administratum_intent(client_source, payload):
            administratum_intent = detect_administratum_intent(str(turn_decision.get("task") or "") or text, model=model)
            administratum_result = create_administratum_task_from_intent(administratum_intent, session_id, client_source)
            administratum_message = administratum_intent_context(administratum_result)
            if administratum_message is None:
                administratum_message = {
                    "role": "system",
                    "content": (
                        "Turn controller выбрал создание задачи Администратума, но структуратор не распознал в ней "
                        "task/watch. Задача НЕ создана — скажи владельцу честно и уточни, что именно записать."
                    ),
                }
        # Pending-reports outbox: on a deliver turn the queued reports are injected
        # in full and marked delivered after a successful answer; on ordinary turns
        # only a topics note is injected so Shushunya can mention news exists
        # without spilling the content uninvited.
        reports_message = None
        reports_to_deliver = []
        vox_on_tongue = []
        if str(turn_decision.get("action") or "") == "deliver_pending_reports":
            reports_to_deliver = pending_reports()
            if reports_to_deliver:
                reports_message = {"role": "system", "content": reports_event_text(reports_to_deliver)}
            else:
                reports_message = {
                    "role": "system",
                    "content": "Владелец спросил про новости, но у Шушуни ничего не накопилось. Скажи честно, что сказать нечего.",
                }
        elif not internal_flag(payload.get("system_event", False), default=False):
            reports_message = pending_topics_note(context_text=text)
            if reports_message:
                vox_on_tongue = reports_message.get("on_tongue") or []
        # Live task roster is always at hand on ordinary turns, so task status is
        # answered from truth (authoritative over stale focus/ack lines).
        roster_message = None
        if not internal_flag(payload.get("system_event", False), default=False):
            roster_message = task_roster_note()
        # When the roster carries live work, suppress the focus file: a focus that
        # narrates delegated work as "я собираю" is the stale crutch that fought
        # the truth. Topic knowledge still comes through Magos (vector/wiki).
        focus_for_prompt = focus_enabled and roster_message is None
        mobile_payload = {
            "model": model,
            "user": session_id,
            "archive_enabled": archive_enabled,
            "focus_enabled": focus_enabled,
            "vector_enabled": vector_enabled,
            "graph_enabled": graph_enabled,
            "archive_system_prompt_enabled": archive_system_prompt_enabled,
            "memory_namespace": memory_namespace,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
            "messages": request_messages,
        }
        memory_messages = sanitize_messages_for_memory(request_messages)
        magos_message = None
        magos_result = None
        magos = focus_components(memory_namespace)["magos"]
        if focus_enabled and magos is not None:
            try:
                magos_message = magos.prepare_request(
                    memory_messages,
                    model=model,
                    conversation_id=session_id,
                    turn_id=turn_id,
                    memory_namespace=memory_namespace,
                )
                magos_result = magos.last_result
            except Exception as exc:
                print(f"Magos hard fail-soft mobile chat job: {exc}", flush=True)
                magos_result = {"error": str(exc)}

        prepared_payload = dict(mobile_payload)
        prepared_payload["messages"] = prepare_messages(
            request_messages,
            include_focus=focus_for_prompt,
            include_vector=vector_enabled,
            include_graph=graph_enabled,
            include_system_prompt=archive_system_prompt_enabled,
            magos_message=magos_message,
            administratum_message=administratum_message,
            reports_message=reports_message,
            roster_message=roster_message,
            query_messages=memory_messages,
            memory_namespace=memory_namespace,
        )
        archive_prepared_messages = prepare_messages(
            memory_messages,
            include_focus=focus_for_prompt,
            include_vector=vector_enabled,
            include_graph=graph_enabled,
            include_system_prompt=archive_system_prompt_enabled,
            magos_message=magos_message,
            administratum_message=administratum_message,
            reports_message=reports_message,
            roster_message=roster_message,
            query_messages=memory_messages,
            memory_namespace=memory_namespace,
        )
        diagnostics = prompt_diagnostics(
            archive_prepared_messages,
            memory_messages,
            include_focus=focus_enabled,
            include_vector=vector_enabled,
            include_graph=graph_enabled,
            include_system_prompt=archive_system_prompt_enabled,
            magos_message=magos_message,
            memory_namespace=memory_namespace,
        )
        record = {
            "turn_id": turn_id,
            "created_at": created_at,
            "source": f"{client_source}-chat-session",
            "conversation_id": session_id,
            "memory_namespace": memory_namespace,
            "archive_enabled": archive_enabled,
            "focus_enabled": focus_enabled,
            "vector_enabled": vector_enabled,
            "graph_enabled": graph_enabled,
            "archive_system_prompt_enabled": archive_system_prompt_enabled,
            "magos_enabled": bool(magos_message),
            "magos_result": magos_result,
            "administratum_intent": administratum_intent,
            "administratum_result": administratum_result,
            "turn_decision": turn_decision,
            "turn_capabilities": turn_capabilities,
            "vox_on_tongue": vox_on_tongue,
            "prompt_diagnostics": diagnostics,
            "model": model,
            "request": {
                "session_id": session_id,
                "client_source": client_source,
                "text": text,
                "has_image": bool(image_data_url),
                "stream": False,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            "prepared_messages": archive_prepared_messages,
            "status": "pending",
            "http_status": None,
            "response": None,
            "assistant_message": None,
            "error": None,
        }
        try:
            status, response = proxy_json("POST", "/v1/chat/completions", payload=prepared_payload)
            assistant = assistant_message(response)
            if assistant:
                append_chat_message(session_id, "assistant", assistant.get("content") or "", source=client_source)
            record["status"] = "ok"
            record["http_status"] = status
            record["response"] = response
            record["assistant_message"] = assistant
            if reports_to_deliver and assistant:
                mark_delivered([report["id"] for report in reports_to_deliver])
            maybe_write_archives(record)
            maintenance_record = record
            return {"ok": True, "session_id": session_id, "response": response, "message": (assistant or {}).get("content", "")}
        except Exception as exc:
            record["status"] = "error"
            record["http_status"] = getattr(exc, "code", 500)
            record["error"] = str(exc)
            maybe_abandon_magos_focus(record)
            maybe_write_archives(record)
            raise
        finally:
            if maintenance_record is not None:
                # Post-answer memory maintenance must not sit inside the user's
                # wait: the answer is already persisted, so the librarian cycle
                # (and its periodic wiki/graph syncs) runs in the background.
                threading.Thread(
                    target=maybe_update_focus_memory,
                    args=(maintenance_record,),
                    daemon=True,
                    name=f"librarian-{maintenance_record.get('turn_id')}",
                ).start()


def memory_catalog(memory_namespace):
    namespace = safe_memory_namespace(memory_namespace)
    bookshelf = focus_components(namespace)["bookshelf"]
    focus_index = bookshelf.load_index()
    wiki_bookshelf = wiki_bookshelf_for_namespace(namespace)
    wiki_index = wiki_bookshelf.load_index()
    return {
        "memory_namespace": namespace,
        "gateway": {
            "read_endpoints": [
                "/archive/memory/catalog",
                "/archive/memory/gateway",
                "/archive/memory/focus",
                "/archive/memory/wiki",
                "/archive/memory/search",
                "/archive/vector/search",
                "/archive/graph/search",
                "/archive/memory/events",
            ],
            "write_endpoint": "/archive/memory/propose-change",
            "write_policy": "Agents propose changes; ArchiveOfHeresy records the proposal and the librarian decides how to update memory.",
        },
        "focus": bookshelf.catalog(focus_index),
        "wiki": wiki_bookshelf.catalog(wiki_index),
        "vector": vector_stats(namespace),
        "graph": graph_stats(namespace),
        "recent_events": recent_memory_events(limit=5, memory_namespace=namespace),
    }


def memory_gateway_manifest():
    return {
        "service": "ArchiveOfHeresy Memory Gateway",
        "version": 1,
        "base_url": ARCHIVE_BASE_URL,
        "auth": "Authorization: Bearer $ARCHIVE_API_KEY when ARCHIVE_API_KEY is configured",
        "known_namespaces": known_memory_namespaces(),
        "memory_quality_report": {
            "enabled": MEMORY_QUALITY_REPORT_ENABLED,
            "hour": MEMORY_QUALITY_REPORT_HOUR,
            "reports_root": str(REPORTS_ROOT),
        },
        "namespace_policy": {
            "shushunya": "shared user-facing persona memory for app, Telegram, default chat, and Warmaster final delivery",
            "default": "legacy alias mapped to shushunya for chat/proposal writes",
            "warmaster": "legacy alias mapped to shushunya for final delivery and task journal writes",
            "telegram": "legacy alias mapped to shushunya for chat/proposal writes",
            "mobile": "legacy alias mapped to shushunya for chat/proposal writes",
            "agent": "legacy alias mapped to shushunya for chat/proposal writes",
            "demonsforge": "DemonsForge forge memory; runtime SQLite stays outside long-term memory",
            "read_unknown_namespace": "rejected unless create=1 is passed intentionally",
            "write_unknown_namespace": "allowed only through chat/proposal paths that let the librarian create memory",
        },
        "magos_context_layers": sorted(MAGOS_CONTEXT_LAYERS),
        "direct_injection": {
            "vector": VECTOR_INJECTION_ENABLED,
            "graph": GRAPH_INJECTION_ENABLED,
        },
        "read_endpoints": {
            "catalog": "GET /archive/memory/catalog?namespace=warmaster&requester=name",
            "search": "GET /archive/memory/search?namespace=warmaster&q=query&limit=5&layers=focus,wiki,vector,graph&include_content=0&requester=name",
            "focus": "GET /archive/memory/focus?namespace=warmaster&id=active&max_chars=12000&requester=name",
            "wiki": "GET /archive/memory/wiki?namespace=warmaster&id=page-id&max_chars=12000&requester=name",
            "events": "GET /archive/memory/events?namespace=warmaster&limit=20&component=memory_gateway&event_action=search&requester=warmaster",
        },
        "search_layers": sorted(GATEWAY_SEARCH_LAYERS),
        "write_endpoints": {
            "proposal": "POST /archive/memory/propose-change",
            "proposal_policy": "Requester submits a proposal. ArchiveOfHeresy archives it and the librarian decides what to update.",
            "proposal_schema": {
                "namespace": "memory namespace, default",
                "requester": "agent or tool name",
                "target": sorted(GATEWAY_TARGETS),
                "importance": "integer 1..5",
                "proposal": "required string",
                "evidence": "optional string",
            },
        },
        "worker_actions": [
            "archive_memory_gateway",
            "archive_memory_catalog",
            "archive_memory_search",
            "archive_memory_read",
            "archive_memory_propose",
            "archive_memory_events",
        ],
        "rules": [
            "Do not read memory files directly from agents.",
            "Read memory through gateway endpoints.",
            "Do not write memory files directly from agents.",
            "Submit changes through /archive/memory/propose-change and let the librarian apply them.",
            "Treat gateway search results as reference memory; current tool results and current user request are fresher.",
            "Search defaults to compact snippets. Pass include_content=1 only when raw vector chunks are needed.",
            "Use layers=focus,wiki,vector,graph to restrict search scope when lower layers are too noisy.",
        ],
    }


def graph_memory_for_namespace(namespace):
    namespace = safe_memory_namespace(namespace)
    cached = GRAPH_COMPONENTS.get(namespace)
    if cached is not None:
        return cached
    graph_memory = GraphMemory(
        graph_root_for_namespace(namespace),
        proxy_json,
        SQLITE_PATH,
        memory_namespace=namespace,
    )
    GRAPH_COMPONENTS[namespace] = graph_memory
    return graph_memory


def focus_components(namespace):
    namespace = safe_memory_namespace(namespace)
    cached = FOCUS_COMPONENTS.get(namespace)
    if cached is not None:
        return cached
    root = focus_root_for_namespace(namespace)
    bookshelf = FocusBookshelf(root)
    librarian = Librarian(
        root,
        proxy_json,
        wiki_root=wiki_root_for_namespace(namespace),
        sqlite_path=SQLITE_PATH,
        vector_memory=archive_state.VECTOR_MEMORY,
        graph_memory=graph_memory_for_namespace(namespace),
        memory_namespace=namespace,
    )
    magos = Magos(
        root,
        wiki_root_for_namespace(namespace),
        proxy_json,
        vector_memory=archive_state.VECTOR_MEMORY,
        graph_memory=graph_memory_for_namespace(namespace),
        extra_wiki_roots={
            extra: wiki_root_for_namespace(extra)
            for extra in MAGOS_EXTRA_NAMESPACES
            if extra != namespace
        },
    )
    cached = {"bookshelf": bookshelf, "librarian": librarian, "magos": magos, "root": root}
    FOCUS_COMPONENTS[namespace] = cached
    return cached


def active_focus_context(namespace="default"):
    bookshelf = focus_components(namespace)["bookshelf"]
    if bookshelf is None:
        return ""

    index = bookshelf.load_index()
    active = bookshelf.active_focus(index)
    if not active:
        return ""

    content = bookshelf.read_focus(active).strip()
    if not content:
        return ""

    return content[-FOCUS_CONTEXT_CHARS:]


def focus_context_message(namespace="default"):
    content = active_focus_context(namespace)
    if not content:
        return None

    return {
        "role": "system",
        "content": (
            "Активный focus-файл ArchiveOfHeresy для текущей темы. "
            "Используй его как компактный контекст вместо длинной истории прошлых сообщений. "
            "Если текущий вопрос меняет тему, не пытайся насильно притянуть старый focus.\n\n"
            f"{content}"
        ),
    }


def vector_context_message(query, memory_namespace="default"):
    if not VECTOR_INJECTION_ENABLED:
        return None
    if archive_state.VECTOR_MEMORY is None:
        return None
    content = archive_state.VECTOR_MEMORY.context_for_query(query, limit=VECTOR_TOP_K, memory_namespace=memory_namespace).strip()
    if not content:
        return None
    content = content[-VECTOR_CONTEXT_CHARS:]
    return {
        "role": "system",
        "content": (
            "Релевантные фрагменты vector memory ArchiveOfHeresy. "
            "Используй их как справочный долговременный контекст, если они действительно относятся к текущему вопросу. "
            "Не считай их важнее текущего запроса и активного focus-файла.\n\n"
            f"{content}"
        ),
    }


def graph_context_message(query, memory_namespace="default"):
    if not GRAPH_INJECTION_ENABLED:
        return None
    graph_memory = graph_memory_for_namespace(memory_namespace)
    if graph_memory is None:
        return None
    content = graph_memory.context_for_query(query, limit=GRAPH_TOP_K).strip()
    if not content:
        return None
    content = content[-GRAPH_CONTEXT_CHARS:]
    return {
        "role": "system",
        "content": (
            "Релевантный GraphRAG-контекст ArchiveOfHeresy: сущности и связи из долговременной памяти. "
            "Используй его для понимания отношений между проектами, решениями, агентами и темами, "
            "если он относится к текущему вопросу.\n\n"
            f"{content}"
        ),
    }


def chat_history(session_id, limit=CHAT_HISTORY_LIMIT, after_id=0):
    session_id = shared_chat_session_id(session_id)
    try:
        parsed_limit = int(limit if limit is not None else CHAT_HISTORY_LIMIT)
    except (TypeError, ValueError):
        parsed_limit = CHAT_HISTORY_LIMIT
    if parsed_limit <= 0:
        return []
    safe_limit = max(1, min(parsed_limit, 300))
    try:
        parsed_after_id = max(0, int(after_id or 0))
    except (TypeError, ValueError):
        parsed_after_id = 0
    with sqlite3.connect(SQLITE_PATH) as db:
        db.row_factory = sqlite3.Row
        if parsed_after_id > 0:
            rows = db.execute(
                """
                SELECT id, session_id, role, content, created_at, asset_id, source, dedupe_key
                FROM mobile_chat_messages
                WHERE session_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (session_id, parsed_after_id, safe_limit),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT id, session_id, role, content, created_at, asset_id, source, dedupe_key
                FROM mobile_chat_messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, safe_limit),
            ).fetchall()
            rows = list(reversed(rows))
    return [
        {
            "id": row["id"],
            "session_id": row["session_id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"],
            "asset_id": row["asset_id"],
            "source": row["source"] if "source" in row.keys() else "unknown",
            "dedupe_key": row["dedupe_key"] if "dedupe_key" in row.keys() else None,
        }
        for row in rows
    ]


def append_chat_message(session_id, role, content, asset_id=None, created_at=None, source="unknown", dedupe_key=None):
    session_id = shared_chat_session_id(session_id)
    role = "assistant" if role == "assistant" else "user"
    content = trim_chat_text(content)
    created_at = created_at or now_iso()
    source = str(source or "unknown").strip()[:80] or "unknown"
    dedupe_key = str(dedupe_key or "").strip()[:160] or None
    with ARCHIVE_LOCK:
        with sqlite3.connect(SQLITE_PATH) as db:
            db.execute(
                """
                INSERT INTO mobile_chat_sessions (id, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (session_id, created_at, created_at),
            )
            db.execute(
                """
                INSERT OR IGNORE INTO mobile_chat_messages (session_id, role, content, created_at, asset_id, source, dedupe_key)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, role, content, created_at, asset_id, source, dedupe_key),
            )


def create_mobile_job(job_type, request_payload):
    job_id = f"{safe_chat_session_id(job_type)}-{uuid.uuid4().hex[:12]}"
    created_at = now_iso()
    with sqlite3.connect(SQLITE_PATH) as db:
        db.execute(
            """
            INSERT INTO mobile_jobs (id, type, status, created_at, updated_at, request_json, response_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, job_type, "queued", created_at, created_at, json.dumps(request_payload, ensure_ascii=False), None, None),
        )
    return job_id


def update_mobile_job(job_id, status, response=None, error=None):
    with sqlite3.connect(SQLITE_PATH) as db:
        db.execute(
            """
            UPDATE mobile_jobs
            SET status = ?, updated_at = ?, response_json = ?, error = ?
            WHERE id = ?
            """,
            (
                status,
                now_iso(),
                json.dumps(response, ensure_ascii=False) if response is not None else None,
                str(error) if error is not None else None,
                job_id,
            ),
        )


def mobile_job_snapshot(job_id):
    with sqlite3.connect(SQLITE_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            """
            SELECT id, type, status, created_at, updated_at, request_json, response_json, error
            FROM mobile_jobs
            WHERE id = ?
            """,
            (safe_chat_session_id(job_id),),
        ).fetchone()
    if not row:
        return {"ok": False, "error": "mobile job not found", "job_id": safe_chat_session_id(job_id)}
    response = None
    if row["response_json"]:
        try:
            response = json.loads(row["response_json"])
        except json.JSONDecodeError:
            response = {"raw": row["response_json"]}
    return {
        "ok": row["status"] not in {"failed"},
        "job_id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "running": row["status"] in {"queued", "running"},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "response": response,
        "error": row["error"],
    }


def run_mobile_job(job_id, worker):
    def _run():
        update_mobile_job(job_id, "running")
        try:
            response = worker()
            update_mobile_job(job_id, "done", response=response)
        except Exception as exc:
            update_mobile_job(job_id, "failed", error=exc)

    thread = threading.Thread(target=_run, name=f"mobile-job-{job_id}", daemon=True)
    thread.start()
    return thread


def messages_for_chat_context(session_id, system_prompt, user_text, image_data_url=None):
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": str(system_prompt)})
    if CHAT_CONTEXT_MESSAGES > 0:
        for item in chat_history(session_id, limit=CHAT_CONTEXT_MESSAGES):
            content = trim_chat_text(item.get("content") or "")
            if content:
                messages.append({"role": item.get("role") or "user", "content": content})
    user_content = user_text
    if image_data_url:
        user_content = [
            {
                "type": "text",
                "text": user_text or "Посмотри картинку и ответь по ней.",
            },
            {
                "type": "image_url",
                "image_url": {"url": image_data_url},
            },
        ]
    messages.append({"role": "user", "content": user_content})
    return messages


def prompt_diagnostics(
    prepared_messages,
    client_messages,
    include_focus=True,
    include_vector=True,
    include_graph=True,
    include_system_prompt=True,
    magos_message=None,
    memory_namespace="default",
):
    counters = {
        "total_messages": len(prepared_messages or []),
        "client_messages": len(client_messages or []),
        "client_history_messages": 0,
        "archive_system_prompt": 0,
        "persona": 0,
        "capability_contract": 0,
        "focus": 0,
        "magos": 0,
        "administratum": 0,
        "direct_vector": 0,
        "direct_graph": 0,
    }
    for message in prepared_messages or []:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if content.startswith("ArchiveOfHeresy identity context"):
            counters["archive_system_prompt"] += 1
            counters["persona"] += 1
        elif content.startswith("ArchiveOfHeresy capability contract"):
            counters["capability_contract"] += 1
        elif content.startswith("Ты Шушуня:"):
            counters["archive_system_prompt"] += 1
        elif content.startswith("Активный focus-файл ArchiveOfHeresy"):
            counters["focus"] += 1
        elif content.startswith("Magos memory context from ArchiveOfHeresy"):
            counters["magos"] += 1
        elif content.startswith("Administratum task created") or content.startswith("Administratum detected"):
            counters["administratum"] = counters.get("administratum", 0) + 1
        elif content.startswith("Релевантные фрагменты vector memory ArchiveOfHeresy"):
            counters["direct_vector"] += 1
        elif content.startswith("Релевантный GraphRAG-контекст ArchiveOfHeresy"):
            counters["direct_graph"] += 1

    client_count = len(client_messages or [])
    client_system = 1 if client_messages and client_messages[0].get("role") == "system" else 0
    counters["client_history_messages"] = max(0, client_count - client_system - 1)
    return {
        "memory_namespace": memory_namespace,
        "chat_context_messages_setting": CHAT_CONTEXT_MESSAGES,
        "requested": {
            "focus": bool(include_focus),
            "vector": bool(include_vector),
            "graph": bool(include_graph),
            "archive_system_prompt": bool(include_system_prompt),
            "magos": bool(magos_message),
        },
        "direct_injection_enabled": {
            "vector": VECTOR_INJECTION_ENABLED,
            "graph": GRAPH_INJECTION_ENABLED,
        },
        "counts": counters,
    }


def sanitize_messages_for_memory(messages):
    sanitized = []
    for message in messages or []:
        copy = dict(message)
        copy["content"] = text_from_content(copy.get("content"))
        sanitized.append(copy)
    return sanitized


def strip_wiki_frontmatter(content):
    text = str(content or "").strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2].strip()
    return text


def extract_json_object(text):
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    elif "{" in raw and "}" in raw:
        raw = raw[raw.find("{") : raw.rfind("}") + 1]
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("expected JSON object")
    return parsed


def append_persona_mission_ack(session_id, task_id, task_text=""):
    """Voice a short in-character confirmation that a mission went to work and
    append it to the shared chat. Runs in a background thread so delegation
    stays instant; the dry static line is replaced by Shushunya's own reply."""
    note = (
        "[Миссия принята в работу]\n"
        f"task_id: {task_id}\n"
        + (f"суть задачи: {trim_chat_text(task_text)[:300]}\n" if task_text else "")
        + "Подтверди владельцу одной-двумя фразами своим голосом, что ты взял это в работу и доложишь результат. "
        "По-русски. Не выдумывай прогресс, не задавай вопросов, не пересказывай задачу целиком."
    )
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [persona_page_context(shared_memory_namespace(None)), {"role": "user", "content": note}],
        "temperature": 0.5,
        "max_tokens": 220,
    }
    try:
        _status, response = proxy_json("POST", "/v1/chat/completions", payload=payload, timeout=120)
        content = str((((response.get("choices") or [{}])[0].get("message") or {}).get("content")) or "").strip()
    except Exception as exc:  # noqa: BLE001 - ack voicing must not break delegation
        print(f"Mission ack voicing failed for {task_id}: {exc}", flush=True)
        return
    if content:
        append_chat_message(
            shared_chat_session_id(session_id),
            "assistant",
            content,
            source="warmaster",
            dedupe_key=f"warmaster:{task_id}:accepted",
        )


def start_persona_mission_ack(session_id, task_id, task_text=""):
    if not str(task_id or "").strip():
        return
    threading.Thread(
        target=append_persona_mission_ack,
        args=(session_id, task_id, task_text),
        daemon=True,
        name=f"mission-ack-{task_id}",
    ).start()


def decide_chat_turn_action(session_id, text, image_data_url="", model=None):
    user_text = trim_chat_text(text)
    manifest = turn_capability_manifest(image_attached=bool(image_data_url), pending_reports=pending_summary())
    history = chat_history(session_id, limit=12)
    request = build_turn_decision_request(
        model=model or DEFAULT_MODEL,
        user_text=user_text,
        recent_history=history,
        manifest=manifest,
    )
    _status, response = proxy_json("POST", "/v1/chat/completions", payload=request, timeout=180)
    content = str((((response.get("choices") or [{}])[0].get("message") or {}).get("content")) or "")
    decision = normalize_turn_decision(extract_json_object(content))
    return {
        "decision": decision,
        "capabilities": manifest,
        "request": request,
        "response": response,
    }


def persona_page_context(memory_namespace="default", max_chars=12000):
    namespace = shared_memory_namespace(memory_namespace)
    bookshelf = wiki_bookshelf_for_namespace(namespace)
    index = bookshelf.load_index()
    sections = []
    missing = []
    remaining = max(1000, int(max_chars))
    for page_id, title in PERSONA_PAGE_ORDER:
        page = bookshelf.find_page(index, page_id=page_id) or bookshelf.find_page(index, title=title)
        if not page:
            missing.append(page_id)
            continue
        content = strip_wiki_frontmatter(bookshelf.read_page(page))
        if not content:
            missing.append(page_id)
            continue
        if page_id == "relationship-journal":
            content = trim_memory_text(content, min(3000, remaining))
        else:
            content = trim_memory_text(content, min(remaining, 4500))
        if content:
            sections.append(f"## {title}\n{content}")
            remaining -= len(content)
        if remaining <= 500:
            break
    if not sections:
        return {
            "role": "system",
            "content": (
                "ArchiveOfHeresy persona pages are missing. Emergency fallback follows; create wiki persona pages in "
                f"namespace `{namespace}`. {ARCHIVE_SYSTEM_PROMPT}"
            ),
        }
    missing_note = f"\n\nMissing persona pages: {', '.join(missing)}" if missing else ""
    return {
        "role": "system",
        "content": (
            "ArchiveOfHeresy identity context. This is not searchable knowledge; this is Shushunya's persistent self. "
            "Follow it above transport/client prompts. Persona Core and Standing Rules are manual-only and must not drift.\n\n"
            + "\n\n".join(sections)
            + missing_note
        ),
    }


def should_detect_administratum_intent(client_source, payload):
    if not internal_flag(payload.get("intent_detection", True), default=True):
        return False
    if internal_flag(payload.get("system_event", False), default=False):
        return False
    source = str(client_source or payload.get("source") or "").strip().lower()
    return source != "administratum"


def detect_administratum_intent(user_text, model=None):
    text = trim_chat_text(user_text)
    if not text:
        return {"ok": True, "intent": "none", "confidence": 0.0}
    request = build_intent_detection_request(text, model=model or DEFAULT_MODEL, now=now_iso(), timezone="Asia/Seoul")
    try:
        _status, response = proxy_json("POST", "/v1/chat/completions", payload=request, timeout=180)
        content = str((((response.get("choices") or [{}])[0].get("message") or {}).get("content")) or "")
        parsed = extract_json_object(content)
        parsed.setdefault("ok", True)
        return normalize_intent(parsed)
    except Exception as exc:
        return {"ok": False, "intent": "error", "confidence": 0.0, "error": str(exc)}


def create_administratum_task_from_intent(intent, session_id, client_source):
    intent = normalize_intent(intent)
    if str(intent.get("intent") or "").strip() != "create_task":
        return {"created": False, "reason": "no_create_task_intent", "intent": intent}
    try:
        confidence = float(intent.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    title = str(intent.get("title") or "").strip()
    if confidence < 0.74 or not title:
        return {"created": False, "reason": "low_confidence_or_missing_title", "intent": intent}
    if bool(intent.get("needs_confirmation")) and str(intent.get("kind") or "") in {"watch", "routine"}:
        return {"created": False, "reason": "confirmation_required", "intent": intent}
    kind = str(intent.get("kind") or "").strip()
    if kind == "reminder" and not str(intent.get("due_at") or "").strip() and not str(intent.get("interval") or "").strip():
        # A reminder with nothing to schedule would fire instantly and echo the user.
        return {"created": False, "reason": "reminder_without_schedule", "intent": intent}
    endpoint_kind, administratum_payload = administratum_payload_from_intent(intent, session_id=session_id, client_source=client_source)
    if endpoint_kind == "watch":
        try:
            _status, response = proxy_json_url("POST", f"{ADMINISTRATUM_BASE_URL}/watch", payload=administratum_payload, timeout=60)
            return {"created": bool(response.get("ok")), "watch": response.get("watch"), "intent": intent, "response": response}
        except Exception as exc:
            return {"created": False, "reason": "administratum_unavailable", "error": str(exc), "intent": intent}
    try:
        _status, response = proxy_json_url("POST", f"{ADMINISTRATUM_BASE_URL}/task", payload=administratum_payload, timeout=60)
        return {"created": bool(response.get("ok")), "task": response.get("task"), "intent": intent, "response": response}
    except Exception as exc:
        return {"created": False, "reason": "administratum_unavailable", "error": str(exc), "intent": intent}


def administratum_intent_context(result):
    if not result:
        return None
    if result.get("created") and isinstance(result.get("task"), dict):
        task = result["task"]
        return {
            "role": "system",
            "content": (
                "Administratum task created. Confirm to the owner in Shushunya's voice exactly what was recorded.\n"
                f"id: {task.get('id')}\nkind: {task.get('kind')}\ntitle: {task.get('title')}\n"
                f"due_at: {task.get('due_at')}\ninterval: {task.get('interval')}\nnext_run: {task.get('next_run')}"
            ),
        }
    if result.get("created") and isinstance(result.get("watch"), dict):
        watch = result["watch"]
        return {
            "role": "system",
            "content": (
                "Administratum watch created. Confirm to the owner in Shushunya's voice exactly what was recorded.\n"
                f"id: {watch.get('id')}\ntitle: {watch.get('title')}\nwatch_type: {watch.get('watch_type')}\n"
                f"target: {watch.get('target')}\ncondition_json: {watch.get('condition_json')}"
            ),
        }
    if result.get("reason") == "reminder_without_schedule":
        return {
            "role": "system",
            "content": (
                "Administratum detected a reminder request but no time or interval was given, so nothing was created. "
                "Do not claim a reminder was recorded. Ask the owner when to remind, in one short question."
            ),
        }
    if result.get("reason") == "confirmation_required":
        return {
            "role": "system",
            "content": (
                "Administratum detected a possible routine/watch task but did not create it because confirmation is required. "
                f"Ask one concise clarification. Parsed intent: {json.dumps(result.get('intent') or {}, ensure_ascii=False)}"
            ),
        }
    if result.get("reason") == "administratum_unavailable":
        return {
            "role": "system",
            "content": f"Administratum intent was detected, but AshurKai is unavailable: {result.get('error')}. Tell the owner clearly.",
        }
    if result.get("reason") == "low_confidence_or_missing_title":
        return {
            "role": "system",
            "content": (
                "Administratum did not create a task because the parsed task was incomplete or low-confidence. "
                "Do not claim that a reminder/task was recorded. Ask one concise clarification if the user seems to want a reminder. "
                f"Parsed intent: {json.dumps(result.get('intent') or {}, ensure_ascii=False)}"
            ),
        }
    return None


def maybe_write_archives(record):
    if record.get("archive_enabled", True):
        write_archives(record)


def maybe_update_focus_memory(record):
    if record.get("archive_enabled", True):
        with MAINTENANCE_LOCK:
            update_focus_memory(record)


def prepare_messages(
    messages,
    include_focus=True,
    include_vector=True,
    include_graph=True,
    include_system_prompt=True,
    magos_message=None,
    administratum_message=None,
    reports_message=None,
    roster_message=None,
    query_messages=None,
    memory_namespace="default",
):
    prepared = []
    if include_system_prompt:
        prepared.append(persona_page_context(memory_namespace))
    query = latest_user_message(query_messages if query_messages is not None else messages)
    if magos_message:
        # Magos now carries both the semantic recall AND the recent-thread memory
        # that the focus file used to hold — no separate focus injection.
        prepared.append(magos_message)
    if administratum_message:
        prepared.append(administratum_message)
    if reports_message:
        # Strip the Vox judge payload before it reaches the prompt.
        prepared.append({"role": reports_message["role"], "content": reports_message["content"]})
    if roster_message:
        # Last system block, right before the conversation: live task status must
        # win on recency over the (possibly stale) focus file and history.
        prepared.append(roster_message)
    # Memory retrieval into the prompt now flows only through Magos's curated
    # memory_context (above). The old mechanical vector/graph auto-injection was
    # removed so nothing bypasses Magos's relevance filtering.
    prepared.extend(messages)
    return prepared


def conversation_id(payload):
    user = str(payload.get("user") or "").strip()
    if user:
        return user
    return "unknown"


def daily_jsonl_path(created_at):
    dt = datetime.fromisoformat(created_at)
    return JSONL_ROOT / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{dt.date().isoformat()}.jsonl"


def daily_memory_events_path(created_at):
    dt = datetime.fromisoformat(created_at)
    return MEMORY_EVENTS_ROOT / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{dt.date().isoformat()}.jsonl"


def init_storage():
    JSONL_ROOT.mkdir(parents=True, exist_ok=True)
    MEMORY_EVENTS_ROOT.mkdir(parents=True, exist_ok=True)
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SQLITE_PATH) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS turns (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                memory_namespace TEXT NOT NULL DEFAULT 'default',
                created_at TEXT NOT NULL,
                model TEXT,
                status TEXT NOT NULL,
                http_status INTEGER,
                request_json TEXT NOT NULL,
                prepared_messages_json TEXT NOT NULL,
                response_json TEXT,
                error TEXT,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            )
            """
        )
        turn_columns = {row[1] for row in db.execute("PRAGMA table_info(turns)")}
        if "memory_namespace" not in turn_columns:
            db.execute("ALTER TABLE turns ADD COLUMN memory_namespace TEXT NOT NULL DEFAULT 'default'")
        db.execute(
            """
            UPDATE turns
            SET memory_namespace = 'warmaster'
            WHERE conversation_id = 'warmaster' AND memory_namespace = 'default'
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                FOREIGN KEY(turn_id) REFERENCES turns(id),
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_turns_conversation_created ON turns(conversation_id, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_turns_namespace_created ON turns(memory_namespace, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation_created ON messages(conversation_id, created_at)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS mobile_chat_sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS mobile_chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                asset_id TEXT,
                source TEXT NOT NULL DEFAULT 'unknown',
                dedupe_key TEXT,
                FOREIGN KEY(session_id) REFERENCES mobile_chat_sessions(id)
            )
            """
        )
        mobile_message_columns = {row[1] for row in db.execute("PRAGMA table_info(mobile_chat_messages)")}
        if "source" not in mobile_message_columns:
            db.execute("ALTER TABLE mobile_chat_messages ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown'")
        if "dedupe_key" not in mobile_message_columns:
            db.execute("ALTER TABLE mobile_chat_messages ADD COLUMN dedupe_key TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS idx_mobile_chat_messages_session_id ON mobile_chat_messages(session_id, id)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_chat_messages_dedupe ON mobile_chat_messages(dedupe_key) WHERE dedupe_key IS NOT NULL")
        shared_session = shared_chat_session_id(SHARED_CHAT_SESSION_ID)
        now = now_iso()
        db.execute(
            """
            INSERT INTO mobile_chat_sessions (id, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (shared_session, now, now),
        )
        db.execute(
            """
            UPDATE mobile_chat_messages
            SET session_id = ?
            WHERE session_id != ?
            """,
            (shared_session, shared_session),
        )
        shared_namespace = shared_memory_namespace(SHARED_MEMORY_NAMESPACE)
        legacy_namespaces = tuple(sorted(LEGACY_SHARED_MEMORY_NAMESPACES | {safe_memory_namespace(SHARED_MEMORY_NAMESPACE)}))
        if legacy_namespaces:
            placeholders = ",".join("?" for _ in legacy_namespaces)
            db.execute(
                f"""
                UPDATE turns
                SET memory_namespace = ?
                WHERE memory_namespace IN ({placeholders})
                """,
                (shared_namespace, *legacy_namespaces),
            )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS mobile_jobs (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                request_json TEXT NOT NULL,
                response_json TEXT,
                error TEXT
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_mobile_jobs_updated ON mobile_jobs(updated_at)")


def assistant_content(message):
    content = message.get("content")
    if content is None or str(content).strip() == "":
        content = message.get("reasoning_content")
    return str(content or "")


def assistant_message(response):
    choices = response.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    content = assistant_content(message).strip()
    if not content:
        return None
    return {"role": message.get("role") or "assistant", "content": content}


def stream_delta(payload):
    choices = payload.get("choices") or []
    if not choices:
        return "", None

    choice = choices[0]
    delta = choice.get("delta") or {}
    message = choice.get("message") or {}
    content = delta.get("content")
    if content is None:
        content = delta.get("reasoning_content")
    if content is None:
        content = assistant_content(message)
    return str(content or ""), choice.get("finish_reason")


def write_archives(record):
    with ARCHIVE_LOCK:
        jsonl_path = daily_jsonl_path(record["created_at"])
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8") as archive:
            archive.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

        with sqlite3.connect(SQLITE_PATH) as db:
            db.execute(
                """
                INSERT INTO conversations (id, source, external_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (
                    record["conversation_id"],
                    record["source"],
                    record["conversation_id"],
                    record["created_at"],
                    record["created_at"],
                ),
            )
            db.execute(
                """
                INSERT INTO turns (
                    id, conversation_id, memory_namespace, created_at, model, status, http_status,
                    request_json, prepared_messages_json, response_json, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["turn_id"],
                    record["conversation_id"],
                    record.get("memory_namespace") or "default",
                    record["created_at"],
                    record.get("model"),
                    record["status"],
                    record.get("http_status"),
                    json.dumps(record["request"], ensure_ascii=False, sort_keys=True),
                    json.dumps(record["prepared_messages"], ensure_ascii=False, sort_keys=True),
                    json.dumps(record.get("response"), ensure_ascii=False, sort_keys=True)
                    if record.get("response") is not None
                    else None,
                    record.get("error"),
                ),
            )

            messages = list(record["prepared_messages"])
            reply = record.get("assistant_message")
            if reply:
                messages.append(reply)

            for sequence, message in enumerate(messages):
                db.execute(
                    """
                    INSERT INTO messages (
                        turn_id, conversation_id, created_at, sequence, role, content, source
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["turn_id"],
                        record["conversation_id"],
                        record["created_at"],
                        sequence,
                        str(message.get("role") or ""),
                        str(message.get("content") or ""),
                        "prepared" if message is not reply else "assistant_response",
                    ),
                )


def write_memory_event(record, event):
    payload = {
        "created_at": now_iso(),
        "turn_created_at": record.get("created_at"),
        "turn_id": record.get("turn_id"),
        "conversation_id": record.get("conversation_id"),
        "memory_namespace": record.get("memory_namespace") or "default",
        "event": event,
    }
    with ARCHIVE_LOCK:
        path = daily_memory_events_path(record["created_at"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as archive:
            archive.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def write_gateway_event(memory_namespace, action, requester=None, **details):
    namespace = safe_memory_namespace(memory_namespace)
    requester = str(requester or "unknown").strip()[:80] or "unknown"
    record = {
        "created_at": now_iso(),
        "turn_id": str(uuid.uuid4()),
        "conversation_id": f"memory-gateway:{requester}",
        "memory_namespace": namespace,
    }
    write_memory_event(
        record,
        {
            "component": "memory_gateway",
            "action": action,
            "requester": requester,
            **details,
        },
    )


def memory_report_catalogs():
    catalogs = {}
    for namespace in known_memory_namespaces():
        try:
            catalog = memory_catalog(namespace)
        except Exception as exc:
            catalogs[namespace] = {"error": str(exc)}
            continue
        focus = catalog.get("focus", {})
        wiki = catalog.get("wiki", {})
        catalogs[namespace] = {
            "focus_count": len(focus.get("books", []) or []),
            "active_focus_id": focus.get("active_id"),
            "wiki_pages": len(wiki.get("pages", []) or []),
            "vector": catalog.get("vector", {}),
            "graph": catalog.get("graph", {}),
        }
    return catalogs


def run_memory_quality_report(report_date=None):
    result = generate_quality_report(
        proxy_json,
        JSONL_ROOT,
        MEMORY_EVENTS_ROOT,
        REPORTS_ROOT,
        report_date=report_date,
        catalogs=memory_report_catalogs(),
    )
    record = {
        "created_at": now_iso(),
        "turn_id": str(uuid.uuid4()),
        "conversation_id": "archive-memory-quality",
        "memory_namespace": "default",
    }
    write_memory_event(
        record,
        {
            "component": "memory_quality",
            "action": "daily_report",
            "date": result.get("date"),
            "score": (result.get("assessment") or {}).get("score"),
            "paths": result.get("paths"),
        },
    )
    print(f"Memory quality report: {json.dumps(result.get('paths'), ensure_ascii=False)}", flush=True)
    return result


def seconds_until_quality_report():
    now = datetime.now().astimezone()
    target = now.replace(hour=MEMORY_QUALITY_REPORT_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(60, int((target - now).total_seconds()))


def memory_quality_report_loop():
    while MEMORY_QUALITY_REPORT_ENABLED:
        threading.Event().wait(seconds_until_quality_report())
        try:
            run_memory_quality_report()
        except Exception as exc:
            record = {
                "created_at": now_iso(),
                "turn_id": str(uuid.uuid4()),
                "conversation_id": "archive-memory-quality",
                "memory_namespace": "default",
            }
            write_memory_event(record, {"component": "memory_quality", "status": "error", "error": str(exc)})
            print(f"Memory quality report error: {exc}", flush=True)


def recent_memory_events(limit=50, memory_namespace=None, component=None, event_action=None, requester=None):
    limit = max(1, min(int(limit or 50), 500))
    component = str(component or "").strip()
    event_action = str(event_action or "").strip()
    requester = str(requester or "").strip()
    events = []
    paths = sorted(MEMORY_EVENTS_ROOT.glob("*/*/*.jsonl"), reverse=True)
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if memory_namespace and event.get("memory_namespace") != memory_namespace:
                continue
            body = event.get("event") if isinstance(event.get("event"), dict) else {}
            if component and body.get("component") != component:
                continue
            if event_action and body.get("action") != event_action:
                continue
            if requester and body.get("requester") != requester:
                continue
            events.append(event)
            if len(events) >= limit:
                return events
    return events


def update_focus_memory(record):
    namespace = record.get("memory_namespace") or "default"
    librarian = focus_components(namespace)["librarian"]
    if librarian is None:
        return
    try:
        event = librarian.process_turn(record)
        write_memory_event(record, {"component": "librarian", "result": event})
    except Exception as exc:
        write_memory_event(record, {"component": "librarian", "status": "error", "error": str(exc)})
        print(f"Librarian error namespace={namespace}: {exc}", flush=True)
    # Vox conveyance judge: which on-tongue intents actually sounded in the
    # answer become conveyed. Background, alongside the librarian.
    on_tongue = record.get("vox_on_tongue") or []
    if on_tongue and (record.get("assistant_message") or {}).get("content"):
        judge_conveyed(record["assistant_message"]["content"], on_tongue)


def maybe_abandon_magos_focus(record):
    namespace = record.get("memory_namespace") or "default"
    magos = focus_components(namespace)["magos"]
    if magos is None:
        return
    if record.get("status") == "ok":
        return
    try:
        magos.abandon_created_focus(record.get("turn_id"), f"model request ended with status={record.get('status')}")
    except Exception as exc:
        print(f"Magos abandon error namespace={namespace}: {exc}", flush=True)
