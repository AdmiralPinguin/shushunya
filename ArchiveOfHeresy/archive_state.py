"""Mutable runtime state for ArchiveOfHeresy: locks, chat-queue primitives,
and the process singletons (set once in main()). Imported by main and the
gateway modules so all share one set of objects."""
import os
import threading


ARCHIVE_LOCK = threading.Lock()
CHAT_QUEUE_WAIT_TIMEOUT_SEC = float(os.environ.get("ARCHIVE_CHAT_QUEUE_WAIT_TIMEOUT_SEC", "30"))


class ChatQueueBusy(Exception):
    pass


class TimedChatQueueLock:
    def __init__(self, timeout_sec):
        self._lock = threading.Lock()
        self.timeout_sec = max(0.0, float(timeout_sec))

    def __enter__(self):
        acquired = self._lock.acquire(timeout=self.timeout_sec)
        if not acquired:
            raise ChatQueueBusy(f"chat queue is busy after {self.timeout_sec:g}s")
        return self

    def __exit__(self, exc_type, exc, tb):
        self._lock.release()
        return False


CHAT_QUEUE_LOCK = TimedChatQueueLock(CHAT_QUEUE_WAIT_TIMEOUT_SEC)
MAINTENANCE_LOCK = threading.Lock()
MOBILE_JOB_LOCK = threading.Lock()
LIBRARIAN = None
MAGOS = None
FOCUS_BOOKSHELF = None
VECTOR_MEMORY = None
GRAPH_MEMORY = None
