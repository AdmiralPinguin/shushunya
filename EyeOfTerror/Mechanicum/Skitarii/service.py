"""Skitarii brigade HTTP service.

The single door the governor knocks on for a code mission. Replaces the old
six paper-workers: one POST runs the whole brigade (spec -> fighter loop -> accept)
inside the sandbox VM and returns an honest verdict.

  POST /mission  {"goal": "...", "task_id": "...", "checks": [...optional...]}
      -> {"status": "done|failed", "accepted": bool, "summary", "artifacts",
          "checks", "rounds":[...], "files": {path: content}}
  GET  /health
"""
from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brigade import run_mission  # noqa: E402
from planner import plan_and_run  # noqa: E402
from executor import VmExecutor  # noqa: E402
from explorer import explore, brief_for_fighter  # noqa: E402
from reviewer import review  # noqa: E402
import mission_store  # noqa: E402

VM_KEY = os.environ.get("SKITARII_VM_KEY",
                        "/media/shushunya/SHUSHUNYA/shushunya/CoreOfMadness/vm-sandbox/skitarii_key")
VM_PORT = int(os.environ.get("SKITARII_VM_PORT", "2222"))


def _memory(task_id: str, note: str) -> None:
    """Best-effort note to the task's wiki memory page (also feeds Shushunya)."""
    try:
        from harness import _memory_note
        _memory_note(task_id, note)
    except Exception:
        pass


def _mission_executor(task_id: str) -> VmExecutor:
    # Each RUN gets its own unique clean workdir — a random suffix so two concurrent
    # requests with the same task_id can't wipe each other's directory (race fix).
    import uuid
    safe = "".join(c for c in task_id if c.isalnum() or c in "-_") or "mission"
    workdir = f"/home/skitarii/work/{safe}-{uuid.uuid4().hex[:8]}"
    ex = VmExecutor(host="127.0.0.1", port=VM_PORT, user="skitarii", key=VM_KEY, workdir=workdir)
    ex.bash(f"rm -rf {workdir}; mkdir -p {workdir}", timeout=30)
    return ex


