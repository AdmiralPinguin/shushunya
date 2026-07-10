"""Mission store — async lifecycle for Skitarii missions.

Turns the warband from a blocking HTTP call into managed missions: start in the
background, poll status/events, answer a mid-run question, cancel. Every event is
appended to a JSONL journal so a restart can see what happened (crash-visible).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

STORE_ROOT = Path(__file__).resolve().parent / "runtime" / "missions"


class Mission:
    def __init__(self, mission_id: str, goal: str):
        self.id = mission_id
        self.goal = goal
        self.status = "queued"           # queued|running|needs_user|done|failed|blocked|cancelled
        self.events: list[dict[str, Any]] = []
        self.result: dict[str, Any] | None = None
        self.created = time.time()
        self.updated = time.time()
        # clarify: fighter blocks on _answer_ev until the user POSTs an answer
        self.question: str | None = None
        self.answer: str | None = None
        self._answer_ev = threading.Event()
        self.cancelled = threading.Event()
        self._lock = threading.Lock()

    def _dir(self) -> Path:
        d = STORE_ROOT / "".join(c for c in self.id if c.isalnum() or c in "-_")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def record(self, etype: str, data: dict[str, Any] | None = None) -> None:
        ev = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "type": etype, **(data or {})}
        with self._lock:
            self.events.append(ev)
            self.updated = time.time()
        try:
            with open(self._dir() / "events.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def set_status(self, status: str) -> None:
        self.status = status
        self.record("status", {"status": status})

    # --- clarify: called from inside the fighter thread ---
    def ask_user(self, question: str, timeout: float = 3600) -> str:
        """Block the mission until the user answers (or cancel/timeout). Returns the
        answer text, or '' if none — the fighter then proceeds on its best judgement."""
        with self._lock:
            self.question = question
            self.answer = None
            self._answer_ev.clear()
        self.set_status("needs_user")
        self.record("question", {"question": question})
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.cancelled.is_set():
                return ""
            if self._answer_ev.wait(timeout=1.0):
                break
        if self.cancelled.is_set():
            return ""   # don't flip back to running — cancel is terminal
        ans = self.answer or ""
        with self._lock:
            self.question = None
        self.set_status("running")
        self.record("answer", {"answer": ans[:500]})
        return ans

    def provide_answer(self, text: str) -> bool:
        if self.status != "needs_user":
            return False
        self.answer = str(text)
        self._answer_ev.set()
        return True

    def snapshot(self, event_limit: int = 0) -> dict[str, Any]:
        with self._lock:
            evs = self.events[-event_limit:] if event_limit else list(self.events)
        return {"id": self.id, "status": self.status, "question": self.question,
                "result": self.result, "events": evs,
                "created": self.created, "updated": self.updated}


_MISSIONS: dict[str, Mission] = {}
_GLOCK = threading.Lock()


def create(mission_id: str, goal: str) -> Mission:
    with _GLOCK:
        m = Mission(mission_id, goal)
        _MISSIONS[mission_id] = m
        return m


def get(mission_id: str) -> Mission | None:
    return _MISSIONS.get(mission_id)


def run_async(m: Mission, fn: Callable[[Mission], dict[str, Any]]) -> None:
    """Run fn(m) in a background thread; fn returns the verdict dict."""
    def _run():
        m.set_status("running")
        try:
            verdict = fn(m)
            if m.cancelled.is_set():
                m.result = {"status": "cancelled", "accepted": False}
                return  # cancel is terminal — don't let a late return overwrite it
            m.result = verdict
            m.set_status(verdict.get("status") or ("done" if verdict.get("accepted") else "failed"))
        except Exception as exc:  # noqa: BLE001
            m.result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            m.set_status("failed")
            m.record("error", {"error": str(exc)})
    threading.Thread(target=_run, daemon=True, name=f"mission-{m.id}").start()


def resume(mission_id: str, fn: Callable[["Mission"], dict[str, Any]]) -> bool:
    """Re-run a STOPPED mission (failed/blocked/cancelled/done) from its goal as a fresh
    attempt, keeping the same id and journal. Refuses to touch a mission that is still
    active (running/queued/needs_user) — use answer/cancel for those."""
    m = _MISSIONS.get(mission_id)
    if not m or m.status in ("running", "queued", "needs_user"):
        return False
    # fresh sync primitives so a stale cancel/answer can't poison the new attempt
    m.cancelled = threading.Event()
    m._answer_ev = threading.Event()
    m.answer = None
    m.question = None
    m.result = None
    m.record("resume", {"from_status": m.status})
    run_async(m, fn)
    return True


def cancel(mission_id: str) -> bool:
    m = _MISSIONS.get(mission_id)
    if not m or m.status in ("done", "failed", "cancelled", "blocked"):
        return False
    m.cancelled.set()
    m._answer_ev.set()  # unblock a waiting ask_user
    m.set_status("cancelled")
    return True
