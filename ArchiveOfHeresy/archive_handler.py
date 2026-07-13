"""ArchiveOfHeresy HTTP request handler (all gateway/proxy/memory routes)."""
import hashlib
import json
import os
import queue
import re
import sqlite3
import threading
import time
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
from artifact_store import (
    ArtifactError,
    ArtifactRangeError,
    artifact_metadata,
    open_artifact_content,
    parse_single_byte_range,
)


class ArchiveHandler(BaseHTTPRequestHandler):
    server_version = "ArchiveOfHeresy/0.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    @staticmethod
    def _artifact_content_disposition(filename):
        filename = str(filename or "artifact.bin")
        fallback = "".join(char if 32 <= ord(char) < 127 and char not in {'"', "\\", ";"} else "_" for char in filename)
        fallback = fallback.strip(" .")[:180] or "artifact.bin"
        return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"

    def _artifact_empty_response(self, status, *, size=None):
        self.send_response(status)
        if size is not None:
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _handle_artifact_request(self, *, head_only=False):
        parsed = urlsplit(self.path)
        match = re.fullmatch(
            r"/archive/(?:mobile/)?artifacts/(art_[0-9a-f]{32})(/content)?",
            parsed.path,
        )
        if not match:
            return False
        if not require_artifact_auth(self, head_only=head_only):
            return True
        audience_source = authenticated_audience_source(self)
        artifact_id = match.group(1)
        params = parse_qs(parsed.query)
        session_id = shared_chat_session_id((params.get("session_id") or [SHARED_CHAT_SESSION_ID])[0])
        metadata = artifact_metadata(
            artifact_id,
            session_id=session_id,
            audience_source=audience_source,
        )
        if metadata is None:
            if head_only:
                self._artifact_empty_response(404)
            else:
                write_json(self, 404, {"ok": False, "error": "artifact not found"})
            return True
        etag = f'"{metadata["sha256"]}"'
        if not match.group(2):
            payload = {
                "ok": True,
                "artifact": {
                    **metadata,
                    "content_url": f"/archive/client/artifacts/{artifact_id}/content",
                },
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return True

        if_none_match = str(self.headers.get("If-None-Match") or "")
        if etag in {item.strip() for item in if_none_match.split(",")} or if_none_match.strip() == "*":
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, no-cache")
            self.end_headers()
            return True
        range_header = self.headers.get("Range")
        if_range = str(self.headers.get("If-Range") or "").strip()
        if if_range and if_range not in {etag, metadata["sha256"]}:
            range_header = None
        try:
            byte_range = parse_single_byte_range(range_header, int(metadata["size_bytes"]))
        except ArtifactRangeError:
            self._artifact_empty_response(416, size=int(metadata["size_bytes"]))
            return True
        start, end = byte_range if byte_range is not None else (0, int(metadata["size_bytes"]) - 1)
        length = max(0, end - start + 1)
        status = 206 if byte_range is not None else 200
        response_started = False
        try:
            with open_artifact_content(
                artifact_id,
                session_id=session_id,
                audience_source=audience_source,
            ) as (opened_metadata, stream):
                if opened_metadata["sha256"] != metadata["sha256"]:
                    raise ArtifactError("artifact metadata changed while opening content")
                self.send_response(status)
                self.send_header("Content-Type", metadata["media_type"])
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("ETag", etag)
                self.send_header("Content-Disposition", self._artifact_content_disposition(metadata["filename"]))
                self.send_header("Cache-Control", "private, no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                if byte_range is not None:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{metadata['size_bytes']}")
                self.end_headers()
                response_started = True
                if not head_only and length:
                    stream.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = stream.read(min(ARTIFACT_STREAM_CHUNK_BYTES, remaining))
                        if not chunk:
                            raise ArtifactError("artifact blob ended before its catalogued size")
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
        except FileNotFoundError:
            if not response_started:
                self._artifact_empty_response(404)
        except ArtifactError as exc:
            if not response_started:
                if head_only:
                    self._artifact_empty_response(500)
                else:
                    write_json(self, 500, {"ok": False, "error": "artifact integrity failure"})
            print(f"artifact content failure for {artifact_id}: {exc}", flush=True)
        return True

    def do_HEAD(self):
        if self.path.startswith("/archive/client/"):
            self.path = "/archive/mobile/" + self.path[len("/archive/client/") :]
        if self._handle_artifact_request(head_only=True):
            return
        self._artifact_empty_response(404)

    def do_GET(self):
        if self.path.startswith("/archive/client/"):
            self.path = "/archive/mobile/" + self.path[len("/archive/client/") :]

        if self._handle_artifact_request():
            return

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
                    "artifact_store": artifact_store_stats(),
                    "chat_context_messages": CHAT_CONTEXT_MESSAGES,
                    "chat_queue": {
                        **CHAT_QUEUE_LOCK.snapshot(),
                        "wait_timeout_sec": CHAT_QUEUE_WAIT_TIMEOUT_SEC,
                        **archive_state.CHAT_SESSION_LOCKS.snapshot(),
                    },
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

        if self.path.startswith("/archive/mobile/chat/asset/") or self.path.startswith("/archive/chat/asset/"):
            if not require_auth(self, allow_mobile=True):
                return
            asset_id = urlsplit(self.path).path.rsplit("/", 1)[-1]
            found = read_chat_asset(asset_id)
            if not found:
                write_json(self, 404, {"ok": False, "error": "asset not found"})
                return
            data, mime = found
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(data)
            return

        if self.path.startswith("/archive/mobile/chat/messages") or self.path.startswith("/archive/chat/messages"):
            if not require_auth(self, allow_mobile=True):
                return
            artifact_audience_source = authenticated_audience_source(self)
            session_id = "default"
            limit = CHAT_HISTORY_LIMIT
            after_id = 0
            before_id = 0
            wait_sec = 0.0
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
                try:
                    before_id = int((params.get("before_id") or [0])[0])
                except (TypeError, ValueError):
                    before_id = 0
                try:
                    wait_sec = max(0.0, min(float((params.get("wait") or [0])[0]), 25.0))
                except (TypeError, ValueError):
                    wait_sec = 0.0
            else:
                session_id = shared_chat_session_id(SHARED_CHAT_SESSION_ID)
            # Telegram-style delta long-poll: with after_id+wait the request is
            # held until new messages exist (or the wait expires), so clients
            # append deltas instead of re-downloading and re-rendering history.
            # before_id gives scroll-up pagination (an older page).
            messages = chat_history(
                session_id,
                limit=limit,
                after_id=after_id,
                before_id=before_id,
                audience_source=artifact_audience_source,
            )
            if wait_sec > 0 and after_id > 0 and not messages:
                deadline = time.time() + wait_sec
                while time.time() < deadline:
                    time.sleep(1.0)
                    messages = chat_history(
                        session_id,
                        limit=limit,
                        after_id=after_id,
                        audience_source=artifact_audience_source,
                    )
                    if messages:
                        break
            write_json(
                self,
                200,
                {
                    "session_id": session_id,
                    "messages": messages,
                    "source_of_truth": "server",
                },
            )
            return

        if self.path.startswith("/archive/chat/reports/pending") or self.path.startswith("/archive/mobile/chat/reports/pending"):
            if not require_auth(self, allow_mobile=True):
                return
            write_json(self, 200, {"ok": True, **pending_summary()})
            return

        if self.path.startswith("/archive/chat/reports/announce") or self.path.startswith("/archive/mobile/chat/reports/announce"):
            if not require_auth(self, allow_mobile=True):
                return
            # Vox decides what to buzz and marks it announced server-side; the
            # phone calls this only when backgrounded and keeps no state.
            write_json(self, 200, phone_announce())
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

        if self.path.startswith("/archive/task-page"):
            import task_page
            params = parse_qs(urlsplit(self.path).query) if "?" in self.path else {}
            task_id = (params.get("task_id") or [""])[0]
            namespace = (params.get("namespace") or [None])[0]
            content = task_page.read_task_page(task_id, namespace=namespace) if task_id else ""
            write_json(self, 200, {"task_id": task_id, "content": content})
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

        if self.path == "/archive/internal/core/administratum-effect":
            if not require_internal_core_auth(self):
                return
            try:
                content_length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                content_length = -1
            if content_length < 0 or content_length > 262_144:
                write_json(self, 413, {"ok": False, "error": "invalid internal effect size"})
                return
            try:
                request = read_json(self)
                result = run_core_administratum_effect(request.get("effect_id"), request.get("payload"))
            except (json.JSONDecodeError, ValueError) as exc:
                write_json(self, 400, {"ok": False, "retryable": False, "code": "invalid_effect", "explanation": str(exc)})
                return
            status = 200 if result.get("ok") else 503 if result.get("retryable") else 422
            write_json(self, status, result)
            return

        if self.path == "/archive/internal/core/artifact-effect":
            if not require_internal_core_auth(self):
                return
            try:
                content_length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                content_length = -1
            if content_length < 0 or content_length > 262_144:
                write_json(self, 413, {"ok": False, "error": "invalid internal effect size"})
                return
            try:
                request = read_json(self)
                result = run_core_artifact_effect(request.get("effect_id"), request.get("payload"))
            except (json.JSONDecodeError, ValueError) as exc:
                write_json(
                    self,
                    400,
                    {"ok": False, "retryable": False, "code": "invalid_effect", "explanation": str(exc)},
                )
                return
            except Exception as exc:
                write_json(
                    self,
                    503,
                    {
                        "ok": False,
                        "retryable": True,
                        "code": "artifact_store_unavailable",
                        "explanation": f"Archive не смог надёжно записать доставку файла: {exc}",
                    },
                )
                return
            status = 200 if result.get("ok") else 503 if result.get("retryable") else 422
            write_json(self, status, result)
            return

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

        if self.path in ("/archive/mobile/chat/stream", "/archive/chat/stream"):
            if not require_auth(self, allow_mobile=True):
                return
            self.mobile_chat_stream()
            return

        if self.path.startswith("/archive/task-page"):
            try:
                payload = read_json(self)
            except json.JSONDecodeError as exc:
                write_json(self, 400, {"ok": False, "error": f"Invalid JSON: {exc}"})
                return
            import task_page
            task_id = str(payload.get("task_id") or "")
            if not task_id:
                write_json(self, 400, {"ok": False, "error": "task_id required"})
                return
            namespace = payload.get("namespace")
            if payload.get("note"):
                task_page.append_task_note(task_id, str(payload["note"]), namespace=namespace)
            elif payload.get("body") is not None:
                task_page.write_task_page(task_id, str(payload["body"]), namespace=namespace)
            write_json(self, 200, {"ok": True, "task_id": task_id})
            return

        if self.path in ("/archive/chat/reports/register-token", "/archive/mobile/chat/reports/register-token"):
            if not require_auth(self, allow_mobile=True):
                return
            try:
                payload = read_json(self)
            except json.JSONDecodeError as exc:
                write_json(self, 400, {"ok": False, "error": f"Invalid JSON: {exc}"})
                return
            write_json(self, 200, register_push_token(payload.get("token")))
            return

        if self.path in ("/archive/chat/reports/enqueue", "/archive/mobile/chat/reports/enqueue"):
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

        if self.path in ("/archive/chat/reports/deliver", "/archive/mobile/chat/reports/deliver"):
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
            run_mobile_job(
                job_id,
                lambda payload=job_payload: run_mobile_chat_payload(
                    payload,
                    trusted_turn_context=payload,
                ),
            )
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
        headline = str(display.get("headline") or event.get("type") or "Abaddon event").strip()
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
        headline = str(entry.get("headline") or entry.get("kind") or "Abaddon activity").strip()
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
        terminal_statuses = {
            "blocked", "cancelled", "completed", "corrupt", "failed", "preflight_failed",
        }
        running = (
            status not in terminal_statuses
            and (bool(active) or status in {"running", "queued", "cancelling"})
        )
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
        if str(mission_state.get("status") or "").strip().lower() in terminal_statuses:
            running = False
        if running and activity_entries:
            last_entry = activity_entries[-1] if isinstance(activity_entries[-1], dict) else {}
            current_step = str(last_entry.get("headline") or current_step).strip()
        elif not running:
            # A terminal canonical state must not inherit an old `revising`
            # headline merely because progress events are append-only.
            current_step = ""
            if summary_cards:
                final_card = summary_cards[-1] if isinstance(summary_cards[-1], dict) else {}
                current_step = str(final_card.get("headline") or "").strip()
            brigade_tabs = [
                {**tab, "active": False} if isinstance(tab, dict) else tab
                for tab in brigade_tabs
            ]
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
            write_json(self, 502, {"ok": False, "error": f"Abaddon unavailable: {exc}"})

    def collect_agent_tasks(self, limit):
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
        return status, response, tasks

    @staticmethod
    def agent_tasks_state_key(tasks):
        """Meaning-only fingerprint (mirrors the app's diff key): timestamps and
        cursors change on every poll and must not count as a state change."""
        parts = []
        for task in tasks:
            cards = task.get("activity_cards") if isinstance(task.get("activity_cards"), list) else []
            card_bits = ";".join(
                f"{hash(str(card.get('headline') or ''))}~{card.get('status') or ''}~{card.get('severity') or ''}~{hash(str(card.get('detail') or ''))}"
                for card in cards
                if isinstance(card, dict)
            )
            mission_state = task.get("mission_state") if isinstance(task.get("mission_state"), dict) else {}
            parts.append(
                "|".join(
                    [
                        str(task.get("task_id") or ""),
                        str(task.get("status") or ""),
                        str(bool(task.get("running"))),
                        str(task.get("current_step") or ""),
                        str(hash(str(task.get("final") or ""))),
                        str(mission_state.get("user_visible_state") or ""),
                        card_bits,
                    ]
                )
            )
        return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()

    def mobile_agent_tasks(self):
        params = parse_qs(urlsplit(self.path).query)
        raw_limit = (params.get("limit") or ["20"])[0]
        try:
            limit = max(1, min(int(raw_limit), 100))
        except (TypeError, ValueError):
            limit = 20
        client_key = str((params.get("state_key") or [""])[0]).strip()
        try:
            wait_sec = max(0.0, min(float((params.get("wait") or [0])[0]), 25.0))
        except (TypeError, ValueError):
            wait_sec = 0.0
        try:
            status, response, tasks = self.collect_agent_tasks(limit)
            state_key = self.agent_tasks_state_key(tasks)
            # Delta long-poll: hold the request while the meaningful state
            # matches what the client already renders.
            if wait_sec > 0 and client_key and state_key == client_key:
                deadline = time.time() + wait_sec
                while time.time() < deadline:
                    time.sleep(2.0)
                    status, response, tasks = self.collect_agent_tasks(limit)
                    state_key = self.agent_tasks_state_key(tasks)
                    if state_key != client_key:
                        break
            write_json(
                self,
                status,
                {
                    "ok": True,
                    "backend": "warmaster",
                    "state_key": state_key,
                    "changed": state_key != client_key,
                    "tasks": tasks,
                    "warmaster": response,
                },
            )
        except HTTPError as exc:
            self.write_proxy_error(exc)
        except Exception as exc:
            write_json(self, 502, {"ok": False, "error": f"Abaddon unavailable: {exc}"})

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
            write_json(self, 502, {"ok": False, "error": f"Abaddon unavailable: {exc}"})

    def mobile_agent_last_task(self):
        try:
            status, response = proxy_json_url("GET", f"{WARMASTER_BASE_URL}/runs?limit=1", timeout=30)
            runs = response.get("runs") if isinstance(response.get("runs"), list) else []
            if not runs:
                write_json(self, 404, {"ok": False, "error": "no Abaddon runs found"})
                return
            task_id = str(runs[0].get("task_id") or "")
            write_json(self, status, {"ok": True, "backend": "warmaster", "task_id": task_id, "task": self.warmaster_run_as_agent_task(runs[0])})
        except HTTPError as exc:
            self.write_proxy_error(exc)
        except Exception as exc:
            write_json(self, 502, {"ok": False, "error": f"Abaddon unavailable: {exc}"})

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
        return f"Абаддон принял задачу и ведет исполнение: task_id={task_id}. Ход работы доступен во вкладке Бригады."

    def append_warmaster_acceptance_message(self, session_id, task_id, task_text=""):
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            return
        append_chat_message(
            shared_chat_session_id(session_id or SHARED_CHAT_SESSION_ID),
            "assistant",
            self.warmaster_acceptance_message(clean_task_id),
            source="shushunya-core",
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
            duplicate_id = warmaster_duplicate_task_id(task)
            if duplicate_id:
                # Same job already on the board: resume it, don't spawn a twin.
                status, response = 200, {"ok": True, "task_id": duplicate_id, "resumed_existing": True}
                resolved_task_id = duplicate_id
            else:
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
            self.append_warmaster_acceptance_message(SHARED_CHAT_SESSION_ID, resolved_task_id, task_text=task)
            response["backend"] = "warmaster"
            response["task_id"] = resolved_task_id
            response["message"] = self.warmaster_acceptance_message(resolved_task_id) if resolved_task_id else "Абаддон принял задачу."
            response["research_loop"] = loop_response
            accepted_status = self.warmaster_loop_started_or_active(loop_status, loop_response) if loop_status else 200 <= status < 300
            response["ok"] = accepted_status
            write_json(self, 202 if accepted_status else (loop_status if loop_status else 409), response)
        except HTTPError as exc:
            self.write_proxy_error(exc)
        except Exception as exc:
            write_json(self, 502, {"ok": False, "error": f"Abaddon unavailable: {exc}"})

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
            response["message"] = "Отмена отправлена Абаддону."
            write_json(self, status, response)
        except HTTPError as exc:
            self.write_proxy_error(exc)
        except Exception as exc:
            write_json(self, 502, {"ok": False, "error": f"Abaddon unavailable: {exc}"})

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
        prefixes = ("/task ", "/w ", "/abaddon ", "!task ", "!абаддон ", "/warmaster ", "!вармастер ")
        for prefix in prefixes:
            if lower.startswith(prefix):
                return clean[len(prefix) :].strip()
        colon_prefixes = ("абаддон:", "abaddon:", "вармастер:", "warmaster:")
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
            raise ValueError("Abaddon task is empty")

        task_id = str(payload.get("task_id") or f"client-{uuid.uuid4().hex[:12]}").strip()
        core_effect = payload.get("core_effect") if isinstance(payload.get("core_effect"), dict) else None
        if core_effect and core_effect.get("id"):
            try:
                dispatched = core_dispatch_effect(str(core_effect["id"]))
            except Exception as exc:  # commitment/outbox are already durable in Core
                dispatched = {
                    "ok": False,
                    "effect": {
                        "state": "retry_wait",
                        "payload": core_effect.get("payload") if isinstance(core_effect.get("payload"), dict) else {},
                        "result": {
                            "explanation": (
                                "Core сохранил обязательство, но Archive не дождался подтверждения Абаддона: "
                                f"{exc}"
                            )
                        },
                    },
                }
            effect = dispatched.get("effect") if isinstance(dispatched.get("effect"), dict) else {}
            effect_result = effect.get("result") if isinstance(effect.get("result"), dict) else {}
            resolved_task_id = str(
                effect_result.get("delegate_ref")
                or (effect.get("payload") or {}).get("task_id")
                or task_id
            ).strip()
            if effect.get("state") != "delivered" or not effect_result.get("delegate_ref"):
                explanation = str(
                    effect_result.get("explanation")
                    or "Абаддон пока не подтвердил приём задачи."
                ).strip()
                message = (
                    f"{explanation} Обязательство не потеряно: Core сохранил его и повторит тем же "
                    f"idempotency key. task_id={resolved_task_id}."
                )
                append_chat_message(
                    session_id,
                    "user",
                    original_text or task,
                    source=client_source,
                    dedupe_key=f"core-effect:{core_effect['id']}:user",
                )
                append_chat_message(
                    session_id,
                    "assistant",
                    message,
                    source="shushunya-core",
                    dedupe_key=f"core-effect:{core_effect['id']}:retry",
                )
                return {
                    "ok": False,
                    "backend": "shushunya-core",
                    "task_id": resolved_task_id,
                    "message": message,
                    "core_effect": effect,
                    "status": effect.get("state") or "retry_wait",
                }
            status = 202
            response = {
                "ok": True,
                "task_id": resolved_task_id,
                "core_effect": effect,
                "canonical_start": True,
            }
            loop_status = 202
            loop_response = {"ok": True, "core_owned": True, "auto_start": True}
        else:
            warmaster_payload = {
                "message": task,
                "task_id": task_id,
                "auto_start": False,
                "reuse_existing": True,
                "run_mode": str(payload.get("run_mode") or "http"),
                "governor_transport": str(payload.get("governor_transport") or "http"),
            }
            duplicate_id = warmaster_duplicate_task_id(task)
            if duplicate_id:
                # Legacy explicit /abaddon path: resume the same run, do not clone it.
                status, response = 200, {"ok": True, "task_id": duplicate_id, "resumed_existing": True}
                resolved_task_id = duplicate_id
            else:
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
        self.append_warmaster_acceptance_message(session_id, resolved_task_id or task_id, task_text=original_text or task)
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

    def run_core_turn_payload(self, payload, on_token=None):
        """Resolve and execute one complete turn under the shared admission gates."""
        payload = dict(payload)
        session_id = shared_chat_session_id(payload.get("session_id") or payload.get("user") or SHARED_CHAT_SESSION_ID)
        text = trim_chat_text(payload.get("text") or payload.get("message") or "")
        image_data_url = str(payload.get("image_data_url") or "").strip()
        ensure_core_transport_identity(payload)
        with archive_state.CHAT_SESSION_LOCKS.hold(session_id), CHAT_QUEUE_LOCK:
            turn = decide_chat_turn_action(
                session_id,
                text,
                image_data_url=image_data_url,
                model=payload.get("model") or DEFAULT_MODEL,
                payload=payload,
            )
            decision = turn.get("decision") if isinstance(turn.get("decision"), dict) else {"action": "answer_in_chat"}
            payload["session_id"] = session_id
            payload["turn_decision"] = decision
            payload["turn_capabilities"] = turn.get("capabilities") if isinstance(turn.get("capabilities"), dict) else {}
            payload["turn_protocol"] = {"request": turn.get("request"), "response": turn.get("response")}
            payload["core_context_bundle"] = turn.get("context_bundle") if isinstance(turn.get("context_bundle"), dict) else {}
            payload["core_resolution"] = turn.get("core_resolution") if isinstance(turn.get("core_resolution"), dict) else {}
            payload["core_effect"] = turn.get("effect") if isinstance(turn.get("effect"), dict) else None
            if decision.get("action") == "request_warmaster_mission":
                payload["warmaster_task"] = warmaster_request_to_message(
                    decision.get("warmaster_request") if isinstance(decision.get("warmaster_request"), dict) else {}
                )
                effect_payload = payload["core_effect"].get("payload") if isinstance(payload.get("core_effect"), dict) else {}
                payload["task_id"] = str((effect_payload or {}).get("task_id") or payload.get("task_id") or "")
                return self.run_mobile_warmaster_payload(payload)
            if decision.get("action") in {"answer_in_chat", "ask_clarification"} and str(decision.get("reply") or "").strip():
                payload["forced_chat_reply"] = str(decision.get("reply") or "").strip()
            return run_mobile_chat_payload(
                payload,
                on_token=on_token,
                trusted_turn_context=payload,
            )

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
        payload["artifact_audience_source"] = authenticated_artifact_audience(self, payload)
        request_id = ensure_core_transport_identity(payload)
        # Queue immediately. Decision, Magos and execution run under the same
        # four-slot/session gate, so a slow 31B turn cannot time out the start
        # request or observe history out of order.
        try:
            job_id, created, job_status = create_mobile_turn_job_once(payload)
        except ValueError as exc:
            write_json(self, 409, {"ok": False, "error": str(exc), "client_request_id": request_id})
            return
        if created:
            run_mobile_job(job_id, lambda payload=payload: self.run_core_turn_payload(payload))
        write_json(
            self,
            202,
            {
                "ok": True,
                "job_id": job_id,
                "type": "turn",
                "client_request_id": request_id,
                "session_id": session_id,
                "status": job_status,
                "reused": not created,
            },
        )

    def mobile_chat_stream(self):
        """Token-by-token SSE for a chat send: the answer appears fluidly as it
        is generated, same pipeline (retrieval, turn protocol, memory) as the
        job path — only the delivery is streamed."""
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
        payload["session_id"] = session_id
        payload["memory_namespace"] = shared_memory_namespace(payload.get("memory_namespace"))
        payload["client_source"] = str(payload.get("client_source") or payload.get("source") or "app").strip()[:80] or "app"
        payload["artifact_audience_source"] = authenticated_artifact_audience(self, payload)
        request_id = ensure_core_transport_identity(payload)
        try:
            job_id, created, _job_status = create_mobile_turn_job_once(payload)
        except ValueError as exc:
            write_json(self, 409, {"ok": False, "error": str(exc), "client_request_id": request_id})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def sse(obj):
            self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()

        events = queue.Queue()
        outcome = {}

        def on_token(piece):
            if piece:
                events.put(("token", str(piece)))

        def worker():
            try:
                if created:
                    try:
                        update_mobile_job(job_id, "running")
                    except Exception as exc:  # noqa: BLE001 - surface durable-state failure through SSE
                        diagnostic = f"mobile_job_running_persist_failed: {type(exc).__name__}: {exc}"
                        outcome["error"] = RuntimeError(diagnostic)
                        mark_mobile_job_interrupted(job_id, diagnostic)
                        return
                    try:
                        outcome["result"] = self.run_core_turn_payload(payload, on_token=on_token)
                    except Exception as exc:  # noqa: BLE001 - reported through SSE
                        outcome["error"] = exc
                        try:
                            update_mobile_job(job_id, "failed", error=exc)
                        except Exception as persist_exc:  # noqa: BLE001
                            diagnostic = (
                                "mobile_job_failure_persist_failed: "
                                f"worker={type(exc).__name__}: {exc}; "
                                f"storage={type(persist_exc).__name__}: {persist_exc}"
                            )
                            outcome["error"] = RuntimeError(diagnostic)
                            mark_mobile_job_interrupted(job_id, diagnostic)
                        return
                    try:
                        update_mobile_job(job_id, "done", response=outcome["result"])
                    except Exception as exc:  # noqa: BLE001
                        diagnostic = f"mobile_job_result_persist_failed: {type(exc).__name__}: {exc}"
                        outcome["error"] = RuntimeError(diagnostic)
                        mark_mobile_job_interrupted(job_id, diagnostic)
                    return
                # A duplicate transport request observes the original durable job.
                # It does not run Core, Magos or an external effect again.
                while True:
                    snapshot = mobile_job_snapshot(job_id)
                    status = str(snapshot.get("status") or "")
                    if status == "done":
                        outcome["result"] = snapshot.get("response") if isinstance(snapshot.get("response"), dict) else {}
                        break
                    if status in {"failed", "interrupted"}:
                        outcome["error"] = RuntimeError(str(snapshot.get("error") or f"turn job {status}"))
                        break
                    if status not in {"queued", "running"}:
                        outcome["error"] = RuntimeError(f"turn job has unexpected status: {status or 'missing'}")
                        break
                    time.sleep(0.5)
            except Exception as exc:  # noqa: BLE001 - no worker failure may strand the SSE loop
                outcome["error"] = exc
            finally:
                events.put(("done", ""))

        threading.Thread(target=worker, daemon=True, name=f"core-turn-{request_id}").start()
        collected = []
        try:
            sse({"type": "status", "status": "thinking", "client_request_id": request_id})
            while True:
                try:
                    kind, value = events.get(timeout=10)
                except queue.Empty:
                    sse({"type": "status", "status": "working", "client_request_id": request_id})
                    continue
                if kind == "done":
                    break
                collected.append(value)
                sse({"type": "token", "text": value})
            if outcome.get("error"):
                raise outcome["error"]
            result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
            message = str(result.get("message") or "")
            artifact = result.get("artifact") if isinstance(result.get("artifact"), dict) else None
            if result.get("action") == "deliver_artifact":
                sse(
                    {
                        "type": "done",
                        "action": "deliver_artifact",
                        "full": message,
                        "artifact_id": result.get("artifact_id") or (artifact or {}).get("artifact_id"),
                        "artifact": artifact,
                        "effect_ok": bool(result.get("effect_ok")),
                        "client_request_id": request_id,
                    }
                )
                return
            if result.get("backend") == "warmaster":
                sse(
                    {
                        "type": "route",
                        "backend": "warmaster",
                        "accepted": bool(result.get("ok")),
                        "task_id": result.get("task_id"),
                        "status": result.get("status") or ("accepted" if result.get("ok") else "not_confirmed"),
                        "message": message,
                    }
                )
                if message:
                    collected[:] = [message]
                sse({"type": "done", "full": message or "".join(collected), "client_request_id": request_id})
                return
            if not collected and message:
                collected.append(message)
                sse({"type": "token", "text": message})
            sse({"type": "done", "full": "".join(collected), "client_request_id": request_id})
        except (BrokenPipeError, ConnectionResetError):
            return  # worker continues; durable effects and history are not cancelled
        except Exception as exc:  # noqa: BLE001
            try:
                sse({"type": "error", "error": str(exc)})
            except Exception:  # noqa: BLE001
                pass

    def mobile_chat_protocol_completion_payload(self, message, finish_reason="turn_protocol_reply", extra=None):
        payload = {
            "object": "chat.completion",
            "model": "shushunya-core",
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
            "model": "shushunya-core",
            "choices": [{"index": 0, "delta": {"content": message}, "finish_reason": None}],
        }
        done = {
            "object": "chat.completion.chunk",
            "model": "shushunya-core",
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
        try:
            payload = read_json(self)
        except json.JSONDecodeError as exc:
            write_json(self, 400, {"error": f"Invalid JSON: {exc}"})
            return

        session_id = shared_chat_session_id(payload.get("session_id") or payload.get("user") or SHARED_CHAT_SESSION_ID)
        # Preserve turn order inside one conversation, but do not let queued
        # turns from that conversation occupy all four global pipeline slots.
        with archive_state.CHAT_SESSION_LOCKS.hold(session_id), CHAT_QUEUE_LOCK:
            created_at = now_iso()
            turn_id = str(uuid.uuid4())
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
            payload["artifact_audience_source"] = authenticated_artifact_audience(self, payload)
            stream = internal_flag(payload.get("stream", True), default=True)
            model = payload.get("model") or DEFAULT_MODEL
            system_prompt = ""
            max_tokens = int(payload.get("max_tokens") or 2048)
            temperature = float(payload.get("temperature") or 0.4)

            try:
                turn = decide_chat_turn_action(
                    session_id,
                    text,
                    image_data_url=image_data_url,
                    model=model,
                    payload=payload,
                )
            except Exception as exc:
                write_json(self, 502, {"error": f"turn protocol unavailable: {exc}", "session_id": session_id})
                return
            decision = turn.get("decision") if isinstance(turn.get("decision"), dict) else {"action": "answer_in_chat"}
            turn_capabilities = turn.get("capabilities") if isinstance(turn.get("capabilities"), dict) else {}
            if decision.get("action") == "request_warmaster_mission":
                task_id = str(payload.get("task_id") or f"client-{uuid.uuid4().hex[:12]}").strip()
                payload["stream"] = False
                payload["session_id"] = session_id
                payload["memory_namespace"] = memory_namespace
                payload["client_source"] = client_source
                payload["task_id"] = task_id
                payload["warmaster_task"] = warmaster_request_to_message(decision.get("warmaster_request") if isinstance(decision.get("warmaster_request"), dict) else {})
                payload["turn_decision"] = decision
                payload["turn_capabilities"] = turn_capabilities
                payload["turn_protocol"] = {"request": turn.get("request"), "response": turn.get("response")}
                payload["core_context_bundle"] = turn.get("context_bundle") if isinstance(turn.get("context_bundle"), dict) else {}
                payload["core_resolution"] = turn.get("core_resolution") if isinstance(turn.get("core_resolution"), dict) else {}
                payload["core_effect"] = turn.get("effect") if isinstance(turn.get("effect"), dict) else None
                effect_payload = payload["core_effect"].get("payload") if isinstance(payload.get("core_effect"), dict) else {}
                task_id = str((effect_payload or {}).get("task_id") or task_id).strip()
                payload["task_id"] = task_id
                job_id = create_mobile_job("warmaster", payload)
                run_mobile_job(job_id, lambda payload=payload: self.run_mobile_warmaster_payload(payload))
                message = (
                    f"Core зафиксировал обязательство и поставил отправку Абаддону в durable-очередь: task_id={task_id}. "
                    "Приём Абаддоном будет подтверждён отдельно фактическим ответом; ход принятой миссии появится во вкладке Бригады."
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

            # Single pipeline: this endpoint used to carry its own copy of the
            # chat flow (intent + Magos + prepare + record + librarian), which
            # drifted from the job path and bred bugs. It now delegates to
            # run_mobile_chat_payload; stream=true replays the final text as one
            # SSE chunk (true token streaming had no live consumers).
            payload["stream"] = False
            payload["session_id"] = session_id
            payload["memory_namespace"] = memory_namespace
            payload["client_source"] = client_source
            payload["turn_decision"] = decision
            payload["turn_capabilities"] = turn_capabilities
            payload["turn_protocol"] = {"request": turn.get("request"), "response": turn.get("response")}
            payload["core_context_bundle"] = turn.get("context_bundle") if isinstance(turn.get("context_bundle"), dict) else {}
            payload["core_resolution"] = turn.get("core_resolution") if isinstance(turn.get("core_resolution"), dict) else {}
            payload["core_effect"] = turn.get("effect") if isinstance(turn.get("effect"), dict) else None
            if decision.get("action") in {"answer_in_chat", "ask_clarification"} and str(decision.get("reply") or "").strip():
                payload["forced_chat_reply"] = str(decision.get("reply") or "").strip()
            try:
                result = run_mobile_chat_payload(payload, trusted_turn_context=payload)
            except ChatQueueBusy as exc:
                write_json(self, 503, {"error": str(exc), "type": "chat_queue_busy"})
                return
            except Exception as exc:
                write_json(self, 502, {"error": f"chat pipeline failed: {exc}", "session_id": session_id})
                return
            response = result.get("response") if isinstance(result.get("response"), dict) else {}
            if stream:
                self.stream_static_mobile_chat_completion(str(result.get("message") or ""), finish_reason="stop")
            else:
                write_json(self, 200, response)

    def chat_completion(self):
        # Generic OpenAI-compatible endpoint. It used to carry a third copy of
        # the chat pipeline; it now extracts the latest user message and
        # delegates to the single job pipeline (run_mobile_chat_payload).
        payload = read_json(self)
        user_text = trim_chat_text(latest_user_message(sanitize_messages_for_memory(list(payload.get("messages") or []))))
        if not user_text:
            write_json(self, 400, {"error": "no user message in messages[]"})
            return
        job_payload = {
            "text": user_text,
            "model": payload.get("model") or DEFAULT_MODEL,
            "session_id": payload.get("session_id") or payload.get("user") or SHARED_CHAT_SESSION_ID,
            "memory_namespace": payload.get("memory_namespace") or SHARED_MEMORY_NAMESPACE,
            "client_source": str(payload.get("client_source") or payload.get("source") or "api").strip()[:80] or "api",
            "artifact_audience_source": authenticated_artifact_audience(self, payload, fallback="api"),
            "archive_enabled": internal_flag(payload.get("archive_enabled", True), default=True),
            "focus_enabled": internal_flag(payload.get("focus_enabled", True), default=True),
            "vector_enabled": internal_flag(payload.get("vector_enabled", True), default=True),
            "graph_enabled": internal_flag(payload.get("graph_enabled", True), default=True),
            "archive_system_prompt_enabled": internal_flag(payload.get("archive_system_prompt_enabled", True), default=True),
            "system_event": internal_flag(payload.get("system_event", False), default=False),
            "intent_detection": internal_flag(payload.get("intent_detection", True), default=True),
            "max_tokens": payload.get("max_tokens") or 2048,
            "temperature": payload.get("temperature") or 0.4,
            "stream": False,
            "turn_decision": payload.get("turn_decision") if isinstance(payload.get("turn_decision"), dict) else {"action": "answer_in_chat"},
        }
        try:
            result = run_mobile_chat_payload(job_payload)
        except ChatQueueBusy as exc:
            write_json(self, 503, {"error": str(exc), "type": "chat_queue_busy"})
            return
        except Exception as exc:
            write_json(self, 502, {"error": f"chat pipeline failed: {exc}"})
            return
        response = result.get("response") if isinstance(result.get("response"), dict) else {}
        write_json(self, 200, response)

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