def _collect_files(ex: VmExecutor, artifacts: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in artifacts[:12]:
        try:
            out[path] = ex.fetch_artifact(path).decode("utf-8", errors="replace")[:100_000]
        except Exception:
            pass
    if not out:
        # decomposed missions may not name artifacts — grab the code files in the workdir
        listing = ex.bash("find . -maxdepth 2 -type f "
                          "\\( -name '*.py' -o -name '*.php' -o -name '*.js' -o -name '*.sh' -o -name '*.md' "
                          "-o -name '*.html' -o -name '*.css' -o -name '*.json' \\) | head -20", timeout=30)
        for path in (listing.get("stdout") or "").split():
            path = path.lstrip("./")
            try:
                out[path] = ex.fetch_artifact(path).decode("utf-8", errors="replace")[:100_000]
            except Exception:
                pass
    return out


def execute_mission(payload: dict, mission=None) -> dict:
    """Run one mission end to end and return the verdict. If `mission` is given it is an
    async mission_store.Mission: the fighter can ask it questions and be cancelled, and
    progress is journalled to it."""
    goal = str(payload.get("goal") or "").strip()
    task_id = str(payload.get("task_id") or f"m{int(time.time())}")
    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else None
    workspace_files = payload.get("workspace_files") if isinstance(payload.get("workspace_files"), dict) else {}
    mode = str(payload.get("mode") or "greenfield")
    ask_fn = (lambda q: mission.ask_user(q)) if mission is not None else None
    cancel_fn = (lambda: mission.cancelled.is_set()) if mission is not None else None
    note = (lambda m: (mission.record("note", {"text": m}) if mission is not None else None)) or (lambda m: None)

    ex = _mission_executor(task_id)
    if not ex.alive():
        return {"status": "blocked", "accepted": False, "error": "sandbox VM is not reachable"}

    preloaded = 0
    for rel, content in (workspace_files or {}).items():
        try:
            ex.write_file(str(rel), str(content)); preloaded += 1
        except Exception:
            pass
    base_commit = ""
    if preloaded:
        snap = ex.bash("git init -q . && git add -A && "
                       "git -c user.email=b@x -c user.name=skitarii commit -qm baseline && "
                       "git rev-parse HEAD", timeout=60)
        base_commit = (snap.get("stdout") or "").strip().split("\n")[-1]
        goal += (f"\n\n(ПРАВКА СУЩЕСТВУЮЩЕГО кода: {preloaded} файл(ов) проекта уже лежат в рабочем "
                 "каталоге с их путями — читай и правь их, НЕ переписывай с нуля.)")
    _memory(task_id, f"Загружено {preloaded} файлов проекта." if preloaded else f"Старт: {goal[:200]}")

    exploration = explore(goal, workspace_files) if workspace_files else {}
    brief = brief_for_fighter(exploration) if exploration else ""
    if brief:
        goal += brief; note("Explorer наметил цели/инварианты.")

    if checks:
        verdict = run_mission(goal, ex, checks=checks, task_id=task_id, ask_fn=ask_fn, cancel_fn=cancel_fn,
                              max_fighter_rounds=int(payload.get("max_rounds") or 3),
                              max_steps=int(payload.get("max_steps") or 40),
                              max_wall_sec=int(payload.get("max_wall_sec") or 3600))
    else:
        verdict = plan_and_run(goal, ex, task_id=task_id, ask_fn=ask_fn, cancel_fn=cancel_fn,
                               max_wall_sec=int(payload.get("max_wall_sec") or 3600),
                               memory=lambda m: (note(m), _memory(task_id, m)))
    verdict["files"] = _collect_files(ex, verdict.get("artifacts") or [])
    verdict["task_id"] = task_id
    if base_commit:
        diff = ex.bash("git diff HEAD", timeout=60).get("stdout") or ""
        changed = [ln for ln in (ex.bash("git diff --name-only HEAD", timeout=30).get("stdout") or "").splitlines() if ln]
        last_acc = {}
        for r in reversed(verdict.get("rounds") or []):
            if isinstance(r.get("acceptance"), dict):
                last_acc = r["acceptance"]; break
        if not last_acc and isinstance(verdict.get("acceptance"), dict):
            last_acc = verdict["acceptance"]
        rev = review(goal, diff, last_acc, invariants=exploration.get("invariants"))
        verdict["review"] = rev
        if verdict.get("accepted") and not rev["approved"]:
            verdict["accepted"] = False
            verdict["status"] = "needs_revision"
            verdict["summary"] = "Ревьюер завернул: " + "; ".join(rev["issues"])[:400]
            note("Ревьюер завернул патч.")
        verdict["patch_bundle"] = {"base_commit": base_commit, "changed_files": changed,
                                   "unified_diff": diff[:400_000], "rollback": "git apply -R <patch>",
                                   "apply_gate": "accepted" if verdict.get("accepted") else "blocked"}
    _memory(task_id, f"Итог: {verdict.get('status')} (accepted={verdict.get('accepted')}).")
    return verdict


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionError):
            # client hung up (e.g. curl timed out) — never let it crash the server
            pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionError):
            self.close_connection = True

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path == "/health":
            # cheap health: does not block on the VM (that could take seconds over SSH
            # and make a client time out). Report VM reachability only if asked.
            probe = "vm" in (self.path.split("?", 1)[1] if "?" in self.path else "")
            payload = {"status": "ok", "service": "Skitarii"}
            if probe:
                payload["vm_alive"] = VmExecutor(host="127.0.0.1", port=VM_PORT,
                                                 user="skitarii", key=VM_KEY).alive()
            self._send(200, payload)
            return
        parts = [p for p in self.path.split("?", 1)[0].split("/") if p]
        if len(parts) >= 2 and parts[0] == "missions":
            m = mission_store.get(parts[1])
            if not m:
                self._send(404, {"error": "mission not found"}); return
            if len(parts) == 3 and parts[2] == "events":   # GET /missions/{id}/events
                self._send(200, {"id": m.id, "events": m.snapshot()["events"]}); return
            self._send(200, m.snapshot(event_limit=50)); return   # GET /missions/{id}
        self._send(404, {"error": "not found"})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self):
        parts = [p for p in self.path.split("?", 1)[0].split("/") if p]
        try:
            payload = self._body()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": f"bad json: {exc}"}); return

        # sync (blocking) — used by the Warmaster bridge / research loop
        if parts == ["mission"]:
            if not str(payload.get("goal") or "").strip():
                self._send(400, {"error": "goal is required"}); return
            self._send(200, execute_mission(payload)); return

        # async lifecycle
        if parts == ["missions"]:              # POST /missions -> start in background
            goal = str(payload.get("goal") or "").strip()
            if not goal:
                self._send(400, {"error": "goal is required"}); return
            mid = str(payload.get("task_id") or f"m{int(time.time()*1000)}")
            m = mission_store.create(mid, goal)
            m.record("created", {"goal": goal[:300]})
            mission_store.run_async(m, lambda mm: execute_mission(payload, mm))
            self._send(202, {"mission_id": mid, "status": m.status}); return
        if len(parts) == 3 and parts[0] == "missions":
            m = mission_store.get(parts[1])
            if not m:
                self._send(404, {"error": "mission not found"}); return
            if parts[2] == "answer":           # POST /missions/{id}/answer
                ok = m.provide_answer(str(payload.get("answer") or ""))
                self._send(200 if ok else 409, {"ok": ok, "status": m.status}); return
            if parts[2] == "cancel":           # POST /missions/{id}/cancel
                self._send(200, {"ok": mission_store.cancel(parts[1]), "status": m.status}); return
        self._send(404, {"error": "not found"})


def main():
    host = os.environ.get("SKITARII_HOST", "127.0.0.1")
    port = int(os.environ.get("SKITARII_PORT", "7200"))
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True          # a crashing request thread never takes the server down
    print(f"Skitarii brigade listening on http://{host}:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
