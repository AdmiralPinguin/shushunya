"""Mutable runtime state for ArchiveOfHeresy: locks, chat-queue primitives,
and the process singletons (set once in main()). Imported by main and the
gateway modules so all share one set of objects."""
import os
import threading


ARCHIVE_LOCK = threading.Lock()
# Match the interactive dispatcher lane: a fifth request should wait behind the
# four active pipelines instead of being failed by this front-door guard first.
CHAT_QUEUE_WAIT_TIMEOUT_SEC = float(os.environ.get("ARCHIVE_CHAT_QUEUE_WAIT_TIMEOUT_SEC", "300"))
CHAT_QUEUE_CONCURRENCY = int(os.environ.get("ARCHIVE_CHAT_CONCURRENCY", "4"))
if CHAT_QUEUE_CONCURRENCY < 1:
    raise RuntimeError("ARCHIVE_CHAT_CONCURRENCY must be >= 1")


class ChatQueueBusy(Exception):
    pass


class TimedChatQueueLock:
    """Re-entrant per-thread admission gate for chat pipelines.

    Re-entrancy matters because the HTTP handler delegates to the shared chat
    pipeline while already holding this gate.  A plain semaphore would consume
    two permits per request and can deadlock when every outer request waits for
    an inner permit.
    """

    def __init__(self, timeout_sec, concurrency=1):
        self.timeout_sec = max(0.0, float(timeout_sec))
        self.concurrency = max(1, int(concurrency))
        self._slots = threading.BoundedSemaphore(self.concurrency)
        self._local = threading.local()
        self._state_lock = threading.Lock()
        self._active = 0
        self._admitted_total = 0
        self._timed_out_total = 0

    def __enter__(self):
        depth = int(getattr(self._local, "depth", 0))
        if depth:
            self._local.depth = depth + 1
            return self
        acquired = self._slots.acquire(timeout=self.timeout_sec)
        if not acquired:
            with self._state_lock:
                self._timed_out_total += 1
            raise ChatQueueBusy(f"chat queue is busy after {self.timeout_sec:g}s")
        self._local.depth = 1
        with self._state_lock:
            self._active += 1
            self._admitted_total += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        depth = int(getattr(self._local, "depth", 0))
        if depth < 1:
            raise RuntimeError("chat queue lock released without acquisition")
        if depth > 1:
            self._local.depth = depth - 1
            return False
        self._local.depth = 0
        with self._state_lock:
            self._active -= 1
        self._slots.release()
        return False

    def snapshot(self):
        with self._state_lock:
            return {
                "capacity": self.concurrency,
                "active": self._active,
                "admitted_total": self._admitted_total,
                "timed_out_total": self._timed_out_total,
            }


class _SessionLockLease:
    def __init__(self, pool, key):
        self._pool = pool
        self._key = str(key)
        self._lock = None

    def __enter__(self):
        self._lock = self._pool._reserve(self._key)
        if not self._lock.acquire(timeout=self._pool.timeout_sec):
            self._pool._drop_reference(self._key, self._lock)
            self._lock = None
            raise ChatQueueBusy(
                f"chat session is busy after {self._pool.timeout_sec:g}s"
            )
        return self

    def __exit__(self, exc_type, exc, tb):
        lock = self._lock
        if lock is None:
            raise RuntimeError("chat session lock released without acquisition")
        lock.release()
        self._pool._drop_reference(self._key, lock)
        self._lock = None
        return False


class TimedSessionLocks:
    """Serialize turns within one session without serializing other sessions."""

    def __init__(self, timeout_sec):
        self.timeout_sec = max(0.0, float(timeout_sec))
        self._guard = threading.Lock()
        self._entries = {}

    def _reserve(self, key):
        with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = [threading.RLock(), 0]
                self._entries[key] = entry
            entry[1] += 1
            return entry[0]

    def _drop_reference(self, key, lock):
        with self._guard:
            entry = self._entries.get(key)
            if entry is None or entry[0] is not lock or entry[1] < 1:
                raise RuntimeError("chat session lock registry is inconsistent")
            entry[1] -= 1
            if entry[1] == 0:
                del self._entries[key]

    def hold(self, key):
        return _SessionLockLease(self, key)

    def snapshot(self):
        with self._guard:
            return {"sessions": len(self._entries)}


CHAT_QUEUE_LOCK = TimedChatQueueLock(
    CHAT_QUEUE_WAIT_TIMEOUT_SEC,
    concurrency=CHAT_QUEUE_CONCURRENCY,
)
CHAT_SESSION_LOCKS = TimedSessionLocks(CHAT_QUEUE_WAIT_TIMEOUT_SEC)
MAINTENANCE_LOCK = threading.Lock()
MOBILE_JOB_LOCK = threading.Lock()
LIBRARIAN = None
MAGOS = None
FOCUS_BOOKSHELF = None
VECTOR_MEMORY = None
GRAPH_MEMORY = None
