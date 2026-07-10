"""Bridge from Warmaster's research loop to the Skitarii warband v2.

When a Ceraxia code mission runs, instead of the dead six-worker paper pipeline it
is handed to the Skitarii HTTP service, which does the whole thing (spec -> agentic
fighter loop -> real acceptance) inside the sandbox VM and returns an honest verdict.
Skitarii already re-runs the checks itself, so no Warmaster LLM acceptance is needed.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

SKITARII_URL = os.environ.get("SKITARII_URL", "http://127.0.0.1:7200")
REPO_ROOT = Path(os.environ.get("SHUSHUNYA_REPO_ROOT", "/media/shushunya/SHUSHUNYA/shushunya"))
_MODIFY_MARKERS = ("исправ", "почин", "fix ", "измен", "рефактор", "в файле", "добавь в",
                   "поправ", "доработай", "bug", "рефактори", "оптимизир")


def _safe_repo_file(rel: str) -> Path | None:
    """Resolve rel under REPO_ROOT, refusing anything that escapes the repo (../,
    symlinks, absolute paths). Returns the real path or None."""
    rel = str(rel).lstrip("/")
    try:
        root = REPO_ROOT.resolve()
        p = (REPO_ROOT / rel).resolve()
    except OSError:
        return None
    if p == root or root not in p.parents:
        return None
    return p if p.is_file() else None


_SLICE_STOP = {"почини", "исправь", "измени", "добавь", "файле", "проект", "код", "нужно",
               "который", "которая", "please", "code", "file", "project", "function", "должна", "чтобы"}


_CODE_EXT = (".py", ".php", ".js", ".ts", ".go", ".java", ".rb", ".rs", ".c", ".h", ".cpp")


def _add_file(files: dict[str, str], root: str, p: Path, limit: int) -> bool:
    """Add p to files (rel→content) if safe/small/new. Returns True if room remains."""
    try:
        rel = str(p.resolve().relative_to(root))
    except (OSError, ValueError):
        return len(files) < limit
    _JUNK = ("site-packages/", "dist-packages/", "/venv/", "/.venv/", "node_modules/",
             "/__pycache__/", "DemonsForge/DemonsForge/", "/lib/python")
    if rel in files or _safe_repo_file(rel) is None or any(j in "/" + rel for j in _JUNK):
        return len(files) < limit
    try:
        if p.stat().st_size < 100_000:
            files[rel] = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return len(files) < limit


def _repo_slice(goal: str, max_files: int = 40) -> dict[str, str]:
    """PATCH task named no files. Build a MODULE-level slice: grep the goal's keywords
    to find target files, then pull their directory neighbours and nearby tests so the
    fighter sees real context (siblings, tests, config) — not one isolated file, but
    also not the whole monorepo. Bounded and scoped to the repo."""
    import re
    import subprocess
    words = [w for w in re.findall(r"[A-Za-zА-Яа-я_][\w-]{3,}", goal) if w.lower() not in _SLICE_STOP]
    if not words:
        return {}
    root = str(REPO_ROOT.resolve())
    files: dict[str, str] = {}
    target_dirs: set[Path] = set()
    # 1) target files by keyword
    for kw in words[:6]:
        try:
            out = subprocess.run(
                ["grep", "-rliI"] + [f"--include=*{e}" for e in _CODE_EXT] +
                ["--exclude-dir=.git", "--exclude-dir=node_modules", "--exclude-dir=runtime",
                 "--exclude-dir=models", "--exclude-dir=vm-sandbox", "--exclude-dir=__pycache__",
                 "--exclude-dir=venv", "--exclude-dir=.venv", "--exclude-dir=site-packages",
                 "--exclude-dir=lib", "--exclude-dir=dist-packages", "--exclude-dir=DemonsForge",
                 kw, root],
                capture_output=True, text=True, timeout=25).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.splitlines():
            if not line.startswith(root):
                continue
            p = Path(line)
            target_dirs.add(p.parent)
            if not _add_file(files, root, p, max_files // 2):
                break
    # 2) directory neighbours + tests around the targets (module context)
    for d in list(target_dirs)[:8]:
        try:
            for p in sorted(d.iterdir()):
                if p.is_file() and p.suffix in _CODE_EXT:
                    if not _add_file(files, root, p, max_files):
                        return files
        except OSError:
            pass
    return files


def _collect_workspace(goal: str) -> tuple[dict[str, str], bool]:
    """Load existing repo files this PATCH task references, so it edits real source
    instead of writing a blank greenfield file. Named files first, then a keyword
    slice (see _repo_slice). Traversal outside the repo is refused. Returns
    ({rel_path: content}, is_patch)."""
    import re
    files: dict[str, str] = {}
    for m in re.findall(r"[\w./-]+\.[A-Za-z0-9]{1,6}", goal):
        rel = m.lstrip("./")
        p = _safe_repo_file(rel)
        if p is None:
            continue
        try:
            if p.stat().st_size < 200_000:
                files[rel] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        if len(files) >= 30:
            break
    is_patch = bool(files) or any(k in goal.lower() for k in _MODIFY_MARKERS)
    # PATCH task with no explicitly-named files → pull a relevant slice of the repo
    if is_patch and not files:
        files = _repo_slice(goal)
    return files, is_patch


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _verify_and_stage_patch(verdict: dict[str, Any], run_dir: Path, ledger: Any) -> dict[str, Any] | None:
    """Take the accepted patch bundle and (1) save it as a .patch file, (2) check it still
    applies to the LIVE source (`git apply --check` — read-only, no mutation), (3) apply it
    in an ISOLATED throwaway git worktree and re-run the checks THERE. The live working tree
    is never touched by default (the coders are sandboxed in a VM on purpose, and this repo
    also holds other agents' WIP). Auto-apply to the live tree only if SKITARII_AUTOAPPLY=1.
    Returns a summary dict, or None if there is no patch to stage."""
    import shutil
    import subprocess
    import tempfile
    pb = verdict.get("patch_bundle") if isinstance(verdict.get("patch_bundle"), dict) else None
    diff = str((pb or {}).get("unified_diff") or "")
    if not diff.strip():
        return None
    patch_file = run_dir / "work" / "skitarii.patch"
    patch_file.parent.mkdir(parents=True, exist_ok=True)
    patch_file.write_text(diff, encoding="utf-8")

    def _run(cmd: list[str], cwd: Path | str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)

    root = REPO_ROOT
    out: dict[str, Any] = {"patch_file": str(patch_file), "applies_to_live": None,
                           "tests_pass_in_worktree": None, "applied_to_live": False,
                           "apply_cmd": f"cd {root} && git apply {patch_file}"}
    try:
        if _run(["git", "rev-parse", "--git-dir"], root, 30).returncode != 0:
            out["reason"] = "REPO_ROOT is not a git repo — patch saved but not verified"
            ledger.record_event("skitarii_patch_stage", out); return out
        # 1) does the patch still apply cleanly to the current live source? (read-only)
        chk = _run(["git", "apply", "--check", "--3way", str(patch_file)], root, 60)
        out["applies_to_live"] = (chk.returncode == 0)
        if chk.returncode != 0:
            out["apply_stderr"] = (chk.stderr or "")[:300]
        # 2) apply + re-run checks in an isolated worktree at HEAD — never the live tree
        checks = [c for c in (verdict.get("checks") or []) if isinstance(c, dict) and c.get("cmd")]
        if out["applies_to_live"] and checks:
            head = (_run(["git", "rev-parse", "HEAD"], root, 30).stdout or "").strip()
            wt = tempfile.mkdtemp(prefix="skitarii-verify-")
            try:
                if _run(["git", "worktree", "add", "--detach", wt, head or "HEAD"], root, 120).returncode == 0:
                    _run(["git", "apply", str(patch_file)], wt, 60)
                    passed = True
                    for c in checks:
                        r = _run(["bash", "-c", str(c["cmd"])], wt, 120)
                        ok = r.returncode == 0
                        if c.get("expect_stdout"):
                            ok = ok and (r.stdout or "").strip() == str(c["expect_stdout"]).strip()
                        passed = passed and ok
                    out["tests_pass_in_worktree"] = passed
            finally:
                _run(["git", "worktree", "remove", "--force", wt], root, 30)
                shutil.rmtree(wt, ignore_errors=True)
        # 3) auto-apply to the live tree ONLY behind an explicit opt-in
        if (os.environ.get("SKITARII_AUTOAPPLY") == "1" and out["applies_to_live"]
                and out.get("tests_pass_in_worktree") is not False):
            out["applied_to_live"] = _run(["git", "apply", str(patch_file)], root, 60).returncode == 0
    except Exception as exc:  # noqa: BLE001 - staging is best-effort, never fail the mission
        out["error"] = str(exc)[:200]
    ledger.record_event("skitarii_patch_stage", out)
    return out


def should_handle(run_dir: Path) -> bool:
    if os.environ.get("SKITARII_ENABLED", "1") != "1":
        return False
    ref = _read_json(run_dir / "mission_ref.json")
    governor = str(ref.get("assigned_governor") or "")
    if governor and governor != "Ceraxia":
        return False
    # a code mission has a code contract in the run dir
    contract = _read_json(run_dir / "contract.json")
    kind = str(contract.get("kind") or "")
    if governor == "Ceraxia" or "code" in kind.lower():
        return bool(str(contract.get("goal") or "").strip())
    return False


def _mission_dir(run_dir: Path) -> Path | None:
    ref = _read_json(run_dir / "mission_ref.json")
    md = str(ref.get("mission_dir") or "")
    return Path(md) if md else None


def run_via_skitarii(run_dir: Path, task_id: str, timeout_sec: int = 5400) -> dict[str, Any]:
    """Execute the code mission through Skitarii and record a terminal result."""
    from .ledger import TaskLedger  # local import to avoid cycles
    from . import mission_control as mc

    contract = _read_json(run_dir / "contract.json")
    goal = str(contract.get("goal") or "")
    ledger = TaskLedger.load(run_dir / "task_ledger.json")
    workspace, is_patch = _collect_workspace(goal)
    mode = "patch" if is_patch else "greenfield"
    # SAFETY: a patch task whose source we couldn't load must NOT silently turn into a
    # greenfield rewrite from scratch — that produces a plausible-looking but wrong
    # "fix". Block and ask the user to name the files/dir instead.
    if is_patch and not workspace:
        msg = ("Это правка существующего кода, но я не смог определить какие файлы/каталог "
               "менять. Уточни путь(и) к файлам или каталог проекта — писать с нуля я не буду.")
        ledger.record_event("skitarii_patch_no_source", {"goal": goal[:200]})
        ledger.set_result({"ok": False, "status": "blocked", "final_step": "skitarii",
                           "summary": msg, "artifacts": []})
        ledger.force_status("blocked", reason="patch task with no loadable source")
        mdir = _mission_dir(run_dir)
        if mdir and mdir.exists():
            try:
                mc.record_mission_state(mdir, "blocked")
            except Exception:
                pass
        return {"ok": False, "phase": "blocked", "task_id": task_id, "status": "blocked",
                "summary": msg, "needs_user": True}
    ledger.record_event("skitarii_dispatch", {"service": SKITARII_URL, "mode": mode,
                                              "preloaded_files": sorted(workspace.keys())})
    ledger.set_status("running")

    body = json.dumps({"goal": goal, "task_id": task_id, "max_wall_sec": timeout_sec,
                       "mode": mode, "workspace_files": workspace},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{SKITARII_URL}/mission", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec + 120) as resp:
            verdict = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        ledger.record_event("skitarii_error", {"error": str(exc)})
        ledger.set_result({"ok": False, "status": "blocked", "final_step": "skitarii",
                              "summary": f"Skitarii unreachable: {exc}", "artifacts": []})
        ledger.set_status("blocked")
        return {"ok": False, "phase": "skitarii_error", "task_id": task_id, "error": str(exc)}

    accepted = bool(verdict.get("accepted"))
    summary = str(verdict.get("summary") or "")
    artifacts = [str(a) for a in (verdict.get("artifacts") or [])]
    files = verdict.get("files") if isinstance(verdict.get("files"), dict) else {}

    # persist the deliverable files next to the run and in the mission dir
    saved: list[str] = []
    out_dir = run_dir / "work" / "code"
    out_dir.mkdir(parents=True, exist_ok=True)
    mdir = _mission_dir(run_dir)
    for path, content in files.items():
        # keep the project's directory structure — never collapse src/x.py and
        # tests/x.py to one x.py. Normalise the relative path and block traversal.
        rel = str(path).lstrip("/").replace("\\", "/")
        rel = "/".join(p for p in rel.split("/") if p not in ("", ".", ".."))
        if not rel:
            continue
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(str(content), encoding="utf-8")
        if mdir:
            mdest = mdir / "deliverables" / rel
            mdest.parent.mkdir(parents=True, exist_ok=True)
            mdest.write_text(str(content), encoding="utf-8")
        saved.append(rel)

    # accepted patch → validate it against the live source and stage it (isolated worktree
    # re-run); the live tree is not mutated unless SKITARII_AUTOAPPLY=1.
    patch_stage = _verify_and_stage_patch(verdict, run_dir, ledger) if accepted else None

    ledger.record_event("skitarii_verdict", {"accepted": accepted, "rounds": len(verdict.get("rounds") or []),
                                             "seconds": int(time.monotonic() - started), "artifacts": saved})
    status = "completed" if accepted else "blocked"
    ledger.set_result({"ok": accepted, "status": status, "final_step": "skitarii",
                          "summary": summary, "artifacts": saved})
    ledger.set_status(status)

    # finalize the mission so the user gets a clean answer (no LLM acceptance needed —
    # Skitarii already re-ran the checks for real).
    if mdir and mdir.exists():
        try:
            mission_id = str((_read_json(mdir / "mission.json")).get("mission_id") or mdir.name)
            if accepted:
                final = mc.final_response(mission_id, "completed",
                                          summary or "Готово. Код написан и проверки прошли.",
                                          artifacts=saved)
                mc._write_json(mdir / "final_response.json", final)
                mc.record_mission_state(mdir, "completed")
            else:
                mc.record_mission_state(mdir, "blocked")
            mc.append_progress_event(
                mdir / "progress_events.jsonl",
                mc.progress_event(mission_id, "Ceraxia", "governor",
                                  "completed" if accepted else "blocked",
                                  "done" if accepted else "blocked",
                                  "Варбанда Skitarii завершила код-миссию",
                                  summary[:400]),
            )
        except Exception as exc:  # noqa: BLE001 - finalization is best-effort
            ledger.record_event("skitarii_finalize_error", {"error": str(exc)})

    return {"ok": accepted, "phase": "completed" if accepted else "blocked",
            "task_id": task_id, "status": status, "summary": summary,
            "artifacts": saved, "via": "skitarii", "rounds": verdict.get("rounds") or [],
            "patch_stage": patch_stage}
