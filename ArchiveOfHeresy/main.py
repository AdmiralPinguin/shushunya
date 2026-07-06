#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from archivist_agent import Librarian
from archivist_agent.agent import FocusBookshelf, WikiBookshelf
from archivist_agent.graph_memory import GRAPH_TOP_K, GraphMemory
from archivist_agent.magos_agent import MAGOS_CONTEXT_LAYERS, Magos
from archivist_agent.quality_report import generate_quality_report
from archivist_agent.vector_memory import VECTOR_TOP_K, VectorMemory, latest_user_message


class ArchiveThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


from archive_config import *  # noqa: F401,F403 - shared config constants
from archive_httpio import *  # noqa: F401,F403 - http/proxy helpers
from archive_util import *  # noqa: F401,F403 - namespace/memory-text helpers
from archive_ops import *  # noqa: F401,F403 - memory/chat/storage/mobile ops
import archive_state
from archive_state import (  # noqa: F401 - shared locks & chat-queue primitives
    ARCHIVE_LOCK,
    CHAT_QUEUE_LOCK,
    CHAT_QUEUE_WAIT_TIMEOUT_SEC,
    ChatQueueBusy,
    MAINTENANCE_LOCK,
    MOBILE_JOB_LOCK,
    TimedChatQueueLock,
)


from archive_handler import ArchiveHandler  # noqa: F401
def main():
    init_storage()
    FOCUS_COMPONENTS.clear()  # shared cache lives in archive_config; mutate in place
    GRAPH_COMPONENTS.clear()
    archive_state.VECTOR_MEMORY = VectorMemory(VECTOR_ROOT)
    vector_backfilled = archive_state.VECTOR_MEMORY.backfill_from_archive(SQLITE_PATH)
    archive_state.GRAPH_MEMORY = graph_memory_for_namespace("default")
    graph_backfilled = archive_state.GRAPH_MEMORY.backfill_from_archive()
    default_components = focus_components("default")
    archive_state.FOCUS_BOOKSHELF = default_components["bookshelf"]
    archive_state.LIBRARIAN = default_components["librarian"]
    archive_state.MAGOS = default_components["magos"]
    server = ArchiveThreadingHTTPServer((HOST, PORT), ArchiveHandler)
    print(f"ArchiveOfHeresy main started: http://{HOST}:{PORT}", flush=True)
    print(f"Upstream LLM: {LLM_BASE_URL}", flush=True)
    print(f"JSONL archive: {JSONL_ROOT}", flush=True)
    print(f"Memory events: {MEMORY_EVENTS_ROOT}", flush=True)
    print(f"SQLite archive: {SQLITE_PATH}", flush=True)
    print(f"Focus files: {FOCUS_ROOT}", flush=True)
    print(f"Wiki memory: {WIKI_ROOT}", flush=True)
    print(f"Vector memory: {VECTOR_ROOT}", flush=True)
    print(f"Graph memory: {GRAPH_ROOT}", flush=True)
    print(f"Vector backfill turns: {vector_backfilled}", flush=True)
    print(f"Graph backfill nodes: {graph_backfilled}", flush=True)
    if MEMORY_QUALITY_REPORT_ENABLED:
        threading.Thread(target=memory_quality_report_loop, daemon=True, name="memory-quality-report").start()
        print(f"Memory quality report: enabled at {MEMORY_QUALITY_REPORT_HOUR:02d}:00", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
