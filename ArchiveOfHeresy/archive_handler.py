"""ArchiveOfHeresy HTTP request handler (all gateway/proxy/memory routes)."""
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError, URLError
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import archive_state
from archive_config import *  # noqa: F401,F403
from archive_httpio import *  # noqa: F401,F403
from archive_util import *  # noqa: F401,F403
from archive_ops import *  # noqa: F401,F403
from archive_state import (ARCHIVE_LOCK, CHAT_QUEUE_LOCK, CHAT_QUEUE_WAIT_TIMEOUT_SEC, ChatQueueBusy,
    MAINTENANCE_LOCK, MOBILE_JOB_LOCK, TimedChatQueueLock)
from archivist_agent import Librarian
from archivist_agent.agent import FocusBookshelf, WikiBookshelf
from archivist_agent.graph_memory import GRAPH_TOP_K, GraphMemory
from archivist_agent.magos_agent import MAGOS_CONTEXT_LAYERS, Magos
from archivist_agent.quality_report import generate_quality_report
from archivist_agent.vector_memory import VECTOR_TOP_K, VectorMemory, latest_user_message
from task_journal import final_response_message_from_orchestration


class ArchiveHandler(BaseHTTPRequestHandler):
    server_version = "ArchiveOfHeresy/0.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def do_GET(self):
        if self.path.startswith("/archive/client/"):
            self.path = "/archive/mobile/" + self.path[len("/archive/client/") :]

        if self.path == "/health":
            namespaces = known_memory_namespaces()
            write_json(
                self,
                200,
                {
                    "status": "ok",
                    "service": "ArchiveOfHeresy",
                    "llm_base_url": LLM_BASE_URL,
                    "jsonl_root": str(JSONL_ROOT),
                    "memory_events_root": str(MEMORY_EVENTS_ROOT),
                    "sqlite_path": str(SQLITE_PATH),
                    "reports_root": str(REPORTS_ROOT),
                    "chat_context_messages": CHAT_CONTEXT_MESSAGES,
                    "magos_context_layers": sorted(MAGOS_CONTEXT_LAYERS),
                    "direct_injection": {
                        "vector": VECTOR_INJECTION_ENABLED,
                        "graph": GRAPH_INJECTION_ENABLED,
                    },
                    "vector_embedding": archive_state.VECTOR_MEMORY.embedding_status() if archive_state.VECTOR_MEMORY else {},
                    "memory_quality_report": {
                        "enabled": MEMORY_QUALITY_REPORT_ENABLED,
                        "hour": MEMORY_QUALITY_REPORT_HOUR,
                    },
                    "focus_root": str(FOCUS_ROOT),
                    "focus_namespaces": {
                        namespace: str(focus_root_for_namespace(namespace))
                        for namespace in namespaces
                    },
                    "wiki_root": str(WIKI_ROOT),
                    "wiki_namespaces": {
                        namespace: str(wiki_root_for_namespace(namespace))
                        for namespace in namespaces
                    },
                    "vector_root": str(VECTOR_ROOT),
                    "graph_root": str(GRAPH_ROOT),
                    "graph_namespaces": {
                        namespace: str(graph_root_for_namespace(namespace))
                        for namespace in namespaces
                    },
                },
            )
            return

        if self.path.startswith("/archive/mobile/chat/messages") or self.path.startswith("/archive/chat/messages"):
            if not require_auth(self, allow_mobile=True):
                return
            session_id = "default"
            limit = CHAT_HISTORY_LIMIT
            after_id = 0
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                session_id = shared_chat_session_id((params.get("session_id") or [SHARED_CHAT_SESSION_ID])[0])
                try:
                    limit = int((params.get("limit") or [CHAT_HISTORY_LIMIT])[0])
                except (TypeError, ValueError):
                    limit = CHAT_HISTORY_LIMIT
                try:
                    after_id = int((params.get("after_id") or [0])[0])
                except (TypeError, ValueError):
                    after_id = 0
            else:
                session_id = shared_chat_session_id(SHARED_CHAT_SESSION_ID)
            write_json(
                self,
                200,
                {
                    "session_id": session_id,
                    "messages": chat_history(session_id, limit=limit, after_id=after_id),
                    "source_of_truth": "server",
                },
            )
            return

        if self.path.startswith("/archive/chat/reports/pending"):
            if not require_auth(self, allow_mobile=True):
                return
            write_json(self, 200, {"ok": True, **pending_summary()})
            return

        if self.path == "/archive/mobile/warmaster/state":
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_state()
            return

        if self.path.startswith("/archive/mobile/warmaster/tasks"):
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_tasks()
            return

        if self.path.startswith("/archive/mobile/warmaster/task"):
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_task()
            return

        if self.path.startswith("/archive/mobile/warmaster/last-task"):
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_last_task()
            return

        if self.path == "/archive/mobile/agent/state":
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_state()
            return

        if self.path.startswith("/archive/mobile/job"):
            if not require_auth(self, allow_mobile=True):
                return
            params = parse_qs(urlsplit(self.path).query)
            job_id = (params.get("job_id") or [""])[0].strip()
            write_json(self, 200 if job_id else 400, mobile_job_snapshot(job_id) if job_id else {"ok": False, "error": "missing job_id"})
            return

        if self.path.startswith("/archive/mobile/agent/tasks"):
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_tasks()
            return

        if self.path.startswith("/archive/mobile/agent/task"):
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_task()
            return

        if self.path.startswith("/archive/mobile/agent/last-task"):
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_last_task()
            return

        if not require_auth(self):
            return

        if self.path.startswith("/archive/focus/active"):
            namespace = "default"
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
            write_json(
                self,
                200,
                {
                    "memory_namespace": namespace,
                    "focus_context": active_focus_context(namespace),
                    "max_chars": FOCUS_CONTEXT_CHARS,
                },
            )
            return

        if self.path.startswith("/archive/vector/search"):
            query = ""
            namespace = "default"
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                query = (params.get("q") or [""])[0]
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
            matches = archive_state.VECTOR_MEMORY.search(query, memory_namespace=namespace) if archive_state.VECTOR_MEMORY and query else []
            write_json(self, 200, {"query": query, "memory_namespace": namespace, "matches": matches})
            return

        if self.path.startswith("/archive/graph/search"):
            query = ""
            namespace = "default"
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                query = (params.get("q") or [""])[0]
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
            graph_memory = graph_memory_for_namespace(namespace)
            matches = graph_memory.search(query) if graph_memory and query else {"nodes": [], "edges": []}
            write_json(self, 200, {"query": query, "memory_namespace": namespace, "matches": matches})
            return

        if self.path.startswith("/archive/memory/events"):
            namespace = None
            limit = 50
            component = ""
            event_action = ""
            requester = ""
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                raw_namespace = (params.get("namespace") or [""])[0]
                namespace = safe_memory_namespace(raw_namespace) if raw_namespace else None
                component = (params.get("component") or [""])[0]
                event_action = (params.get("event_action") or [""])[0]
                requester = (params.get("requester") or [""])[0]
                try:
                    limit = int((params.get("limit") or ["50"])[0])
                except (TypeError, ValueError):
                    limit = 50
            write_json(
                self,
                200,
                {
                    "memory_namespace": namespace,
                    "limit": max(1, min(limit, 500)),
                    "component": component or None,
                    "event_action": event_action or None,
                    "requester": requester or None,
                    "events": recent_memory_events(
                        limit=limit,
                        memory_namespace=namespace,
                        component=component,
                        event_action=event_action,
                        requester=requester,
                    ),
                },
            )
            return

        if self.path.startswith("/archive/memory/gateway"):
            write_json(self, 200, memory_gateway_manifest())
            return

        if self.path.startswith("/archive/memory/catalog"):
            namespace = "default"
            requester = "unknown"
            create_namespace = False
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
                requester = (params.get("requester") or ["unknown"])[0]
                create_namespace = internal_flag((params.get("create") or [False])[0], default=False)
            if not allow_gateway_namespace(self, namespace, create=create_namespace):
                return
            payload = memory_catalog(namespace)
            write_gateway_event(
                namespace,
                "catalog",
                requester=requester,
                focus_books=len(payload.get("focus", {}).get("books", [])),
                wiki_pages=len(payload.get("wiki", {}).get("pages", [])),
            )
            write_json(self, 200, payload)
            return

        if self.path.startswith("/archive/memory/search"):
            namespace = "default"
            query = ""
            limit = 5
            requester = "unknown"
            create_namespace = False
            include_content = False
            layers = ""
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
                query = (params.get("q") or [""])[0]
                requester = (params.get("requester") or ["unknown"])[0]
                create_namespace = internal_flag((params.get("create") or [False])[0], default=False)
                include_content = internal_flag((params.get("include_content") or [False])[0], default=False)
                layers = (params.get("layers") or [""])[0]
                try:
                    limit = int((params.get("limit") or ["5"])[0])
                except (TypeError, ValueError):
                    limit = 5
            if not allow_gateway_namespace(self, namespace, create=create_namespace):
                return
            if not query.strip():
                write_json(self, 400, {"error": "Missing required query parameter: q", "memory_namespace": namespace})
                return
            try:
                payload = memory_search(namespace, query, limit=limit, include_content=include_content, layers=layers)
            except ValueError as exc:
                write_json(
                    self,
                    400,
                    {
                        "error": str(exc),
                        "memory_namespace": namespace,
                        "allowed_layers": sorted(GATEWAY_SEARCH_LAYERS),
                    },
                )
                return
            write_gateway_event(
                namespace,
                "search",
                requester=requester,
                query=trim_memory_text(query, 300),
                include_content=include_content,
                layers=payload.get("layers"),
                focus_matches=len(payload.get("focus", [])),
                wiki_matches=len(payload.get("wiki", [])),
                vector_matches=len(payload.get("vector", [])),
                graph_nodes=len(payload.get("graph", {}).get("nodes", [])),
            )
            write_json(self, 200, payload)
            return

        if self.path.startswith("/archive/memory/focus"):
            namespace = "default"
            focus_id = ""
            active = False
            requester = "unknown"
            create_namespace = False
            max_chars = 12000
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
                focus_id = (params.get("id") or [""])[0]
                active = focus_id in ("", "active")
                requester = (params.get("requester") or ["unknown"])[0]
                create_namespace = internal_flag((params.get("create") or [False])[0], default=False)
                max_chars = parse_max_chars((params.get("max_chars") or [12000])[0])
            if not allow_gateway_namespace(self, namespace, create=create_namespace):
                return
            bookshelf = focus_components(namespace)["bookshelf"]
            index = bookshelf.load_index()
            focus = find_focus(index, focus_id=focus_id, active=active)
            if not focus:
                write_json(self, 404, {"error": "Focus not found", "memory_namespace": namespace, "id": focus_id or "active"})
                return
            write_gateway_event(
                namespace,
                "read_focus",
                requester=requester,
                focus_id=focus.get("id"),
                title=focus.get("title"),
                active=focus.get("id") == index.get("active_id"),
            )
            content_payload = gateway_book_payload(bookshelf.read_focus(focus), max_chars)
            write_json(
                self,
                200,
                {
                    "memory_namespace": namespace,
                    "focus": focus,
                    **content_payload,
                },
            )
            return

        if self.path.startswith("/archive/memory/wiki"):
            namespace = "default"
            page_id = ""
            title = ""
            requester = "unknown"
            create_namespace = False
            max_chars = 12000
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
                page_id = (params.get("id") or [""])[0]
                title = (params.get("title") or [""])[0]
                requester = (params.get("requester") or ["unknown"])[0]
                create_namespace = internal_flag((params.get("create") or [False])[0], default=False)
                max_chars = parse_max_chars((params.get("max_chars") or [12000])[0])
            if not allow_gateway_namespace(self, namespace, create=create_namespace):
                return
            bookshelf = wiki_bookshelf_for_namespace(namespace)
            index = bookshelf.load_index()
            page = bookshelf.find_page(index, page_id=page_id or None, title=title or None)
            if not page:
                write_gateway_event(
                    namespace,
                    "read_wiki_miss",
                    requester=requester,
                    page_id=page_id,
                    title=title,
                )
                write_json(
                    self,
                    404,
                    {"error": "Wiki page not found", "memory_namespace": namespace, "id": page_id, "title": title},
                )
                return
            write_gateway_event(
                namespace,
                "read_wiki",
                requester=requester,
                page_id=page.get("id"),
                title=page.get("title"),
            )
            content_payload = gateway_book_payload(bookshelf.read_page(page), max_chars)
            write_json(
                self,
                200,
                {
                    "memory_namespace": namespace,
                    "page": page,
                    **content_payload,
                },
            )
            return

        if self.path == "/v1/models":
            self.forward("GET", self.path)
            return

        write_json(self, 404, {"error": "Not found"})

    def do_POST(self):
        if self.path.startswith("/archive/client/"):
            self.path = "/archive/mobile/" + self.path[len("/archive/client/") :]

        if self.path in {"/archive/mobile/chat/completions", "/archive/chat/completions"}:
            if not require_auth(self, allow_mobile=True):
                return
            try:
                self.mobile_chat_completion()
            except ChatQueueBusy as exc:
                write_json(self, 503, {"error": str(exc), "type": "chat_queue_busy"})
            return

        if self.path == "/archive/mobile/chat/start":
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_chat_start()
            return

        if self.path == "/archive/chat/reports/enqueue":
            if not require_auth(self, allow_mobile=True):
                return
            try:
                payload = read_json(self)
            except json.JSONDecodeError as exc:
                write_json(self, 400, {"ok": False, "error": f"Invalid JSON: {exc}"})
                return
            report_id = enqueue_report(
                payload.get("source"),
                payload.get("kind"),
                payload.get("topic"),
                payload.get("body"),
                dedupe_key=payload.get("dedupe_key"),
            )
            write_json(self, 201 if report_id else 400, {"ok": bool(report_id), "report_id": report_id, **pending_summary()})
            return

        if self.path == "/archive/chat/reports/deliver":
            if not require_auth(self, allow_mobile=True):
                return
            summary = pending_summary()
            if not summary["count"]:
                write_json(self, 200, {"ok": True, "delivered": 0, "message": "очередь докладов пуста"})
                return
            job_payload = {
                "session_id": SHARED_CHAT_SESSION_ID,
                "client_source": "report-button",
                "source": "report-button",
                "system_event": True,
                "intent_detection": False,
                "turn_decision": {"action": "deliver_pending_reports"},
                "text": "[Кнопка доклада] Владелец нажал кнопку и разрешил доложить накопленное.",
                "stream": False,
            }
            job_id = create_mobile_job("chat", job_payload)
            run_mobile_job(job_id, lambda payload=job_payload: run_mobile_chat_payload(payload))
            write_json(self, 202, {"ok": True, "job_id": job_id, "pending": summary["count"], "status": "queued"})
            return

        if self.path == "/archive/mobile/translate":
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_proxy_json(f"{TRANSLATOR_BASE_URL}/translate", timeout=180)
            return

        if self.path == "/archive/mobile/translate/start":
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_translate_start()
            return

        if self.path in ("/archive/mobile/stt-live", "/archive/mobile/stt-pcm"):
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_proxy_stt("/stt-live" if self.path.endswith("stt-live") else "/stt-pcm")
            return

        if self.path == "/archive/mobile/warmaster/run":
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_run()
            return

        if self.path == "/archive/mobile/warmaster/start":
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_start()
            return

        if self.path == "/archive/mobile/warmaster/cancel":
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_cancel()
            return

        if self.path == "/archive/mobile/agent/run":
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_run()
            return

        if self.path == "/archive/mobile/agent/start":
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_start()
            return

        if self.path == "/archive/mobile/agent/cancel":
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_cancel()
            return

        if self.path == "/archive/mobile/agent/run-stream":
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_agent_stream_unsupported()
            return

        if self.path == "/v1/chat/completions":
            if not require_auth(self):
                return
            try:
                self.chat_completion()
            except ChatQueueBusy as exc:
                write_json(self, 503, {"error": str(exc), "type": "chat_queue_busy"})
            return

        if not require_auth(self):
            return

        if self.path == "/archive/memory/propose-change":
            self.memory_propose_change()
            return

        write_json(self, 404, {"error": "Not found"})

    def write_proxy_error(self, exc):
        try:
            error_payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            error_payload = {"error": str(exc)}
        write_json(self, exc.code, error_payload)

    def mobile_proxy_json(self, url, timeout=180):
        try:
            payload = read_json(self)
            status, response = proxy_json_url("POST", url, payload=payload, timeout=timeout)
            write_json(self, status, response)
        except json.JSONDecodeError as exc:
            write_json(self, 400, {"error": f"Invalid JSON: {exc}"})
        except HTTPError as exc:
            self.write_proxy_error(exc)
        except Exception as exc:
            write_json(self, 502, {"error": f"mobile backend unavailable: {exc}"})

    def mobile_proxy_stt(self, upstream_path):
        try:
            body = read_raw_body(self)
            headers = {
                "Content-Type": self.headers.get("Content-Type", "application/octet-stream"),
                "Accept": "application/json",
                "X-Language": self.headers.get("X-Language", ""),
                "X-Sample-Rate": self.headers.get("X-Sample-Rate", "16000"),
            }
            status, response = proxy_binary_url(
                "POST",
                f"{STT_BASE_URL}{upstream_path}",
                body,
                headers=headers,
                timeout=240,
            )
            write_json(self, status, response)
        except HTTPError as exc:
            self.write_proxy_error(exc)
        except Exception as exc:
            write_json(self, 502, {"error": f"stt backend unavailable: {exc}"})

    def warmaster_event_as_agent_event(self, event, index, total):
        display = event.get("display") if isinstance(event, dict) else {}
        payload = event.get("payload") if isinstance(event, dict) else {}
        headline = str(display.get("headline") or event.get("type") or "Warmaster event").strip()
        detail = str(display.get("detail") or payload.get("summary") or "").strip()
        message = headline if not detail else f"{headline}: {detail}"
        return {
            "type": "step",
            "step": index + 1,
            "max_steps": max(total, 1),
            "message": message,
            "warmaster_event_type": str(event.get("type") or ""),
            "at": str(event.get("at") or ""),
        }

    def warmaster_activity_entry_as_agent_event(self, entry, index, total):
        headline = str(entry.get("headline") or entry.get("kind") or "Warmaster activity").strip()
        detail = str(entry.get("detail") or "").strip()
        message = headline if not detail else f"{headline}: {detail}"
        return {
            "type": "step",
            "step": index + 1,
            "max_steps": max(total, 1),
            "message": message,
            "warmaster_event_type": str(entry.get("kind") or "governor_activity"),
            "severity": str(entry.get("severity") or ""),
            "worker": str(entry.get("worker") or ""),
            "step_id": str(entry.get("step_id") or ""),
            "status": str(entry.get("status") or ""),
            "at": str(entry.get("at") or ""),
        }

    def warmaster_activity_from_payload(self, payload):
        if not isinstance(payload, dict):
            return {}
        activity = payload.get("governor_activity")
        if isinstance(activity, dict) and activity:
            return activity
        snapshot = payload.get("snapshot")
        if isinstance(snapshot, dict):
            activity = snapshot.get("governor_activity")
            if isinstance(activity, dict) and activity:
                return activity
        return {}

    def warmaster_fetch_activity(self, task_id):
        if not task_id:
            return {}
        _status, response = proxy_json_url("GET", f"{WARMASTER_BASE_URL}/runs/{quote(task_id, safe='')}/activity", timeout=10)
        return self.warmaster_activity_from_payload(response)

    def warmaster_final_message(self, orchestration):
        return final_response_message_from_orchestration(orchestration)

    def warmaster_run_as_agent_task(self, run, active=False, final_text="", activity=None):
        activity = activity if isinstance(activity, dict) else self.warmaster_activity_from_payload(run)
        status = str(run.get("status") or "").lower()
        task_id = str(run.get("task_id") or "").strip()
        running = bool(active) or status in {"running", "queued", "cancelling"}
        cancelled = status == "cancelled"
        success = status == "completed"
        progress = run.get("progress") if isinstance(run.get("progress"), dict) else {}
        current_step = str(
            progress.get("current_step")
            or progress.get("current_step_id")
            or progress.get("next_step")
            or progress.get("next_step_id")
            or progress.get("next_ready_step_id")
            or ""
        ).strip()
        activity_entries = activity.get("entries") if isinstance(activity.get("entries"), list) else []
        activity_cards = activity.get("activity_cards") if isinstance(activity.get("activity_cards"), list) else activity_entries
        progress_events = activity.get("progress_events") if isinstance(activity.get("progress_events"), list) else []
        protocol_cards = activity.get("protocol_activity_cards") if isinstance(activity.get("protocol_activity_cards"), list) else []
        summary_cards = activity.get("summary_activity_cards") if isinstance(activity.get("summary_activity_cards"), list) else []
        brigade_tabs = activity.get("brigade_tabs") if isinstance(activity.get("brigade_tabs"), list) else []
        mission_state = run.get("mission_state") if isinstance(run.get("mission_state"), dict) else {}
        if not mission_state:
            mission_state = activity.get("mission_state") if isinstance(activity.get("mission_state"), dict) else {}
        if activity_entries:
            last_entry = activity_entries[-1] if isinstance(activity_entries[-1], dict) else {}
            current_step = str(last_entry.get("headline") or current_step).strip()
        return {
            "backend": "warmaster",
            "task_id": task_id,
            "task": str(run.get("goal") or "").strip(),
            "running": running,
            "cancelled": cancelled,
            "success": success,
            "status": status,
            "mission_state": mission_state,
            "governor": str(run.get("governor") or ""),
            "current_step": current_step,
            "progress": progress,
            "final": final_text,
            "activity_log": "",
            "progress_events": progress_events,
            "protocol_activity_cards": protocol_cards,
            "summary_activity_cards": summary_cards,
            "brigade_tabs": brigade_tabs,
            "activity_entries": activity_entries,
            "activity_cards": activity_cards,
            "governor_activity": activity,
            "updated_at": str(run.get("updated_at") or ""),
            "created_at": str(run.get("created_at") or ""),
        }

    def mobile_agent_state(self):
        try:
            status, response = proxy_json_url("GET", f"{WARMASTER_BASE_URL}/state", timeout=30)
            runs = response.get("runs") if isinstance(response.get("runs"), list) else []
            active = response.get("process_active_runs") if isinstance(response.get("process_active_runs"), list) else []
            current_task_id = str(active[0]) if active else ""
            last_task_id = str(runs[0].get("task_id") or "") if runs and isinstance(runs[0], dict) else ""
            response["state"] = {
                "backend": "warmaster",
                "busy": bool(active),
                "current_task_id": current_task_id,
                "last_task_id": last_task_id,
                "revision": "warmaster",
            }
            write_json(self, status, response)
        except HTTPError as exc:
            self.write_proxy_error(exc)
        except Exception as exc:
            write_json(self, 502, {"ok": False, "error": f"warmaster unavailable: {exc}"})

    def mobile_agent_tasks(self):
        params = parse_qs(urlsplit(self.path).query)
        raw_limit = (params.get("limit") or ["20"])[0]
        try:
            limit = max(1, min(int(raw_limit), 100))
        except (TypeError, ValueError):
            limit = 20
        try:
            status, response = proxy_json_url("GET", f"{WARMASTER_BASE_URL}/runs?limit={limit}", timeout=30)
            active = set(response.get("process_active_runs") if isinstance(response.get("process_active_runs"), list) else [])
            runs = response.get("runs") if isinstance(response.get("runs"), list) else []
            tasks = []
            for run in runs:
                if not isinstance(run, dict):
                    continue
                task_id = str(run.get("task_id") or "").strip()
                activity = {}
                try:
                    activity = self.warmaster_fetch_activity(task_id)
                except Exception:
                    activity = self.warmaster_activity_from_payload(run)
                tasks.append(self.warmaster_run_as_agent_task(run, active=task_id in active, activity=activity))
            write_json(self, status, {"ok": True, "backend": "warmaster", "tasks": tasks, "warmaster": response})
        except HTTPError as exc:
            self.write_proxy_error(exc)
        except Exception as exc:
            write_json(self, 502, {"ok": False, "error": f"warmaster unavailable: {exc}"})

    def mobile_agent_task(self):
        params = parse_qs(urlsplit(self.path).query)
        task_id = str((params.get("task_id") or [""])[0]).strip()
        raw_limit = (params.get("limit") or ["160"])[0]
        try:
            limit = max(1, min(int(raw_limit), 500))
        except (TypeError, ValueError):
            limit = 160
        if not task_id:
            write_json(self, 400, {"ok": False, "error": "missing task_id"})
            return
        try:
            path = f"/runs/{quote(task_id, safe='')}/orchestration?event_limit={limit}&events_after=0&max_bytes=20000"
            status, orchestration = proxy_json_url("GET", f"{WARMASTER_BASE_URL}{path}", timeout=30)
            snapshot = orchestration.get("snapshot") if isinstance(orchestration.get("snapshot"), dict) else {}
            summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
            activity = self.warmaster_activity_from_payload(orchestration)
            activity_entries = activity.get("entries") if isinstance(activity.get("entries"), list) else []
            activity_cards = activity.get("activity_cards") if isinstance(activity.get("activity_cards"), list) else activity_entries
            progress_events = activity.get("progress_events") if isinstance(activity.get("progress_events"), list) else []
            protocol_cards = activity.get("protocol_activity_cards") if isinstance(activity.get("protocol_activity_cards"), list) else []
            summary_cards = activity.get("summary_activity_cards") if isinstance(activity.get("summary_activity_cards"), list) else []
            brigade_tabs = activity.get("brigade_tabs") if isinstance(activity.get("brigade_tabs"), list) else []
            mission_state = orchestration.get("mission_state") if isinstance(orchestration.get("mission_state"), dict) else {}
            if not mission_state:
                mission_state = activity.get("mission_state") if isinstance(activity.get("mission_state"), dict) else {}
            display_events = orchestration.get("display_events") if isinstance(orchestration.get("display_events"), list) else []
            raw_events = snapshot.get("events") if isinstance(snapshot.get("events"), list) else []
            if activity_entries:
                events = [
                    self.warmaster_activity_entry_as_agent_event(entry, index, len(activity_entries))
                    for index, entry in enumerate(activity_entries)
                    if isinstance(entry, dict)
                ]
            else:
                event_source = raw_events or display_events
                events = [
                    self.warmaster_event_as_agent_event(event, index, len(event_source))
                    for index, event in enumerate(event_source)
                    if isinstance(event, dict)
                ]
            final_message = self.warmaster_final_message(orchestration)
            active = bool(orchestration.get("active"))
            task = self.warmaster_run_as_agent_task(summary, active=active, final_text=final_message, activity=activity)
            terminal_status = str(summary.get("status") or "").lower()
            terminal = not active and terminal_status not in {"running", "queued", "cancelling", ""}
            final_event = None
            if terminal:
                final_event = {
                    "type": "final",
                    "ok": terminal_status == "completed" and bool(final_message),
                    "cancelled": terminal_status == "cancelled",
                    "status": terminal_status,
                    "message": final_message,
                }
                if final_message:
                    append_chat_message(
                        SHARED_CHAT_SESSION_ID,
                        "assistant",
                        final_message,
                        source="warmaster",
                        dedupe_key=f"warmaster:{task_id}:final",
                    )
            payload = {
                **task,
                "ok": True,
                "backend": "warmaster",
                "task_id": task_id,
                "running": active,
                "mission_state": mission_state,
                "events": events,
                "activity_entries": activity_entries,
                "activity_cards": activity_cards,
                "activity_log": "",
                "progress_events": progress_events,
                "protocol_activity_cards": protocol_cards,
                "summary_activity_cards": summary_cards,
                "brigade_tabs": brigade_tabs,
                "governor_activity": activity,
                "final": final_message,
                "final_event": final_event,
                "warmaster": orchestration,
            }
            write_json(self, status, payload)
        except HTTPError as exc:
            self.write_proxy_error(exc)
        except Exception as exc:
            write_json(self, 502, {"ok": False, "error": f"warmaster unavailable: {exc}"})

    def mobile_agent_last_task(self):
        try:
            status, response = proxy_json_url("GET", f"{WARMASTER_BASE_URL}/runs?limit=1", timeout=30)
            runs = response.get("runs") if isinstance(response.get("runs"), list) else []
            if not runs:
                write_json(self, 404, {"ok": False, "error": "no warmaster runs found"})
                return
            task_id = str(runs[0].get("task_id") or "")
            write_json(self, status, {"ok": True, "backend": "warmaster", "task_id": task_id, "task": self.warmaster_run_as_agent_task(runs[0])})
        except HTTPError as exc:
            self.write_proxy_error(exc)
        except Exception as exc:
            write_json(self, 502, {"ok": False, "error": f"warmaster unavailable: {exc}"})

    def warmaster_start_research_loop(self, task_id, payload):
        loop_payload = {
            "run_mode": str(payload.get("run_mode") or "http"),
            "host": str(payload.get("host") or "127.0.0.1"),
            "timeout_sec": int(payload.get("timeout_sec") or 1800),
            "max_revision_cycles": int(payload.get("max_revision_cycles") or 3),
            "allow_resume": bool(payload.get("allow_resume", True)),
        }
        try:
            return proxy_json_url(
                "POST",
                f"{WARMASTER_BASE_URL}/runs/{quote(task_id, safe='')}/start_research_loop_http",
                payload=loop_payload,
                timeout=60,
            )
        except HTTPError as exc:
            body = exc.read().decode("utf-8") if exc.fp else ""
            try:
                parsed = json.loads(body) if body else {}
            except json.JSONDecodeError:
                parsed = {"ok": False, "error": body or str(exc)}
            return exc.code, parsed

    def warmaster_acceptance_message(self, task_id):
        return f"Вармастер принял задачу и ведет исполнение: task_id={task_id}. Ход работы доступен во вкладке Бригады."

    def append_warmaster_acceptance_message(self, session_id, task_id):
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            return
        append_chat_message(
            shared_chat_session_id(session_id or SHARED_CHAT_SESSION_ID),
            "assistant",
            self.warmaster_acceptance_message(clean_task_id),
            source="warmaster",
            dedupe_key=f"warmaster:{clean_task_id}:accepted",
        )

    def warmaster_loop_started_or_active(self, status, payload):
        if 200 <= int(status or 0) < 300:
            return True
        error = str((payload or {}).get("error") or "").lower()
        return int(status or 0) == 409 and "already active" in error

    def mobile_agent_start(self):
        try:
            payload = read_json(self)
        except json.JSONDecodeError as exc:
            write_json(self, 400, {"error": f"Invalid JSON: {exc}"})
            return
        task = str(payload.get("task") or payload.get("message") or "").strip()
        if not task:
            write_json(self, 400, {"ok": False, "error": "task is required"})
            return
        task_id = str(payload.get("task_id") or "").strip()
        client_source = str(payload.get("client_source") or payload.get("source") or "app").strip()[:80] or "app"
        warmaster_payload = {
            "message": task,
            "task_id": task_id,
            "auto_start": False,
            "reuse_existing": True,
            "run_mode": str(payload.get("run_mode") or "http"),
            "governor_transport": str(payload.get("governor_transport") or "http"),
        }
        try:
            status, response = proxy_json_url("POST", f"{WARMASTER_BASE_URL}/orchestrate_run", payload=warmaster_payload, timeout=240)
            response_status = response.get("status") if isinstance(response.get("status"), dict) else {}
            resolved_task_id = str(response.get("task_id") or response_status.get("task_id") or task_id)
            loop_status = 0
            loop_response = {}
            if 200 <= status < 300 and resolved_task_id:
                loop_status, loop_response = self.warmaster_start_research_loop(resolved_task_id, payload)
            append_chat_message(
                SHARED_CHAT_SESSION_ID,
                "user",
                task,
                source=client_source,
                dedupe_key=f"warmaster:{resolved_task_id}:user" if resolved_task_id else None,
            )
            self.append_warmaster_acceptance_message(SHARED_CHAT_SESSION_ID, resolved_task_id)
            response["backend"] = "warmaster"
            response["task_id"] = resolved_task_id
            response["message"] = self.warmaster_acceptance_message(resolved_task_id) if resolved_task_id else "Вармастер принял задачу."
            response["research_loop"] = loop_response
            accepted_status = self.warmaster_loop_started_or_active(loop_status, loop_response) if loop_status else 200 <= status < 300
            response["ok"] = accepted_status
            write_json(self, 202 if accepted_status else (loop_status if loop_status else 409), response)
        except HTTPError as exc:
            self.write_proxy_error(exc)
        except Exception as exc:
            write_json(self, 502, {"ok": False, "error": f"warmaster unavailable: {exc}"})

    def mobile_agent_run(self):
        self.mobile_agent_start()

    def mobile_agent_cancel(self):
        try:
            payload = read_json(self)
        except json.JSONDecodeError as exc:
            write_json(self, 400, {"error": f"Invalid JSON: {exc}"})
            return
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            write_json(self, 400, {"ok": False, "error": "task_id is required"})
            return
        try:
            status, response = proxy_json_url(
                "POST",
                f"{WARMASTER_BASE_URL}/runs/{quote(task_id, safe='')}/cancel",
                payload={"reason": str(payload.get("reason") or "client requested cancel")},
                timeout=30,
            )
            response["backend"] = "warmaster"
            response["message"] = "Отмена отправлена в Warmaster."
            write_json(self, status, response)
        except HTTPError as exc:
            self.write_proxy_error(exc)
        except Exception as exc:
            write_json(self, 502, {"ok": False, "error": f"warmaster unavailable: {exc}"})

    def mobile_agent_stream_unsupported(self):
        write_json(
            self,
            410,
            {
                "ok": False,
                "backend": "warmaster",
                "error": "streaming client agent endpoint was removed with standalone agent; use /archive/client/warmaster/start and poll /archive/client/warmaster/task",
            },
        )

    def mobile_chat_explicit_warmaster_task(self, text):
        clean = str(text or "").strip()
        if not clean:
            return ""
        lower = clean.lower()
        prefixes = ("/task ", "/w ", "/warmaster ", "!task ", "!вармастер ")
        for prefix in prefixes:
            if lower.startswith(prefix):
                return clean[len(prefix) :].strip()
        colon_prefixes = ("вармастер:", "warmaster:")
        for prefix in colon_prefixes:
            if lower.startswith(prefix):
                return clean[len(prefix) :].strip()
        return ""

    def mobile_chat_looks_like_task(self, text):
        lower = str(text or "").strip().lower()
        if len(lower) < 16:
            return False
        task_markers = (
            "задача такая",
            "задача:",
            "задача ",
            "собери ",
            "собрать ",
            "найди ",
            "найти ",
            "сделай ",
            "сделать ",
            "подготовь ",
            "вытащи ",
            "достань ",
            "запусти ",
            "проверь ",
            "исследуй ",
            "разберись ",
        )
        scope_markers = (
            "в интернете",
            "интернет",
            "источник",
            "источники",
            "книг",
            "кодекс",
            "файл",
            "проект",
            "репо",
            "отчет",
            "fb2",
            "в одну книгу",
            "всю возможную инф",
            "всю информацию",
        )
        if any(marker in lower for marker in task_markers) and any(marker in lower for marker in scope_markers):
            return True
        return "крч, задача" in lower or "короче, задача" in lower

    def mobile_chat_is_task_confirmation(self, text):
        lower = re.sub(r"[\s.!?,:;]+", " ", str(text or "").strip().lower()).strip()
        if not lower or len(lower) > 80:
            return False
        confirmations = {
            "давай",
            "ну давай",
            "ну давай работай",
            "работай",
            "начинай",
            "начинай работать",
            "приступай",
            "погнали",
            "погнали делать",
            "запускай",
            "делай",
        }
        return lower in confirmations

    def mobile_chat_contextual_task(self, history, task_index, task_text):
        context = []
        for message in reversed(history[:task_index]):
            if str(message.get("role") or "") != "user":
                continue
            content = trim_chat_text(message.get("content") or "")
            if not content or content == task_text or self.mobile_chat_is_task_confirmation(content):
                continue
            context.append(content)
            if len(context) >= 3:
                break
        context = list(reversed(context))
        if not context:
            return task_text
        context_text = "\n".join(f"- {item}" for item in context)
        return trim_chat_text(f"{task_text}\n\nКонтекст предыдущих сообщений:\n{context_text}")

    def mobile_chat_last_task_request(self, session_id):
        history = chat_history(session_id, limit=16)
        for index in range(len(history) - 1, -1, -1):
            message = history[index]
            if str(message.get("role") or "") != "user":
                continue
            content = str(message.get("content") or "").strip()
            explicit = self.mobile_chat_explicit_warmaster_task(content)
            if explicit:
                return self.mobile_chat_contextual_task(history, index, explicit)
            if self.mobile_chat_looks_like_task(content):
                return self.mobile_chat_contextual_task(history, index, content)
        return ""

    def mobile_chat_warmaster_task(self, session_id, text):
        explicit = self.mobile_chat_explicit_warmaster_task(text)
        if explicit:
            return explicit
        if self.mobile_chat_looks_like_task(text):
            return str(text or "").strip()
        if self.mobile_chat_is_task_confirmation(text):
            return self.mobile_chat_last_task_request(session_id)
        return ""

    def run_mobile_warmaster_payload(self, payload):
        session_id = shared_chat_session_id(payload.get("session_id") or SHARED_CHAT_SESSION_ID)
        client_source = str(payload.get("client_source") or payload.get("source") or "app").strip()[:80] or "app"
        original_text = trim_chat_text(payload.get("text") or payload.get("message") or "")
        task = trim_chat_text(payload.get("warmaster_task") or "")
        if not task:
            raise ValueError("warmaster task is empty")

        task_id = str(payload.get("task_id") or f"client-{uuid.uuid4().hex[:12]}").strip()
        warmaster_payload = {
            "message": task,
            "task_id": task_id,
            "auto_start": False,
            "reuse_existing": True,
            "run_mode": str(payload.get("run_mode") or "http"),
            "governor_transport": str(payload.get("governor_transport") or "http"),
        }
        status, response = proxy_json_url("POST", f"{WARMASTER_BASE_URL}/orchestrate_run", payload=warmaster_payload, timeout=240)
        response_status = response.get("status") if isinstance(response.get("status"), dict) else {}
        resolved_task_id = str(response.get("task_id") or response_status.get("task_id") or task_id).strip()
        loop_status = 0
        loop_response = {}
        if 200 <= status < 300 and resolved_task_id:
            loop_status, loop_response = self.warmaster_start_research_loop(resolved_task_id, payload)

        append_chat_message(
            session_id,
            "user",
            original_text or task,
            source=client_source,
            dedupe_key=f"warmaster:{resolved_task_id}:user" if resolved_task_id else None,
        )
        self.append_warmaster_acceptance_message(session_id, resolved_task_id or task_id)
        activity = {}
        try:
            activity = self.warmaster_fetch_activity(resolved_task_id or task_id)
        except Exception:
            activity = {}
        activity_entries = activity.get("entries") if isinstance(activity.get("entries"), list) else []
        activity_cards = activity.get("activity_cards") if isinstance(activity.get("activity_cards"), list) else activity_entries
        progress_events = activity.get("progress_events") if isinstance(activity.get("progress_events"), list) else []
        protocol_cards = activity.get("protocol_activity_cards") if isinstance(activity.get("protocol_activity_cards"), list) else []
        summary_cards = activity.get("summary_activity_cards") if isinstance(activity.get("summary_activity_cards"), list) else []
        brigade_tabs = activity.get("brigade_tabs") if isinstance(activity.get("brigade_tabs"), list) else []
        mission_state = activity.get("mission_state") if isinstance(activity.get("mission_state"), dict) else {}
        accepted = self.warmaster_acceptance_message(resolved_task_id or task_id)
        response["ok"] = self.warmaster_loop_started_or_active(loop_status, loop_response) if loop_status else 200 <= status < 300
        response["backend"] = "warmaster"
        response["task_id"] = resolved_task_id or task_id
        response["message"] = accepted
        response["mission_state"] = mission_state
        response["activity_log"] = ""
        response["progress_events"] = progress_events
        response["protocol_activity_cards"] = protocol_cards
        response["summary_activity_cards"] = summary_cards
        response["brigade_tabs"] = brigade_tabs
        response["activity_entries"] = activity_entries
        response["activity_cards"] = activity_cards
        response["governor_activity"] = activity
        response["research_loop"] = loop_response
        return response

    def mobile_chat_start(self):
        try:
            payload = read_json(self)
        except json.JSONDecodeError as exc:
            write_json(self, 400, {"error": f"Invalid JSON: {exc}"})
            return
        session_id = shared_chat_session_id(payload.get("session_id") or payload.get("user") or SHARED_CHAT_SESSION_ID)
        text = trim_chat_text(payload.get("text") or payload.get("message") or "")
        image_data_url = str(payload.get("image_data_url") or "").strip()
        if not text and not image_data_url:
            write_json(self, 400, {"ok": False, "error": "Missing text or image_data_url", "session_id": session_id})
            return
        payload["stream"] = False
        payload["session_id"] = session_id
        payload["memory_namespace"] = shared_memory_namespace(payload.get("memory_namespace"))
        payload["client_source"] = str(payload.get("client_source") or payload.get("source") or "app").strip()[:80] or "app"
        try:
            turn = decide_chat_turn_action(session_id, text, image_data_url=image_data_url, model=payload.get("model") or DEFAULT_MODEL)
        except Exception as exc:
            write_json(self, 502, {"ok": False, "error": f"turn protocol unavailable: {exc}", "session_id": session_id})
            return
        decision = turn.get("decision") if isinstance(turn.get("decision"), dict) else {"action": "answer_in_chat"}
        payload["turn_decision"] = decision
        payload["turn_capabilities"] = turn.get("capabilities") if isinstance(turn.get("capabilities"), dict) else {}
        payload["turn_protocol"] = {
            "request": turn.get("request"),
            "response": turn.get("response"),
        }
        if decision.get("action") == "issue_mission_order":
            payload["warmaster_task"] = mission_order_to_warmaster_message(decision.get("mission_order") if isinstance(decision.get("mission_order"), dict) else {})
            job_id = create_mobile_job("warmaster", payload)
            run_mobile_job(job_id, lambda payload=payload: self.run_mobile_warmaster_payload(payload))
            write_json(self, 202, {"ok": True, "job_id": job_id, "type": "warmaster", "session_id": session_id, "status": "queued"})
            return
        if decision.get("action") == "ask_clarification":
            payload["forced_chat_reply"] = str(decision.get("reply") or "").strip()
        job_id = create_mobile_job("chat", payload)
        run_mobile_job(job_id, lambda payload=payload: run_mobile_chat_payload(payload))
        write_json(self, 202, {"ok": True, "job_id": job_id, "type": "chat", "session_id": session_id, "status": "queued"})

    def mobile_chat_protocol_completion_payload(self, message, finish_reason="turn_protocol_reply", extra=None):
        payload = {
            "object": "chat.completion",
            "model": "archive-turn-protocol",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {"role": "assistant", "content": message},
                }
            ],
        }
        if isinstance(extra, dict):
            payload.update(extra)
        return payload

    def stream_static_mobile_chat_completion(self, message, finish_reason="turn_protocol_reply", extra=None):
        self.send_response(202)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        chunk = {
            "object": "chat.completion.chunk",
            "model": "archive-turn-protocol",
            "choices": [{"index": 0, "delta": {"content": message}, "finish_reason": None}],
        }
        done = {
            "object": "chat.completion.chunk",
            "model": "archive-turn-protocol",
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        }
        if isinstance(extra, dict):
            chunk.update(extra)
            done.update(extra)
        self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.write(f"data: {json.dumps(done, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def mobile_translate_start(self):
        try:
            payload = read_json(self)
        except json.JSONDecodeError as exc:
            write_json(self, 400, {"error": f"Invalid JSON: {exc}"})
            return
        job_id = create_mobile_job("translate", payload)

        def worker(payload=payload):
            status, response = proxy_json_url("POST", f"{TRANSLATOR_BASE_URL}/translate", payload=payload, timeout=180)
            return {"ok": 200 <= status < 300, "status": status, **response}

        run_mobile_job(job_id, worker)
        write_json(self, 202, {"ok": True, "job_id": job_id, "type": "translate", "status": "queued"})

    def memory_propose_change(self):
        with CHAT_QUEUE_LOCK:
            created_at = now_iso()
            turn_id = str(uuid.uuid4())
            try:
                payload = read_json(self)
            except json.JSONDecodeError as exc:
                write_json(self, 400, {"error": f"Invalid JSON: {exc}"})
                return

            namespace = safe_memory_namespace(payload.get("namespace") or payload.get("memory_namespace") or "default")
            requester = str(payload.get("requester") or "memory-gateway").strip()[:80] or "memory-gateway"
            raw_proposal = str(payload.get("proposal") or "").strip()
            proposal = trim_memory_text(raw_proposal, GATEWAY_PROPOSAL_CHARS)
            if not proposal:
                write_json(self, 400, {"error": "Missing required field: proposal", "memory_namespace": namespace})
                return

            target = str(payload.get("target") or "auto").strip().lower()[:40] or "auto"
            if target not in GATEWAY_TARGETS:
                write_json(
                    self,
                    400,
                    {
                        "error": "Unsupported memory proposal target",
                        "target": target,
                        "allowed_targets": sorted(GATEWAY_TARGETS),
                    },
                )
                return
            raw_evidence = str(payload.get("evidence") or "").strip()
            evidence = trim_memory_text(raw_evidence, GATEWAY_EVIDENCE_CHARS)
            importance = payload.get("importance", 3)
            try:
                importance = max(1, min(5, int(importance)))
            except (TypeError, ValueError):
                importance = 3

            proposal_payload = {
                "type": "memory_change_proposal",
                "requester": requester,
                "memory_namespace": namespace,
                "target": target,
                "importance": importance,
                "truncated": {
                    "proposal": len(raw_proposal) > len(proposal),
                    "evidence": len(raw_evidence) > len(evidence),
                },
                "proposal": proposal,
                "evidence": evidence,
                "instruction": (
                    "This is a proposed memory update from an agent through Memory Gateway. "
                    "Do not apply it blindly. Evaluate it as a normal archived turn and let the librarian decide "
                    "what belongs in focus, wiki, vector, and graph memory."
                ),
            }
            request_payload = {
                "user": f"memory-gateway:{requester}",
                "memory_namespace": namespace,
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(proposal_payload, ensure_ascii=False, indent=2),
                    }
                ],
            }
            assistant_text = (
                "Memory Gateway accepted the proposal for ArchiveOfHeresy librarian review. "
                "The requester does not receive direct write access to memory files."
            )
            response = {
                "object": "archive.memory.proposal",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "accepted",
                        "message": {"role": "assistant", "content": assistant_text},
                    }
                ],
            }
            record = {
                "turn_id": turn_id,
                "created_at": created_at,
                "source": "memory-gateway-proposal",
                "conversation_id": f"memory-gateway:{requester}",
                "memory_namespace": namespace,
                "archive_enabled": True,
                "focus_enabled": True,
                "vector_enabled": True,
                "graph_enabled": True,
                "magos_enabled": False,
                "magos_result": None,
                "model": "archive-memory-gateway",
                "request": request_payload,
                "prepared_messages": request_payload["messages"],
                "status": "ok",
                "http_status": 202,
                "response": response,
                "assistant_message": {"role": "assistant", "content": assistant_text},
                "error": None,
            }

            maybe_write_archives(record)
            write_gateway_event(
                namespace,
                "proposal_accepted",
                requester=requester,
                target=target,
                importance=importance,
                turn_id=turn_id,
            )
            maybe_update_focus_memory(record)
            write_json(
                self,
                202,
                {
                    "ok": True,
                    "turn_id": turn_id,
                    "memory_namespace": namespace,
                    "requester": requester,
                    "target": target,
                    "message": "Proposal queued through ArchiveOfHeresy librarian cycle.",
                },
            )

    def mobile_chat_completion(self):
        maintenance_record = None
        with CHAT_QUEUE_LOCK:
            created_at = now_iso()
            turn_id = str(uuid.uuid4())
            try:
                payload = read_json(self)
            except json.JSONDecodeError as exc:
                write_json(self, 400, {"error": f"Invalid JSON: {exc}"})
                return

            session_id = shared_chat_session_id(payload.get("session_id") or payload.get("user") or SHARED_CHAT_SESSION_ID)
            text = trim_chat_text(payload.get("text") or payload.get("message") or "")
            image_data_url = str(payload.get("image_data_url") or "").strip()
            if not text and not image_data_url:
                write_json(self, 400, {"error": "Missing text or image_data_url", "session_id": session_id})
                return

            archive_enabled = internal_flag(payload.get("archive_enabled", True), default=True)
            focus_enabled = internal_flag(payload.get("focus_enabled", True), default=True)
            vector_enabled = internal_flag(payload.get("vector_enabled", focus_enabled), default=True)
            graph_enabled = internal_flag(payload.get("graph_enabled", focus_enabled), default=True)
            archive_system_prompt_enabled = internal_flag(payload.get("archive_system_prompt_enabled", True), default=True)
            memory_namespace = shared_memory_namespace(payload.get("memory_namespace") or SHARED_MEMORY_NAMESPACE)
            client_source = str(payload.get("client_source") or payload.get("source") or "app").strip()[:80] or "app"
            stream = internal_flag(payload.get("stream", True), default=True)
            model = payload.get("model") or DEFAULT_MODEL
            system_prompt = ""
            max_tokens = int(payload.get("max_tokens") or 2048)
            temperature = float(payload.get("temperature") or 0.4)

            try:
                turn = decide_chat_turn_action(session_id, text, image_data_url=image_data_url, model=model)
            except Exception as exc:
                write_json(self, 502, {"error": f"turn protocol unavailable: {exc}", "session_id": session_id})
                return
            decision = turn.get("decision") if isinstance(turn.get("decision"), dict) else {"action": "answer_in_chat"}
            turn_capabilities = turn.get("capabilities") if isinstance(turn.get("capabilities"), dict) else {}
            if decision.get("action") == "issue_mission_order":
                task_id = str(payload.get("task_id") or f"client-{uuid.uuid4().hex[:12]}").strip()
                payload["stream"] = False
                payload["session_id"] = session_id
                payload["memory_namespace"] = memory_namespace
                payload["client_source"] = client_source
                payload["task_id"] = task_id
                payload["warmaster_task"] = mission_order_to_warmaster_message(decision.get("mission_order") if isinstance(decision.get("mission_order"), dict) else {})
                payload["turn_decision"] = decision
                payload["turn_capabilities"] = turn_capabilities
                payload["turn_protocol"] = {"request": turn.get("request"), "response": turn.get("response")}
                job_id = create_mobile_job("warmaster", payload)
                run_mobile_job(job_id, lambda payload=payload: self.run_mobile_warmaster_payload(payload))
                message = (
                    f"Вармастер-пайплайн поставлен в очередь: task_id={task_id}. "
                    "Ход работы будет во вкладке Бригады, а в основной чат вернется финальный результат или запрос решения."
                )
                extra = {"warmaster": {"ok": True, "task_id": task_id, "job_id": job_id, "status": "queued"}}
                if stream:
                    self.stream_static_mobile_chat_completion(message, finish_reason="warmaster_queued", extra=extra)
                else:
                    write_json(
                        self,
                        202,
                        self.mobile_chat_protocol_completion_payload(
                            message,
                            finish_reason="warmaster_queued",
                            extra=extra,
                        ),
                    )
                return

            request_messages = messages_for_chat_context(session_id, system_prompt, text, image_data_url=image_data_url)
            request_messages.insert(0, capability_contract_message(turn_capabilities, decision))
            append_chat_message(
                session_id,
                "user",
                text if not image_data_url else f"{text}\n[image attached server-side]",
                created_at=created_at,
                source=client_source,
            )
            administratum_intent = None
            administratum_result = None
            administratum_message = None
            if should_detect_administratum_intent(client_source, payload):
                administratum_intent = detect_administratum_intent(text, model=model)
                administratum_result = create_administratum_task_from_intent(administratum_intent, session_id, client_source)
                administratum_message = administratum_intent_context(administratum_result)
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
                "stream": stream,
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
                    print(f"Magos hard fail-soft mobile chat: {exc}", flush=True)
                    magos_result = {"error": str(exc)}

            prepared_payload = dict(mobile_payload)
            prepared_payload["messages"] = prepare_messages(
                request_messages,
                include_focus=focus_enabled,
                include_vector=vector_enabled,
                include_graph=graph_enabled,
                include_system_prompt=archive_system_prompt_enabled,
                magos_message=magos_message,
                administratum_message=administratum_message,
                query_messages=memory_messages,
                memory_namespace=memory_namespace,
            )
            archive_prepared_messages = prepare_messages(
                memory_messages,
                include_focus=focus_enabled,
                include_vector=vector_enabled,
                include_graph=graph_enabled,
                include_system_prompt=archive_system_prompt_enabled,
                magos_message=magos_message,
                administratum_message=administratum_message,
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
                "turn_decision": decision,
                "turn_capabilities": turn_capabilities,
                "turn_protocol": {"request": turn.get("request"), "response": turn.get("response")},
                "prompt_diagnostics": diagnostics,
                "model": model,
                "request": {
                    "session_id": session_id,
                    "text": text,
                    "has_image": bool(image_data_url),
                    "stream": stream,
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
                if stream:
                    self.stream_mobile_chat_completion(prepared_payload, record, session_id)
                    if record.get("status") == "ok":
                        maintenance_record = record
                else:
                    status, response = proxy_json("POST", "/v1/chat/completions", payload=prepared_payload)
                    assistant = assistant_message(response)
                    if assistant:
                        append_chat_message(session_id, "assistant", assistant.get("content") or "")
                    record["status"] = "ok"
                    record["http_status"] = status
                    record["response"] = response
                    record["assistant_message"] = assistant
                    maybe_write_archives(record)
                    write_json(self, status, response)
                    maintenance_record = record
            except HTTPError as exc:
                try:
                    error_payload = json.loads(exc.read().decode("utf-8"))
                except Exception:
                    error_payload = {"error": str(exc)}
                record["status"] = "upstream_error"
                record["http_status"] = exc.code
                record["response"] = error_payload
                record["error"] = json.dumps(error_payload, ensure_ascii=False)
                maybe_abandon_magos_focus(record)
                maybe_write_archives(record)
                write_json(self, exc.code, error_payload)
            except (TimeoutError, URLError) as exc:
                error_payload = {"error": f"LLM host unavailable: {exc}"}
                record["status"] = "unavailable"
                record["http_status"] = 502
                record["response"] = error_payload
                record["error"] = error_payload["error"]
                maybe_abandon_magos_focus(record)
                maybe_write_archives(record)
                write_json(self, 502, error_payload)
            except Exception as exc:
                error_payload = {"error": str(exc)}
                record["status"] = "archive_error"
                record["http_status"] = 500
                record["response"] = error_payload
                record["error"] = error_payload["error"]
                maybe_abandon_magos_focus(record)
                maybe_write_archives(record)
                write_json(self, 500, error_payload)
        if maintenance_record is not None:
            maybe_update_focus_memory(maintenance_record)

    def stream_mobile_chat_completion(self, prepared_payload, record, session_id):
        assistant_parts = []
        finish_reason = None
        streamed_chunks = []

        try:
            with open_upstream("POST", "/v1/chat/completions", payload=prepared_payload) as upstream:
                self.send_response(upstream.status)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                for raw_line in upstream:
                    self.wfile.write(raw_line)
                    self.wfile.flush()
                    decoded = raw_line.decode("utf-8", errors="replace").strip()
                    if not decoded.startswith("data:"):
                        continue

                    data = decoded[5:].strip()
                    if data == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    streamed_chunks.append(chunk)
                    delta, chunk_finish = stream_delta(chunk)
                    if delta:
                        assistant_parts.append(delta)
                    if chunk_finish:
                        finish_reason = chunk_finish

            assistant_text = "".join(assistant_parts).strip()
            if assistant_text:
                append_chat_message(session_id, "assistant", assistant_text)
            response = {
                "object": "chat.completion",
                "model": record.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": finish_reason or "stop",
                        "message": {"role": "assistant", "content": assistant_text},
                    }
                ],
                "streamed_chunks": streamed_chunks,
            }
            record["status"] = "ok"
            record["http_status"] = 200
            record["response"] = response
            record["assistant_message"] = {"role": "assistant", "content": assistant_text} if assistant_text else None
            if record["assistant_message"]:
                append_chat_message(
                    record.get("shared_chat_session_id") or SHARED_CHAT_SESSION_ID,
                    "assistant",
                    assistant_text,
                    source=record.get("client_source") or "api",
                )
            maybe_write_archives(record)
        except (BrokenPipeError, ConnectionResetError) as exc:
            assistant_text = "".join(assistant_parts).strip()
            if assistant_text:
                append_chat_message(session_id, "assistant", assistant_text)
            record["status"] = "client_disconnected"
            record["http_status"] = 499
            record["response"] = {
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "client_disconnected",
                        "message": {"role": "assistant", "content": assistant_text},
                    }
                ],
                "streamed_chunks": streamed_chunks,
            }
            record["assistant_message"] = {"role": "assistant", "content": assistant_text} if assistant_text else None
            record["error"] = str(exc)
            maybe_abandon_magos_focus(record)
            maybe_write_archives(record)

    def chat_completion(self):
        maintenance_record = None
        with CHAT_QUEUE_LOCK:
            created_at = now_iso()
            turn_id = str(uuid.uuid4())
            payload = read_json(self)
            archive_enabled = internal_flag(payload.pop("archive_enabled", True), default=True)
            focus_enabled = internal_flag(payload.pop("focus_enabled", True), default=True)
            vector_enabled = internal_flag(payload.pop("vector_enabled", focus_enabled), default=True)
            graph_enabled = internal_flag(payload.pop("graph_enabled", focus_enabled), default=True)
            archive_system_prompt_enabled = internal_flag(payload.pop("archive_system_prompt_enabled", True), default=True)
            memory_namespace = shared_memory_namespace(payload.pop("memory_namespace", SHARED_MEMORY_NAMESPACE))
            client_source = str(payload.pop("client_source", payload.pop("source", "api")) or "api").strip()[:80] or "api"
            shared_session_id = shared_chat_session_id(payload.get("session_id") or payload.get("user") or SHARED_CHAT_SESSION_ID)
            payload["messages"] = list(payload.get("messages", []))
            payload["messages"].insert(
                0,
                capability_contract_message(
                    turn_capability_manifest(),
                    {"action": "answer_in_chat", "reason": "generic OpenAI-compatible chat endpoint"},
                ),
            )
            memory_messages = sanitize_messages_for_memory(payload["messages"])
            user_text_for_channel = trim_chat_text(latest_user_message(memory_messages))
            if user_text_for_channel:
                append_chat_message(shared_session_id, "user", user_text_for_channel, created_at=created_at, source=client_source)
            administratum_intent = None
            administratum_result = None
            administratum_message = None
            if user_text_for_channel and should_detect_administratum_intent(client_source, payload):
                administratum_intent = detect_administratum_intent(user_text_for_channel, model=payload.get("model"))
                administratum_result = create_administratum_task_from_intent(administratum_intent, shared_session_id, client_source)
                administratum_message = administratum_intent_context(administratum_result)
            magos_message = None
            magos_result = None
            magos = focus_components(memory_namespace)["magos"]
            if focus_enabled and magos is not None:
                try:
                    magos_message = magos.prepare_request(
                        memory_messages,
                        model=payload.get("model"),
                        conversation_id=shared_session_id,
                        turn_id=turn_id,
                        memory_namespace=memory_namespace,
                    )
                    magos_result = magos.last_result
                except Exception as exc:
                    print(f"Magos hard fail-soft: {exc}", flush=True)
                    magos_message = None
                    magos_result = {"error": str(exc)}
            prepared_payload = dict(payload)
            prepared_payload["messages"] = prepare_messages(
                payload["messages"],
                include_focus=focus_enabled,
                include_vector=vector_enabled,
                include_graph=graph_enabled,
                include_system_prompt=archive_system_prompt_enabled,
                magos_message=magos_message,
                administratum_message=administratum_message,
                query_messages=memory_messages,
                memory_namespace=memory_namespace,
            )
            sanitized_payload = dict(payload)
            sanitized_payload["messages"] = memory_messages
            archive_prepared_messages = prepare_messages(
                memory_messages,
                include_focus=focus_enabled,
                include_vector=vector_enabled,
                include_graph=graph_enabled,
                include_system_prompt=archive_system_prompt_enabled,
                magos_message=magos_message,
                administratum_message=administratum_message,
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
                "source": f"{client_source}-chat-completions",
                "conversation_id": shared_session_id,
                "memory_namespace": memory_namespace,
                "shared_chat_session_id": shared_session_id,
                "client_source": client_source,
                "archive_enabled": archive_enabled,
                "focus_enabled": focus_enabled,
                "vector_enabled": vector_enabled,
                "graph_enabled": graph_enabled,
                "archive_system_prompt_enabled": archive_system_prompt_enabled,
                "magos_enabled": bool(magos_message),
                "magos_result": magos_result,
                "administratum_intent": administratum_intent,
                "administratum_result": administratum_result,
                "prompt_diagnostics": diagnostics,
                "model": payload.get("model"),
                "request": sanitized_payload,
                "prepared_messages": archive_prepared_messages,
                "status": "pending",
                "http_status": None,
                "response": None,
                "assistant_message": None,
                "error": None,
            }

            try:
                if prepared_payload.get("stream"):
                    self.stream_chat_completion(prepared_payload, record)
                    if record.get("status") == "ok":
                        maintenance_record = record
                else:
                    status, response = proxy_json("POST", self.path, payload=prepared_payload)
                    record["status"] = "ok"
                    record["http_status"] = status
                    record["response"] = response
                    record["assistant_message"] = assistant_message(response)
                    if record["assistant_message"]:
                        append_chat_message(shared_session_id, "assistant", record["assistant_message"].get("content") or "", source=client_source)
                    maybe_write_archives(record)
                    write_json(self, status, response)
                    maintenance_record = record
            except HTTPError as exc:
                try:
                    error_payload = json.loads(exc.read().decode("utf-8"))
                except Exception:
                    error_payload = {"error": str(exc)}
                record["status"] = "upstream_error"
                record["http_status"] = exc.code
                record["response"] = error_payload
                record["error"] = json.dumps(error_payload, ensure_ascii=False)
                maybe_abandon_magos_focus(record)
                maybe_write_archives(record)
                write_json(self, exc.code, error_payload)
            except (TimeoutError, URLError) as exc:
                error_payload = {"error": f"LLM host unavailable: {exc}"}
                record["status"] = "unavailable"
                record["http_status"] = 502
                record["response"] = error_payload
                record["error"] = error_payload["error"]
                maybe_abandon_magos_focus(record)
                maybe_write_archives(record)
                write_json(self, 502, error_payload)
            except Exception as exc:
                error_payload = {"error": str(exc)}
                record["status"] = "archive_error"
                record["http_status"] = 500
                record["response"] = error_payload
                record["error"] = error_payload["error"]
                maybe_abandon_magos_focus(record)
                maybe_write_archives(record)
                write_json(self, 500, error_payload)
        if maintenance_record is not None:
            maybe_update_focus_memory(maintenance_record)

    def stream_chat_completion(self, prepared_payload, record):
        assistant_parts = []
        finish_reason = None
        streamed_chunks = []

        try:
            with open_upstream("POST", self.path, payload=prepared_payload) as upstream:
                self.send_response(upstream.status)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                for raw_line in upstream:
                    self.wfile.write(raw_line)
                    self.wfile.flush()
                    decoded = raw_line.decode("utf-8", errors="replace").strip()
                    if not decoded.startswith("data:"):
                        continue

                    data = decoded[5:].strip()
                    if data == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    streamed_chunks.append(chunk)
                    delta, chunk_finish = stream_delta(chunk)
                    if delta:
                        assistant_parts.append(delta)
                    if chunk_finish:
                        finish_reason = chunk_finish

            assistant_text = "".join(assistant_parts).strip()
            response = {
                "object": "chat.completion",
                "model": record.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": finish_reason or "stop",
                        "message": {"role": "assistant", "content": assistant_text},
                    }
                ],
                "streamed_chunks": streamed_chunks,
            }
            record["status"] = "ok"
            record["http_status"] = 200
            record["response"] = response
            record["assistant_message"] = {"role": "assistant", "content": assistant_text} if assistant_text else None
            maybe_write_archives(record)
        except HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                error_payload = {"error": str(exc)}
            record["status"] = "upstream_error"
            record["http_status"] = exc.code
            record["response"] = error_payload
            record["error"] = json.dumps(error_payload, ensure_ascii=False)
            maybe_abandon_magos_focus(record)
            maybe_write_archives(record)
            write_json(self, exc.code, error_payload)
        except (BrokenPipeError, ConnectionResetError) as exc:
            assistant_text = "".join(assistant_parts).strip()
            record["status"] = "client_disconnected"
            record["http_status"] = 499
            record["response"] = {
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "client_disconnected",
                        "message": {"role": "assistant", "content": assistant_text},
                    }
                ],
                "streamed_chunks": streamed_chunks,
            }
            record["assistant_message"] = {"role": "assistant", "content": assistant_text} if assistant_text else None
            record["error"] = str(exc)
            maybe_abandon_magos_focus(record)
            maybe_write_archives(record)
        except (TimeoutError, URLError) as exc:
            error_payload = {"error": f"LLM host unavailable: {exc}"}
            record["status"] = "unavailable"
            record["http_status"] = 502
            record["response"] = error_payload
            record["error"] = error_payload["error"]
            maybe_abandon_magos_focus(record)
            maybe_write_archives(record)
            write_json(self, 502, error_payload)
        except Exception as exc:
            error_payload = {"error": str(exc)}
            record["status"] = "archive_error"
            record["http_status"] = 500
            record["response"] = error_payload
            record["error"] = error_payload["error"]
            maybe_abandon_magos_focus(record)
            maybe_write_archives(record)
            write_json(self, 500, error_payload)

    def forward(self, method, path, payload=None):
        try:
            status, response = proxy_json(method, path, payload=payload)
            write_json(self, status, response)
        except HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                error_payload = {"error": str(exc)}
            write_json(self, exc.code, error_payload)
        except (TimeoutError, URLError) as exc:
            write_json(self, 502, {"error": f"LLM host unavailable: {exc}"})
        except Exception as exc:
            write_json(self, 500, {"error": str(exc)})
