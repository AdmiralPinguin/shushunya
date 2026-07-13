"""ArchiveOfHeresy operations: memory search/context, chat, storage, mobile,
and maintenance. Uses shared singletons via archive_state."""
import json
import hashlib
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
from archive_state import (ARCHIVE_LOCK, CHAT_QUEUE_LOCK, CHAT_QUEUE_WAIT_TIMEOUT_SEC,
    CHAT_SESSION_LOCKS, ChatQueueBusy, MAINTENANCE_LOCK, MOBILE_JOB_LOCK,
    TimedChatQueueLock)
from archivist_agent import Librarian
from archivist_agent.agent import FocusBookshelf, WikiBookshelf
from archivist_agent.graph_memory import GRAPH_TOP_K, GraphMemory
from archivist_agent.magos_agent import MAGOS_CONTEXT_LAYERS, MAGOS_EXTRA_NAMESPACES, Magos
from archivist_agent.quality_report import generate_quality_report
from archivist_agent.vector_memory import VECTOR_TOP_K, VectorMemory, latest_user_message
from turn_protocol import (
    TURN_ACTIONS,
    capability_contract_message,
    turn_capability_manifest,
    warmaster_request_to_message,
)
from shushunya_core_client import (
    dispatch_effect as core_dispatch_effect,
    resolve_turn as core_resolve_turn,
)
from pending_reports import (
    continuable_tasks,
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
from decision_requests import (
    commit_answer_result,
    conversational_text,
    core_context as pending_decision_context,
    decision_prompt_version,
    decision_version,
    extract_decision_request,
    find_answer_receipt,
    find_pending as find_pending_decision,
    mark_answer_reconcile_pending,
    normalize_decision_request,
    reserve_answer_attempt,
    render_decision_request,
    render_dispatch_retry,
    render_internal_stall,
)
from artifact_store import (
    ArtifactError,
    artifact_catalog_for_query,
    artifact_metadata,
    artifact_store_stats,
    attach_artifact_to_chat,
    init_artifact_storage,
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

CORE_DEGRADED_SAFE_REPLY = (
    "Я сейчас не смог надёжно определить действие. Ничего не запущено и не изменено."
)


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


def run_mobile_chat_payload(payload, on_token=None, *, trusted_turn_context=None):
    LLM_PRIORITY.set("chat")  # the owner's live answer jumps ahead of brigade work
    maintenance_record = None
    payload = dict(payload)
    # HTTP JSON is never authority for Core decisions/capabilities/effects.
    # Only a handler that has just completed the server-side turn protocol may
    # pass these fields through the separate Python-only keyword argument.
    trusted_turn_context = (
        trusted_turn_context if isinstance(trusted_turn_context, dict) else {}
    )
    session_id = shared_chat_session_id(payload.get("session_id") or payload.get("user") or "default")
    core_context_bundle = (
        trusted_turn_context.get("core_context_bundle")
        if isinstance(trusted_turn_context.get("core_context_bundle"), dict)
        else {}
    )
    core_resolution = (
        trusted_turn_context.get("core_resolution")
        if isinstance(trusted_turn_context.get("core_resolution"), dict)
        else {}
    )
    core_effect = (
        trusted_turn_context.get("core_effect")
        if isinstance(trusted_turn_context.get("core_effect"), dict)
        else None
    )
    # Same-session waiters stay outside the four global pipeline slots, so one
    # noisy conversation cannot head-of-line block unrelated sessions.
    with CHAT_SESSION_LOCKS.hold(session_id), CHAT_QUEUE_LOCK:
        created_at = now_iso()
        turn_id = str(core_context_bundle.get("turn_id") or uuid.uuid4())
        # Carry only a named, allow-listed route. Direct llama.cpp deployments
        # safely ignore the internal header emitted by archive_httpio.
        model_route = set_llm_route(payload.get("model_route"))
        payload["stream"] = False
        client_source = str(payload.get("client_source") or payload.get("source") or "app").strip()[:80] or "app"
        artifact_audience_source = str(
            payload.get("artifact_audience_source") or client_source
        ).strip().lower()[:80] or "app"
        client_request_id = ensure_core_transport_identity(payload)
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
        turn_capabilities = (
            trusted_turn_context.get("turn_capabilities")
            if isinstance(trusted_turn_context.get("turn_capabilities"), dict)
            else turn_capability_manifest(
                image_attached=bool(image_data_url),
                continuable_tasks=continuable_tasks(),
                artifacts=artifact_catalog_for_query(
                    session_id,
                    audience_source=artifact_audience_source,
                    query=text,
                    limit=ARTIFACT_CAPABILITY_LIMIT,
                ),
            )
        )
        turn_decision = (
            trusted_turn_context.get("turn_decision")
            if isinstance(trusted_turn_context.get("turn_decision"), dict)
            else {"action": "answer_in_chat"}
        )
        forced_chat_reply = trim_chat_text(trusted_turn_context.get("forced_chat_reply") or "")

        request_messages = messages_for_chat_context(session_id, system_prompt, text, image_data_url=image_data_url)
        request_messages.insert(0, capability_contract_message(turn_capabilities, turn_decision))
        memory_messages = sanitize_messages_for_memory(request_messages)
        precomputed_magos = core_context_bundle.get("magos_message") if isinstance(core_context_bundle.get("magos_message"), dict) else None
        precomputed_magos_result = core_context_bundle.get("magos_result") if isinstance(core_context_bundle.get("magos_result"), dict) else None
        magos_already_attempted = bool(core_context_bundle.get("magos_attempted"))
        precomputed_roster = core_context_bundle.get("roster_message") if isinstance(core_context_bundle.get("roster_message"), dict) else None
        append_chat_message(
            session_id,
            "user",
            text if not image_data_url else f"{text}\n[image attached server-side]",
            created_at=created_at,
            source=client_source,
            dedupe_key=f"turn:{client_request_id}:user",
            client_request_id=client_request_id,
        )
        if str(turn_decision.get("action") or "") == "deliver_artifact":
            if core_effect and core_effect.get("id"):
                try:
                    dispatched = core_dispatch_effect(str(core_effect["id"]))
                except Exception as exc:  # transport loss does not prove a retry was scheduled
                    dispatched = {
                        "ok": False,
                        "effect": {
                            "state": "not_confirmed",
                            "payload": core_effect.get("payload") if isinstance(core_effect.get("payload"), dict) else {},
                            "result": {
                                "status": "core_delivery_not_confirmed",
                                "explanation": (
                                    "Core сохранил обязательство, но Archive не дождался подтверждения доставки файла: "
                                    f"{exc}"
                                ),
                            },
                        },
                    }
            else:
                dispatched = {
                    "ok": False,
                    "effect": {
                        "state": "failed",
                        "payload": {},
                        "result": {
                            "status": "missing_durable_effect",
                            "explanation": "Core не создал durable-эффект доставки файла.",
                        },
                    },
                }
            delivered_effect = dispatched.get("effect") if isinstance(dispatched.get("effect"), dict) else {}
            factual = delivered_effect.get("result") if isinstance(delivered_effect.get("result"), dict) else {}
            effect_payload = delivered_effect.get("payload") if isinstance(delivered_effect.get("payload"), dict) else {}
            evidence = factual.get("evidence") if isinstance(factual.get("evidence"), dict) else {}
            artifact = evidence.get("artifact") if isinstance(evidence.get("artifact"), dict) else None
            artifact_id = str(factual.get("artifact_id") or effect_payload.get("artifact_id") or "").strip()
            if artifact is None and artifact_id:
                try:
                    artifact = artifact_metadata(
                        artifact_id,
                        session_id=session_id,
                        audience_source=artifact_audience_source,
                    )
                except ValueError:
                    artifact = None
            artifact = chat_artifact_payload(artifact)
            delivered = bool(
                delivered_effect.get("state") == "delivered"
                and factual.get("delegate_ref")
                and artifact
                and str((artifact or {}).get("artifact_id") or "") == artifact_id
            )
            if delivered and not bind_artifact_message_request_id(
                factual.get("delegate_ref"),
                session_id,
                artifact_id,
                client_request_id,
            ):
                print(
                    f"artifact delivery correlation was not persisted: artifact_id={artifact_id}",
                    flush=True,
                )
            delivered_artifact = artifact if delivered else None
            if delivered:
                factual_message = conversational_text(factual.get("caption") or "Файл приложен.") or "Файл приложен."
            else:
                explanation = factual.get("explanation") or "файл пока не удалось приложить"
                retry_scheduled = str(delivered_effect.get("state") or "") in {
                    "pending",
                    "leased",
                    "retry_wait",
                }
                factual_message = (
                    render_dispatch_retry(explanation)
                    if retry_scheduled
                    else render_internal_stall(
                        "доставить файл",
                        explanation,
                        failed=True,
                    )
                )
                append_chat_message(
                    session_id,
                    "assistant",
                    factual_message,
                    source="shushunya-core",
                    dedupe_key=f"turn:{client_request_id}:assistant",
                    client_request_id=client_request_id,
                )
            assistant = {"role": "assistant", "content": factual_message}
            response = {
                "object": "chat.completion",
                "model": "shushunya-core",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "artifact_effect",
                        "message": assistant,
                    }
                ],
                "artifact": delivered_artifact,
                "action": "deliver_artifact",
                "artifact_id": artifact_id,
                "client_request_id": client_request_id,
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
                "magos_enabled": bool(precomputed_magos),
                "magos_result": precomputed_magos_result,
                "administratum_intent": None,
                "administratum_result": None,
                "turn_decision": turn_decision,
                "turn_capabilities": turn_capabilities,
                "prompt_diagnostics": {},
                "core_resolution": core_resolution,
                "core_effect": delivered_effect,
                "artifact_delivery": delivered_artifact,
                "model": "shushunya-core",
                "request": {
                    "session_id": session_id,
                    "client_source": client_source,
                    "text": text,
                    "has_image": bool(image_data_url),
                    "stream": False,
                },
                "prepared_messages": prepare_messages(
                    memory_messages,
                    include_focus=focus_enabled and precomputed_roster is None,
                    include_vector=vector_enabled,
                    include_graph=graph_enabled,
                    include_system_prompt=archive_system_prompt_enabled,
                    magos_message=precomputed_magos,
                    roster_message=precomputed_roster,
                    query_messages=memory_messages,
                    memory_namespace=memory_namespace,
                ),
                "status": "ok",
                "http_status": 200,
                "response": response,
                "assistant_message": assistant,
                "error": None,
            }
            maybe_write_archives(record)
            threading.Thread(
                target=maybe_update_focus_memory,
                args=(record,),
                daemon=True,
                name=f"librarian-{turn_id}",
            ).start()
            return {
                "ok": True,
                "effect_ok": delivered,
                "session_id": session_id,
                "response": response,
                "message": factual_message,
                "artifact": delivered_artifact,
                "core_effect": delivered_effect,
                "action": "deliver_artifact",
                "artifact_id": artifact_id,
                "client_request_id": client_request_id,
            }
        if forced_chat_reply:
            assistant = {"role": "assistant", "content": forced_chat_reply}
            append_chat_message(
                session_id,
                "assistant",
                forced_chat_reply,
                source=client_source,
                dedupe_key=f"turn:{client_request_id}:assistant",
                client_request_id=client_request_id,
            )
            response = {
                "object": "chat.completion",
                "model": "shushunya-core",
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
                "magos_enabled": bool(precomputed_magos),
                "magos_result": precomputed_magos_result,
                "administratum_intent": None,
                "administratum_result": None,
                "turn_decision": turn_decision,
                "turn_capabilities": turn_capabilities,
                "prompt_diagnostics": {},
                "core_resolution": core_resolution,
                "model": "shushunya-core",
                "request": {
                    "session_id": session_id,
                    "client_source": client_source,
                    "text": text,
                    "has_image": bool(image_data_url),
                    "stream": False,
                },
                "prepared_messages": prepare_messages(
                    memory_messages,
                    include_focus=focus_enabled and precomputed_roster is None,
                    include_vector=vector_enabled,
                    include_graph=graph_enabled,
                    include_system_prompt=archive_system_prompt_enabled,
                    magos_message=precomputed_magos,
                    roster_message=precomputed_roster,
                    query_messages=memory_messages,
                    memory_namespace=memory_namespace,
                ),
                "status": "ok",
                "http_status": 200,
                "response": response,
                "assistant_message": assistant,
                "error": None,
            }
            maybe_write_archives(record)
            threading.Thread(
                target=maybe_update_focus_memory,
                args=(record,),
                daemon=True,
                name=f"librarian-{turn_id}",
            ).start()
            return {"ok": True, "session_id": session_id, "response": response, "message": forced_chat_reply}
        administratum_intent = None
        administratum_result = None
        administratum_message = None
        # Core is the only delivery owner. It leases the durable effect, calls
        # Archive's loopback structurer, and records the factual result. Keeping
        # creation here as a second path would race recovery and duplicate tasks.
        if str(turn_decision.get("action") or "") == "create_administratum_task":
            if core_effect and core_effect.get("id"):
                try:
                    dispatched = core_dispatch_effect(str(core_effect["id"]))
                except Exception as exc:  # transport loss does not prove a retry was scheduled
                    dispatched = {
                        "ok": False,
                        "effect": {
                            "state": "not_confirmed",
                            "result": {
                                "status": "core_delivery_not_confirmed",
                                "explanation": (
                                    "Core сохранил обязательство, но Archive не дождался фактического ответа адаптера: "
                                    f"{exc}"
                                ),
                            },
                        },
                    }
                delivered_effect = dispatched.get("effect") if isinstance(dispatched.get("effect"), dict) else {}
                factual = delivered_effect.get("result") if isinstance(delivered_effect.get("result"), dict) else {}
                evidence = factual.get("evidence") if isinstance(factual.get("evidence"), dict) else {}
                administratum_result = evidence if evidence else {
                    "created": False,
                    "reason": factual.get("status") or delivered_effect.get("state") or "not_confirmed",
                    "explanation": factual.get("explanation") or "Administratum не подтвердил создание задачи.",
                }
                administratum_intent = (
                    administratum_result.get("intent")
                    if isinstance(administratum_result.get("intent"), dict)
                    else None
                )
            else:
                administratum_result = {
                    "created": False,
                    "reason": "missing_durable_effect",
                    "explanation": "Core не создал durable-эффект; задача не записана.",
                }
            administratum_message = administratum_intent_context(administratum_result)
            if administratum_message is None:
                administratum_message = {
                    "role": "system",
                    "content": (
                        "Создание напоминания или задачи не подтвердилось. "
                        f"Причина: {conversational_text(administratum_result.get('explanation') or administratum_result.get('reason'))}. "
                        "Ничего не было создано. Скажи об этом от первого лица и уточни только то, что реально нужно. "
                        "Не называй внутренние сервисы, коды, ключи или идентификаторы."
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
                    "content": "Собеседник спросил про новости, но у тебя ничего не накопилось. Скажи честно, что сказать нечего.",
                }
        elif not internal_flag(payload.get("system_event", False), default=False):
            reports_message = pending_topics_note(context_text=text)
            if reports_message:
                vox_on_tongue = reports_message.get("on_tongue") or []
        # Live task roster is always at hand on ordinary turns, so task status is
        # answered from truth (authoritative over stale focus/ack lines).
        roster_message = precomputed_roster
        if roster_message is None and not internal_flag(payload.get("system_event", False), default=False):
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
        magos_message = precomputed_magos
        magos_result = precomputed_magos_result
        magos = focus_components(memory_namespace)["magos"]
        if focus_enabled and magos is not None and magos_message is None and not magos_already_attempted:
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
            "core_resolution": core_resolution,
            "vox_on_tongue": vox_on_tongue,
            "prompt_diagnostics": diagnostics,
            "model": model,
            "request": {
                "session_id": session_id,
                "client_source": client_source,
                "model_route": model_route or None,
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
            if on_token is not None:
                status, response, assistant = stream_chat_completion(prepared_payload, on_token)
            else:
                status, response = proxy_json("POST", "/v1/chat/completions", payload=prepared_payload)
                assistant = assistant_message(response)
            if assistant:
                append_chat_message(
                    session_id,
                    "assistant",
                    assistant.get("content") or "",
                    source=client_source,
                    dedupe_key=f"turn:{client_request_id}:assistant",
                    client_request_id=client_request_id,
                )
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


def chat_artifact_payload(metadata):
    if not isinstance(metadata, dict) or not metadata.get("artifact_id"):
        return None
    artifact_id = str(metadata["artifact_id"])
    return {
        **metadata,
        "content_url": f"/archive/client/artifacts/{quote(artifact_id, safe='')}/content",
    }


def chat_history(session_id, limit=CHAT_HISTORY_LIMIT, after_id=0, before_id=0, audience_source=None):
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
    try:
        parsed_before_id = max(0, int(before_id or 0))
    except (TypeError, ValueError):
        parsed_before_id = 0
    with sqlite3.connect(SQLITE_PATH) as db:
        db.row_factory = sqlite3.Row
        if parsed_after_id > 0:
            rows = db.execute(
                """
                SELECT id, session_id, role, content, created_at, asset_id, artifact_id, client_request_id, source, dedupe_key
                FROM mobile_chat_messages
                WHERE session_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (session_id, parsed_after_id, safe_limit),
            ).fetchall()
        elif parsed_before_id > 0:
            # Scroll-up pagination: the newest `limit` messages older than before_id.
            rows = db.execute(
                """
                SELECT id, session_id, role, content, created_at, asset_id, artifact_id, client_request_id, source, dedupe_key
                FROM mobile_chat_messages
                WHERE session_id = ? AND id < ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, parsed_before_id, safe_limit),
            ).fetchall()
            rows = list(reversed(rows))
        else:
            rows = db.execute(
                """
                SELECT id, session_id, role, content, created_at, asset_id, artifact_id, client_request_id, source, dedupe_key
                FROM mobile_chat_messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, safe_limit),
            ).fetchall()
            rows = list(reversed(rows))
    artifact_cache = {}
    for row in rows:
        artifact_id = row["artifact_id"] if "artifact_id" in row.keys() else None
        if artifact_id and artifact_id not in artifact_cache:
            artifact_cache[artifact_id] = chat_artifact_payload(
                artifact_metadata(
                    artifact_id,
                    session_id=session_id,
                    audience_source=audience_source,
                )
            )
    messages = []
    for row in rows:
        raw_artifact_id = row["artifact_id"] if "artifact_id" in row.keys() else None
        visible_artifact = artifact_cache.get(raw_artifact_id) if raw_artifact_id else None
        visible_artifact_id = raw_artifact_id if audience_source is None or visible_artifact is not None else None
        messages.append({
            "id": row["id"],
            "session_id": row["session_id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"],
            "asset_id": row["asset_id"],
            "artifact_id": visible_artifact_id,
            "artifact": visible_artifact,
            "client_request_id": row["client_request_id"] if "client_request_id" in row.keys() else None,
            "source": row["source"] if "source" in row.keys() else "unknown",
            "dedupe_key": row["dedupe_key"] if "dedupe_key" in row.keys() else None,
        })
    return messages


ASSETS_ROOT = Path(os.environ.get("ARCHIVE_ASSETS_ROOT", str(SQLITE_PATH.parent.parent / "assets")))
ASSET_MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


def register_chat_asset(data, mime="image/png"):
    """Store binary asset bytes on disk under a fresh id; return the asset_id.
    The chat message references it, the app fetches it by id — the chat stream
    itself stays light."""
    if not data:
        return None
    mime = str(mime or "image/png").split(";")[0].strip().lower()
    if mime not in ASSET_MIME_EXT:
        mime = "image/png"
    asset_id = uuid.uuid4().hex
    ASSETS_ROOT.mkdir(parents=True, exist_ok=True)
    (ASSETS_ROOT / f"{asset_id}.bin").write_bytes(data)
    (ASSETS_ROOT / f"{asset_id}.mime").write_text(mime, encoding="utf-8")
    return asset_id


def read_chat_asset(asset_id):
    """Return (bytes, mime) for a stored asset, or None. id is sanitised to a
    bare hex token so the path can never escape the assets dir."""
    token = "".join(ch for ch in str(asset_id or "") if ch in "0123456789abcdef")
    if not token or len(token) > 64:
        return None
    blob = ASSETS_ROOT / f"{token}.bin"
    if not blob.is_file():
        return None
    mime_file = ASSETS_ROOT / f"{token}.mime"
    mime = mime_file.read_text(encoding="utf-8").strip() if mime_file.is_file() else "image/png"
    return blob.read_bytes(), mime


def deliver_image_to_chat(session_id, image, mime="image/png", caption="", source="pictorium", dedupe_key=None):
    """Put a generated image into the shared chat as an assistant message with an
    asset_id — the delivery bridge from an image brigade (Moriana) to the app.
    `image` is raw bytes or a path to a file on disk."""
    if isinstance(image, (str, Path)):
        path = Path(image)
        if not path.is_file():
            print(f"deliver_image_to_chat: no such file {path}", flush=True)
            return None
        data = path.read_bytes()
        if mime == "image/png":
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}.get(path.suffix.lower().lstrip("."), "image/png")
    else:
        data = image
    asset_id = register_chat_asset(data, mime)
    if not asset_id:
        return None
    append_chat_message(
        shared_chat_session_id(session_id),
        "assistant",
        caption or "Готовое изображение.",
        asset_id=asset_id,
        source=source,
        dedupe_key=dedupe_key,
    )
    return asset_id


def append_chat_message(
    session_id,
    role,
    content,
    asset_id=None,
    artifact_id=None,
    client_request_id=None,
    created_at=None,
    source="unknown",
    dedupe_key=None,
):
    session_id = shared_chat_session_id(session_id)
    role = "assistant" if role == "assistant" else "user"
    content = trim_chat_text(content)
    created_at = created_at or now_iso()
    source = str(source or "unknown").strip()[:80] or "unknown"
    dedupe_key = str(dedupe_key or "").strip()[:160] or None
    client_request_id = "".join(
        char for char in str(client_request_id or "").strip() if char.isalnum() or char in "-_.:"
    )[:160] or None
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
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO mobile_chat_messages (
                    session_id, role, content, created_at, asset_id, artifact_id, client_request_id, source, dedupe_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, role, content, created_at, asset_id, artifact_id, client_request_id, source, dedupe_key),
            )
            if dedupe_key:
                row = db.execute("SELECT id FROM mobile_chat_messages WHERE dedupe_key=?", (dedupe_key,)).fetchone()
                return int(row[0]) if row else None
            return int(cursor.lastrowid) if cursor.lastrowid else None


def bind_artifact_message_request_id(message_id, session_id, artifact_id, client_request_id):
    """Add transport correlation after Core returns the persisted message id."""
    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        return False
    session_id = shared_chat_session_id(session_id)
    artifact_id = str(artifact_id or "").strip()
    request_id = "".join(
        char for char in str(client_request_id or "").strip() if char.isalnum() or char in "-_.:"
    )[:160]
    if message_id < 1 or not artifact_id or not request_id:
        return False
    with ARCHIVE_LOCK:
        with sqlite3.connect(SQLITE_PATH) as db:
            row = db.execute(
                "SELECT client_request_id FROM mobile_chat_messages WHERE id=? AND session_id=? AND artifact_id=?",
                (message_id, session_id, artifact_id),
            ).fetchone()
            if not row or (row[0] and row[0] != request_id):
                return False
            db.execute(
                "UPDATE mobile_chat_messages SET client_request_id=? WHERE id=? AND session_id=? AND artifact_id=?",
                (request_id, message_id, session_id, artifact_id),
            )
    return True


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


def create_mobile_turn_job_once(request_payload):
    """Create the one durable execution slot for a client request.

    Android/Telegram network retries must observe the existing job instead of
    running the same turn, LLM answer and external effects a second time.
    """
    request_payload = dict(request_payload or {})
    request_id = ensure_core_transport_identity(request_payload)
    stable_request = {
        "client_request_id": request_id,
        "session_id": shared_chat_session_id(request_payload.get("session_id") or request_payload.get("user") or "default"),
        "text": trim_chat_text(request_payload.get("text") or request_payload.get("message") or ""),
        "image_sha256": hashlib.sha256(str(request_payload.get("image_data_url") or "").encode("utf-8")).hexdigest(),
        "client_source": str(request_payload.get("client_source") or request_payload.get("source") or "app")[:80],
        "artifact_audience_source": str(request_payload.get("artifact_audience_source") or "app")[:80],
        "model": str(request_payload.get("model") or DEFAULT_MODEL),
        "memory_namespace": shared_memory_namespace(request_payload.get("memory_namespace")),
    }
    # The durable slot is keyed by transport identity, not payload. Reusing
    # one client id with different text must conflict, not quietly create a
    # second job under another payload-derived id.
    identity = {
        "client_request_id": request_id,
        "session_id": stable_request["session_id"],
        "client_source": stable_request["client_source"],
        "artifact_audience_source": stable_request["artifact_audience_source"],
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    job_id = f"turn-{digest[:32]}"
    created_at = now_iso()
    encoded = json.dumps(request_payload, ensure_ascii=False, sort_keys=True)
    with MOBILE_JOB_LOCK, sqlite3.connect(SQLITE_PATH) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT request_json,status FROM mobile_jobs WHERE id=?", (job_id,)).fetchone()
        if row:
            stored = json.loads(row[0])
            stored_stable = {
                "client_request_id": str(stored.get("client_request_id") or stored.get("core_turn_id") or ""),
                "session_id": shared_chat_session_id(stored.get("session_id") or stored.get("user") or "default"),
                "text": trim_chat_text(stored.get("text") or stored.get("message") or ""),
                "image_sha256": hashlib.sha256(str(stored.get("image_data_url") or "").encode("utf-8")).hexdigest(),
                "client_source": str(stored.get("client_source") or stored.get("source") or "app")[:80],
                "artifact_audience_source": str(stored.get("artifact_audience_source") or "app")[:80],
                "model": str(stored.get("model") or DEFAULT_MODEL),
                "memory_namespace": shared_memory_namespace(stored.get("memory_namespace")),
            }
            if stable_request != stored_stable:
                raise ValueError("client_request_id conflicts with a different turn payload")
            if str(row[1]) in {"interrupted", "failed"}:
                # A turn is safe to reopen under the same transport identity:
                # Core resolves with the same idempotency key, durable effects
                # keep their own receipts, decision answers keep an answer
                # receipt, and chat rows are request-deduped.  This recovers a
                # transient model/Core failure without authorizing any external
                # action a second time.
                db.execute(
                    "UPDATE mobile_jobs SET status='queued',updated_at=?,response_json=NULL,error=NULL "
                    "WHERE id=? AND status IN ('interrupted','failed')",
                    (created_at, job_id),
                )
                return job_id, True, "queued"
            return job_id, False, str(row[1])
        db.execute(
            """
            INSERT INTO mobile_jobs (id, type, status, created_at, updated_at, request_json, response_json, error)
            VALUES (?, 'turn', 'queued', ?, ?, ?, NULL, NULL)
            """,
            (job_id, created_at, created_at, encoded),
        )
    return job_id, True, "queued"


def update_mobile_job(job_id, status, response=None, error=None):
    # All mobile-job state transitions share one process-wide lock.  The
    # explicit busy timeout also covers a short-lived writer in another
    # Archive process during deployment/recovery instead of losing the
    # transition immediately with ``database is locked``.
    with MOBILE_JOB_LOCK, sqlite3.connect(SQLITE_PATH, timeout=30) as db:
        db.execute("PRAGMA busy_timeout=30000")
        cursor = db.execute(
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
        if cursor.rowcount != 1:
            raise KeyError(f"mobile job not found: {job_id}")


def mark_mobile_job_interrupted(job_id, diagnostic):
    """Best-effort durable tombstone for a worker that cannot finish safely."""
    try:
        update_mobile_job(job_id, "interrupted", error=diagnostic)
    except Exception:  # The caller must still terminate its delivery channel.
        return False
    return True


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
        try:
            update_mobile_job(job_id, "running")
        except Exception as exc:
            mark_mobile_job_interrupted(
                job_id,
                f"mobile_job_running_persist_failed: {type(exc).__name__}: {exc}",
            )
            return
        try:
            response = worker()
        except Exception as exc:
            try:
                update_mobile_job(job_id, "failed", error=exc)
            except Exception as persist_exc:
                mark_mobile_job_interrupted(
                    job_id,
                    "mobile_job_failure_persist_failed: "
                    f"worker={type(exc).__name__}: {exc}; "
                    f"storage={type(persist_exc).__name__}: {persist_exc}",
                )
            return
        try:
            update_mobile_job(job_id, "done", response=response)
        except Exception as exc:
            mark_mobile_job_interrupted(
                job_id,
                f"mobile_job_result_persist_failed: {type(exc).__name__}: {exc}",
            )

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
        + (f"суть задачи: {trim_chat_text(task_text)[:300]}\n" if task_text else "")
        + "Подтверди одной-двумя фразами от первого лица, что ты взял это в работу и сообщишь результат. "
        "По-русски. Не называй внутренние сервисы, исполнителей или идентификаторы. "
        "Не выдумывай прогресс, не задавай вопросов, не пересказывай задачу целиком."
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


WARMASTER_NON_TERMINAL_STATES = {
    "created", "assigned", "queued", "running", "planning", "plan_review",
    "executing", "governor_review", "warmaster_acceptance", "revision", "blocked", "interrupted",
}


def warmaster_duplicate_task_id(task_text):
    """One topic — one mission. Before delegating a new mission, ask the model
    whether the same job is already on Warmaster's board; if it is, the caller
    resumes that run instead of spawning a duplicate."""
    task_text = trim_chat_text(task_text)
    if not task_text:
        return ""
    try:
        _status, snapshot = proxy_json_url("GET", f"{WARMASTER_BASE_URL}/runs?limit=30", timeout=20)
    except Exception as exc:  # noqa: BLE001 - Warmaster down: let normal delegation handle it
        print(f"Duplicate-task check skipped (runs unavailable): {exc}", flush=True)
        return ""
    candidates = []
    for run in snapshot.get("runs") or []:
        if not isinstance(run, dict):
            continue
        state = str(run.get("mission_status") or run.get("status") or "").lower()
        if state not in WARMASTER_NON_TERMINAL_STATES:
            continue
        candidates.append({"task_id": str(run.get("task_id") or ""), "state": state, "goal": str(run.get("goal") or "")[:400]})
    if not candidates:
        return ""
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты диспетчер задач. Дана новая задача владельца и список задач, которые уже висят у бригад. "
                    'Верни строгий JSON {"match_task_id":""} — id задачи, которая является ТЕМ ЖЕ самым поручением '
                    "(та же тема и та же цель, даже если формулировка другая). Продолжение/повтор той же работы — это совпадение. "
                    "Если новая задача про другое — верни пустую строку. Не выдумывай id."
                ),
            },
            {"role": "user", "content": json.dumps({"new_task": task_text[:800], "existing": candidates}, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": 100,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        _status, response = proxy_json("POST", "/v1/chat/completions", payload=payload, timeout=120)
        content = str((((response.get("choices") or [{}])[0].get("message") or {}).get("content")) or "")
        match = str(extract_json_object(content).get("match_task_id") or "").strip()
    except Exception as exc:  # noqa: BLE001 - an unanswered judge must not block delegation
        print(f"Duplicate-task check failed: {exc}", flush=True)
        return ""
    if match and any(c["task_id"] == match for c in candidates):
        return match
    return ""


def ensure_core_transport_identity(payload):
    payload = payload if isinstance(payload, dict) else {}
    raw = str(payload.get("client_request_id") or payload.get("core_turn_id") or "").strip()
    clean = "".join(ch for ch in raw if ch.isalnum() or ch in "-_.:")[:160]
    if not clean:
        clean = uuid.uuid4().hex
    payload["client_request_id"] = clean
    payload["core_turn_id"] = clean
    payload["core_idempotency_key"] = str(
        payload.get("core_idempotency_key") or f"archive-turn:{clean}"
    )[:240]
    return clean


def _run_resume_state(task_id):
    """Classify authoritative state after an ambiguous clarification reply.

    A missing question is not proof that an answer was accepted: the run may
    have failed, been cancelled, or moved into internal repair.  Only an
    explicitly active or successfully completed run closes the lost-ACK gap as
    a successful resume.
    """
    try:
        http_status, state = proxy_json_url(
            "GET",
            f"{WARMASTER_BASE_URL}/runs/{quote(task_id, safe='')}/orchestration?event_limit=0",
            timeout=20,
        )
    except Exception:  # noqa: BLE001 - this is only post-failure reconciliation
        return {"kind": "unknown", "status": "unreadable"}
    if not 200 <= int(http_status or 0) < 300 or not isinstance(state, dict):
        return {"kind": "unknown", "status": f"http_{int(http_status or 0)}"}

    snapshot = state.get("snapshot") if isinstance(state.get("snapshot"), dict) else {}
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else snapshot
    result = summary.get("result") if isinstance(summary.get("result"), dict) else {}
    mission_state = (
        state.get("mission_state")
        if isinstance(state.get("mission_state"), dict)
        else summary.get("mission_state")
        if isinstance(summary.get("mission_state"), dict)
        else {}
    )
    run = state.get("run") if isinstance(state.get("run"), dict) else {}

    def needs_user_marker(node):
        if not isinstance(node, dict):
            return None
        if "needs_user" in node:
            return node.get("needs_user") is True
        visible = str(node.get("user_visible_state") or "").strip().lower()
        status_value = str(node.get("status") or node.get("phase") or "").strip().lower()
        if visible:
            return visible == "needs_user_decision"
        if status_value in {"needs_user", "waiting_user"}:
            return True
        return None

    current_needs_user = None
    for node in (mission_state, result, summary, state):
        current_needs_user = needs_user_marker(node)
        if current_needs_user is not None:
            break
    if current_needs_user is True:
        raw_question = None
        for node in (mission_state, result, summary, state):
            if not isinstance(node, dict):
                continue
            explicit = node.get("decision_request")
            question = (
                node.get("question")
                or node.get("clarification_question")
                or node.get("exact_question")
            )
            if isinstance(explicit, dict):
                raw_question = explicit
                break
            if question:
                raw_question = node
                break
        decision = normalize_decision_request(raw_question, task_id=task_id) if raw_question else None
        if decision:
            return {"kind": "waiting", "status": "needs_user", "decision_request": decision}

    completed = {"completed", "complete", "succeeded", "success", "done", "published"}
    terminal = {
        "failed", "failure", "cancelled", "canceled", "aborted", "rejected", "expired",
        "corrupt", "preflight_failed",
    }
    active = {
        "accepted", "active", "accepted_start", "created", "queued", "planning", "plan_review",
        "running", "executing",
        "applying", "publishing", "ready_to_apply", "resumed", "starting",
    }
    internal = {
        "blocked", "needs_revision", "revision_required", "internal_repair_required",
        "recovering", "repairing", "retry_wait", "revision", "revising", "interrupted",
    }
    # Durable mission/result state outranks the coarse outer run wrapper. That
    # wrapper often remains `running` while the mission has already failed,
    # completed, or entered an internal revision.
    observed_status = ""
    for node in (mission_state, result, summary, snapshot, run, state):
        if not isinstance(node, dict):
            continue
        status_value = str(
            node.get("status") or node.get("phase") or node.get("state") or ""
        ).strip().lower()
        if not status_value:
            continue
        observed_status = observed_status or status_value
        if status_value in terminal:
            return {"kind": "terminal", "status": status_value}
        if status_value in completed:
            return {"kind": "completed", "status": status_value}
        if status_value in internal:
            return {"kind": "internal", "status": status_value}
        if status_value in active:
            return {"kind": "active", "status": status_value}
    if state.get("active") is True:
        return {"kind": "active", "status": observed_status or "active"}
    return {"kind": "unknown", "status": observed_status or "unknown"}


def _mark_decision_report_closed(request):
    report_id = request.get("vox_intent_id")
    if report_id:
        mark_delivered([report_id])


def _commit_answer_transition(
    request,
    *,
    bound_task_id,
    answer,
    request_id,
    result,
    replacement=None,
    clear=False,
):
    committed = commit_answer_result(
        request_id,
        task_id=bound_task_id,
        answer=answer,
        request=request,
        result=result,
        pending_request=replacement,
        clear_pending=clear,
    )
    if committed and (replacement is not None or clear):
        _mark_decision_report_closed(request)
    return committed


def _finish_reconciled_decision(request, bound_task_id, run_state):
    state_kind = str((run_state or {}).get("kind") or "unknown")
    if state_kind not in {"active", "completed", "terminal", "internal"}:
        return None
    if state_kind == "active":
        return {
            "ok": True,
            "status": "resumed_reconciled",
            "message": "Понял. Ответ дошёл: вопрос закрыт, и задача продолжает выполняться.",
            "task_id": bound_task_id,
        }
    if state_kind == "completed":
        return {
            "ok": True,
            "status": "completed_reconciled",
            "message": "Понял. Пока подтверждение шло обратно, задача уже завершилась.",
            "task_id": bound_task_id,
        }
    if state_kind == "terminal":
        message = (
            "Не смог подтвердить передачу ответа: задача уже завершилась с ошибкой. "
            "Старый вопрос закрыл как неактуальный; если решение ещё нужно, дай задачу заново."
        )
    else:
        message = (
            "Не смог подтвердить передачу ответа: задача уже ушла во внутреннее восстановление. "
            "Старый вопрос закрыл как неактуальный — твой выбор сейчас не требуется."
        )
    return {
        "ok": False,
        "status": "resume_terminal" if state_kind == "terminal" else "resume_internal",
        "message": message,
        "task_id": bound_task_id,
    }


def _replacement_decision(response, bound_task_id, resume_kind, resume_payload):
    raw = extract_decision_request(response)
    replacement = normalize_decision_request(raw, task_id=bound_task_id) if raw else None
    if not replacement:
        return None
    replacement["task_id"] = bound_task_id
    if resume_kind == "retry_preflight_with_answer":
        replacement["resume"] = {
            "kind": "retry_preflight_with_answer",
            "method": "POST",
            "path": "/orchestrate_run",
            "body": {
                "task_id": bound_task_id,
                "message": str(resume_payload.get("message") or "").strip(),
            },
            "condition": "после твоего ответа продолжу ту же задачу",
        }
    replacement["decision_id"] = decision_version(replacement)
    return replacement


def _replacement_decision_result(request, replacement, bound_task_id, technical):
    return {
        "ok": False,
        "status": "needs_another_decision",
        "message": render_decision_request(replacement),
        "task_id": bound_task_id,
        "technical": technical,
    }


def _answer_resume_call(request, bound_task_id, answer):
    resume = request.get("resume") if isinstance(request.get("resume"), dict) else {}
    resume_kind = str(resume.get("kind") or "").strip()
    if resume_kind == "retry_preflight_with_answer":
        resume_body = resume.get("body") if isinstance(resume.get("body"), dict) else {}
        original_message = str(resume_body.get("message") or "").strip()
        resume_task_id = str(resume_body.get("task_id") or "").strip()
        if not original_message or resume_task_id != bound_task_id:
            return {
                "ok": False,
                "result": {
                    "ok": False,
                    "status": "invalid_resume_contract",
                    "message": "Ответ сохранил, но внутренний путь продолжения повреждён. Твой выбор не потерян.",
                    "task_id": bound_task_id,
                },
            }
        return {
            "ok": True,
            "resume_kind": resume_kind,
            "endpoint": f"{WARMASTER_BASE_URL}/orchestrate_run",
            "payload": {
                "message": original_message
                + "\n\nОтвет пользователя на уточнение к этой же задаче: "
                + answer,
                "task_id": bound_task_id,
                "auto_start": True,
                "reuse_existing": True,
                "run_mode": "http",
                "governor_transport": "http",
                "governor_host": "127.0.0.1",
                "host": "127.0.0.1",
                "include_brigade_health": False,
            },
        }
    return {
        "ok": True,
        "resume_kind": resume_kind,
        "endpoint": f"{WARMASTER_BASE_URL}/runs/{quote(bound_task_id, safe='')}/clarification",
        "payload": {"answer": answer},
    }


def _answer_request_conflict(task_id):
    return {
        "ok": False,
        "status": "answer_request_conflict",
        "message": "Этот идентификатор ответа уже связан с другим выбором. Ничего повторно не отправил.",
        "task_id": str(task_id or ""),
        "retryable": False,
    }


def _answer_reconcile_pending(request_id, task_id, error="", *, replay=False):
    if request_id:
        mark_answer_reconcile_pending(request_id, task_id, error)
    return {
        "ok": False,
        "status": "answer_reconcile_pending",
        "message": (
            "Этот ответ уже отправлял и повторно не посылаю. Подтверждение пока недоступно; "
            "вопрос оставил открытым. Повтори проверку позже тем же сообщением — "
            "я сначала сверю состояние, не отправляя ответ заново."
        ),
        "task_id": task_id,
        "retryable": True,
        "idempotent_replay": bool(replay),
        "technical": {"error": str(error or "")},
    }


def _reconcile_reserved_answer(receipt, answer, request_id, *, replay, error=""):
    bound_task_id = str(receipt.get("task_id") or "").strip()
    original_request = (
        receipt.get("request") if isinstance(receipt.get("request"), dict) else {}
    )
    run_state = _run_resume_state(bound_task_id)
    current_decision = (
        run_state.get("decision_request")
        if run_state.get("kind") == "waiting"
        and isinstance(run_state.get("decision_request"), dict)
        else None
    )
    if current_decision is not None:
        original_prompt_id = str(
            receipt.get("prompt_id") or decision_prompt_version(original_request)
        )
        if decision_prompt_version(current_decision) != original_prompt_id:
            resume_call = _answer_resume_call(original_request, bound_task_id, answer)
            if not resume_call.get("ok"):
                return _answer_reconcile_pending(
                    request_id,
                    bound_task_id,
                    "stored resume contract is invalid",
                    replay=replay,
                )
            replacement = _replacement_decision(
                {"decision_request": current_decision},
                bound_task_id,
                str(resume_call.get("resume_kind") or ""),
                resume_call.get("payload") if isinstance(resume_call.get("payload"), dict) else {},
            )
            if replacement:
                replacement_result = _replacement_decision_result(
                    original_request,
                    replacement,
                    bound_task_id,
                    {
                        "lost_ack": True,
                        "error": str(error or ""),
                        "authoritative_state": run_state,
                    },
                )
                if _commit_answer_transition(
                    original_request,
                    bound_task_id=bound_task_id,
                    answer=answer,
                    request_id=request_id,
                    result=replacement_result,
                    replacement=replacement,
                ):
                    replacement_result["idempotent_replay"] = bool(replay)
                    return replacement_result
    reconciled = _finish_reconciled_decision(original_request, bound_task_id, run_state)
    if reconciled:
        reconciled["technical"] = {
            "lost_ack": True,
            "error": str(error or ""),
            "authoritative_state": run_state,
        }
        if _commit_answer_transition(
            original_request,
            bound_task_id=bound_task_id,
            answer=answer,
            request_id=request_id,
            result=reconciled,
            clear=True,
        ):
            reconciled["idempotent_replay"] = bool(replay)
            return reconciled
    return _answer_reconcile_pending(
        request_id,
        bound_task_id,
        error or str(run_state.get("status") or "state unavailable"),
        replay=replay,
    )


def _http_error_json(exc):
    if not isinstance(exc, HTTPError):
        return {}
    try:
        raw = exc.read().decode("utf-8", errors="replace")
        payload = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001 - best-effort error body recovery
        return {}
    return payload if isinstance(payload, dict) else {}


def resume_pending_decision(task_id, answer, request_id=""):
    # Two clients can display the same question.  Serialize their answers by
    # the durable run identity so a retry cannot race a still-running forward.
    lock_key = f"pending-decision:{str(task_id or request_id or '').strip()}"
    with archive_state.CHAT_SESSION_LOCKS.hold(lock_key):
        return _resume_pending_decision_locked(task_id, answer, request_id=request_id)


def _resume_pending_decision_locked(task_id, answer, *, request_id=""):
    """Forward a chat answer only to a durably bound waiting run.

    The model may select the action, but it cannot invent authority: the task
    id must already exist in Archive's pending-decision store.
    """
    answer = trim_chat_text(answer)
    request_id = str(request_id or "").strip()[:200]
    receipt = find_answer_receipt(request_id) if request_id else None
    if receipt:
        expected_task_id = str(receipt.get("task_id") or "").strip()
        answer_sha256 = hashlib.sha256(answer.encode("utf-8")).hexdigest()
        if (
            (str(task_id or "").strip() and str(task_id or "").strip() != expected_task_id)
            or str(receipt.get("answer_sha256") or "") != answer_sha256
        ):
            return _answer_request_conflict(expected_task_id or task_id)
        replay = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
        if replay:
            return {
                **replay,
                "ok": bool(replay.get("ok")),
                "status": str(replay.get("status") or "answer_replayed"),
                "message": str(replay.get("message") or "Этот ответ я уже обработал."),
                "task_id": str(replay.get("task_id") or expected_task_id or task_id or ""),
                "idempotent_replay": True,
            }
        return _reconcile_reserved_answer(
            receipt,
            answer,
            request_id,
            replay=True,
        )
    request = find_pending_decision(task_id)
    if not request:
        return {
            "ok": False,
            "status": "no_pending_decision",
            "message": "У меня нет открытого вопроса, к которому можно привязать этот ответ.",
        }
    if not answer:
        return {
            "ok": False,
            "status": "empty_answer",
            "message": "Не понял сам выбор. Ответь одним из вариантов или скажи решение своими словами.",
        }
    bound_task_id = str(request.get("task_id") or "").strip()
    resume_call = _answer_resume_call(request, bound_task_id, answer)
    if not resume_call.get("ok"):
        return resume_call.get("result")
    resume_kind = str(resume_call.get("resume_kind") or "")
    endpoint = str(resume_call.get("endpoint") or "")
    resume_payload = (
        resume_call.get("payload") if isinstance(resume_call.get("payload"), dict) else {}
    )
    if request_id:
        try:
            reservation = reserve_answer_attempt(
                request_id,
                task_id=bound_task_id,
                answer=answer,
                request=request,
            )
        except Exception as exc:  # a non-durable answer must never be sent
            return {
                "ok": False,
                "status": "answer_reservation_failed",
                "message": "Не смог надёжно сохранить отправку ответа, поэтому ничего не отправил. Попробуй позже.",
                "task_id": bound_task_id,
                "retryable": True,
                "technical": {"error": str(exc)},
            }
        if not reservation.get("ok"):
            return _answer_request_conflict(bound_task_id)
        if reservation.get("created") is not True:
            return _reconcile_reserved_answer(
                reservation.get("receipt") if isinstance(reservation.get("receipt"), dict) else {},
                answer,
                request_id,
                replay=True,
            )
        receipt = reservation.get("receipt") if isinstance(reservation.get("receipt"), dict) else {}
    try:
        status, response = proxy_json_url(
            "POST",
            endpoint,
            payload=resume_payload,
            timeout=90,
        )
    except Exception as exc:  # noqa: BLE001 - keep the bound question durable
        failure_response = _http_error_json(exc)
        replacement = _replacement_decision(
            failure_response,
            bound_task_id,
            resume_kind,
            resume_payload,
        )
        if replacement:
            replacement_result = _replacement_decision_result(
                request,
                replacement,
                bound_task_id,
                {"error": str(exc), "response": failure_response},
            )
            _commit_answer_transition(
                request,
                bound_task_id=bound_task_id,
                answer=answer,
                request_id=request_id,
                result=replacement_result,
                replacement=replacement,
            )
            return replacement_result
        if request_id and receipt:
            return _reconcile_reserved_answer(
                receipt,
                answer,
                request_id,
                replay=False,
                error=str(exc),
            )
        # The gateway may have accepted the answer and only lost its HTTP ACK.
        # Its durable run state is the truth; never resend blindly in that case.
        run_state = _run_resume_state(bound_task_id)
        if (
            run_state.get("kind") == "waiting"
            and isinstance(run_state.get("decision_request"), dict)
        ):
            authoritative_replacement = _replacement_decision(
                {"decision_request": run_state["decision_request"]},
                bound_task_id,
                resume_kind,
                resume_payload,
            )
            if (
                authoritative_replacement
                and decision_prompt_version(run_state["decision_request"])
                != decision_prompt_version(request)
            ):
                replacement_result = _replacement_decision_result(
                    request,
                    authoritative_replacement,
                    bound_task_id,
                    {"lost_ack": True, "error": str(exc), "authoritative_state": run_state},
                )
                _commit_answer_transition(
                    request,
                    bound_task_id=bound_task_id,
                    answer=answer,
                    request_id=request_id,
                    result=replacement_result,
                    replacement=authoritative_replacement,
                )
                return replacement_result
        reconciled = _finish_reconciled_decision(request, bound_task_id, run_state)
        if reconciled:
            reconciled["technical"] = {"lost_ack": True, "error": str(exc)}
            _commit_answer_transition(
                request,
                bound_task_id=bound_task_id,
                answer=answer,
                request_id=request_id,
                result=reconciled,
                clear=True,
            )
            return reconciled
        return {
            "ok": False,
            "status": "resume_unavailable",
            "message": "Не смог передать ответ. Вопрос оставил открытым — повтори ответ позже.",
            "task_id": bound_task_id,
            "technical": {"error": str(exc)},
        }
    response = response if isinstance(response, dict) else {}
    replacement = _replacement_decision(response, bound_task_id, resume_kind, resume_payload)
    if replacement:
        replacement_result = _replacement_decision_result(request, replacement, bound_task_id, response)
        _commit_answer_transition(
            request,
            bound_task_id=bound_task_id,
            answer=answer,
            request_id=request_id,
            result=replacement_result,
            replacement=replacement,
        )
        return replacement_result
    accepted = 200 <= int(status or 0) < 300 and response.get("ok") is True
    if accepted:
        accepted_result = {
            "ok": True,
            "status": "resumed",
            "message": "Понял. Принял твой выбор и продолжаю ту же задачу.",
            "task_id": bound_task_id,
            "technical": response,
        }
        _commit_answer_transition(
            request,
            bound_task_id=bound_task_id,
            answer=answer,
            request_id=request_id,
            result=accepted_result,
            clear=True,
        )
        return accepted_result
    run_state = _run_resume_state(bound_task_id)
    reconciled = _finish_reconciled_decision(request, bound_task_id, run_state)
    if reconciled:
        reconciled["technical"] = response
        _commit_answer_transition(
            request,
            bound_task_id=bound_task_id,
            answer=answer,
            request_id=request_id,
            result=reconciled,
            clear=True,
        )
        return reconciled
    if request_id and receipt:
        return _answer_reconcile_pending(
            request_id,
            bound_task_id,
            str(response),
            replay=False,
        )
    return {
        "ok": False,
        "status": "resume_rejected",
        "message": "Не смог передать ответ. Вопрос оставил открытым — повтори ответ позже.",
        "task_id": bound_task_id,
        "technical": response,
    }


_CONTINUATION_MATCH_STOPWORDS = {
    "задача", "задачу", "задачи", "работа", "работу", "сделать", "создать",
    "продолжить", "доделать", "результат", "внутренней", "проверке", "для",
    "этой", "этот", "это", "мой", "моей", "нужно", "текущий", "текущую",
    "приложение", "приложения", "проект", "проекта", "система", "системы",
}


def _history_has_exact_task_id(value, task_id):
    if not value or not task_id:
        return False
    pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(task_id)}(?![A-Za-z0-9_.-])"
    return re.search(pattern, str(value)) is not None


def continuation_candidates_for_history(history, limit=5, excluded_task_ids=None):
    """Order trusted live candidates by the task most recently mentioned.

    Exact ids come only from the roster. History can rank those identities,
    but it can neither introduce an id nor turn an active/user-waiting run into
    a continuation candidate.
    """
    excluded = {
        str(task_id or "").strip()
        for task_id in (excluded_task_ids or [])
        if str(task_id or "").strip()
    }
    candidates = [
        dict(item)
        for item in continuable_tasks(limit=limit)
        if isinstance(item, dict)
        and str(item.get("parent_task_id") or "").strip() not in excluded
    ]
    if not candidates:
        return []
    history = [item for item in (history or []) if isinstance(item, dict)]
    by_id = {str(item.get("parent_task_id") or ""): item for item in candidates}
    root_id = ""
    for message in reversed(history):
        dedupe_key = str(message.get("dedupe_key") or "")
        content = str(message.get("content") or "").strip()
        exact_matches = [
            parent_task_id
            for parent_task_id in by_id
            if _history_has_exact_task_id(dedupe_key, parent_task_id)
            or _history_has_exact_task_id(content, parent_task_id)
        ]
        if len(exact_matches) == 1:
            root_id = exact_matches[0]
            if content and str(message.get("role") or "") == "assistant":
                by_id[root_id]["failure_summary"] = content[:1_200]
            break
        if len(exact_matches) > 1:
            # One journal item explicitly names several candidates. Looking
            # farther back would replace present ambiguity with an old guess.
            break

        message_terms = {
            token
            for token in memory_tokens(content) - _CONTINUATION_MATCH_STOPWORDS
            if len(token) >= 3 or "." in token
        }
        if not message_terms:
            continue
        scores = []
        for candidate_index, candidate in enumerate(candidates):
            goal_terms = {
                token
                for token in memory_tokens(candidate.get("goal")) - _CONTINUATION_MATCH_STOPWORDS
                if len(token) >= 3 or "." in token
            }
            scores.append(
                (
                    len(goal_terms & message_terms),
                    -candidate_index,
                    str(candidate.get("parent_task_id") or ""),
                )
            )
        scores.sort(reverse=True)
        top_score, _order, top_task_id = scores[0]
        runner_up = scores[1][0] if len(scores) > 1 else 0
        if top_score >= 2 and top_score - runner_up >= 2:
            root_id = top_task_id
            break
    if not root_id:
        if len(candidates) == 1:
            candidates[0]["context_root"] = True
        return candidates
    root = by_id[root_id]
    root["context_root"] = True
    return [root] + [item for item in candidates if item is not root]


def assemble_shushunya_turn_context(session_id, user_text, image_data_url="", model=None, payload=None):
    """Assemble the rich situation once, before action selection.

    The old controller saw only raw history while the answering model later saw
    persona, Magos and live task truth.  Core must see the same reality it will
    speak from, and the downstream answer path reuses this bundle.
    """
    payload = payload if isinstance(payload, dict) else {}
    ensure_core_transport_identity(payload)
    memory_namespace = shared_memory_namespace(payload.get("memory_namespace"))
    turn_id = str(payload.get("core_turn_id") or uuid.uuid4())
    artifact_audience_source = str(
        payload.get("artifact_audience_source")
        or payload.get("client_source")
        or payload.get("source")
        or "app"
    ).strip().lower()[:80] or "app"
    history = chat_history(session_id, limit=12, audience_source=artifact_audience_source)
    request_messages = messages_for_chat_context(
        session_id,
        "",
        user_text,
        image_data_url=image_data_url,
    )
    memory_messages = sanitize_messages_for_memory(request_messages)
    persona_message = persona_page_context(memory_namespace)
    magos_message = None
    magos_result = None
    diagnostics = {}
    magos = focus_components(memory_namespace)["magos"]
    focus_enabled = internal_flag(payload.get("focus_enabled", True), default=True)
    if focus_enabled and magos is not None:
        try:
            magos_message = magos.prepare_request(
                memory_messages,
                model=model or DEFAULT_MODEL,
                conversation_id=session_id,
                turn_id=turn_id,
                memory_namespace=memory_namespace,
            )
            magos_result = magos.last_result
        except Exception as exc:
            diagnostics["magos_error"] = str(exc)
            magos_result = {"error": str(exc)}
    roster_message = None if internal_flag(payload.get("system_event", False), default=False) else task_roster_note()
    reports = pending_summary()
    decisions = pending_decision_context()
    if decisions:
        reports = {**reports, "decision_requests": decisions}
    core_context = {
        "persona": str((persona_message or {}).get("content") or ""),
        "recalled_memory": str((magos_message or {}).get("content") or ""),
        "live_roster": str((roster_message or {}).get("content") or ""),
        "pending_reports": reports,
        "diagnostics": diagnostics,
    }
    return {
        "turn_id": turn_id,
        "memory_namespace": memory_namespace,
        "history": history,
        "request_messages": request_messages,
        "memory_messages": memory_messages,
        "persona_message": persona_message,
        "magos_message": magos_message,
        "magos_result": magos_result,
        "magos_attempted": bool(focus_enabled and magos is not None),
        "roster_message": roster_message,
        "core_context": core_context,
    }


def decide_chat_turn_action(session_id, text, image_data_url="", model=None, payload=None):
    LLM_PRIORITY.set("chat")
    payload = payload if isinstance(payload, dict) else {}
    user_text = trim_chat_text(text)
    source = str(
        payload.get("artifact_audience_source")
        or payload.get("client_source")
        or payload.get("source")
        or "app"
    ).strip().lower()[:80] or "app"
    open_decisions = pending_decision_context()
    try:
        context_bundle = assemble_shushunya_turn_context(
            session_id,
            user_text,
            image_data_url=image_data_url,
            model=model,
            payload=payload,
        )
    except Exception as exc:
        ensure_core_transport_identity(payload)
        context_bundle = {
            "turn_id": payload["core_turn_id"],
            "memory_namespace": shared_memory_namespace(payload.get("memory_namespace")),
            "history": [],
            "request_messages": [],
            "memory_messages": [],
            "persona_message": None,
            "magos_message": None,
            "magos_result": {"error": str(exc)},
            "magos_attempted": True,
            "roster_message": None,
            "core_context": {
                "persona": "",
                "recalled_memory": "",
                "live_roster": "",
                "pending_reports": {},
                "diagnostics": {"context_assembly_error": str(exc)[:2_000]},
            },
        }
    continuation_candidates = continuation_candidates_for_history(
        context_bundle.get("history") or [],
        excluded_task_ids={
            str(item.get("task_id") or "").strip()
            for item in open_decisions
            if isinstance(item, dict) and str(item.get("task_id") or "").strip()
        },
    )
    manifest = turn_capability_manifest(
        image_attached=bool(image_data_url),
        pending_reports=pending_summary(),
        pending_decisions=open_decisions,
        continuable_tasks=continuation_candidates,
        artifacts=artifact_catalog_for_query(
            shared_chat_session_id(session_id),
            audience_source=source,
            query=user_text,
            limit=ARTIFACT_CAPABILITY_LIMIT,
        ),
    )
    idempotency_key = str(payload.get("core_idempotency_key") or f"archive-turn:{context_bundle['turn_id']}")
    try:
        resolution = core_resolve_turn(
            idempotency_key=idempotency_key,
            session_id=session_id,
            memory_namespace=context_bundle["memory_namespace"],
            source=source,
            text=user_text,
            image_attached=bool(image_data_url),
            model=model or DEFAULT_MODEL,
            recent_history=context_bundle["history"],
            capability_manifest=manifest,
            context=context_bundle["core_context"],
        )
        decision = resolution.get("decision") if isinstance(resolution.get("decision"), dict) else {}
        if str(decision.get("action") or "") not in TURN_ACTIONS:
            raise ValueError("Core returned an unknown action")
        core_state = resolution.get("core") if isinstance(resolution.get("core"), dict) else {}
        if (
            core_state.get("degraded") is True
            and decision.get("action") in {"answer_in_chat", "ask_clarification"}
            and not str(decision.get("reply") or "").strip()
        ):
            decision = dict(decision)
            decision["reply"] = CORE_DEGRADED_SAFE_REPLY
        return {
            "decision": decision,
            "capabilities": manifest,
            "request": {"core_turn": idempotency_key, "context_diagnostics": context_bundle["core_context"]["diagnostics"]},
            "response": resolution.get("protocol"),
            "core_resolution": resolution,
            "context_bundle": context_bundle,
            "effect": resolution.get("effect") if isinstance(resolution.get("effect"), dict) else None,
        }
    except Exception as exc:
        # A failed Core decision must not launch a second unvalidated model
        # whose streamed promise could escape before any truth guard runs.
        return {
            "decision": {
                "action": "answer_in_chat",
                "reply": CORE_DEGRADED_SAFE_REPLY,
                "task": "",
                "warmaster_request": {},
                "confidence": 0.0,
                "reason": f"ShushunyaCore speech-only degradation: {exc}",
            },
            "capabilities": manifest,
            "request": {"core_turn": idempotency_key},
            "response": {"degraded": True, "error": str(exc)},
            "core_resolution": {"ok": False, "core": {"degraded": True, "error": str(exc)}},
            "context_bundle": context_bundle,
            "effect": None,
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


def create_administratum_task_from_intent(
    intent,
    session_id,
    client_source,
    *,
    dedupe_key="",
    created_from_message_id="",
):
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
        if created_from_message_id:
            stable = hashlib.sha256(created_from_message_id.encode("utf-8")).hexdigest()[:32]
            administratum_payload["id"] = f"core-watch-{stable}"
        try:
            _status, response = proxy_json_url("POST", f"{ADMINISTRATUM_BASE_URL}/watch", payload=administratum_payload, timeout=60)
            return {"created": bool(response.get("ok")), "watch": response.get("watch"), "intent": intent, "response": response}
        except Exception as exc:
            return {"created": False, "reason": "administratum_unavailable", "error": str(exc), "intent": intent}
    try:
        if dedupe_key:
            administratum_payload["dedupe_key"] = dedupe_key
        if created_from_message_id:
            administratum_payload["created_from_message_id"] = created_from_message_id
        _status, response = proxy_json_url("POST", f"{ADMINISTRATUM_BASE_URL}/task", payload=administratum_payload, timeout=60)
        return {"created": bool(response.get("ok")), "task": response.get("task"), "intent": intent, "response": response}
    except Exception as exc:
        return {"created": False, "reason": "administratum_unavailable", "error": str(exc), "intent": intent}


def _core_effect_receipt(effect_id, request_sha256):
    with sqlite3.connect(SQLITE_PATH, timeout=30) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM core_effect_receipts WHERE effect_id=?", (effect_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    if item["request_sha256"] != request_sha256:
        raise ValueError("effect_id was reused with a different request")
    item["intent"] = json.loads(item["intent_json"]) if item.get("intent_json") else None
    item["result"] = json.loads(item["result_json"]) if item.get("result_json") else None
    return item


def _reserve_core_effect_receipt(effect_id, request_sha256, *, intent=None):
    """Atomically claim an effect id before any downstream side effect."""
    now = now_iso()
    db = sqlite3.connect(SQLITE_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """
            INSERT OR IGNORE INTO core_effect_receipts(
                effect_id,request_sha256,intent_json,state,result_json,created_at,updated_at
            ) VALUES (?,?,?,?,NULL,?,?)
            """,
            (
                effect_id,
                request_sha256,
                json.dumps(intent, ensure_ascii=False, sort_keys=True) if intent is not None else None,
                "reserved",
                now,
                now,
            ),
        )
        row = db.execute("SELECT * FROM core_effect_receipts WHERE effect_id=?", (effect_id,)).fetchone()
        if row is None or row["request_sha256"] != request_sha256:
            raise ValueError("effect_id was reused with a different request")
        db.commit()
        item = dict(row)
        item["intent"] = json.loads(item["intent_json"]) if item.get("intent_json") else None
        item["result"] = json.loads(item["result_json"]) if item.get("result_json") else None
        return item
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _save_core_effect_receipt(effect_id, request_sha256, *, intent=None, state="classified", result=None):
    now = now_iso()
    db = sqlite3.connect(SQLITE_PATH, timeout=30)
    try:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT request_sha256 FROM core_effect_receipts WHERE effect_id=?",
            (effect_id,),
        ).fetchone()
        if existing is not None and existing[0] != request_sha256:
            raise ValueError("effect_id was reused with a different request")
        db.execute(
            """
            INSERT INTO core_effect_receipts(
                effect_id,request_sha256,intent_json,state,result_json,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(effect_id) DO UPDATE SET
                intent_json=COALESCE(excluded.intent_json,core_effect_receipts.intent_json),
                state=excluded.state,
                result_json=COALESCE(excluded.result_json,core_effect_receipts.result_json),
                updated_at=excluded.updated_at
            WHERE core_effect_receipts.request_sha256=excluded.request_sha256
            """,
            (
                effect_id,
                request_sha256,
                json.dumps(intent, ensure_ascii=False, sort_keys=True) if intent is not None else None,
                state,
                json.dumps(result, ensure_ascii=False, sort_keys=True) if result is not None else None,
                now,
                now,
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_core_administratum_effect(effect_id, payload):
    effect_id = str(effect_id or "").strip()
    payload = payload if isinstance(payload, dict) else {}
    task = trim_chat_text(payload.get("task") or payload.get("source_text") or "")
    if not effect_id or not task:
        return {
            "ok": False,
            "retryable": False,
            "code": "invalid_administratum_effect",
            "explanation": "В durable-эффекте нет effect_id или текста задачи.",
            "evidence": {},
        }
    request_payload = {
        "task": task,
        "session_id": shared_chat_session_id(payload.get("session_id") or SHARED_CHAT_SESSION_ID),
        "source": str(payload.get("source") or "shushunya-core")[:80],
        "model": str(payload.get("model") or DEFAULT_MODEL),
    }
    request_sha256 = hashlib.sha256(
        json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    receipt = _core_effect_receipt(effect_id, request_sha256)
    if receipt and receipt.get("result") and receipt.get("state") in {"delivered", "failed"}:
        return receipt["result"]
    intent = receipt.get("intent") if receipt else None
    if isinstance(intent, dict) and str(intent.get("intent") or "") == "error":
        # A transport/model failure is not a classification. Reusing it would
        # make every idempotent delivery retry fail forever without asking the
        # classifier again.
        intent = None
    if not isinstance(intent, dict):
        intent = detect_administratum_intent(task, model=request_payload["model"])
        intent = normalize_intent(intent)
        if str(intent.get("intent") or "") != "error":
            _save_core_effect_receipt(effect_id, request_sha256, intent=intent, state="classified")
    created = create_administratum_task_from_intent(
        intent,
        request_payload["session_id"],
        request_payload["source"],
        dedupe_key=f"shushunya-core:{effect_id}",
        created_from_message_id=effect_id,
    )
    delegate = created.get("task") if isinstance(created.get("task"), dict) else created.get("watch")
    delegate = delegate if isinstance(delegate, dict) else {}
    if created.get("created"):
        result = {
            "ok": True,
            "retryable": False,
            "delegate_ref": str(delegate.get("id") or ""),
            "status": "created",
            "explanation": "Administratum подтвердил запись задачи.",
            "evidence": created,
        }
        _save_core_effect_receipt(effect_id, request_sha256, intent=intent, state="delivered", result=result)
        return result
    reason = str(created.get("reason") or "not_created")
    retryable = reason == "administratum_unavailable" or str(intent.get("intent") or "") == "error"
    result = {
        "ok": False,
        "retryable": retryable,
        "code": "administratum_unavailable" if retryable else "administratum_needs_clarification",
        "status": reason,
        "explanation": (
            "Administratum сейчас недоступен; эффект можно безопасно повторить с тем же id."
            if retryable
            else f"Задача не создана: {reason}. Нужны недостающие параметры или подтверждение владельца."
        ),
        "evidence": created,
    }
    _save_core_effect_receipt(
        effect_id,
        request_sha256,
        intent=intent,
        state="retry_wait" if retryable else "failed",
        result=result,
    )
    return result


def run_core_notification_effect(effect_id, payload):
    """Idempotently publish one Core lifecycle stop to shared chat and Vox."""
    effect_id = str(effect_id or "").strip()
    payload = payload if isinstance(payload, dict) else {}
    if not effect_id or len(effect_id) > 120 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", effect_id):
        return {
            "ok": False,
            "retryable": False,
            "code": "invalid_notification_effect",
            "explanation": "В durable-эффекте отсутствует безопасный effect_id.",
            "evidence": {},
        }
    if payload.get("needs_user") is not False:
        return {
            "ok": False,
            "retryable": False,
            "code": "invalid_notification_authority",
            "explanation": "Уведомление без typed decision не может запрашивать решение владельца.",
            "evidence": {},
        }
    goal = conversational_text(payload.get("goal"), fallback="эта задача").replace("?", ".").strip().rstrip(" .")
    explanation = conversational_text(
        payload.get("explanation"),
        fallback="безопасные автоматические попытки продолжения исчерпаны",
    ).replace("?", ".").strip().rstrip(" .")
    required_action = conversational_text(
        payload.get("required_action"),
        fallback="сформировать новую проверяемую стратегию продолжения",
    ).replace("?", ".").strip().rstrip(" .")
    request_payload = {
        "kind": "commitment_stalled",
        "commitment_id": str(payload.get("commitment_id") or "")[:160],
        "task_id": str(payload.get("task_id") or "")[:160],
        "goal": goal[:500],
        "explanation": explanation[:1200],
        "required_action": required_action[:1200],
        "needs_user": False,
    }
    request_sha256 = hashlib.sha256(
        json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    receipt = _core_effect_receipt(effect_id, request_sha256)
    if receipt and receipt.get("result") and receipt.get("state") == "delivered":
        return receipt["result"]
    _reserve_core_effect_receipt(effect_id, request_sha256)

    message = (
        f"Я пока не могу продолжить задачу «{goal}». Причина: {explanation}. "
        f"Что нужно исправить: {required_action}. От тебя сейчас ничего не требуется."
    )
    report_id = enqueue_report(
        "shushunya-core",
        "task_stalled_internal",
        "работа остановилась: " + goal[:120],
        message,
        dedupe_key=f"core-notification:{effect_id}",
    )
    try:
        message_id = append_chat_message(
            SHARED_CHAT_SESSION_ID,
            "assistant",
            message,
            source="shushunya-core",
            dedupe_key=f"core-notification:{effect_id}:chat",
        )
    except Exception:
        message_id = None
    conveyed = bool(mark_delivered([report_id])) if report_id and message_id else False
    if report_id and message_id and conveyed:
        result = {
            "ok": True,
            "retryable": False,
            "delegate_ref": str(message_id),
            "status": "delivered",
            "explanation": "Archive сохранил уведомление в чате и Vox.",
            "evidence": {
                "message_id": message_id,
                "report_id": int(report_id),
                "conveyed": True,
                "needs_user": False,
            },
        }
        _save_core_effect_receipt(
            effect_id,
            request_sha256,
            state="delivered",
            result=result,
        )
        return result
    result = {
        "ok": False,
        "retryable": True,
        "code": "notification_delivery_incomplete",
        "status": "retry_wait",
        "explanation": "Archive ещё не подтвердил одновременно запись в чат и Vox.",
        "evidence": {
            "message_id": message_id,
            "report_id": int(report_id) if report_id else None,
            "conveyed": conveyed,
            "needs_user": False,
        },
    }
    _save_core_effect_receipt(
        effect_id,
        request_sha256,
        state="retry_wait",
        result=result,
    )
    return result


def run_core_artifact_effect(effect_id, payload):
    """Idempotently attach one catalogued artifact to its scoped chat session."""
    effect_id = str(effect_id or "").strip()
    payload = payload if isinstance(payload, dict) else {}
    artifact_id = str(payload.get("artifact_id") or "").strip().lower()
    if not effect_id or len(effect_id) > 120 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", effect_id):
        return {
            "ok": False,
            "retryable": False,
            "code": "invalid_artifact_effect",
            "explanation": "В durable-эффекте отсутствует безопасный effect_id.",
            "evidence": {},
        }
    session_id = shared_chat_session_id(payload.get("session_id") or SHARED_CHAT_SESSION_ID)
    client_source = str(payload.get("artifact_audience_source") or payload.get("source") or "app").strip().lower()[:80] or "app"
    if client_source != "*" and not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,127}", client_source):
        return {
            "ok": False,
            "retryable": False,
            "code": "invalid_artifact_effect",
            "explanation": "В durable-эффекте отсутствует безопасный audience source.",
            "evidence": {},
        }
    client_request_id = "".join(
        char
        for char in str(payload.get("client_request_id") or "").strip()
        if char.isalnum() or char in "-_.:"
    )[:160]
    request_payload = {
        "artifact_id": artifact_id,
        "session_id": session_id,
        "source": client_source,
        "client_request_id": client_request_id,
    }
    request_sha256 = hashlib.sha256(
        json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    receipt = _reserve_core_effect_receipt(
        effect_id,
        request_sha256,
        intent={"kind": "artifact_delivery"},
    )
    if receipt and receipt.get("result") and receipt.get("state") in {"delivered", "failed"}:
        return receipt["result"]
    try:
        delivery = attach_artifact_to_chat(
            artifact_id,
            session_id=session_id,
            audience_source=client_source,
            effect_id=effect_id,
            client_request_id=client_request_id or None,
        )
    except (ArtifactError, OSError, sqlite3.Error) as exc:
        print(
            f"artifact atomic delivery failed for {artifact_id}: {type(exc).__name__}: {exc}",
            flush=True,
        )
        result = {
            "ok": False,
            "retryable": True,
            "code": "artifact_delivery_integrity_error",
            "status": "retry_wait",
            "artifact_id": artifact_id,
            "explanation": "Archive не подтвердил целостность файла и атомарную запись в чат; эффект сохранён для безопасного повтора.",
            "evidence": {"artifact_id": artifact_id, "session_id": session_id},
        }
        _save_core_effect_receipt(
            effect_id,
            request_sha256,
            intent={"kind": "artifact_delivery"},
            state="retry_wait",
            result=result,
        )
        return result
    if delivery is None:
        result = {
            "ok": False,
            "retryable": False,
            "code": "artifact_not_available",
            "status": "failed",
            "artifact_id": artifact_id,
            "explanation": "Файл не приложен: artifact_id отсутствует или недоступен для этой сессии и предъявленного ключа.",
            "evidence": {"artifact_id": artifact_id, "session_id": session_id},
        }
        _save_core_effect_receipt(
            effect_id,
            request_sha256,
            intent={"kind": "artifact_delivery"},
            state="failed",
            result=result,
        )
        return result
    artifact = delivery["artifact"]
    message_id = int(delivery["message_id"])
    caption = str(delivery["caption"])
    result = {
        "ok": True,
        "retryable": False,
        "delegate_ref": str(message_id),
        "artifact_id": artifact_id,
        "caption": caption,
        "status": "delivered",
        "explanation": "Archive приложил зарегистрированный файл к чату владельца.",
        "evidence": {"message_id": int(message_id), "artifact": artifact},
    }
    _save_core_effect_receipt(
        effect_id,
        request_sha256,
        intent={"kind": "artifact_delivery"},
        state="delivered",
        result=result,
    )
    return result


def administratum_intent_context(result):
    if not result:
        return None
    if result.get("created") and isinstance(result.get("task"), dict):
        task = result["task"]
        return {
            "role": "system",
            "content": (
                "A task was actually created. Confirm it in first person without naming internal services or ids.\n"
                f"title: {task.get('title')}\ndue_at: {task.get('due_at')}\n"
                f"interval: {task.get('interval')}\nnext_run: {task.get('next_run')}"
            ),
        }
    if result.get("created") and isinstance(result.get("watch"), dict):
        watch = result["watch"]
        return {
            "role": "system",
            "content": (
                "A watch was actually created. Confirm it in first person without naming internal services or ids.\n"
                f"title: {watch.get('title')}\nwatch_type: {watch.get('watch_type')}\n"
                f"target: {watch.get('target')}\ncondition_json: {watch.get('condition_json')}"
            ),
        }
    if result.get("reason") == "reminder_without_schedule":
        return {
            "role": "system",
            "content": (
                "A reminder was requested but no time or interval was given, so nothing was created. "
                "Do not claim it was recorded. Ask when to remind, in one short question."
            ),
        }
    if result.get("reason") == "confirmation_required":
        return {
            "role": "system",
            "content": (
                "A possible routine/watch was detected but not created because confirmation is required. "
                f"Ask one concise clarification. Parsed intent: {json.dumps(result.get('intent') or {}, ensure_ascii=False)}"
            ),
        }
    if result.get("reason") == "administratum_unavailable":
        return {
            "role": "system",
            "content": (
                "The requested reminder/task could not be created. "
                f"Reason: {conversational_text(result.get('error'))}. Say this clearly in first person without internal names."
            ),
        }
    if result.get("reason") == "low_confidence_or_missing_title":
        return {
            "role": "system",
            "content": (
                "The task was not created because its details were incomplete or low-confidence. "
                "Do not claim that a reminder/task was recorded. Ask one concise clarification if the user seems to want a reminder. "
                f"Parsed intent: {json.dumps(result.get('intent') or {}, ensure_ascii=False)}"
            ),
        }
    return None


def maybe_write_archives(record):
    if record.get("archive_enabled", True):
        write_archives(record)


_LIBRARIAN_STATE_LOCK = threading.Lock()
_LIBRARIAN_INFLIGHT = {"running": False, "rerun": False}


def maybe_update_focus_memory(record):
    """Run the librarian after a turn. Coalesced single-flight: if one is already
    running, just flag a rerun instead of stacking another (the running cycle
    already syncs every turn since last_sync, so stacking is pure waste and GPU
    contention). The in-flight cycle drains the rerun before exiting, so the
    latest turn is always consolidated."""
    if not record.get("archive_enabled", True):
        return
    with _LIBRARIAN_STATE_LOCK:
        if _LIBRARIAN_INFLIGHT["running"]:
            _LIBRARIAN_INFLIGHT["rerun"] = True
            return
        _LIBRARIAN_INFLIGHT["running"] = True
    LLM_PRIORITY.set("librarian")  # memory consolidation outranks a fresh answer
    try:
        while True:
            with MAINTENANCE_LOCK:
                update_focus_memory(record)
            with _LIBRARIAN_STATE_LOCK:
                if not _LIBRARIAN_INFLIGHT["rerun"]:
                    _LIBRARIAN_INFLIGHT["running"] = False
                    return
                _LIBRARIAN_INFLIGHT["rerun"] = False  # drain: one more pass catches turns that arrived mid-run
    except Exception:
        with _LIBRARIAN_STATE_LOCK:
            _LIBRARIAN_INFLIGHT["running"] = False
            _LIBRARIAN_INFLIGHT["rerun"] = False
        raise


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
                artifact_id TEXT,
                client_request_id TEXT,
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
        if "artifact_id" not in mobile_message_columns:
            db.execute("ALTER TABLE mobile_chat_messages ADD COLUMN artifact_id TEXT")
        if "client_request_id" not in mobile_message_columns:
            db.execute("ALTER TABLE mobile_chat_messages ADD COLUMN client_request_id TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS idx_mobile_chat_messages_session_id ON mobile_chat_messages(session_id, id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_mobile_chat_messages_artifact_id ON mobile_chat_messages(artifact_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_mobile_chat_messages_client_request ON mobile_chat_messages(session_id, client_request_id)")
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
        db.execute(
            """
            UPDATE mobile_jobs
            SET status='interrupted',updated_at=?,
                error='Archive restarted before the durable turn job recorded its result; retry the same client_request_id.'
            WHERE status IN ('queued','running')
            """,
            (now_iso(),),
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS core_effect_receipts (
                effect_id TEXT PRIMARY KEY,
                request_sha256 TEXT NOT NULL,
                intent_json TEXT,
                state TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    init_artifact_storage()


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


def stream_chat_completion(prepared_payload, on_token):
    """Stream tokens from llama.cpp and forward each visible content delta to
    on_token, while returning the same (status, response, assistant) shape the
    blocking path produces — so all downstream persistence stays identical."""
    streaming_payload = dict(prepared_payload)
    streaming_payload["stream"] = True
    parts = []
    finish_reason = None
    upstream = open_upstream("POST", "/v1/chat/completions", payload=streaming_payload, timeout=600)
    try:
        for raw in upstream:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choice = (chunk.get("choices") or [{}])[0]
            if choice.get("finish_reason"):
                finish_reason = choice.get("finish_reason")
            # Only the visible answer is streamed to the user; any reasoning
            # tokens are ignored, matching what the blocking path would keep.
            piece = (choice.get("delta") or {}).get("content")
            if piece:
                parts.append(piece)
                try:
                    on_token(piece)
                except Exception:  # noqa: BLE001 - a dropped client must not kill generation bookkeeping
                    pass
    finally:
        try:
            upstream.close()
        except Exception:  # noqa: BLE001
            pass
    full = "".join(parts)
    assistant = {"role": "assistant", "content": full}
    response = {
        "object": "chat.completion",
        "model": prepared_payload.get("model"),
        "choices": [{"index": 0, "finish_reason": finish_reason or "stop", "message": assistant}],
    }
    return 200, response, assistant


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
