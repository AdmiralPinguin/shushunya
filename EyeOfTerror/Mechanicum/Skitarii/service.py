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
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/mission":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": f"bad json: {exc}"})
            return
        goal = str(payload.get("goal") or "").strip()
        if not goal:
            self._send(400, {"error": "goal is required"})
            return
        task_id = str(payload.get("task_id") or f"m{int(time.time())}")
        checks = payload.get("checks") if isinstance(payload.get("checks"), list) else None
        workspace_files = payload.get("workspace_files") if isinstance(payload.get("workspace_files"), dict) else {}
        mode = str(payload.get("mode") or "greenfield")
        ex = _mission_executor(task_id)
        if not ex.alive():
            self._send(503, {"error": "sandbox VM is not reachable"})
            return
        # PATCH mode: preload the real project files so the fighter edits existing
        # source instead of writing a blank greenfield file.
        preloaded = 0
        for rel, content in (workspace_files or {}).items():
            try:
                ex.write_file(str(rel), str(content))
                preloaded += 1
            except Exception:
                pass
        base_commit = ""
        if preloaded:
            # snapshot the preloaded project as a git baseline so we can return a clean
            # unified diff of exactly what the fighter changed (Этап 3: patch bundle).
            snap = ex.bash("git init -q . && git add -A && "
                           "git -c user.email=b@x -c user.name=skitarii commit -qm baseline && "
                           "git rev-parse HEAD", timeout=60)
            base_commit = (snap.get("stdout") or "").strip().split("\n")[-1]
            goal = (goal + f"\n\n(ПРАВКА СУЩЕСТВУЮЩЕГО кода: {preloaded} файл(ов) проекта уже лежат в "
                           "рабочем каталоге с их путями — читай и правь их, НЕ переписывай с нуля.)")
            _memory(task_id, f"Загружено {preloaded} файлов проекта для правки ({mode}).")
        elif mode == "patch":
            _memory(task_id, "Режим patch, но целевые файлы не определены — работаю как greenfield по цели.")
        # Explorer: recon over the loaded project → steer the fighter (target files,
        # invariants, existing tests) instead of grepping blindly.
        exploration = {}
        if workspace_files:
            exploration = explore(goal, workspace_files)
            brief = brief_for_fighter(exploration)
            if brief:
                goal += brief
                _memory(task_id, "Explorer наметил цели/инварианты.")
        _memory(task_id, f"Старт код-миссии. Цель: {goal[:400]}")
        if checks:
            # caller pinned exact checks → straight fighter, skip planning
            verdict = run_mission(goal, ex, checks=checks, task_id=task_id,
                                  max_fighter_rounds=int(payload.get("max_rounds") or 3),
                                  max_steps=int(payload.get("max_steps") or 40),
                                  max_wall_sec=int(payload.get("max_wall_sec") or 3600))
        else:
            # planner head decides: decompose into subtasks or run one fighter
            verdict = plan_and_run(goal, ex, task_id=task_id,
                                   max_wall_sec=int(payload.get("max_wall_sec") or 3600),
                                   memory=lambda m: _memory(task_id, m))
        verdict["files"] = _collect_files(ex, verdict.get("artifacts") or [])
        verdict["task_id"] = task_id
        # patch bundle: a reviewable unified diff of what actually changed vs the baseline,
        # plus rollback — the host applies it only after acceptance, never blindly.
        if base_commit:
            diff = ex.bash("git diff HEAD", timeout=60).get("stdout") or ""
            changed = [ln for ln in (ex.bash("git diff --name-only HEAD", timeout=30).get("stdout") or "").splitlines() if ln]
            # Reviewer: independent second head — sees only goal + diff + failures, hunts
            # regressions/weakened tests. Can veto an otherwise-"accepted" patch.
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
                _memory(task_id, "Ревьюер завернул патч: " + "; ".join(rev["issues"])[:200])
            verdict["patch_bundle"] = {
                "base_commit": base_commit,
                "changed_files": changed,
                "unified_diff": diff[:400_000],
                "rollback": "git apply -R <patch>",
                "apply_gate": "accepted" if verdict.get("accepted") else "blocked",
            }
        _memory(task_id, f"Итог: {verdict.get('status')} (accepted={verdict.get('accepted')}). "
                         f"{str(verdict.get('summary') or '')[:300]} Файлы: {verdict.get('artifacts')}")
        self._send(200, verdict)


def main():
    host = os.environ.get("SKITARII_HOST", "127.0.0.1")
    port = int(os.environ.get("SKITARII_PORT", "7200"))
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True          # a crashing request thread never takes the server down
    print(f"Skitarii brigade listening on http://{host}:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
