"""Skitarii warband HTTP service.

The single door the governor knocks on for a code mission. Replaces the old
six paper-workers: one POST runs the whole warband (spec -> fighter loop -> accept)
inside the sandbox VM and returns an honest verdict.

  POST /mission  {"goal": "...", "task_id": "...", "leadership_directive": {...},
                   "acceptance_source": {...}}
      -> {"status": "done|failed", "accepted": bool, "summary", "artifacts",
          "checks", "rounds":[...], "files": {path: content}}
  GET  /health

Production HTTP execution requires a valid Ceraxia leadership directive.  The
only undirected HTTP path is deliberately double-gated for evaluation/dev:
SKITARII_STANDALONE_TEST_MODE=1 on the daemon and standalone_test=true in the
individual request.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import posixpath
import re
import shlex
import sys
import threading
import time
import uuid
import weakref
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from EyeOfTerror.common_protocol.ceraxia_directive import (  # noqa: E402
    CeraxiaDirectiveError,
    leadership_context_text,
    validate_ceraxia_directive,
)
from EyeOfTerror.common_protocol.protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    review_finding,
)
from warband import run_mission  # noqa: E402
from planner import plan_and_run  # noqa: E402
from harness import (  # noqa: E402
    LLMRequestError,
    _normalize_checkpoint,
    _persist_checkpoint,
    _task_page_document,
)
from executor import (  # noqa: E402
    BOUNDARY_HELPER_SHA256, BOUNDARY_HELPER_VERSION, ProcessBoundaryBusy,
    ProcessBoundaryQuarantined, VmExecutor,
)
from explorer import explore, brief_for_fighter  # noqa: E402
from reviewer import review  # noqa: E402
from clarify import needs_clarification  # noqa: E402
from spec import _private_oracle_for_check, build_held_out_plan  # noqa: E402
from acceptor import accept  # noqa: E402
import mission_store  # noqa: E402

_PUBLIC_ACCEPT = accept

VM_KEY = os.environ.get("SKITARII_VM_KEY",
                        "/media/shushunya/SHUSHUNYA/shushunya/CoreOfMadness/vm-sandbox/skitarii_key")
VM_PORT = int(os.environ.get("SKITARII_VM_PORT", "2222"))

_PATCH_FILE = ".git/skitarii-patch.diff"
_CHANGED_FILES_FILE = ".git/skitarii-changed-files"
MAX_REQUEST_BYTES = int(os.environ.get("SKITARII_MAX_REQUEST_BYTES", "75000000"))
MAX_PATCH_BYTES = int(os.environ.get("SKITARII_MAX_PATCH_BYTES", "20000000"))
MAX_CHANGED_MANIFEST_BYTES = int(os.environ.get("SKITARII_MAX_CHANGED_MANIFEST_BYTES", "1000000"))
MAX_RETURNED_FILE_BYTES = int(os.environ.get("SKITARII_MAX_RETURNED_FILE_BYTES", "100000"))
MAX_RETURNED_TOTAL_BYTES = int(os.environ.get("SKITARII_MAX_RETURNED_TOTAL_BYTES", "1200000"))
ACCEPTANCE_SOURCE_TYPE = "commander_order_user_request"
MAX_ACCEPTANCE_SOURCE_BYTES = 131_072
_ACCEPTANCE_SOURCE_KEYS = frozenset({
    "type", "protocol_version", "mission_id", "delegating_task_id",
    "from", "to", "user_request",
})
_WORKSPACE_MODE_BATCH_BYTES = 256_000
_WORKSPACE_MODE_BATCH_ENTRIES = 4_096
BEARER_TOKEN = os.environ.get("SKITARII_BEARER_TOKEN", "")
SERVICE_STARTED_AT = int(time.time())
SERVICE_INSTANCE_ID = uuid.uuid4().hex
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ASYNC_CREATE_LOCK = threading.Lock()
_MISSION_EXECUTOR_LOCK = threading.Lock()
_EXECUTION_LOCAL = threading.local()
SERVICE_SOURCE_FILES = (
    "service.py", "spec.py", "acceptor.py", "warband.py", "planner.py",
    "executor.py", "explorer.py", "reviewer.py", "clarify.py",
    "mission_store.py", "tools.py", "harness.py",
)
SHARED_SOURCE_FILES = (
    "EyeOfTerror/common_protocol/ceraxia_directive.py",
    "EyeOfTerror/common_protocol/protocol.py",
)


def _service_source_sha256() -> str:
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for name in SERVICE_SOURCE_FILES:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update((root / name).read_bytes())
    repo_root = root.parents[2]
    for relative in SHARED_SOURCE_FILES:
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update((repo_root / relative).read_bytes())
    return digest.hexdigest()


SERVICE_SOURCE_SHA256 = _service_source_sha256()


def service_identity() -> dict:
    planner_model = os.environ.get("PLANNER_LLM_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf")
    planner_base = os.environ.get("PLANNER_LLM_BASE_URL", "http://127.0.0.1:8079/v1")
    spec_model = os.environ.get("SPEC_LLM_MODEL", "Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf")
    spec_base = os.environ.get("SPEC_LLM_BASE_URL", "http://127.0.0.1:8081/v1")
    return {
        "source_sha256": SERVICE_SOURCE_SHA256,
        "instance_id": SERVICE_INSTANCE_ID,
        "started_at": SERVICE_STARTED_AT,
        "held_out_required": os.environ.get("SKITARII_REQUIRE_HELD_OUT", "1") == "1",
        "process_boundary_required": True,
        "process_boundary_helper": BOUNDARY_HELPER_VERSION,
        "process_boundary_helper_sha256": BOUNDARY_HELPER_SHA256,
        "bearer_auth_required": bool(BEARER_TOKEN),
        "autonomous_revision": {
            "enabled": True,
            "max_attempts": mission_store.MAX_AUTO_REVISION_ATTEMPTS,
            "actionable_findings_required": True,
            "ordinary_check_failure_is_blocked": False,
        },
        "task_checkpoint_commit_retry": {
            "enabled": True,
            "max_attempts": mission_store.MAX_TASK_CHECKPOINT_COMMIT_ATTEMPTS,
            "base_backoff_seconds": mission_store.TASK_CHECKPOINT_RETRY_BASE_SECONDS,
            "max_backoff_seconds": mission_store.TASK_CHECKPOINT_RETRY_MAX_SECONDS,
            "spends_coding_attempts": False,
        },
        "execution_authorization": {
            "ceraxia_leadership_directive_required": True,
            "acceptance_source_required": True,
            "standalone_test_mode_enabled": (
                os.environ.get("SKITARII_STANDALONE_TEST_MODE", "0") == "1"
            ),
            "standalone_test_payload_flag_required": True,
        },
        "models": {
            "planner": {"model": planner_model, "base_url": planner_base},
            "reviewer": {
                "model": os.environ.get("REVIEWER_LLM_MODEL", planner_model),
                "base_url": planner_base,
            },
            "spec": {
                "model": spec_model,
                "base_url": spec_base,
            },
            "fighter": {
                "model": os.environ.get("SKITARII_LLM_MODEL", "Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf"),
                "base_url": os.environ.get("SKITARII_LLM_BASE_URL", "http://127.0.0.1:8081/v1"),
            },
            "held_out": {
                "model": os.environ.get("HELD_OUT_LLM_MODEL", spec_model),
                "base_url": os.environ.get("HELD_OUT_LLM_BASE_URL", spec_base),
            },
        },
    }


def _memory(task_id: str, note: str) -> None:
    """Best-effort note to the task's wiki memory page (also feeds Shushunya)."""
    if not _valid_task_id(task_id):
        return
    try:
        from harness import _memory_note
        _memory_note(task_id, note)
    except Exception:
        pass


def _mission_executor(task_id: str) -> VmExecutor:
    # Each RUN gets its own unique clean workdir — a random suffix so two concurrent
    # requests with the same task_id can't wipe each other's directory (race fix).
    run_suffix = uuid.uuid4().hex[:16]
    workdir = f"/home/skitarii/work/mission-{run_suffix}"
    cache_root = f"/tmp/skitarii-cache-{run_suffix}"
    command_env = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_ADDOPTS": "-p no:cacheprovider",
        "XDG_CACHE_HOME": f"{cache_root}/xdg",
        "npm_config_cache": f"{cache_root}/npm",
    }
    ex = VmExecutor(
        host="127.0.0.1", port=VM_PORT, user="skitarii", key=VM_KEY,
        workdir=workdir, process_boundary=True, command_env=command_env,
    )
    ex.initialize_process_boundary(strict=True)
    cleanup_executor = VmExecutor(
        host=ex.host, port=ex.port, user=ex.user, key=ex.key, workdir=ex.workdir,
        mission_marker=ex.mission_marker,
        command_env=ex.command_env,
        process_boundary=True, boundary_runtime_sec=ex.boundary_runtime_sec,
        boundary_process_baseline=ex.boundary_process_baseline,
        boundary_auth_state=ex.boundary_auth_state,
        boundary_lease=ex.boundary_lease,
        boundary_release_on_cleanup=True,
    )
    cleanup_state = {
        "lock": threading.Lock(), "attempted": False, "error": None,
    }
    ex._cleanup_state = cleanup_state
    cleanup_executor._cleanup_state = cleanup_state
    ex._cleanup_finalizer = weakref.finalize(ex, _cleanup_workspace_processes, cleanup_executor)
    return ex


def _stop_workspace_processes(ex: VmExecutor, *, strict: bool = False) -> bool:
    """Reap mission descendants and prove none remain before freezing a candidate."""
    if getattr(ex, "process_boundary", False):
        stop_boundary = getattr(ex, "stop_process_boundary", None)
        if not callable(stop_boundary):
            if strict:
                raise RuntimeError("mission process boundary is unavailable")
            return False
        try:
            return bool(stop_boundary(strict=strict))
        except Exception as exc:
            if strict:
                raise RuntimeError(f"could not stop mission cgroup processes: {exc}") from exc
            return False
    if not getattr(ex, "mission_marker", ""):
        close = getattr(ex, "close", None)
        if callable(close):
            close()
        return True
    marker = shlex.quote(f"SKITARII_MISSION_MARKER={getattr(ex, 'mission_marker', '')}")
    command = (
        f"root=$(pwd -P); marker={marker}; me=$$; parent=$PPID; "
        "is_tagged() { proc=/proc/$1; cwd=$(readlink \"$proc/cwd\" 2>/dev/null || true); "
        "case \"$cwd\" in \"$root\"|\"$root\"/*|\"$root (deleted)\"|\"${root}_wt_\"*) return 0;; esac; "
        "tr '\\0' '\\n' < \"$proc/environ\" 2>/dev/null | grep -Fqx -- \"$marker\"; }; "
        "for proc in /proc/[0-9]*; do pid=${proc##*/}; "
        "[ \"$pid\" = \"$me\" ] && continue; [ \"$pid\" = \"$parent\" ] && continue; "
        "is_tagged \"$pid\" && kill -TERM \"$pid\" 2>/dev/null || true; done; "
        "sleep 0.2; "
        "for proc in /proc/[0-9]*; do pid=${proc##*/}; "
        "[ \"$pid\" = \"$me\" ] && continue; [ \"$pid\" = \"$parent\" ] && continue; "
        "is_tagged \"$pid\" && kill -KILL \"$pid\" 2>/dev/null || true; done; "
        "sleep 0.1; remaining=0; for proc in /proc/[0-9]*; do pid=${proc##*/}; "
        "[ \"$pid\" = \"$me\" ] && continue; [ \"$pid\" = \"$parent\" ] && continue; "
        "is_tagged \"$pid\" && remaining=1 || true; done; [ \"$remaining\" = 0 ]"
    )
    try:
        result = ex.bash(command, timeout=30)
        ok = result.get("returncode") == 0
    except Exception as exc:
        if strict:
            raise RuntimeError(f"could not stop mission processes: {exc}") from exc
        return False
    if strict and not ok:
        detail = (result.get("stderr") or result.get("stdout") or "cleanup failed").strip()
        raise RuntimeError(f"mission processes survived cleanup: {detail[-500:]}")
    return ok


def _remove_workspace(ex: VmExecutor) -> None:
    trusted_remove = getattr(ex, "remove_boundary_storage", None)
    if getattr(ex, "process_boundary", False) and callable(trusted_remove):
        trusted_remove(strict=True)
        return
    cache_paths = sorted({
        str(value) for key, value in getattr(ex, "command_env", {}).items()
        if key in {"XDG_CACHE_HOME", "npm_config_cache"}
        and str(value).startswith("/tmp/skitarii-cache-")
    })
    remove_caches = " ".join(shlex.quote(path) for path in cache_paths)
    root_path = Path(str(ex.workdir))
    owned_children: list[str] = []
    for raw_child in getattr(ex, "_owned_child_workdirs", set()):
        child_path = Path(raw_child)
        if (
            child_path.parent != root_path.parent
            or not re.fullmatch(r"mission-[0-9a-f]{16}", child_path.name)
        ):
            raise RuntimeError(f"unsafe owned verifier workdir: {child_path}")
        owned_children.append(shlex.quote(str(child_path)))
    remove_children = " ".join(sorted(owned_children))
    command = (
        "root=$(pwd -P); base=${root%/*}; cd \"$base\" || exit 0; rm -rf -- \"$root\""
        + (f" {remove_children}" if remove_children else "")
        + "; "
        + (f"rm -rf -- {remove_caches}" if remove_caches else ":")
    )
    result = ex.bash(command, timeout=30)
    if result.get("returncode") != 0:
        detail = (result.get("stderr") or result.get("stdout") or "workspace cleanup failed").strip()
        raise RuntimeError(detail[-500:])


def _cleanup_workspace_processes(ex: VmExecutor) -> None:
    """Strictly prove cleanup before releasing the global sandbox lifecycle lock."""
    state = getattr(ex, "_cleanup_state", None)
    if not isinstance(state, dict):
        state = {"lock": threading.Lock(), "attempted": False, "error": None}
        ex._cleanup_state = state
    with state["lock"]:
        if state["attempted"]:
            if state["error"] is not None:
                raise RuntimeError(str(state["error"]))
            return
        state["attempted"] = True
    cleaned = False
    try:
        try:
            _stop_workspace_processes(ex, strict=True)
            _remove_workspace(ex)
            _stop_workspace_processes(ex, strict=True)
            cleaned = True
        finally:
            if cleaned:
                release = getattr(ex, "release_process_boundary", None)
                if callable(release):
                    release(strict=True)
            else:
                quarantine = getattr(ex, "quarantine_process_boundary", None)
                if callable(quarantine):
                    quarantine()
    except BaseException as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
        raise
    else:
        state["error"] = None
    finally:
        finalizer = getattr(ex, "_cleanup_finalizer", None)
        if finalizer is not None and finalizer.alive:
            finalizer.detach()


def _raw_index_population(*, write_objects: bool, info_only: bool = False) -> str:
    """Populate the selected Git index from literal filesystem bytes, without attrs."""
    hash_flags = "-w " if write_objects else ""
    update_flags = "--info-only " if info_only else ""
    return (
        "all=\"$index.skitarii-all\"; paths=\"$index.skitarii-paths\"; links=\"$index.skitarii-links\"; "
        "hashes=\"$index.skitarii-hashes\"; "
        "rm -f -- \"$all\" \"$paths\" \"$links\" \"$hashes\"; "
        "/usr/bin/find . -path ./.git -prune -o -mindepth 1 -print0 > \"$all\"; "
        ": > \"$paths\"; : > \"$links\"; "
        "while IFS= read -r -d '' entry; do "
        "case \"$entry\" in *$'\\n'*) exit 65;; esac; "
        "if [ -L \"$entry\" ]; then printf '%s\\n' \"$entry\" >> \"$links\"; "
        "elif [ -f \"$entry\" ]; then printf '%s\\n' \"$entry\" >> \"$paths\"; "
        "elif [ -d \"$entry\" ]; then continue; else exit 65; fi; "
        "done < \"$all\"; "
        f"/usr/bin/git hash-object --no-filters {hash_flags}--stdin-paths < \"$paths\" > \"$hashes\"; "
        "{ exec 3< \"$hashes\"; "
        "while IFS= read -r entry; do IFS= read -r oid <&3 || exit 65; "
        "case ${#oid} in 40|64) :;; *) exit 65;; esac; "
        "case \"$oid\" in *[!0-9a-f]*) exit 65;; esac; "
        "if [ -x \"$entry\" ]; then mode=100755; else mode=100644; fi; "
        "path=${entry#./}; printf '%s %s\\t%s\\0' \"$mode\" \"$oid\" \"$path\"; "
        "done < \"$paths\"; if IFS= read -r extra <&3; then exit 65; fi; exec 3<&-; "
        "while IFS= read -r entry; do path=${entry#./}; "
        f"oid=$(/usr/bin/readlink -n -- \"$entry\" | /usr/bin/git hash-object --no-filters {hash_flags}--stdin); "
        "case ${#oid} in 40|64) :;; *) exit 65;; esac; "
        "case \"$oid\" in *[!0-9a-f]*) exit 65;; esac; "
        "printf '120000 %s\\t%s\\0' \"$oid\" \"$path\"; done < \"$links\"; "
        f"}} | /usr/bin/git update-index {update_flags}-z --index-info; "
        "rm -f -- \"$all\" \"$paths\" \"$links\" \"$hashes\"; "
    )


def _workspace_fingerprint(ex: VmExecutor) -> str:
    # Git's index format gives a canonical NUL-safe path/mode/blob manifest.  The
    # blobs are hashed from literal bytes and are deliberately not written into the
    # candidate-controlled object database.
    _sanitize_git_control(ex, preserve_patch=True)
    result = _checked_bash(
        ex,
        _TRUSTED_GIT_ENV
        + "set -e -o pipefail; index=$(pwd -P)/.git/skitarii-fingerprint-index; "
        "rm -f -- \"$index\" \"$index.lock\"; export GIT_INDEX_FILE=\"$index\"; "
        "trap 'rm -f -- \"$index\" \"$index.lock\" \"$index.skitarii-all\" \"$index.skitarii-paths\" "
        "\"$index.skitarii-links\" \"$index.skitarii-hashes\"' EXIT; "
        "/usr/bin/git read-tree --empty; "
        + _raw_index_population(write_objects=False, info_only=True)
        + "/usr/bin/git ls-files --stage -z | /usr/bin/sha256sum",
        timeout=120,
    )
    value = (result.get("stdout") or "").strip().split()
    if not value:
        raise RuntimeError("workspace fingerprint is empty")
    return value[0]


def _copy_candidate_for_verification(ex: VmExecutor, base_commit: str) -> VmExecutor:
    child = ex.child("verifier")
    destination = shlex.quote(str(child.workdir))
    patch_path = shlex.quote(posixpath.join(ex.workdir, _PATCH_FILE))
    base_q = shlex.quote(str(base_commit))
    materialize = (
        f"dest={destination}; base={base_q}; "
        "tree_list=$(pwd -P)/.git/skitarii-baseline-tree; rm -f -- \"$tree_list\"; "
        "/usr/bin/git ls-tree -rz --full-tree \"$base\" > \"$tree_list\"; "
        "while IFS= read -r -d '' entry; do "
        "meta=${entry%%$'\\t'*}; path=${entry#*$'\\t'}; "
        "mode=${meta%% *}; rest=${meta#* }; kind=${rest%% *}; oid=${rest##* }; "
        "case \"$path\" in /*|../*|*/../*|.git|.git/*|*/.git|*/.git/*) exit 65;; esac; "
        "out=\"$dest/$path\"; /usr/bin/mkdir -p -- \"${out%/*}\"; "
        "case \"$mode:$kind\" in "
        "100644:blob) /usr/bin/git cat-file blob \"$oid\" > \"$out\"; /usr/bin/chmod 0644 \"$out\";; "
        "100755:blob) /usr/bin/git cat-file blob \"$oid\" > \"$out\"; /usr/bin/chmod 0755 \"$out\";; "
        "120000:blob) /usr/bin/git cat-file blob \"$oid\" | "
        "/usr/bin/python3 -c 'import os,sys; os.symlink(os.fsdecode(sys.stdin.buffer.read()), sys.argv[1])' "
        "\"$out\";; *) exit 65;; esac; "
        "done < \"$tree_list\"; rm -f -- \"$tree_list\""
    )
    _checked_bash(
        ex,
        "set -e -o pipefail; " + _TRUSTED_GIT_ENV
        + materialize + " && "
        + f"/usr/bin/git -C {destination} init -q && "
        # An empty patch is a legitimate no-op: the candidate tree is byte-identical
        # to the reconstructed baseline (e.g. the goal was already satisfied by base).
        # `git apply` rejects a zero-hunk patch as "No valid patches in input", which
        # would crash trusted verification and be mislabelled a verifier-internal
        # failure. Skip the apply when the patch is empty so the reconstruction is the
        # clean base tree and the behavioural checks still judge it honestly. This is
        # not gameable: the reconstruction is base, never the candidate's own tree, so
        # a candidate that changed nothing is judged against base exactly as it stands.
        + f"if [ -s {patch_path} ]; then "
        + f"/usr/bin/git -C {destination} apply --binary --whitespace=nowarn {patch_path}; fi",
        timeout=180,
    )
    _sanitize_git_control(child)
    return child


def _scrub_interstage_temp(ex: VmExecutor) -> None:
    scrub = getattr(ex, "scrub_boundary_temp", None)
    if getattr(ex, "process_boundary", False):
        if not callable(scrub):
            raise RuntimeError("trusted inter-stage temp scrub is unavailable")
        scrub(strict=True)


def _held_out_failure_class(acceptance: dict) -> str:
    """Distinguish candidate outcomes from failures of trusted verification.

    Exit codes belong to the candidate command and are not verifier provenance:
    a candidate may legitimately time out, be non-executable, miss a dependency,
    or return any byte-sized status.  Trusted failures are identified by the
    phase that produced the result.  Unknown/malformed evidence stays fail-closed.
    """
    if not isinstance(acceptance, dict):
        return "verifier_protocol"
    results = acceptance.get("results")
    if not isinstance(results, list) or not results:
        return "verifier_protocol"
    failed = False
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            return "verifier_protocol"
        if result["ok"]:
            continue
        failed = True
        why = str(result.get("why") or "")
        if why.startswith("oracle failed"):
            return "verifier_internal"
        kind = result.get("kind")
        if kind == "check":
            # The command ran in the candidate snapshot.  Its exit status is a
            # repairable behavioural outcome, including 124/125/126/127/255.
            continue
        if kind == "file_bytes":
            if why in {
                "atomic regular-file reader unavailable",
                "atomic reader violated its byte contract",
            } or why.startswith("atomic frozen artifact read failed:"):
                return "verifier_internal"
            # Missing, non-regular, symlinked, or differing candidate output is
            # an ordinary candidate failure produced by the trusted reader.
            continue
        # Private acceptance currently emits only check/file_bytes records.
        # Treat new or malformed result kinds as verifier protocol failures.
        return "verifier_protocol"
    return "candidate_failure" if failed else "verifier_protocol"


def _verifier_internal_finding(
    failure_class: str,
    detail: str,
    *,
    entity_id: str,
) -> dict:
    protocol_failure = failure_class == "verifier_protocol"
    return review_finding(
        "verifier_protocol_failure" if protocol_failure else "verifier_internal_failure",
        (
            "The verifier returned malformed or incomplete acceptance evidence."
            if protocol_failure else
            "A trusted verifier component failed before it could judge the candidate."
        ),
        detail[:500] or failure_class,
        "A complete structured acceptance result produced in an isolated replay.",
        (
            "Repair the verifier protocol or trusted oracle, then rerun the unchanged candidate; "
            "do not treat this internal failure as a candidate defect."
        ),
        "infrastructure",
        True,
        entity_kind="verification_runtime",
        entity_id=entity_id,
    )


def _verifier_failure_detail(acceptance: dict, fallback: str) -> str:
    if isinstance(acceptance, dict):
        for result in acceptance.get("results") or []:
            if not isinstance(result, dict) or result.get("ok") is True:
                continue
            detail = str(result.get("why") or result.get("stderr") or "").strip()
            if detail:
                return detail[:500]
        reason = str(acceptance.get("reason") or "").strip()
        if reason:
            return reason[:500]
    return fallback[:500]


class _ReplayIntegrityError(RuntimeError):
    """A replay proved byte-identity loss, rather than merely failing internally."""


def _replay_fingerprint(ex: VmExecutor, label: str) -> str:
    try:
        return _workspace_fingerprint(ex)
    except Exception as exc:
        raise _ReplayIntegrityError(
            f"{label} fingerprint could not be proven: {type(exc).__name__}: {str(exc)[:400]}"
        ) from exc


def _held_out_evidence_violation(checks: list[dict]) -> str:
    """Require private evidence that cannot be reduced to candidate-controlled exit 0."""
    invalid: list[str] = []
    for index, check in enumerate(checks):
        if (
            isinstance(check, dict)
            and check.get("kind") == "file_bytes"
            and isinstance(check.get("path"), str)
            and bool(check["path"].strip())
            and isinstance(check.get("expect_bytes"), str)
        ):
            continue
        if not isinstance(check, dict) or not str(check.get("cmd") or "").strip():
            invalid.append(str(index + 1))
            continue
        literal = (
            "expect_stdout" in check
            and isinstance(check.get("expect_stdout"), (str, int, float))
            and bool(str(check.get("expect_stdout")).strip())
        )
        oracle = isinstance(check.get("oracle"), str) and bool(check["oracle"].strip())
        if not (literal or oracle):
            invalid.append(str(index + 1))
    if not checks:
        return "private verifier produced no checks"
    if invalid:
        return "private checks without immutable output evidence: " + ", ".join(invalid[:20])
    return ""


def _held_out_plan_failure(plan: dict, evidence_violation: str) -> tuple[str, str]:
    """Preserve generator provenance instead of relabelling zero checks as evidence."""
    status = str(plan.get("status") or "invalid_spec")
    if status != "ok":
        return status, str(plan.get("error") or "private verifier generator failed")
    if evidence_violation:
        return "invalid_evidence", str(evidence_violation)
    return "", ""


def _held_out_plan_findings(plan: dict, status: str, error: str) -> list[dict]:
    findings = plan.get("findings") if isinstance(plan, dict) else None
    if isinstance(findings, list):
        usable = [dict(item) for item in findings if isinstance(item, dict)]
        if usable:
            return usable[:20]
    return [review_finding(
        f"held_out_{status or 'invalid'}",
        "Private verification could not produce a safe behavioural check plan.",
        error or "No accepted private check was available.",
        "At least one task-linked private check with immutable output evidence.",
        "Repair or retry the private verifier generator; meanwhile require an independent public behavioural replay.",
        "infrastructure",
        True,
        entity_kind="verification_plan",
        entity_id="held-out-plan",
    )]


def _acceptance_findings(
    acceptance: dict,
    *,
    hidden: bool,
    owner: str = "fighter",
) -> list[dict]:
    """Turn executable failures into repair instructions without leaking hidden oracles."""

    findings: list[dict] = []
    for index, result in enumerate(acceptance.get("results") or [], 1):
        if not isinstance(result, dict) or result.get("ok"):
            continue
        why = str(result.get("why") or result.get("stderr") or "check failed")[:500]
        if hidden:
            evidence = (
                "The undisclosed behavioural check exited non-zero."
                if result.get("exit") not in {None, 0}
                else "The candidate output did not match the undisclosed behavioural oracle."
            )
            remediation = (
                "Re-check the requested behaviour and edge cases for the task-named deliverable; "
                "do not rely only on the visible examples."
            )
            entity_id = f"held-out-{index}"
        else:
            target = str(result.get("target") or f"public-check-{index}")[:300]
            evidence = f"{target}: {why}"
            remediation = "Fix the reported behaviour, preserve already passing checks, and rerun the full public acceptance set."
            entity_id = f"public-{index}"
        findings.append(review_finding(
            "hidden_candidate_failure" if hidden else "public_candidate_failure",
            "The candidate failed an executable behavioural acceptance check.",
            evidence,
            "Every independent behavioural check passes in a fresh reconstructed workspace.",
            remediation,
            owner,
            True,
            entity_kind="behavioural_check",
            entity_id=entity_id,
        ))
    if not findings and not acceptance.get("accepted"):
        findings.append(review_finding(
            "acceptance_rejected_without_result",
            "Acceptance rejected the candidate without a per-check result.",
            str(acceptance.get("reason") or "No executable result was recorded.")[:500],
            "A non-empty behavioural acceptance set with explicit results.",
            "Repair the acceptance specification and rerun it before applying the patch.",
            "infrastructure",
            True,
            entity_kind="acceptance",
            entity_id="acceptance",
        ))
    return findings


def _run_hidden_revision_round(
    ex: VmExecutor,
    *,
    goal: str,
    public_checks: list[dict],
    held_out_checks: list[dict],
    base_commit: str,
    task_id: str,
    task_memory_id: str,
    root_task_id: str,
    parent_task_id: str,
    ask_fn: object,
    cancel_fn: object,
    max_steps: int,
    max_wall_sec: int,
) -> tuple[dict, dict]:
    """Give a hidden candidate failure one sanitized repair round and recheck it."""

    feedback = (
        "\n\nINDEPENDENT REVISION FEEDBACK: an undisclosed behavioural check found "
        "a defect. Re-read the requested behaviour and task-named deliverables, cover "
        "edge cases beyond the visible examples, preserve every passing public check, "
        "and rerun the complete public acceptance set. The hidden command and oracle "
        "are intentionally not disclosed."
    )
    try:
        revised = run_mission(
            goal + feedback,
            ex,
            checks=public_checks,
            task_id=task_id,
            memory_task_id=task_memory_id,
            ask_fn=ask_fn,
            cancel_fn=cancel_fn,
            max_fighter_rounds=1,
            max_steps=max_steps,
            max_wall_sec=max_wall_sec,
        )
    except Exception as exc:  # preserve any transient model/runtime failure
        revised = _recoverable_pipeline_verdict(
            exc,
            ex=ex,
            base_commit=base_commit,
            task_id=task_id,
            task_memory_id=task_memory_id,
            root_task_id=root_task_id,
            parent_task_id=parent_task_id,
        )
    revised["hidden_revision_attempted"] = True
    revised["held_out_required"] = True
    revised["held_out_check_count"] = len(held_out_checks)
    try:
        _stop_workspace_processes(ex, strict=True)
    except RuntimeError as exc:
        revised.update({
            "accepted": False,
            "status": "blocked",
            "summary": (
                "Verification stopped safely after revision because fighter process "
                f"cleanup was not proven: {exc}"
            ),
            "held_out_status": "not_run_revision_freeze_failed",
            "verification_findings": [review_finding(
                "revision_freeze_failure",
                "The revised candidate could not be frozen safely for verification.",
                str(exc),
                "All fighter descendants are stopped before snapshot replay.",
                "Repair process-boundary cleanup and rerun the revision from a clean workspace.",
                "infrastructure",
                True,
                entity_kind="verification_runtime",
                entity_id="revision-freeze",
            )],
        })
        return revised, {
            "base_commit": base_commit,
            "changed_files": [],
            "unified_diff": "",
            "apply_gate": "blocked",
        }

    revised["files"] = _collect_files(ex, revised.get("artifacts") or [])
    try:
        patch_bundle = _build_patch_bundle(ex, base_commit, accepted=False)
    except (OSError, RuntimeError, ValueError) as exc:
        revised.update({
            "accepted": False,
            "status": "failed",
            "summary": f"Revision patch could not be reproduced: {exc}",
            "held_out_status": "not_run_revision_patch_failed",
            "verification_findings": [review_finding(
                "revision_patch_not_reproducible",
                "The revised workspace could not be represented as a complete patch.",
                str(exc),
                "A complete patch against the immutable baseline.",
                "Rebuild the candidate from the baseline and capture every changed path.",
                "fighter",
                True,
                entity_kind="patch",
                entity_id="revision-patch",
            )],
            "revision_required": True,
        })
        return revised, {
            "base_commit": base_commit,
            "changed_files": [],
            "unified_diff": "",
            "apply_gate": "blocked",
        }

    runner_violation = _runner_control_violation(
        list(patch_bundle.get("changed_files") or [])
    )
    symlink_violation = _workspace_symlink_violation(ex)
    if runner_violation or symlink_violation:
        reason = runner_violation or symlink_violation
        revised.update({
            "accepted": False,
            "status": "failed",
            "summary": f"Unsafe revised candidate rejected: {reason}",
            "held_out_status": "not_run_revision_safety_violation",
            "verification_findings": [review_finding(
                "revision_safety_violation",
                "The revised candidate crossed a protected verification boundary.",
                str(reason),
                "Candidate changes remain inside task deliverables and never control the verifier.",
                "Remove the runner-control or escaping-symlink change and rebuild the patch.",
                "fighter",
                True,
                entity_kind="patch",
                entity_id="revision-safety",
            )],
            "revision_required": True,
        })
        patch_bundle["apply_gate"] = "blocked"
        return revised, patch_bundle

    if not revised.get("accepted"):
        patch_bundle["apply_gate"] = "blocked"
        revised.setdefault("revision_required", True)
        return revised, patch_bundle

    public_child = None
    held_out_child = None
    primary_before = ""
    primary_after = ""
    public_before = ""
    public_after = ""
    held_out_before = ""
    held_out_after = ""
    public_acceptance: dict = {
        "accepted": False, "results": [], "reason": "public replay did not run",
    }
    hidden_acceptance: dict = {
        "accepted": False, "results": [], "reason": "private replay did not run",
    }
    verifier_error = ""
    verifier_failure_class = "verifier_internal"
    integrity_error = ""
    cleanup_error = ""
    cleanup_phase = False
    try:
        _scrub_interstage_temp(ex)
        primary_before = _replay_fingerprint(ex, "revision primary before replay")
        public_child = _copy_candidate_for_verification(ex, base_commit)
        public_before = _replay_fingerprint(public_child, "revision public reconstruction")
        if primary_before != public_before:
            raise _ReplayIntegrityError(
                "revised patch reconstruction changed candidate bytes"
            )
        deliverables, replay_checks = _public_replay_inputs(revised)
        public_acceptance = _PUBLIC_ACCEPT(public_child, deliverables, replay_checks)
        _stop_workspace_processes(public_child, strict=True)
        _scrub_runtime_debris(public_child)
        public_after = _replay_fingerprint(public_child, "revision public after replay")
        primary_after_public = _replay_fingerprint(ex, "revision primary after public replay")
        if public_before != public_after or primary_before != primary_after_public:
            raise _ReplayIntegrityError(
                "revised public replay mutated a frozen snapshot"
            )
        completed_public_child = public_child
        cleanup_phase = True
        _cleanup_workspace_processes(completed_public_child)
        public_child = None
        cleanup_phase = False
        if public_acceptance.get("accepted"):
            _scrub_interstage_temp(ex)
            held_out_child = _copy_candidate_for_verification(ex, base_commit)
            held_out_before = _replay_fingerprint(
                held_out_child, "revision private reconstruction",
            )
            if primary_before != held_out_before:
                raise _ReplayIntegrityError(
                    "revised private reconstruction changed candidate bytes"
                )
            hidden_acceptance = accept(held_out_child, [], held_out_checks)
            runtime_violation = _held_out_runtime_evidence_violation(
                held_out_checks, hidden_acceptance,
            )
            if runtime_violation:
                raise ValueError(runtime_violation)
            _stop_workspace_processes(held_out_child, strict=True)
            _scrub_runtime_debris(held_out_child)
            held_out_after = _replay_fingerprint(
                held_out_child, "revision private after replay",
            )
            primary_after = _replay_fingerprint(ex, "revision primary after private replay")
        else:
            primary_after = primary_after_public
    except _ReplayIntegrityError as exc:
        integrity_error = f"{type(exc).__name__}: {str(exc)[:500]}"
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:500]}"
        if cleanup_phase:
            cleanup_error = detail
        else:
            verifier_error = detail
            if isinstance(exc, (AttributeError, KeyError, TypeError, ValueError)):
                verifier_failure_class = "verifier_protocol"
    finally:
        for label, child in (("public revision replay", public_child), ("private revision replay", held_out_child)):
            if child is None:
                continue
            try:
                _cleanup_workspace_processes(child)
            except Exception as cleanup_exc:
                detail = f"{label} cleanup failed: {type(cleanup_exc).__name__}: {cleanup_exc}"
                cleanup_error = (
                    f"{cleanup_error}; {detail}" if cleanup_error else detail
                )[:1000]

    if verifier_error and not cleanup_error and not integrity_error and primary_before:
        try:
            primary_after = _replay_fingerprint(
                ex, "revision primary after verifier error",
            )
        except _ReplayIntegrityError as audit_exc:
            integrity_error = str(audit_exc)[:1000]

    mutated = bool(
        primary_before and primary_after and primary_before != primary_after
        or public_before and public_after and public_before != public_after
        or held_out_before and held_out_after and held_out_before != held_out_after
    )
    if mutated and not integrity_error:
        integrity_error = "revision replay mutated the frozen snapshot"
    revised["public_replay_acceptance"] = public_acceptance
    revised["held_out_acceptance"] = hidden_acceptance
    if cleanup_error or integrity_error:
        revised.update({
            "accepted": False,
            "status": "blocked",
            "held_out_status": "verifier_infra",
            "held_out_failure_class": "verifier_infra",
            "held_out_error": cleanup_error or integrity_error,
        })
        revised["verification_findings"] = [review_finding(
            "revision_verifier_infrastructure",
            "The revised candidate could not be verified in an isolated reproducible replay.",
            revised["held_out_error"],
            "Both replay workspaces remain byte-identical and cleanup is proven.",
            "Repair verifier isolation/cleanup, then resume the same revision.",
            "infrastructure",
            True,
            entity_kind="verification_runtime",
            entity_id="revision-replay",
        )]
        revised["summary"] = (
            "Verification stopped safely after revision: isolation or cleanup was not proven. "
            + revised["verification_findings"][0]["remediation"]
        )
    elif verifier_error:
        revised.update({
            "accepted": False,
            "status": "failed",
            "held_out_status": verifier_failure_class,
            "held_out_failure_class": verifier_failure_class,
            "held_out_error": verifier_error,
            "revision_required": True,
        })
        revised["verification_findings"] = [_verifier_internal_finding(
            verifier_failure_class,
            verifier_error,
            entity_id="revision-replay",
        )]
        revised["summary"] = (
            "The revised candidate was not judged because verification failed internally, "
            "but snapshot cleanup and primary byte identity were proven. "
            + revised["verification_findings"][0]["remediation"]
        )
    elif not public_acceptance.get("accepted"):
        revised.update({
            "accepted": False,
            "status": "failed",
            "held_out_status": "reconstructed_public_failure",
            "held_out_failure_class": "candidate_failure",
            "revision_required": True,
        })
        revised["verification_findings"] = _acceptance_findings(
            public_acceptance, hidden=False,
        )
        revised["summary"] = (
            "Revision still fails reconstructed public acceptance. "
            + revised["verification_findings"][0]["remediation"]
        )
    elif not hidden_acceptance.get("accepted"):
        failure_class = _held_out_failure_class(hidden_acceptance)
        revised.update({
            "accepted": False,
            "status": "failed",
            "held_out_status": failure_class,
            "held_out_failure_class": failure_class,
            "revision_required": True,
        })
        revised["verification_findings"] = (
            _acceptance_findings(hidden_acceptance, hidden=True)
            if failure_class == "candidate_failure" else
            [_verifier_internal_finding(
                failure_class,
                _verifier_failure_detail(hidden_acceptance, failure_class),
                entity_id="revision-private",
            )]
        )
        revised["summary"] = (
            (
                "The first automatic hidden-check revision was not enough. "
                "Ceraxia must choose another repair approach using this diagnosis: "
            )
            if failure_class == "candidate_failure" else
            "The revised candidate was not judged because a trusted verifier component failed. "
        ) + revised["verification_findings"][0]["remediation"]
    else:
        revised.update({
            "status": "done",
            "accepted": True,
            "held_out_status": "passed",
            "held_out_failure_class": "",
            "verification_findings": [],
            "checks": public_checks + held_out_checks,
            "summary": (
                str(revised.get("summary") or "Revision completed.")
                + " The automatic revision passed public and undisclosed behavioural replay."
            ).strip(),
        })
    patch_bundle["apply_gate"] = "accepted" if revised.get("accepted") else "blocked"
    revised["patch_bundle"] = patch_bundle
    return revised, patch_bundle


def _isolate_private_oracles(
    checks: list[dict], authoritative_goal: str,
    *,
    primary_authority_goals: tuple[str, ...] = (),
) -> list[dict]:
    """Canonicalize validated oracle code into isolated stdlib-only Python."""
    isolated: list[dict] = []
    for check in checks:
        copied = dict(check) if isinstance(check, dict) else check
        if isinstance(copied, dict) and "oracle" in copied:
            raw = str(copied.get("oracle") or "").strip()
            try:
                tokens = shlex.split(raw, posix=True)
            except ValueError as exc:
                raise ValueError("private oracle is outside the trusted positive grammar") from exc
            if (
                len(tokens) == 5
                and tokens[0] == "/usr/bin/python3"
                and tokens[1:4] == ["-I", "-S", "-c"]
            ):
                validation_form = f"python3 -c {shlex.quote(tokens[4])}"
                code = tokens[4]
            else:
                validation_form = raw
                code = tokens[2] if len(tokens) == 3 else ""
            if not _private_oracle_for_check(
                validation_form,
                str(copied.get("cmd") or ""),
                authoritative_goal,
                precedence_goals=primary_authority_goals,
            ):
                raise ValueError("private oracle is outside the trusted positive grammar")
            copied["oracle"] = f"/usr/bin/python3 -I -S -c {shlex.quote(code)}"
        isolated.append(copied)
    return isolated


def _held_out_runtime_evidence_violation(checks: list[dict], acceptance: dict) -> str:
    """An oracle comparison must produce non-empty values on both independent sides."""
    results = acceptance.get("results") if isinstance(acceptance, dict) else None
    if not isinstance(results, list):
        return "private verifier returned no structured evidence"
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or "oracle" not in check:
            continue
        if index >= len(results) or not isinstance(results[index], dict):
            return f"private oracle check {index + 1} returned no evidence"
        result = results[index]
        if not str(result.get("stdout") or "").strip() or not str(
            result.get("expected") or ""
        ).strip():
            return f"private oracle check {index + 1} produced empty comparable evidence"
    return ""


def _public_replay_inputs(verdict: dict) -> tuple[list[str], list[dict]]:
    """Recover the exact checks and deliverables that produced public acceptance."""
    checks = [
        dict(check)
        for check in (verdict.get("checks") or [])
        if isinstance(check, dict) and str(check.get("cmd") or "").strip()
    ]
    acceptance = None
    for round_state in reversed(verdict.get("rounds") or []):
        if isinstance(round_state, dict) and isinstance(round_state.get("acceptance"), dict):
            acceptance = round_state["acceptance"]
            break
    if acceptance is None and isinstance(verdict.get("acceptance"), dict):
        acceptance = verdict["acceptance"]
    deliverables: list[str] = []
    for result in (acceptance or {}).get("results") or []:
        if not isinstance(result, dict) or result.get("kind") != "deliverable":
            continue
        path = _safe_workspace_path(result.get("target"))
        if path not in deliverables:
            deliverables.append(path)
    return deliverables, checks


def _collect_files(ex: VmExecutor, artifacts: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    total = 0
    for path in artifacts[:12]:
        try:
            safe = _safe_workspace_path(path)
            sized = ex.bash(f"wc -c < {shlex.quote(safe)}", timeout=20)
            size = int((sized.get("stdout") or "").strip())
            if size > MAX_RETURNED_FILE_BYTES or total + size > MAX_RETURNED_TOTAL_BYTES:
                continue
            raw = ex.fetch_artifact(safe, max_bytes=MAX_RETURNED_FILE_BYTES)
            if len(raw) > MAX_RETURNED_FILE_BYTES:
                continue
            content = raw.decode("utf-8", errors="strict")
            out[safe] = content
            total += size
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
                safe = _safe_workspace_path(path)
                sized = ex.bash(f"wc -c < {shlex.quote(safe)}", timeout=20)
                size = int((sized.get("stdout") or "").strip())
                if size > MAX_RETURNED_FILE_BYTES or total + size > MAX_RETURNED_TOTAL_BYTES:
                    continue
                raw = ex.fetch_artifact(safe, max_bytes=MAX_RETURNED_FILE_BYTES)
                if len(raw) > MAX_RETURNED_FILE_BYTES:
                    continue
                out[safe] = raw.decode("utf-8", errors="strict")
                total += size
            except Exception:
                pass
    return out


def _safe_workspace_path(raw: object) -> str:
    """Return a safe, repository-relative POSIX path or fail closed."""
    if not isinstance(raw, str):
        raise ValueError("workspace path must be a string")
    value = raw.replace("\\", "/")
    if not value or "\x00" in value:
        raise ValueError("workspace path is empty or contains NUL")
    path = PurePosixPath(value)
    parts = path.parts
    if path.is_absolute() or not parts or any(part == ".." for part in parts):
        raise ValueError(f"workspace path escapes the repository: {value!r}")
    if parts[0].endswith(":") or ".git" in parts:
        raise ValueError(f"workspace path is reserved: {value!r}")
    normalized = path.as_posix()
    if normalized in ("", "."):
        raise ValueError("workspace path must name a file")
    return normalized


def _safe_symlink_target(link_path: str, raw_target: object) -> str:
    target = str(raw_target).replace("\\", "/")
    if not target or "\x00" in target or posixpath.isabs(target):
        raise ValueError(f"unsafe symlink target for {link_path!r}: {target!r}")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(link_path), target))
    if resolved == ".." or resolved.startswith("../") or posixpath.isabs(resolved):
        raise ValueError(f"symlink target escapes the repository: {link_path!r} -> {target!r}")
    if PurePosixPath(resolved).parts and ".git" in PurePosixPath(resolved).parts:
        raise ValueError(f"symlink target reaches reserved git metadata: {link_path!r} -> {target!r}")
    return target


def _checked_bash(ex: VmExecutor, command: str, *, timeout: int = 30) -> dict:
    result = ex.bash(command, timeout=timeout)
    if result.get("returncode") != 0:
        detail = (result.get("stderr") or result.get("stdout") or "command failed").strip()
        raise RuntimeError(detail[-2000:])
    return result


_TRUSTED_GIT_ENV = (
    "export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_ATTR_NOSYSTEM=1 "
    "GIT_NO_REPLACE_OBJECTS=1 GIT_PAGER=cat PAGER=cat; "
    "unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY "
    "GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_CONFIG_COUNT "
    "GIT_CONFIG_PARAMETERS GIT_EXTERNAL_DIFF; "
)


def _sanitize_git_control(ex: VmExecutor, *, preserve_patch: bool = False) -> None:
    """Remove candidate-controlled Git execution/config channels before trusted Git."""
    patch_cleanup = "" if preserve_patch else ".git/skitarii-patch.diff "
    command = _TRUSTED_GIT_ENV + (
        "set -eu; test -d .git && test ! -L .git; "
        "test ! -e .git/commondir && test ! -L .git/commondir; "
        "test -z \"$(/usr/bin/find . -mindepth 1 -path ./.git -prune -o "
        "-name .git -print -quit)\"; "
        "for d in .git/objects .git/objects/info .git/info .git/refs; do "
        "[ ! -e \"$d\" ] && [ ! -L \"$d\" ] || { test -d \"$d\" && test ! -L \"$d\"; }; done; "
        "for f in .git/config .git/config.worktree .git/info/attributes .git/info/exclude "
        ".git/info/grafts .git/objects/info/alternates; do "
        "[ ! -e \"$f\" ] && [ ! -L \"$f\" ] || { test -f \"$f\" && test ! -L \"$f\"; }; done; "
        "test -z \"$(/usr/bin/find .git -mindepth 1 -type l -print -quit)\"; "
        "test -z \"$(/usr/bin/find .git -mindepth 1 ! -type d ! -type f -print -quit)\"; "
        "rm -rf -- .git/hooks .git/refs/replace; mkdir -p -- .git/hooks .git/info .git/objects/info; "
        "rm -f -- .git/config .git/config.worktree .git/info/attributes .git/info/exclude .git/info/grafts "
        ".git/objects/info/alternates .git/skitarii-index "
        + patch_cleanup
        +
        ".git/skitarii-fingerprint-index .git/skitarii-fingerprint-index.lock "
        ".git/skitarii-baseline-tree "
        ".git/index.skitarii-all .git/skitarii-index.skitarii-all "
        ".git/skitarii-fingerprint-index.skitarii-all "
        ".git/index.skitarii-paths .git/index.skitarii-links .git/index.skitarii-hashes "
        ".git/skitarii-index.skitarii-paths .git/skitarii-index.skitarii-links "
        ".git/skitarii-index.skitarii-hashes .git/skitarii-fingerprint-index.skitarii-paths "
        ".git/skitarii-fingerprint-index.skitarii-links .git/skitarii-fingerprint-index.skitarii-hashes "
        ".git/skitarii-changed-files .git/skitarii-symlink-violations .git/skitarii-symlinks "
        ".git/skitarii-symlink-scan; "
        "printf '%s\\n' '[core]' 'repositoryformatversion = 0' 'filemode = true' "
        "'bare = false' 'logallrefupdates = true' > .git/config; chmod 0600 .git/config; "
        "test \"$(/usr/bin/git rev-parse --git-dir)\" = .git; "
        "test \"$(/usr/bin/git rev-parse --git-common-dir)\" = .git"
    )
    _checked_bash(ex, command, timeout=30)


def _scrub_runtime_debris(ex: VmExecutor) -> None:
    # Interpreter caches PLUS standard build/dependency trees. The fighter
    # legitimately downloads its own tooling into the isolated sandbox (gradle
    # distributions, node_modules, …), but none of that may ride into the patch
    # bundle: a greenfield Android project otherwise drags >20MB of .gradle
    # caches into the diff and trips MAX_PATCH_BYTES. Deliverable is source.
    command = (
        "/usr/bin/find . -path ./.git -prune -o -type f "
        "\\( -name '*.pyc' -o -name '*.pyo' \\) -exec /usr/bin/rm -f -- {} +; "
        "/usr/bin/find . -path ./.git -prune -o -type d "
        "\\( -name __pycache__ -o -name .pytest_cache -o -name .gradle "
        "-o -name build -o -name .cxx -o -name node_modules "
        "-o -name .venv -o -name venv -o -name dist -o -name target "
        "-o -name .m2 -o -name .cache \\) -prune "
        "-exec /usr/bin/rm -rf -- {} +"
    )
    _checked_bash(ex, command, timeout=60)


def _workspace_symlink_violation(ex: VmExecutor) -> str:
    """Reject links that resolve outside the frozen repository or into .git."""
    report = ".git/skitarii-symlink-violations"
    inventory = ".git/skitarii-symlinks"
    scan = ".git/skitarii-symlink-scan"
    command = (
        f"set -e; rm -f -- {report} {inventory} {scan}; : > {report}; : > {inventory}; "
        "root=$(/usr/bin/realpath -e .); "
        f"/usr/bin/find . -mindepth 1 -path ./.git -prune -o -type l -print0 > {scan}; "
        "while IFS= read -r -d '' link; do "
        "target=$(/usr/bin/readlink -- \"$link\" 2>/dev/null || true); "
        f"printf '%s\\0%s\\0' \"$link\" \"$target\" >> {inventory}; "
        "resolved=$(/usr/bin/realpath -m -- \"$link\" 2>/dev/null || true); "
        "case \"$resolved\" in \"$root/.git\"|\"$root/.git/\"*|'') "
        f"printf '%s\\0' \"$link\" >> {report};; "
        f"\"$root\"/*) :;; *) printf '%s\\0' \"$link\" >> {report};; esac; "
        f"done < {scan}"
    )
    _checked_bash(ex, command, timeout=30)
    raw = ex.fetch_artifact(report, max_bytes=MAX_CHANGED_MANIFEST_BYTES)
    if len(raw) > MAX_CHANGED_MANIFEST_BYTES:
        raise ValueError("symlink policy report exceeds manifest limit")
    paths = [
        _safe_workspace_path(part.decode("utf-8"))
        for part in raw.split(b"\0") if part
    ]
    inventory_raw = ex.fetch_artifact(inventory, max_bytes=MAX_CHANGED_MANIFEST_BYTES)
    if len(inventory_raw) > MAX_CHANGED_MANIFEST_BYTES:
        raise ValueError("symlink inventory exceeds manifest limit")
    parts = inventory_raw.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) % 2:
        raise ValueError("symlink inventory is malformed")
    for index in range(0, len(parts), 2):
        path = _safe_workspace_path(parts[index].decode("utf-8"))
        target = parts[index + 1].decode("utf-8")
        try:
            _safe_symlink_target(path, target)
        except ValueError:
            paths.append(path)
    return ", ".join(sorted(set(paths))[:20])


def _prepare_workspace(ex: VmExecutor, files: dict, blobs: dict, deleted: list,
                       modes: dict, symlinks: dict) -> int:
    """Materialise the exact caller snapshot before creating the baseline commit."""
    prepared = 0
    for raw_path, content in files.items():
        path = _safe_workspace_path(raw_path)
        ex.write_file(path, str(content))
        prepared += 1

    for raw_path, encoded in blobs.items():
        path = _safe_workspace_path(raw_path)
        try:
            content = base64.b64decode(str(encoded), validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ValueError(f"invalid base64 workspace blob: {path}") from exc
        ex.write_bytes(path, content)
        prepared += 1

    for raw_path in deleted:
        path = _safe_workspace_path(raw_path)
        _checked_bash(ex, f"rm -rf -- {shlex.quote(path)}")

    for raw_path, raw_target in symlinks.items():
        path = _safe_workspace_path(raw_path)
        target = _safe_symlink_target(path, raw_target)
        parent = posixpath.dirname(path) or "."
        _checked_bash(
            ex,
            f"mkdir -p -- {shlex.quote(parent)} && "
            f"rm -rf -- {shlex.quote(path)} && "
            f"ln -s -- {shlex.quote(target)} {shlex.quote(path)}",
        )
        prepared += 1

    _apply_workspace_modes(ex, modes)
    return prepared


def _apply_workspace_modes(ex: VmExecutor, modes: dict) -> None:
    """Apply caller modes in a few bounded process-boundary invocations.

    Paths are validated before any chmod happens.  A NUL-delimited manifest keeps
    arbitrary safe filenames out of the shell program and lets one bounded VM
    command replace hundreds of systemd/SSH round trips.
    """
    entries: list[tuple[str, str, bytes]] = []
    for raw_path, raw_mode in modes.items():
        path = _safe_workspace_path(raw_path)
        mode = str(raw_mode)
        if mode not in {"100755", "100644", "120000"}:
            raise ValueError(f"unsupported git mode for {path!r}: {mode!r}")
        record = mode.encode("ascii") + b"\0" + path.encode("utf-8") + b"\0"
        if len(record) > _WORKSPACE_MODE_BATCH_BYTES:
            raise ValueError(f"workspace mode record is too large for {path!r}")
        entries.append((mode, path, record))

    batch: list[bytes] = []
    batch_bytes = 0
    for _mode, _path, record in entries:
        if batch and (
            len(batch) >= _WORKSPACE_MODE_BATCH_ENTRIES
            or batch_bytes + len(record) > _WORKSPACE_MODE_BATCH_BYTES
        ):
            _apply_workspace_mode_batch(ex, batch)
            batch = []
            batch_bytes = 0
        batch.append(record)
        batch_bytes += len(record)
    if batch:
        _apply_workspace_mode_batch(ex, batch)


def _apply_workspace_mode_batch(ex: VmExecutor, records: list[bytes]) -> None:
    manifest = f".skitarii-workspace-modes-{uuid.uuid4().hex}"
    ex.write_bytes(manifest, b"".join(records))
    command = (
        "set -euo pipefail; "
        f"manifest={shlex.quote(manifest)}; expected={len(records)}; seen=0; "
        "cleanup() { /usr/bin/rm -f -- \"$manifest\"; }; "
        "trap cleanup EXIT; "
        "no_symlink_parents() { "
        "candidate=$1; parent=${candidate%/*}; "
        "test \"$parent\" != \"$candidate\" || return 0; "
        "while :; do "
        "test ! -L \"$parent\" || return 1; "
        "case \"$parent\" in */*) parent=${parent%/*};; *) break;; esac; "
        "done; }; "
        "while IFS= read -r -d '' mode && IFS= read -r -d '' path; do "
        "no_symlink_parents \"$path\" || exit 65; "
        "case \"$mode\" in "
        "100755) if test ! -f \"$path\" || test -L \"$path\"; then exit 65; fi; "
        "/usr/bin/chmod a+x -- \"$path\" ;; "
        "100644) if test ! -f \"$path\" || test -L \"$path\"; then exit 65; fi; "
        "/usr/bin/chmod a-x -- \"$path\" ;; "
        "120000) if test ! -L \"$path\"; then exit 65; fi ;; "
        "*) exit 64 ;; esac; "
        "seen=$((seen + 1)); "
        "done < \"$manifest\"; test \"$seen\" -eq \"$expected\""
    )
    _checked_bash(ex, command, timeout=60)


def _create_baseline(ex: VmExecutor) -> str:
    """Create a synthetic commit from exact caller bytes, ignoring Git attributes."""
    _checked_bash(ex, _TRUSTED_GIT_ENV + "/usr/bin/git init -q .", timeout=30)
    _sanitize_git_control(ex)
    result = _checked_bash(
        ex,
        _TRUSTED_GIT_ENV
        + "set -e -o pipefail; index=$(pwd -P)/.git/index; "
        "rm -f -- \"$index\" \"$index.lock\"; export GIT_INDEX_FILE=\"$index\"; "
        "/usr/bin/git read-tree --empty; "
        + _raw_index_population(write_objects=True)
        +
        "/usr/bin/git -c user.email=b@x -c user.name=skitarii "
        "commit --allow-empty -qm baseline && /usr/bin/git rev-parse HEAD",
        timeout=120,
    )
    base = (result.get("stdout") or "").strip().splitlines()
    if not base:
        raise RuntimeError("baseline commit did not return a commit id")
    return base[-1]


def _build_patch_bundle(ex: VmExecutor, base_commit: str, *, accepted: bool) -> dict:
    """Stage the final VM tree and diff it against the original caller snapshot.

    Comparing the staged tree with ``base_commit`` is intentional: fighter branches
    may already have been merged into HEAD, and a worktree-only ``git diff HEAD``
    would silently discard those changes.  ``git add -A`` also captures new and
    deleted paths, while ``--binary`` makes the returned patch applyable to binary
    files.  Diff output is written under .git before fetching because executor
    stdout is deliberately capped.
    """
    base = str(base_commit).strip()
    if not base:
        raise ValueError("base commit is missing")
    commit_expr = shlex.quote(f"{base}^{{commit}}")
    base_q = shlex.quote(base)
    clean_pathspec = "."
    _scrub_runtime_debris(ex)
    _sanitize_git_control(ex)
    command = _TRUSTED_GIT_ENV + (
        "set -e -o pipefail; index=$(pwd -P)/.git/skitarii-index; "
        "rm -f -- \"$index\" \"$index.lock\"; export GIT_INDEX_FILE=\"$index\"; "
        f"/usr/bin/git rev-parse --verify {commit_expr} >/dev/null && "
        "/usr/bin/git read-tree --empty && "
        + _raw_index_population(write_objects=True)
        +
        f"/usr/bin/git diff --cached --no-ext-diff --no-textconv --binary --full-index "
        f"{base_q} -- {clean_pathspec} > {_PATCH_FILE} && "
        f"/usr/bin/git diff --cached --no-ext-diff --no-textconv --name-only -z "
        f"{base_q} -- {clean_pathspec} > {_CHANGED_FILES_FILE}"
    )
    _checked_bash(ex, command, timeout=120)
    patch_size = int((_checked_bash(ex, f"wc -c < {_PATCH_FILE}").get("stdout") or "0").strip())
    manifest_size = int((_checked_bash(ex, f"wc -c < {_CHANGED_FILES_FILE}").get("stdout") or "0").strip())
    if patch_size > MAX_PATCH_BYTES:
        raise ValueError(f"complete patch exceeds {MAX_PATCH_BYTES} bytes")
    if manifest_size > MAX_CHANGED_MANIFEST_BYTES:
        raise ValueError(f"changed-file manifest exceeds {MAX_CHANGED_MANIFEST_BYTES} bytes")
    diff_raw = ex.fetch_artifact(_PATCH_FILE, max_bytes=MAX_PATCH_BYTES)
    names_raw = ex.fetch_artifact(_CHANGED_FILES_FILE, max_bytes=MAX_CHANGED_MANIFEST_BYTES)
    if len(diff_raw) > MAX_PATCH_BYTES or len(names_raw) > MAX_CHANGED_MANIFEST_BYTES:
        raise ValueError("patch output grew while it was being collected")
    diff = diff_raw.decode("utf-8", errors="strict")
    names = names_raw.decode("utf-8", errors="strict")
    changed = [path for path in names.split("\x00") if path]
    return {
        "base_commit": base,
        "changed_files": changed,
        "unified_diff": diff,
        "rollback": "git apply -R <patch>",
        "apply_gate": "accepted" if accepted else "blocked",
    }


def _baseline_tree(ex: VmExecutor, base_commit: str) -> str:
    result = _checked_bash(
        ex,
        _TRUSTED_GIT_ENV
        + f"/usr/bin/git rev-parse {shlex.quote(str(base_commit) + '^{tree}')}",
        timeout=30,
    )
    return str(result.get("stdout") or "").strip().splitlines()[-1]


def _capture_workspace_checkpoint(
    ex: VmExecutor,
    base_commit: str,
    *,
    task_memory_id: str = "",
    root_task_id: str = "",
    parent_task_id: str = "",
) -> dict:
    """Capture unaccepted work before the isolated VM workspace is destroyed."""
    bundle = _build_patch_bundle(ex, base_commit, accepted=False)
    diff = str(bundle.get("unified_diff") or "")
    return {
        "schema_version": 1,
        "base_tree": _baseline_tree(ex, base_commit),
        "patch_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "unified_diff": diff,
        "changed_files": list(bundle.get("changed_files") or []),
        "task_memory_id": task_memory_id,
        "root_task_id": root_task_id,
        "parent_task_id": parent_task_id,
    }


def _mission_workspace_checkpoint(mission: Any) -> dict:
    if mission is None or getattr(mission, "_lock", None) is None:
        return {}
    with mission._lock:
        result = dict(mission.result) if isinstance(mission.result, dict) else {}
    checkpoint = result.get("workspace_checkpoint")
    return dict(checkpoint) if isinstance(checkpoint, dict) else {}


def _mission_result_snapshot(mission: Any) -> dict[str, Any]:
    if mission is None or getattr(mission, "_lock", None) is None:
        return {}
    with mission._lock:
        return dict(mission.result) if isinstance(mission.result, dict) else {}


def _parent_skitarii_mission_id_from_payload(payload: dict) -> str:
    raw = payload.get("parent_skitarii_mission_id")
    if raw is None or raw == "":
        return ""
    if not isinstance(raw, str) or not mission_store.valid_mission_id(raw):
        raise ValueError("parent_skitarii_mission_id is invalid")
    return raw


def _workspace_checkpoint_for_attempt(
    payload: dict,
    mission: Any,
    task_memory_id: str,
    root_task_id: str,
) -> dict:
    """Prefer this mission's checkpoint; inherit only from the same task page."""
    current = _mission_workspace_checkpoint(mission)
    if current:
        return current
    parent_id = _parent_skitarii_mission_id_from_payload(payload)
    if not parent_id or not task_memory_id or not root_task_id:
        return {}
    parent = mission_store.get(parent_id)
    if parent is None or getattr(parent, "_lock", None) is None:
        return {}
    with parent._lock:
        parent_result = dict(parent.result) if isinstance(parent.result, dict) else {}
    if str(parent_result.get("task_memory_id") or "").strip() != task_memory_id:
        return {}
    if str(parent_result.get("root_task_id") or "").strip() != root_task_id:
        return {}
    checkpoint = parent_result.get("workspace_checkpoint")
    return dict(checkpoint) if isinstance(checkpoint, dict) else {}


def _pending_task_checkpoint_for_attempt(
    payload: dict,
    mission: Any,
    task_memory_id: str,
    root_task_id: str,
    parent_task_id: str = "",
) -> dict[str, Any]:
    """Find a prior memory-only finalization without treating another task as parent."""
    candidates = [_mission_result_snapshot(mission)]
    parent_id = _parent_skitarii_mission_id_from_payload(payload)
    if parent_id:
        candidates.append(_mission_result_snapshot(mission_store.get(parent_id)))
    for result in candidates:
        if result.get("error_code") != "task_checkpoint_commit_pending":
            continue
        if str(result.get("task_memory_id") or "") != task_memory_id:
            continue
        if str(result.get("root_task_id") or "") != root_task_id:
            continue
        if str(result.get("parent_task_id") or "") != parent_task_id:
            continue
        checkpoint = result.get("pending_task_checkpoint")
        key = str(result.get("pending_task_checkpoint_key") or "")
        original = result.get("checkpoint_pending_original")
        if isinstance(checkpoint, dict) and key and isinstance(original, dict):
            return result
    return {}


def _resume_pending_task_checkpoint(
    pending: dict[str, Any],
    *,
    task_id: str,
    task_memory_id: str,
    root_task_id: str,
    parent_task_id: str = "",
) -> dict[str, Any]:
    """Finish only the durable page commit; the verified candidate is not recoded."""
    result = dict(pending)
    for field in (
        "error", "restart_recovery_required", "revision_exhausted",
        "revision_attempts", "task_checkpoint_retry_exhausted",
        "task_checkpoint_retry_after_seconds", "task_checkpoint_retry_at",
    ):
        result.pop(field, None)
    result["revision_required"] = False
    checkpoint = dict(result["pending_task_checkpoint"])
    checkpoint_key = str(result["pending_task_checkpoint_key"])
    try:
        _persist_checkpoint(
            task_memory_id,
            checkpoint,
            authoritative=True,
            idempotency_key=checkpoint_key,
        )
    except Exception as exc:
        result["task_id"] = task_id
        result["task_checkpoint_error"] = f"{type(exc).__name__}: {exc}"[:500]
        try:
            previous_attempts = int(result.get("task_checkpoint_commit_attempts") or 1)
        except (TypeError, ValueError):
            previous_attempts = 1
        result["task_checkpoint_commit_attempts"] = max(1, previous_attempts) + 1
        return result
    original = dict(result.get("checkpoint_pending_original") or {})
    for field, value in original.items():
        if value is None:
            result.pop(field, None)
        else:
            result[field] = value
    for field in (
        "pending_task_checkpoint", "pending_task_checkpoint_key",
        "checkpoint_pending_original", "task_checkpoint_error",
        "error", "restart_recovery_required",
        "task_checkpoint_commit_attempts", "task_checkpoint_retry_after_seconds",
        "task_checkpoint_retry_at", "task_checkpoint_retry_exhausted",
        "revision_exhausted", "revision_attempts",
    ):
        result.pop(field, None)
    result["task_id"] = task_id
    result["task_memory_id"] = task_memory_id
    result["root_task_id"] = root_task_id
    result["parent_task_id"] = parent_task_id
    result["task_checkpoint_recovered"] = True
    return result


def _restore_workspace_checkpoint(
    ex: VmExecutor,
    base_commit: str,
    checkpoint: dict,
    *,
    task_memory_id: str = "",
    root_task_id: str = "",
    parent_task_id: str = "",
) -> bool:
    """Replay only a controller-produced patch onto the identical caller snapshot."""
    if not checkpoint:
        return False
    diff = checkpoint.get("unified_diff")
    expected_sha = str(checkpoint.get("patch_sha256") or "")
    expected_tree = str(checkpoint.get("base_tree") or "")
    if task_memory_id and str(checkpoint.get("task_memory_id") or "") != task_memory_id:
        raise ValueError("workspace checkpoint task memory identity mismatch")
    if root_task_id and str(checkpoint.get("root_task_id") or "") != root_task_id:
        raise ValueError("workspace checkpoint root task identity mismatch")
    if parent_task_id and str(checkpoint.get("parent_task_id") or "") != parent_task_id:
        raise ValueError("workspace checkpoint parent task identity mismatch")
    if not isinstance(diff, str) or len(diff.encode("utf-8")) > MAX_PATCH_BYTES:
        raise ValueError("workspace checkpoint patch is missing or oversized")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("workspace checkpoint hash is invalid")
    if hashlib.sha256(diff.encode("utf-8")).hexdigest() != expected_sha:
        raise ValueError("workspace checkpoint hash mismatch")
    if not re.fullmatch(r"[0-9a-f]{40,64}", expected_tree):
        raise ValueError("workspace checkpoint base tree is invalid")
    if _baseline_tree(ex, base_commit) != expected_tree:
        raise ValueError("workspace checkpoint belongs to a different baseline")
    if not diff:
        return False
    ex.write_file(_PATCH_FILE, diff)
    try:
        _sanitize_git_control(ex, preserve_patch=True)
        _checked_bash(
            ex,
            _TRUSTED_GIT_ENV
            + f"set -eu; /usr/bin/git apply --check --binary {_PATCH_FILE}; "
            + f"/usr/bin/git apply --binary {_PATCH_FILE}",
            timeout=120,
        )
    finally:
        try:
            _checked_bash(ex, f"rm -f -- {_PATCH_FILE}", timeout=20)
        except Exception:
            pass
    return True


def _recoverable_pipeline_verdict(
    exc: Exception,
    *,
    ex: VmExecutor,
    base_commit: str,
    task_id: str,
    task_memory_id: str,
    root_task_id: str,
    parent_task_id: str = "",
) -> dict[str, Any]:
    """Turn an internal crash into evidence for an autonomous next attempt."""
    is_context_overflow = bool(
        isinstance(exc, LLMRequestError) and exc.context_overflow
    )
    error_code = (
        "context_overflow"
        if is_context_overflow
        else str(getattr(exc, "code", "") or "internal_pipeline_error")
    )
    retryable = True if is_context_overflow else bool(getattr(exc, "retryable", True))
    detail = str(exc)[:2_000]
    checkpoint: dict[str, Any] = {}
    checkpoint_error = ""
    try:
        checkpoint = _capture_workspace_checkpoint(
            ex,
            base_commit,
            task_memory_id=task_memory_id,
            root_task_id=root_task_id,
            parent_task_id=parent_task_id,
        )
    except Exception as capture_exc:  # noqa: BLE001 - preserve the original failure
        checkpoint_error = f"{type(capture_exc).__name__}: {capture_exc}"[:1_000]
    remediation = (
        "Restore the saved workspace patch, compact the fighter context into the task page, and retry the interrupted step."
        if error_code == "context_overflow"
        else "Restore the saved workspace patch, repair the internal execution path, and resume with a materially different step."
    )
    evidence = detail + (f"; checkpoint error: {checkpoint_error}" if checkpoint_error else "")
    finding = review_finding(
        error_code,
        "The coding pipeline stopped internally before it could produce a trustworthy verdict.",
        evidence[:2_000],
        "Internal failures preserve work and automatically continue from a durable checkpoint.",
        remediation,
        "infrastructure",
        retryable,
        entity_kind="fighter_runtime",
        entity_id="execution-loop",
    )
    return {
        "status": "failed",
        "accepted": False,
        "task_id": task_id,
        "task_memory_id": task_memory_id,
        "root_task_id": root_task_id,
        "parent_task_id": parent_task_id,
        "summary": f"Internal repair required: {detail}",
        "error": f"{type(exc).__name__}: {detail}"[:2_000],
        "error_code": error_code,
        "failure_class": "infrastructure",
        "retryable": retryable,
        "revision_required": retryable,
        "verification_findings": [finding] if retryable else [],
        "workspace_checkpoint": checkpoint,
        "checkpoint_error": checkpoint_error,
        "files": {},
    }


def _service_verdict_checkpoint(verdict: dict[str, Any]) -> dict[str, Any]:
    findings = [
        item for item in (verdict.get("verification_findings") or [])
        if isinstance(item, dict)
    ][:12]
    patch_bundle = verdict.get("patch_bundle")
    workspace_checkpoint = verdict.get("workspace_checkpoint")
    if isinstance(patch_bundle, dict):
        changed = list(patch_bundle.get("changed_files") or [])
    elif isinstance(workspace_checkpoint, dict):
        changed = list(workspace_checkpoint.get("changed_files") or [])
    else:
        changed = []
    checks: list[str] = []
    for round_item in (verdict.get("rounds") or [])[-3:]:
        acceptance = round_item.get("acceptance") if isinstance(round_item, dict) else None
        if not isinstance(acceptance, dict):
            continue
        for result in (acceptance.get("results") or [])[-6:]:
            if isinstance(result, dict):
                checks.append(
                    f"{result.get('target') or 'check'}: "
                    f"{'passed' if result.get('ok') else 'failed'}"
                )
    status = str(verdict.get("status") or "failed")
    accepted = verdict.get("accepted") is True
    return _normalize_checkpoint({
        "current_state": f"Skitarii service verdict: {status}; accepted={accepted}.",
        "decisions": [
            f"held_out_status={verdict.get('held_out_status')}"
        ] if verdict.get("held_out_status") else [],
        "completed_work": [str(verdict.get("summary") or "")]
        if accepted and verdict.get("summary") else [],
        "failed_approaches": [
            str(item.get("evidence") or item.get("what_failed") or "")
            for item in findings
        ],
        "working_set": [str(path) for path in changed],
        "checks": checks,
        "next_actions": [
            str(item.get("remediation") or "") for item in findings
            if item.get("retryable") is True
        ],
    }, f"Skitarii service verdict: {status}")


def _finalize_service_verdict(
    verdict: dict[str, Any],
    *,
    ex: VmExecutor,
    base_commit: str,
    task_id: str,
    task_memory_id: str,
    root_task_id: str,
    parent_task_id: str = "",
    capture_workspace: bool = True,
) -> dict[str, Any]:
    """Bind lineage, preserve retry work, then publish one leader checkpoint."""
    verdict["task_id"] = task_id
    verdict["task_memory_id"] = task_memory_id
    verdict["root_task_id"] = root_task_id
    verdict["parent_task_id"] = parent_task_id
    retryable = bool(
        verdict.get("accepted") is False
        and verdict.get("revision_required") is True
        and verdict.get("retryable") is not False
    )
    if retryable and capture_workspace:
        current = verdict.get("workspace_checkpoint")
        if isinstance(current, dict) and current:
            current = dict(current)
            current["task_memory_id"] = task_memory_id
            current["root_task_id"] = root_task_id
            current["parent_task_id"] = parent_task_id
            verdict["workspace_checkpoint"] = current
        else:
            try:
                captured = _capture_workspace_checkpoint(
                    ex,
                    base_commit,
                    task_memory_id=task_memory_id,
                    root_task_id=root_task_id,
                    parent_task_id=parent_task_id,
                )
                captured["task_memory_id"] = task_memory_id
                captured["root_task_id"] = root_task_id
                captured["parent_task_id"] = parent_task_id
                verdict["workspace_checkpoint"] = captured
            except Exception as exc:  # preserve the verdict even if capture fails
                verdict["checkpoint_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )[:1_000]
    if task_memory_id:
        service_checkpoint = _service_verdict_checkpoint(verdict)
        checkpoint_rendered = json.dumps(
            service_checkpoint,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        checkpoint_key = "skitarii-service-" + hashlib.sha256(
            (task_id + "\0" + checkpoint_rendered).encode("utf-8")
        ).hexdigest()
        try:
            _persist_checkpoint(
                task_memory_id,
                service_checkpoint,
                authoritative=True,
                idempotency_key=checkpoint_key,
            )
        except Exception as exc:
            checkpoint_error = f"{type(exc).__name__}: {exc}"[:500]
            verdict["task_checkpoint_error"] = checkpoint_error
            if verdict.get("accepted") is not True:
                return verdict
            original = {
                field: verdict.get(field)
                for field in (
                    "status", "accepted", "summary", "revision_required",
                    "retryable", "error_code", "failure_class",
                    "verification_findings",
                )
            }
            if capture_workspace:
                try:
                    captured = _capture_workspace_checkpoint(
                        ex,
                        base_commit,
                        task_memory_id=task_memory_id,
                        root_task_id=root_task_id,
                        parent_task_id=parent_task_id,
                    )
                    captured["task_memory_id"] = task_memory_id
                    captured["root_task_id"] = root_task_id
                    captured["parent_task_id"] = parent_task_id
                    verdict["workspace_checkpoint"] = captured
                except Exception as capture_exc:
                    verdict["checkpoint_error"] = (
                        f"{type(capture_exc).__name__}: {capture_exc}"
                    )[:1_000]
            pending_finding = review_finding(
                "task_checkpoint_commit_pending",
                "The verified service verdict could not be committed to the canonical task page.",
                checkpoint_error,
                "The canonical task page contains the consolidated leader verdict before the mission is published as accepted.",
                "Retry only the idempotent task-page checkpoint commit; do not recode the verified candidate.",
                "infrastructure",
                True,
                entity_kind="task_memory",
                entity_id=task_memory_id,
            )
            verdict.update({
                "status": "failed",
                "accepted": False,
                "summary": "Candidate verification completed, but task-memory finalization is pending.",
                "error_code": "task_checkpoint_commit_pending",
                "failure_class": "infrastructure",
                "retryable": True,
                "revision_required": False,
                "verification_findings": [pending_finding],
                "task_checkpoint_error": checkpoint_error,
                "pending_task_checkpoint": service_checkpoint,
                "pending_task_checkpoint_key": checkpoint_key,
                "checkpoint_pending_original": original,
                "task_checkpoint_commit_attempts": 1,
            })
    return verdict


def _is_test_path(path: str) -> bool:
    parts = path.lower().split("/")
    name = parts[-1] if parts else ""
    return (
        name.startswith("test")
        or name.endswith(("_test.py", ".spec.js", ".spec.ts", ".test.js", ".test.ts"))
        or any(part in ("test", "tests", "spec", "specs") for part in parts[:-1])
    )


def _allows_test_changes(goal: str) -> bool:
    lowered = goal.lower()
    markers = (
        "fix test", "update test", "change test", "add test", "write test",
        "почини тест", "исправь тест", "обнови тест", "добавь тест", "напиши тест",
    )
    return any(marker in lowered for marker in markers)


def _protected_path_violation(
    goal: str, baseline_paths: Any, changed_files: list[str], explicit: list,
) -> str:
    protected = {_safe_workspace_path(path) for path in explicit}
    if not _allows_test_changes(goal):
        protected.update(
            _safe_workspace_path(path) for path in baseline_paths if _is_test_path(str(path))
        )
    touched = {_safe_workspace_path(path) for path in changed_files}
    violations = sorted(protected & touched)
    return ", ".join(violations[:20])


def _runner_control_violation(changed_files: list[str]) -> str:
    """Forbid candidate-controlled Python/test runner hooks for every goal."""
    forbidden_configs = {
        "pytest.ini", ".pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini",
    }
    forbidden_modules = {
        "sitecustomize", "usercustomize", "conftest", "pytest", "_pytest",
        "unittest", "runpy",
    }
    violations: list[str] = []
    for raw in changed_files:
        path = _safe_workspace_path(str(raw))
        parts = [part.lower() for part in path.split("/")]
        name = parts[-1]
        stem = name.rsplit(".", 1)[0]
        if (
            name in forbidden_configs
            or name.endswith((".pyc", ".pyo"))
            or "__pycache__" in parts
            or stem in forbidden_modules
            or any(part in forbidden_modules for part in parts[:-1])
        ):
            violations.append(path)
    return ", ".join(sorted(set(violations))[:20])


def _is_health_path(path: str) -> bool:
    return path.split("?", 1)[0] == "/health"


def _valid_task_id(value: object) -> bool:
    return bool(_TASK_ID_RE.fullmatch(str(value or "")))


def _task_memory_id_from_payload(payload: dict) -> str:
    """Resolve only an explicit stable identity, never an execution-attempt id."""
    return str(
        payload.get("task_memory_id")
        or payload.get("root_task_id")
        or ""
    ).strip()


def _root_task_id_from_payload(payload: dict, task_memory_id: str) -> str:
    """Resolve stable lineage without ever falling back to the attempt id."""
    return str(payload.get("root_task_id") or task_memory_id or "").strip()


def _parent_task_id_from_payload(payload: dict) -> str:
    """Resolve the explicit immediate task ancestor, never a mission/run id."""
    return str(payload.get("parent_task_id") or "").strip()


def _delegation_lineage_error(
    payload: dict,
    task_id: str,
    root_task_id: str,
    parent_task_id: str,
) -> tuple[int, dict] | None:
    """Require the exact immutable run lineage emitted by Abaddon.

    A valid Ceraxia directive proves who made the decision, but by itself does
    not prove which durable Abaddon run/page it belongs to.  Keep that binding
    explicit at the service door so a direct ``/mission(s)`` caller cannot
    manufacture an orphan continuation around Abaddon's lineage checks.
    """
    delegating_task_id = str(payload.get("delegating_task_id") or "").strip()
    if not _valid_task_id(task_id):
        return 400, {
            "error": "task_id is invalid",
            "error_code": "invalid_task_id",
        }
    if not _valid_task_id(delegating_task_id):
        return 400, {
            "error": "delegating_task_id is required and must be valid",
            "error_code": "invalid_delegating_task_id",
        }
    if parent_task_id and parent_task_id == delegating_task_id:
        return 400, {
            "error": "parent_task_id must differ from delegating_task_id",
            "error_code": "parent_task_id_self_reference",
        }
    if delegating_task_id == root_task_id:
        if parent_task_id:
            return 400, {
                "error": "a root task delegation cannot have parent_task_id",
                "error_code": "root_task_parent_forbidden",
            }
    elif not parent_task_id:
        return 400, {
            "error": "a non-root task delegation requires parent_task_id provenance",
            "error_code": "parent_task_id_required",
        }
    return None


def _task_memory_page_preflight(
    task_memory_id: str,
    root_task_id: str,
) -> dict[str, Any] | None:
    """Require Abaddon's existing canonical page; Skitarii must never invent one."""
    if not task_memory_id:
        return None
    try:
        document = _task_page_document(task_memory_id)
    except Exception as exc:
        code = "task_memory_page_unavailable"
        evidence = f"{type(exc).__name__}: {exc}"[:1_000]
    else:
        revision = document.get("revision")
        page_memory_id = str(document.get("task_memory_id") or "").strip()
        page_root_id = str(document.get("root_task_id") or "").strip()
        if type(revision) is int and revision >= 1 and page_memory_id == task_memory_id:
            if not root_task_id or page_root_id == root_task_id:
                return None
            code = "task_memory_page_identity_mismatch"
            evidence = (
                f"requested root_task_id={root_task_id!r}; "
                f"page root_task_id={page_root_id!r}"
            )
        else:
            code = "task_memory_page_not_initialized"
            evidence = (
                f"requested task_memory_id={task_memory_id!r}; "
                f"page task_memory_id={page_memory_id!r}; revision={revision!r}"
            )
    retryable = code != "task_memory_page_identity_mismatch"
    remediation = (
        "Abaddon must initialize or restore the canonical task page with the same "
        "task_memory_id and root_task_id, then retry this unchanged mission."
        if retryable else
        "Stop this lineage: repair the immutable task/root binding or issue a new explicit task identity before any retry."
    )
    return {
        "status": "failed",
        "accepted": False,
        "task_memory_id": task_memory_id,
        "root_task_id": root_task_id,
        "summary": "Task memory is unavailable; coding did not start.",
        "error": evidence,
        "error_code": code,
        "failure_class": "infrastructure",
        "retryable": retryable,
        "revision_required": retryable,
        "verification_findings": [review_finding(
            code,
            "The canonical task wiki page was unavailable or had different lineage.",
            evidence,
            "Every coding attempt resumes from one existing canonical task page.",
            remediation,
            "infrastructure",
            retryable,
            entity_kind="task_memory",
            entity_id=task_memory_id,
        )],
        "files": {},
    }


def _literal_loopback_authority(value: str) -> tuple[str, int] | None:
    """Parse a Host/authority without DNS and accept only 127.0.0.1 or ::1."""
    value = str(value or "")
    if not value or any(ch.isspace() for ch in value) or any(ch in value for ch in "/\\@"):
        return None
    host = value
    port_text = ""
    if value.startswith("["):
        close = value.find("]")
        if close < 0:
            return None
        host = value[1:close]
        suffix = value[close + 1:]
        if suffix:
            if not suffix.startswith(":"):
                return None
            port_text = suffix[1:]
    elif value.count(":") == 1:
        host, port_text = value.rsplit(":", 1)
    elif ":" in value:
        return None
    if host not in {"127.0.0.1", "::1"}:
        return None
    if not port_text:
        return host, 80
    if not port_text.isascii() or not port_text.isdigit():
        return None
    port = int(port_text)
    return (host, port) if 1 <= port <= 65535 else None


def _trusted_origin(value: str, host_authority: tuple[str, int]) -> bool:
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "http" or parsed.username is not None or parsed.password is not None:
            return False
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return False
        authority = _literal_loopback_authority(parsed.netloc)
    except (TypeError, ValueError):
        return False
    return authority == host_authority


def leadership_context_from_payload(
    payload: dict,
    task_id: str,
) -> tuple[dict, str]:
    """Validate optional Ceraxia context before it can influence planning."""
    raw = payload.get("leadership_directive")
    if raw is None:
        return {}, ""
    if not isinstance(raw, dict):
        raise CeraxiaDirectiveError("leadership_directive must be an object")
    delegating_task_id = str(payload.get("delegating_task_id") or task_id).strip()
    directive = validate_ceraxia_directive(
        raw,
        expected_task_id=delegating_task_id,
        require_delegation=True,
    )
    return directive, leadership_context_text(directive)


def acceptance_user_request_from_payload(
    payload: dict,
    leadership_directive: dict,
    *,
    required: bool,
) -> str:
    """Validate the narrow commander-order projection used only by acceptance."""
    def bounded_text(value: object, maximum: int) -> bool:
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            return False
        try:
            return len(value.encode("utf-8")) <= maximum
        except UnicodeEncodeError:
            return False

    raw = payload.get("acceptance_source")
    if raw is None:
        if required:
            raise CeraxiaDirectiveError(
                "acceptance_source is required for directed production execution"
            )
        return ""
    if not leadership_directive:
        raise CeraxiaDirectiveError(
            "acceptance_source requires a validated Ceraxia leadership directive"
        )
    if not isinstance(raw, dict):
        raise CeraxiaDirectiveError("acceptance_source must be an object")
    raw_keys = set(raw)
    if not all(isinstance(key, str) for key in raw_keys):
        raise CeraxiaDirectiveError("acceptance_source keys must be strings")
    if raw_keys != _ACCEPTANCE_SOURCE_KEYS:
        missing = sorted(_ACCEPTANCE_SOURCE_KEYS - raw_keys)
        unknown = sorted(raw_keys - _ACCEPTANCE_SOURCE_KEYS)
        raise CeraxiaDirectiveError(
            f"acceptance_source fields mismatch; missing={missing}, unknown={unknown}"
        )
    if raw.get("type") != ACCEPTANCE_SOURCE_TYPE:
        raise CeraxiaDirectiveError("acceptance_source.type is invalid")
    if (
        type(raw.get("protocol_version")) is not int
        or raw.get("protocol_version") != PROTOCOL_VERSION
    ):
        raise CeraxiaDirectiveError("acceptance_source.protocol_version is invalid")
    for field, maximum in (
        ("mission_id", 128), ("delegating_task_id", 128),
        ("from", 32), ("to", 32),
    ):
        value = raw.get(field)
        if not bounded_text(value, maximum):
            raise CeraxiaDirectiveError(f"acceptance_source.{field} is invalid")
    if raw["mission_id"] != leadership_directive.get("mission_id"):
        raise CeraxiaDirectiveError(
            "acceptance_source.mission_id does not match the leadership directive"
        )
    if raw["delegating_task_id"] != leadership_directive.get("task_id"):
        raise CeraxiaDirectiveError(
            "acceptance_source.delegating_task_id does not match the leadership directive"
        )
    if raw["from"] != "Warmaster" or raw["to"] != "Ceraxia":
        raise CeraxiaDirectiveError(
            "acceptance_source authority must be Warmaster -> Ceraxia"
        )
    user_request = raw.get("user_request")
    if not bounded_text(user_request, MAX_ACCEPTANCE_SOURCE_BYTES):
        raise CeraxiaDirectiveError("acceptance_source.user_request is invalid")
    return user_request


def standalone_test_execution_allowed(payload: dict) -> bool:
    """The explicit two-key escape hatch for eval/dev HTTP missions."""
    return (
        os.environ.get("SKITARII_STANDALONE_TEST_MODE", "0") == "1"
        and payload.get("standalone_test") is True
    )


def execution_authorization_error(
    payload: dict,
    task_id: str,
) -> tuple[int, dict] | None:
    """Return an HTTP error unless Ceraxia delegated or test mode is double-gated."""
    task_memory_id = _task_memory_id_from_payload(payload)
    root_task_id = _root_task_id_from_payload(payload, task_memory_id)
    parent_task_id = _parent_task_id_from_payload(payload)
    if not task_memory_id or not root_task_id:
        return 400, {
            "error": "task_memory_id and root_task_id are required",
            "error_code": "task_memory_identity_required",
        }
    if task_memory_id and not _valid_task_id(task_memory_id):
        return 400, {
            "error": "task_memory_id is invalid",
            "error_code": "invalid_task_memory_id",
        }
    if root_task_id and not _valid_task_id(root_task_id):
        return 400, {
            "error": "root_task_id is invalid",
            "error_code": "invalid_root_task_id",
        }
    if parent_task_id and not _valid_task_id(parent_task_id):
        return 400, {
            "error": "parent_task_id is invalid",
            "error_code": "invalid_parent_task_id",
        }
    try:
        _parent_skitarii_mission_id_from_payload(payload)
    except ValueError as exc:
        return 400, {
            "error": str(exc),
            "error_code": "invalid_parent_skitarii_mission_id",
        }
    lineage_error = _delegation_lineage_error(
        payload,
        task_id,
        root_task_id,
        parent_task_id,
    )
    if lineage_error is not None:
        return lineage_error
    if payload.get("leadership_directive") is not None:
        try:
            directive, _context = leadership_context_from_payload(payload, task_id)
        except CeraxiaDirectiveError as exc:
            return 400, {
                "error": f"invalid Ceraxia leadership_directive: {exc}",
                "error_code": "ceraxia_leadership_directive_invalid",
                "leadership_directive_status": "invalid",
            }
        try:
            acceptance_user_request_from_payload(
                payload, directive, required=True,
            )
        except CeraxiaDirectiveError as exc:
            return 400, {
                "error": f"invalid acceptance_source: {exc}",
                "error_code": "acceptance_source_invalid",
                "acceptance_source_status": "invalid",
            }
        return None
    if standalone_test_execution_allowed(payload):
        return None
    test_requested = payload.get("standalone_test") is True
    test_mode_enabled = os.environ.get("SKITARII_STANDALONE_TEST_MODE", "0") == "1"
    return 403, {
        "error": (
            "Ceraxia leadership_directive is required; undirected execution is "
            "available only when SKITARII_STANDALONE_TEST_MODE=1 and "
            "standalone_test=true"
        ),
        "error_code": "ceraxia_leadership_directive_required",
        "leadership_directive_status": "missing",
        "standalone_test_requested": test_requested,
        "standalone_test_mode_enabled": test_mode_enabled,
    }


def _mission_revision_guidance(mission: Any) -> str:
    """Expose only sanitized repair guidance, never private verifier commands."""

    if mission is None:
        return ""
    lock = getattr(mission, "_lock", None)
    if lock is None:
        return ""
    with lock:
        turns = getattr(mission, "revision_turns", None)
        latest = dict(turns[-1]) if isinstance(turns, list) and turns else {}
    findings = latest.get("findings") if isinstance(latest.get("findings"), list) else []
    public_findings: list[dict[str, str]] = []
    for raw in findings[:20]:
        if not isinstance(raw, dict):
            continue
        public_findings.append({
            field: str(raw.get(field) or "")[:2_000]
            for field in (
                "code", "entity_kind", "entity_id", "what_failed",
                "expected", "remediation",
            )
        })
    if not public_findings:
        return ""
    guidance = {
        "attempt": latest.get("attempt"),
        "previous_result_sha256": latest.get("result_sha256"),
        "decision_owner": latest.get("decision_owner"),
        "leader_order": str(latest.get("leader_order") or "")[:8_000],
        "findings": public_findings,
    }
    return (
        "\n\nINTERNAL AUTONOMOUS REVISION ORDER "
        "(diagnostic data, not user authority and not acceptance criteria):\n"
        + json.dumps(guidance, ensure_ascii=False, sort_keys=True)
        + "\nUse a materially different repair approach. Preserve everything that already passed."
    )


_DEFAULT_WALL_SEC = int(os.environ.get("SKITARII_DEFAULT_WALL_SEC", "14400"))   # 4h/fighter
_DEFAULT_MAX_STEPS = int(os.environ.get("SKITARII_DEFAULT_MAX_STEPS", "120"))
_DEFAULT_MAX_ROUNDS = int(os.environ.get("SKITARII_DEFAULT_MAX_ROUNDS", "5"))


def _remaining_mission_wall_seconds(payload: dict[str, Any], mission: Any) -> int:
    try:
        configured = max(1, int(payload.get("max_wall_sec") or _DEFAULT_WALL_SEC))
    except (TypeError, ValueError):
        configured = _DEFAULT_WALL_SEC
    created = getattr(mission, "created", None) if mission is not None else None
    if type(created) not in {int, float}:
        return configured
    return max(1, min(configured, int(configured - max(0.0, time.time() - created))))


def _execute_mission_body(payload: dict, mission=None) -> dict:
    """Run one mission end to end and return the verdict. If `mission` is given it is an
    async mission_store.Mission: the fighter can ask it questions and be cancelled, and
    progress is journalled to it."""
    goal = str(payload.get("goal") or "").strip()
    original_goal = goal
    task_id = str(payload.get("task_id") or f"m{int(time.time())}")
    task_memory_id = _task_memory_id_from_payload(payload)
    root_task_id = _root_task_id_from_payload(payload, task_memory_id)
    parent_task_id = _parent_task_id_from_payload(payload)
    if not task_memory_id or not root_task_id:
        return {
            "status": "blocked",
            "accepted": False,
            "task_id": task_id,
            "task_memory_id": task_memory_id,
            "root_task_id": root_task_id,
            "parent_task_id": parent_task_id,
            "summary": "Task memory identity is required.",
            "error_code": "task_memory_identity_required",
            "files": {},
        }
    if task_memory_id and not _valid_task_id(task_memory_id):
        return {
            "status": "blocked",
            "accepted": False,
            "task_id": task_id,
            "summary": "Task memory identity is invalid.",
            "error_code": "invalid_task_memory_id",
            "files": {},
        }
    if root_task_id and not _valid_task_id(root_task_id):
        return {
            "status": "blocked",
            "accepted": False,
            "task_id": task_id,
            "task_memory_id": task_memory_id,
            "summary": "Root task identity is invalid.",
            "error_code": "invalid_root_task_id",
            "files": {},
        }
    if parent_task_id and not _valid_task_id(parent_task_id):
        return {
            "status": "blocked",
            "accepted": False,
            "task_id": task_id,
            "task_memory_id": task_memory_id,
            "root_task_id": root_task_id,
            "parent_task_id": parent_task_id,
            "summary": "Parent task identity is invalid.",
            "error_code": "invalid_parent_task_id",
            "files": {},
        }
    pending_task_checkpoint = _pending_task_checkpoint_for_attempt(
        payload,
        mission,
        task_memory_id,
        root_task_id,
        parent_task_id,
    )
    if pending_task_checkpoint:
        return _resume_pending_task_checkpoint(
            pending_task_checkpoint,
            task_id=task_id,
            task_memory_id=task_memory_id,
            root_task_id=root_task_id,
            parent_task_id=parent_task_id,
        )
    memory_preflight = _task_memory_page_preflight(task_memory_id, root_task_id)
    if memory_preflight is not None:
        memory_preflight["task_id"] = task_id
        memory_preflight["parent_task_id"] = parent_task_id
        return memory_preflight
    try:
        leadership_directive, leadership_context = leadership_context_from_payload(
            payload,
            task_id,
        )
    except CeraxiaDirectiveError as exc:
        return {
            "status": "blocked",
            "accepted": False,
            "task_id": task_id,
            "summary": "Blocked: Ceraxia leadership directive is invalid.",
            "error": str(exc)[:500],
            "leadership_directive_status": "invalid",
            "files": {},
        }
    try:
        acceptance_user_request = acceptance_user_request_from_payload(
            payload, leadership_directive, required=bool(leadership_directive),
        )
    except CeraxiaDirectiveError as exc:
        return {
            "status": "blocked",
            "accepted": False,
            "task_id": task_id,
            "summary": "Blocked: commander acceptance source is invalid.",
            "error": str(exc)[:500],
            "error_code": "acceptance_source_invalid",
            "acceptance_source_status": "invalid",
            "files": {},
        }
    if leadership_context:
        goal += "\n\n" + leadership_context
    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else None
    workspace_files = payload.get("workspace_files") if isinstance(payload.get("workspace_files"), dict) else {}
    workspace_blobs = payload.get("workspace_blobs") if isinstance(payload.get("workspace_blobs"), dict) else {}
    workspace_inventory = payload.get("workspace_inventory") if isinstance(payload.get("workspace_inventory"), list) else []
    workspace_external_assets = (
        payload.get("workspace_external_assets")
        if isinstance(payload.get("workspace_external_assets"), dict) else {}
    )
    workspace_deleted = payload.get("workspace_deleted") if isinstance(payload.get("workspace_deleted"), list) else []
    workspace_modes = payload.get("workspace_modes") if isinstance(payload.get("workspace_modes"), dict) else {}
    workspace_symlinks = payload.get("workspace_symlinks") if isinstance(payload.get("workspace_symlinks"), dict) else {}
    protected_paths = payload.get("protected_paths") if isinstance(payload.get("protected_paths"), list) else []
    mode = str(payload.get("mode") or "greenfield")
    ask_fn = (lambda q: mission.ask_user(q)) if mission is not None else None
    cancel_fn = (lambda: mission.cancelled.is_set()) if mission is not None else None
    note = (lambda m: (mission.record("note", {"text": m}) if mission is not None else None)) or (lambda m: None)
    user_clarification = ""
    revision_guidance = _mission_revision_guidance(mission)

    # Pre-flight ambiguity gate: on a hopelessly vague goal, ask ONE question instead of
    # grinding blind (the eval showed 0/5 clarifications). Skip when explicit checks are
    # given (a checked task is grounded by construction). Fails open.
    if not checks and not revision_guidance:
        clar = needs_clarification(
            goal, has_workspace=bool(workspace_files or workspace_blobs or workspace_external_assets),
        )
        if clar:
            note("Задача размыта — спрашиваю уточнение вместо слепой работы.")
            answer = (ask_fn(clar) if ask_fn is not None else "") or ""
            answer = answer.strip()
            if not answer:
                return {"status": "needs_user", "accepted": False, "question": clar,
                        "summary": clar, "needs_user": True, "task_id": task_id, "files": {}}
            user_clarification = answer
            goal += f"\n\nУточнение пользователя: {answer}"

    # Freeze the only text allowed to authorize private expected values after a
    # real user clarification, but before repo/workspace annotations and Explorer.
    source_section = (
        "\n\nORIGINAL COMMANDER USER REQUEST "
        "(authoritative acceptance source):\n" + acceptance_user_request
        if acceptance_user_request else ""
    )
    authoritative_goal = goal + source_section
    primary_authority_goals = tuple(
        item for item in (user_clarification, acceptance_user_request or original_goal)
        if item
    )
    if revision_guidance:
        goal += revision_guidance
        note("Беру замечания прошлой проверки и захожу чинить именно их.")

    try:
        ex = _mission_executor(task_id)
    except ProcessBoundaryBusy as exc:
        return {
            "status": "blocked", "accepted": False, "task_id": task_id,
            "summary": "Sandbox is busy with another code mission; retry later.",
            "error": str(exc)[:500], "files": {},
        }
    except ProcessBoundaryQuarantined as exc:
        return {
            "status": "blocked", "accepted": False, "task_id": task_id,
            "summary": "Sandbox initialization became uncertain and was quarantined.",
            "error": str(exc)[:500], "cleanup_complete": False,
            "boundary_quarantined": True, "files": {},
        }
    except RuntimeError as exc:
        return {
            "status": "blocked", "accepted": False, "task_id": task_id,
            "summary": "Sandbox process boundary could not be initialized.",
            "error": str(exc)[:500], "files": {},
        }
    _EXECUTION_LOCAL.executor = ex
    if mission is not None:
        with _MISSION_EXECUTOR_LOCK:
            mission.executor = ex
            mission.executor_attempt = getattr(_EXECUTION_LOCAL, "attempt_token", "")
            cancelled_before_attach = mission.cancelled.is_set()
        if cancelled_before_attach:
            ex.cancel_current_commands()
            return {
                "status": "cancelled", "accepted": False, "task_id": task_id,
                "summary": "Mission was cancelled before sandbox execution began.",
                "files": {},
            }
    if not ex.alive():
        return {"status": "blocked", "accepted": False, "error": "sandbox VM is not reachable"}

    try:
        preloaded = _prepare_workspace(
            ex, workspace_files, workspace_blobs, workspace_deleted,
            workspace_modes, workspace_symlinks,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "status": "blocked", "accepted": False,
            "error": f"invalid workspace snapshot: {exc}", "task_id": task_id,
        }
    visible_count = len(workspace_inventory) or (preloaded + len(workspace_external_assets))
    try:
        # Every mission, including greenfield work, gets an immutable empty-or-loaded
        # baseline so the real deliverable is always a reproducible patch.
        base_commit = _create_baseline(ex)
        _EXECUTION_LOCAL.base_commit = base_commit
    except (OSError, RuntimeError, ValueError) as exc:
        return {"status": "blocked", "accepted": False,
                "error": f"could not create workspace baseline: {exc}", "task_id": task_id}
    try:
        restored_checkpoint = _restore_workspace_checkpoint(
            ex,
            base_commit,
            _workspace_checkpoint_for_attempt(
                payload, mission, task_memory_id, root_task_id,
            ),
            task_memory_id=task_memory_id,
            root_task_id=root_task_id,
            parent_task_id=parent_task_id,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "status": "failed",
            "accepted": False,
            "task_id": task_id,
            "task_memory_id": task_memory_id,
            "root_task_id": root_task_id,
            "summary": "The previous workspace checkpoint could not be restored safely.",
            "error": f"{type(exc).__name__}: {exc}"[:1_000],
            "error_code": "workspace_checkpoint_restore_failed",
            "failure_class": "infrastructure",
            "retryable": True,
            "revision_required": True,
            "verification_findings": [review_finding(
                "workspace_checkpoint_restore_failed",
                "The previous unaccepted patch could not be restored onto the current baseline.",
                str(exc)[:2_000],
                "A checkpoint is replayed only onto the byte-identical original snapshot.",
                "Rebuild or repair the checkpoint from its persisted patch and baseline-tree hash.",
                "infrastructure",
                True,
                entity_kind="workspace_checkpoint",
                entity_id="latest",
            )],
            "files": {},
        }
    if restored_checkpoint:
        note("Восстановил рабочий каталог с прошлой незачтённой попытки — продолжаю с того же места.")
        _memory(task_memory_id, "Восстановлен незавершённый патч предыдущей попытки.")
    active_checkpoint_lock = threading.Lock()
    active_checkpoint_state = {"fingerprint": None}

    def durable_workspace_checkpoint(
        checkpoint_executor: Any,
        *,
        step: int,
        boundary: str,
    ) -> None:
        """Atomically WAL the current patch after a safe fighter boundary."""
        if mission is None:
            return
        with active_checkpoint_lock:
            checkpoint = _capture_workspace_checkpoint(
                checkpoint_executor,
                base_commit,
                task_memory_id=task_memory_id,
                root_task_id=root_task_id,
                parent_task_id=parent_task_id,
            )
            has_patch = bool(
                checkpoint.get("unified_diff")
                and checkpoint.get("changed_files")
            )
            fingerprint = (
                str(checkpoint.get("patch_sha256") or "")
                if has_patch else "clean"
            )
            if active_checkpoint_state["fingerprint"] == fingerprint:
                return
            active_result: dict[str, Any] = {
                "status": "running",
                "accepted": False,
                "task_id": task_id,
                "task_memory_id": task_memory_id,
                "root_task_id": root_task_id,
                "parent_task_id": parent_task_id,
                "summary": "Active fighter state was durably checkpointed.",
                "active_workspace_checkpoint": has_patch,
                "workspace_checkpoint_step": max(0, int(step)),
                "workspace_checkpoint_boundary": str(boundary)[:200],
                "workspace_checkpoint_at": time.time(),
            }
            if has_patch:
                active_result["workspace_checkpoint"] = checkpoint
            with mission._lock:
                mission.result = active_result
                mission.record("workspace_checkpoint", {
                    "step": max(0, int(step)),
                    "boundary": str(boundary)[:200],
                    "changed_file_count": len(checkpoint.get("changed_files") or []),
                    "patch_sha256": str(checkpoint.get("patch_sha256") or ""),
                    "has_patch": has_patch,
                })
                mission._persist(raise_errors=True)
            active_checkpoint_state["fingerprint"] = fingerprint

    if preloaded or workspace_deleted or workspace_external_assets:
        goal += (f"\n\n(ПРАВКА СУЩЕСТВУЮЩЕГО кода: {visible_count} файл(ов) проекта уже лежат в рабочем "
                 "каталоге с их путями — читай и правь их, НЕ переписывай с нуля.)")
        if workspace_external_assets:
            external_paths = sorted(_safe_workspace_path(path) for path in workspace_external_assets)
            goal += ("\nLarge Git-visible assets are inventory-only in the fighter VM and must remain "
                     "unchanged: " + ", ".join(external_paths[:50]))
    _memory(task_memory_id, f"Загружено {visible_count} файлов проекта." if visible_count else f"Старт: {goal[:200]}")

    exploration = explore(
        goal, workspace_files, ex, inventory=workspace_inventory,
    ) if (workspace_files or workspace_inventory) else {}
    brief = brief_for_fighter(exploration) if exploration else ""
    if brief:
        goal += brief; note("Разведка задачи: разобрал, что нужно сделать и что должно заработать по итогу.")

    trusted_bypass = (
        payload.get("_trusted_skip_held_out") is True
        and os.environ.get("SKITARII_ALLOW_TRUSTED_HELD_OUT_BYPASS", "0") == "1"
    )
    held_out_required = (
        os.environ.get("SKITARII_REQUIRE_HELD_OUT", "1") == "1" and not trusted_bypass
    )
    held_out_prompt_goal = goal + source_section
    held_out_plan = build_held_out_plan(
        held_out_prompt_goal,
        task_goal=authoritative_goal,
        primary_task_goals=primary_authority_goals,
    ) if held_out_required else {
        "status": "not_required", "checks": [], "error": "",
    }
    held_out_checks = list(held_out_plan.get("checks") or [])
    held_out_degraded = False
    held_out_degraded_status = ""
    held_out_degraded_error = ""
    verification_findings: list[dict] = []
    if held_out_required:
        note(f"Готовлю {len(held_out_checks)} скрытых проверок поведения — боец их не видит, чтобы не подогнал ответ.")
        try:
            held_out_checks = _isolate_private_oracles(
                held_out_checks, authoritative_goal,
                primary_authority_goals=primary_authority_goals,
            )
            evidence_violation = _held_out_evidence_violation(held_out_checks)
        except (TypeError, ValueError) as exc:
            evidence_violation = str(exc)
        held_out_failure_status, held_out_failure_error = _held_out_plan_failure(
            held_out_plan, evidence_violation,
        )
        if held_out_failure_status:
            held_out_degraded = True
            held_out_degraded_status = held_out_failure_status
            held_out_degraded_error = held_out_failure_error[:500]
            verification_findings = _held_out_plan_findings(
                held_out_plan, held_out_failure_status, held_out_failure_error,
            )
            held_out_checks = []
            note(
                "Скрытые проверки собрать не удалось — иду только через открытую "
                "перепроверку поведения. Причина: " + held_out_failure_error[:240]
            )

    try:
        if checks:
            verdict = run_mission(goal, ex, checks=checks, task_id=task_id,
                                  memory_task_id=task_memory_id,
                                  ask_fn=ask_fn, cancel_fn=cancel_fn,
                                  max_fighter_rounds=int(payload.get("max_rounds") or _DEFAULT_MAX_ROUNDS),
                                  max_steps=int(payload.get("max_steps") or _DEFAULT_MAX_STEPS),
                                  max_wall_sec=_remaining_mission_wall_seconds(payload, mission),
                                  durable_checkpoint_fn=durable_workspace_checkpoint,
                                  progress=note)
        else:
            verdict = plan_and_run(goal, ex, task_id=task_id,
                                   memory_task_id=task_memory_id,
                                   ask_fn=ask_fn, cancel_fn=cancel_fn,
                                   max_wall_sec=_remaining_mission_wall_seconds(payload, mission),
                                   memory=lambda m: (note(m), _memory(task_memory_id, m)),
                                   durable_checkpoint_fn=durable_workspace_checkpoint,
                                   progress=note)
    except Exception as exc:  # noqa: BLE001 - preserve work before outer cleanup
        verdict = _recoverable_pipeline_verdict(
            exc,
            ex=ex,
            base_commit=base_commit,
            task_id=task_id,
            task_memory_id=task_memory_id,
            root_task_id=root_task_id,
            parent_task_id=parent_task_id,
        )
        _memory(
            task_memory_id,
            f"Внутренняя авария {verdict.get('error_code')}: работа сохранена для автоматического продолжения.",
        )
    # Stop every fighter descendant before freezing any artifact. From this point on
    # the primary worktree is read only; private checks run in a disposable copy.
    try:
        _stop_workspace_processes(ex, strict=True)
    except RuntimeError as exc:
        retry_error = ""
        try:
            _stop_workspace_processes(ex, strict=True)
        except Exception as retry_exc:
            retry_error = f"{type(retry_exc).__name__}: {retry_exc}"[:500]
        verdict.update({
            "accepted": False,
            "status": "blocked",
            "summary": "Blocked: fighter processes could not be frozen safely.",
            "freeze_error": (
                str(exc)[:500]
                + (f"; stop retry failed: {retry_error}" if retry_error else "")
            ),
            "task_id": task_id,
            "held_out_required": held_out_required,
            "held_out_check_count": len(held_out_checks),
            "held_out_status": "not_run_freeze_failed",
            "files": {},
        })
        if retry_error:
            _EXECUTION_LOCAL.preserve_workspace = True
            cleanup_finalizer = getattr(ex, "_cleanup_finalizer", None)
            detach_finalizer = getattr(cleanup_finalizer, "detach", None)
            if callable(detach_finalizer):
                detach_finalizer()
            try:
                ex.quarantine_process_boundary()
            except Exception:
                pass
            verdict.update({
                "cleanup_complete": False,
                "boundary_quarantined": True,
                "workspace_preserved": True,
                "recovery_handle": str(getattr(ex, "workdir", ""))[:500],
                "retryable": False,
                "revision_required": False,
            })
        else:
            verdict.update({
                "status": "failed",
                "summary": "The fighter freeze initially failed, but stop was proven on retry and work was checkpointed.",
                "retryable": True,
                "revision_required": True,
                "verification_findings": [review_finding(
                    "freeze_retry_recovered",
                    "The first fighter freeze attempt failed before artifact capture.",
                    str(exc)[:1_000],
                    "All fighter descendants are stopped before a durable patch is captured.",
                    "Restore the saved workspace checkpoint and resume verification without discarding edits.",
                    "infrastructure",
                    True,
                    entity_kind="fighter_runtime",
                    entity_id="freeze",
                )],
            })
        return _finalize_service_verdict(
            verdict,
            ex=ex,
            base_commit=base_commit,
            task_id=task_id,
            task_memory_id=task_memory_id,
            root_task_id=root_task_id,
            parent_task_id=parent_task_id,
            capture_workspace=not bool(retry_error),
        )
    verdict["files"] = _collect_files(ex, verdict.get("artifacts") or [])
    verdict["task_id"] = task_id
    if leadership_directive:
        verdict["leadership"] = {
            "leader": leadership_directive["leader"],
            "decision": leadership_directive["decision"],
            "mission_id": leadership_directive["mission_id"],
            "delegating_task_id": leadership_directive["task_id"],
        }
    verdict["held_out_required"] = held_out_required
    verdict["held_out_check_count"] = len(held_out_checks)
    if base_commit:
        try:
            # The patch is captured before any private command executes and remains
            # blocked until every independent gate has passed.
            patch_bundle = _build_patch_bundle(ex, base_commit, accepted=False)
        except (OSError, RuntimeError, ValueError) as exc:
            verdict["accepted"] = False
            verdict["status"] = "failed"
            verdict["patch_bundle"] = {
                "base_commit": base_commit, "changed_files": [], "unified_diff": "",
                "apply_gate": "blocked", "error": f"could not build complete patch: {exc}",
            }
            _memory(task_memory_id, "Patch bundle failed closed: " + str(exc)[:300])
            return _finalize_service_verdict(
                verdict,
                ex=ex,
                base_commit=base_commit,
                task_id=task_id,
                task_memory_id=task_memory_id,
                root_task_id=root_task_id,
                parent_task_id=parent_task_id,
            )
        runner_violation = _runner_control_violation(
            list(patch_bundle.get("changed_files") or [])
        )
        symlink_violation = _workspace_symlink_violation(ex)
        if symlink_violation:
            verdict.update({
                "accepted": False,
                "status": "failed",
                "summary": "Revision required: a candidate symlink escaped the reproducible workspace.",
                "workspace_symlink_violation": symlink_violation,
                "held_out_status": "not_run_workspace_symlink_violation",
                "revision_required": True,
                "verification_findings": [review_finding(
                    "workspace_symlink_escape",
                    "A candidate symlink escapes the reproducible workspace.",
                    symlink_violation,
                    "Every deliverable resolves inside the mission workspace without escaping symlinks.",
                    "Remove or replace the escaping symlink, then rebuild and rerun all checks.",
                    "fighter",
                    True,
                    entity_kind="workspace",
                    entity_id="symlink-boundary",
                )],
            })
            patch_bundle["apply_gate"] = "blocked"
            verdict["patch_bundle"] = patch_bundle
            return _finalize_service_verdict(
                verdict,
                ex=ex,
                base_commit=base_commit,
                task_id=task_id,
                task_memory_id=task_memory_id,
                root_task_id=root_task_id,
                parent_task_id=parent_task_id,
            )
        if runner_violation:
            verdict.update({
                "accepted": False,
                "status": "failed",
                "summary": "Revision required: candidate changes crossed the verification-runner boundary.",
                "runner_control_violation": runner_violation,
                "held_out_status": "not_run_runner_control_violation",
                "revision_required": True,
                "verification_findings": [review_finding(
                    "runner_control_boundary",
                    "Candidate changes include a protected verification-runner path.",
                    runner_violation,
                    "The candidate modifies only task deliverables and cannot influence its verifier.",
                    "Remove all runner-control changes and implement the behaviour only in task-owned files.",
                    "fighter",
                    True,
                    entity_kind="patch",
                    entity_id="runner-boundary",
                )],
            })
            patch_bundle["apply_gate"] = "blocked"
            verdict["patch_bundle"] = patch_bundle
            _memory(task_memory_id, "Runner-control files were blocked: " + runner_violation[:300])
            return _finalize_service_verdict(
                verdict,
                ex=ex,
                base_commit=base_commit,
                task_id=task_id,
                task_memory_id=task_memory_id,
                root_task_id=root_task_id,
                parent_task_id=parent_task_id,
            )
        if verdict.get("accepted"):
            public_child = None
            held_out_child = None
            primary_before = ""
            primary_after = ""
            public_before = ""
            public_after = ""
            held_out_before = ""
            held_out_after = ""
            held_out_acceptance: dict = {
                "accepted": False, "results": [], "reason": "verifier did not run",
            }
            public_replay_acceptance: dict = {
                "accepted": False, "results": [], "reason": "public replay did not run",
            }
            verifier_error = ""
            verifier_failure_class = "verifier_internal"
            integrity_error = ""
            cleanup_error = ""
            cleanup_phase = False
            try:
                _scrub_interstage_temp(ex)
                primary_before = _replay_fingerprint(ex, "primary before replay")
                public_child = _copy_candidate_for_verification(ex, base_commit)
                public_before = _replay_fingerprint(public_child, "public reconstruction")
                if primary_before != public_before:
                    raise _ReplayIntegrityError(
                        "clean baseline plus captured patch does not reproduce candidate tree"
                    )
                public_deliverables, public_checks = _public_replay_inputs(verdict)
                public_replay_acceptance = _PUBLIC_ACCEPT(
                    public_child, public_deliverables, public_checks,
                )
                _stop_workspace_processes(public_child, strict=True)
                _scrub_runtime_debris(public_child)
                public_after = _replay_fingerprint(public_child, "public after replay")
                primary_after_public = _replay_fingerprint(ex, "primary after public replay")
                if public_before != public_after or primary_before != primary_after_public:
                    raise _ReplayIntegrityError(
                        "public replay mutated a frozen candidate snapshot"
                    )
                completed_public_child = public_child
                cleanup_phase = True
                _cleanup_workspace_processes(completed_public_child)
                public_child = None
                cleanup_phase = False
                if (
                    public_replay_acceptance.get("accepted")
                    and held_out_required
                    and not held_out_degraded
                ):
                    # Public replay is candidate-visible. Discard it completely,
                    # scrub shared temp, and give private checks a fresh reconstruction.
                    _scrub_interstage_temp(ex)
                    held_out_child = _copy_candidate_for_verification(ex, base_commit)
                    held_out_before = _replay_fingerprint(
                        held_out_child, "private reconstruction",
                    )
                    if primary_before != held_out_before:
                        raise _ReplayIntegrityError(
                            "fresh private reconstruction does not match the frozen candidate tree"
                        )
                    held_out_acceptance = accept(held_out_child, [], held_out_checks)
                    runtime_evidence_violation = _held_out_runtime_evidence_violation(
                        held_out_checks, held_out_acceptance,
                    )
                    if runtime_evidence_violation:
                        raise ValueError(runtime_evidence_violation)
                    _stop_workspace_processes(held_out_child, strict=True)
                    _scrub_runtime_debris(held_out_child)
                    held_out_after = _replay_fingerprint(
                        held_out_child, "private after replay",
                    )
                    primary_after = _replay_fingerprint(ex, "primary after private replay")
                elif public_replay_acceptance.get("accepted"):
                    held_out_acceptance = (
                        {
                            "accepted": False,
                            "results": [],
                            "reason": (
                                "private checks were not run; public behavioural replay "
                                "is the explicit degraded fallback"
                            ),
                        }
                        if held_out_degraded else
                        {"accepted": True, "results": [], "reason": "not required"}
                    )
                    primary_after = primary_after_public
                else:
                    primary_after = primary_after_public
            except _ReplayIntegrityError as exc:
                integrity_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            except Exception as exc:
                detail = f"{type(exc).__name__}: {str(exc)[:500]}"
                if cleanup_phase:
                    cleanup_error = detail
                else:
                    verifier_error = detail
                    if isinstance(exc, (AttributeError, KeyError, TypeError, ValueError)):
                        verifier_failure_class = "verifier_protocol"
            finally:
                for cleanup_label, cleanup_child in (
                    ("public replay", public_child),
                    ("private verifier", held_out_child),
                ):
                    if cleanup_child is None:
                        continue
                    try:
                        _cleanup_workspace_processes(cleanup_child)
                    except Exception as cleanup_exc:
                        detail = (
                            f"{cleanup_label} cleanup failed: "
                            f"{type(cleanup_exc).__name__}: {str(cleanup_exc)[:500]}"
                        )
                        cleanup_error = (
                            f"{cleanup_error}; {detail}" if cleanup_error else detail
                        )[:1000]

            if verifier_error and not cleanup_error and not integrity_error and primary_before:
                try:
                    primary_after = _replay_fingerprint(
                        ex, "primary after verifier error",
                    )
                except _ReplayIntegrityError as audit_exc:
                    integrity_error = str(audit_exc)[:1000]

            mutated = bool(
                primary_before and primary_after and primary_before != primary_after
                or public_before and public_after and public_before != public_after
                or held_out_before and held_out_after and held_out_before != held_out_after
            )
            if mutated and not integrity_error:
                integrity_error = "private verifier mutated the frozen candidate snapshot"
            verdict["held_out_acceptance"] = held_out_acceptance
            verdict["public_replay_acceptance"] = public_replay_acceptance
            if cleanup_error or integrity_error:
                verdict["accepted"] = False
                verdict["status"] = "blocked"
                verdict["held_out_status"] = "verifier_infra"
                verdict["held_out_failure_class"] = "verifier_infra"
                verdict["held_out_error"] = cleanup_error or integrity_error
                verification_findings = [review_finding(
                    "verification_isolation_failure",
                    "The verifier workspace was not proven isolated and reproducible.",
                    verdict["held_out_error"],
                    "Public and private replay leave the frozen candidate fingerprint unchanged and cleanup is proven.",
                    "Repair verifier isolation or cleanup, then rerun the mission from a clean workspace.",
                    "infrastructure",
                    True,
                    entity_kind="verification_runtime",
                    entity_id="isolation",
                )]
                verdict["summary"] = (
                    "Verification stopped safely: isolation or cleanup was not proven. "
                    + verdict["held_out_error"][:240]
                )
            elif verifier_error:
                verdict["accepted"] = False
                verdict["status"] = "failed"
                verdict["held_out_status"] = verifier_failure_class
                verdict["held_out_failure_class"] = verifier_failure_class
                verdict["held_out_error"] = verifier_error
                verdict["revision_required"] = True
                verification_findings = [_verifier_internal_finding(
                    verifier_failure_class,
                    verifier_error,
                    entity_id="initial-replay",
                )]
                verdict["summary"] = (
                    "The candidate was not judged because verification failed internally, "
                    "but replay cleanup and primary byte identity were proven. "
                    + verification_findings[0]["remediation"]
                )
            elif not public_replay_acceptance.get("accepted"):
                verdict["accepted"] = False
                verdict["status"] = "failed"
                verdict["held_out_status"] = "reconstructed_public_failure"
                verdict["held_out_failure_class"] = "candidate_failure"
                verification_findings = _acceptance_findings(
                    public_replay_acceptance, hidden=False,
                )
                verdict["summary"] = (
                    "Revision required: the reconstructed patch failed public behavioural acceptance. "
                    + verification_findings[0]["remediation"]
                )
                verdict["revision_required"] = True
            elif held_out_degraded:
                verdict["held_out_status"] = (
                    "degraded_" + (held_out_degraded_status or "private_verifier")
                )
                verdict["held_out_failure_class"] = "verification_degraded"
                verdict["held_out_error"] = held_out_degraded_error
                verdict["verification_degraded"] = True
                verdict["verification_mode"] = "public_behavioral_fallback"
                verdict["summary"] = (
                    str(verdict.get("summary") or "Work completed.")
                    + " Verification assurance is degraded: private checks were unavailable, "
                    "but the patch passed an independent public behavioural replay."
                ).strip()
            elif not held_out_acceptance.get("accepted"):
                failure_class = _held_out_failure_class(held_out_acceptance)
                verdict["accepted"] = False
                verdict["held_out_failure_class"] = failure_class
                verdict["held_out_status"] = failure_class
                verdict["status"] = "failed"
                if failure_class == "candidate_failure":
                    verdict["status"] = "failed"
                    verification_findings = _acceptance_findings(
                        held_out_acceptance, hidden=True,
                    )
                    verdict["summary"] = (
                        "Revision required: an undisclosed behavioural check rejected the candidate. "
                        + verification_findings[0]["remediation"]
                    )
                    verdict["revision_required"] = True
                    public_revision_checks = [
                        dict(item)
                        for item in (verdict.get("checks") or [])
                        if isinstance(item, dict)
                    ]
                    leadership_copy = (
                        dict(verdict["leadership"])
                        if isinstance(verdict.get("leadership"), dict)
                        else None
                    )
                    verdict, patch_bundle = _run_hidden_revision_round(
                        ex,
                        goal=goal,
                        public_checks=public_revision_checks,
                        held_out_checks=held_out_checks,
                        base_commit=base_commit,
                        task_id=task_id,
                        task_memory_id=task_memory_id,
                        root_task_id=root_task_id,
                        parent_task_id=parent_task_id,
                        ask_fn=ask_fn,
                        cancel_fn=cancel_fn,
                        max_steps=int(payload.get("max_steps") or _DEFAULT_MAX_STEPS),
                        max_wall_sec=_remaining_mission_wall_seconds(payload, mission),
                    )
                    verdict["task_id"] = task_id
                    verdict["held_out_required"] = True
                    verdict["held_out_check_count"] = len(held_out_checks)
                    if leadership_copy is not None:
                        verdict["leadership"] = leadership_copy
                    verification_findings = list(
                        verdict.get("verification_findings") or []
                    )
                else:
                    verdict["revision_required"] = True
                    verification_findings = [_verifier_internal_finding(
                        failure_class,
                        _verifier_failure_detail(held_out_acceptance, failure_class),
                        entity_id="private-runtime",
                    )]
                    verdict["summary"] = (
                        "The candidate was not judged because a trusted verifier component failed. "
                        + verification_findings[0]["remediation"]
                    )
            else:
                verdict["held_out_status"] = "passed" if held_out_required else "not_required"
                verdict["held_out_failure_class"] = ""
                public_checks = verdict.get("checks") if isinstance(verdict.get("checks"), list) else []
                verdict["checks"] = public_checks + (held_out_checks if held_out_required else [])
            verdict["verification_findings"] = verification_findings
        elif held_out_required:
            verdict["held_out_status"] = "not_run_candidate_rejected"
        else:
            verdict["held_out_status"] = "not_required"
        diff = patch_bundle["unified_diff"]
        violation = _protected_path_violation(
            goal,
            set(workspace_files) | set(workspace_blobs) | set(workspace_symlinks),
            patch_bundle.get("changed_files") or [],
            protected_paths,
        )
        if violation:
            verdict["accepted"] = False
            verdict["status"] = "failed"
            verdict["protected_path_violation"] = violation
            verdict["revision_required"] = True
            verdict["verification_findings"] = [review_finding(
                "protected_path_violation",
                "The candidate changed a path protected by the mission boundary.",
                violation,
                "Only explicitly authorized task paths are changed.",
                "Revert the protected-path edits and implement the task inside its authorized scope.",
                "fighter",
                True,
                entity_kind="patch",
                entity_id="protected-paths",
            )]
            patch_bundle["apply_gate"] = "blocked"
            _memory(task_memory_id, "Protected files were changed: " + violation[:300])
        last_acc = {}
        for r in reversed(verdict.get("rounds") or []):
            if isinstance(r.get("acceptance"), dict):
                last_acc = r["acceptance"]; break
        if not last_acc and isinstance(verdict.get("acceptance"), dict):
            last_acc = verdict["acceptance"]
        rev = review(goal, diff, last_acc, invariants=exploration.get("invariants"))
        verdict["review"] = rev
        # ADVISORY, not a veto. The executable oracle (behavioural checks) is the source of
        # truth — an LLM reviewer's opinion must NOT overturn green checks (that produced
        # real false-rejects: it killed working code on un4/rg2). Surface its concerns as a
        # warning for the user; only genuine check failures (accepted already False) fail a
        # mission.
        if verdict.get("accepted") and not rev["approved"]:
            verdict["review_warning"] = rev["issues"]
            note("Ревьюер отметил замечания (совещательно — проверки зелёные, приёмку не отменяю): "
                 + "; ".join(rev["issues"])[:300])
        patch_bundle["apply_gate"] = "accepted" if verdict.get("accepted") else "blocked"
        verdict["patch_bundle"] = patch_bundle
    _memory(task_memory_id, f"Итог: {verdict.get('status')} (accepted={verdict.get('accepted')}).")
    return _finalize_service_verdict(
        verdict,
        ex=ex,
        base_commit=base_commit,
        task_id=task_id,
        task_memory_id=task_memory_id,
        root_task_id=root_task_id,
        parent_task_id=parent_task_id,
    )


def execute_mission(payload: dict, mission=None) -> dict:
    """Own one executor attempt from creation through proven cleanup.

    HTTP handlers authorize before reserving async state so they can return an
    honest 4xx. This internal boundary repeats the check, preventing a future
    call site from silently becoming a bypass. Eval/dev callers use the same
    explicit standalone-test double gate.
    """
    task_id = str(payload.get("task_id") or "") if isinstance(payload, dict) else ""
    task_memory_id = _task_memory_id_from_payload(payload) if isinstance(payload, dict) else ""
    root_task_id = (
        _root_task_id_from_payload(payload, task_memory_id)
        if isinstance(payload, dict) else ""
    )
    parent_task_id = (
        _parent_task_id_from_payload(payload)
        if isinstance(payload, dict) else ""
    )
    authorization_error = (
        execution_authorization_error(payload, task_id)
        if isinstance(payload, dict)
        else (400, {"error": "mission payload must be an object"})
    )
    if authorization_error is not None:
        _code, authorization_payload = authorization_error
        error_code = str(
            authorization_payload.get("error_code") or "mission_unauthorized"
        )
        blocked = {
            "status": "blocked",
            "accepted": False,
            "task_id": task_id,
            "summary": (
                "Blocked: commander acceptance source is invalid."
                if error_code == "acceptance_source_invalid"
                else (
                    "Blocked: canonical task memory identity is required or invalid."
                    if error_code in {
                        "task_memory_identity_required", "invalid_task_memory_id",
                        "invalid_root_task_id", "invalid_parent_task_id",
                    }
                    else (
                        "Rejected: durable Abaddon task lineage is invalid."
                        if error_code in {
                            "invalid_task_id", "invalid_delegating_task_id",
                            "parent_task_id_required",
                            "root_task_parent_forbidden",
                            "parent_task_id_self_reference",
                        }
                        else (
                            "Blocked: parent Skitarii mission identity is invalid."
                            if error_code == "invalid_parent_skitarii_mission_id"
                            else "Blocked: Ceraxia leadership authorization is required."
                        )
                    )
                )
            ),
            "error": str(authorization_payload.get("error") or "unauthorized mission")[:500],
            "error_code": error_code,
            "cleanup_complete": True,
            "files": {},
        }
        blocked["task_memory_id"] = task_memory_id
        blocked["root_task_id"] = root_task_id
        blocked["parent_task_id"] = parent_task_id
        if "leadership_directive_status" in authorization_payload:
            blocked["leadership_directive_status"] = str(
                authorization_payload["leadership_directive_status"]
            )
        if "acceptance_source_status" in authorization_payload:
            blocked["acceptance_source_status"] = str(
                authorization_payload["acceptance_source_status"]
            )
        return blocked
    token = uuid.uuid4().hex
    previous_executor = getattr(_EXECUTION_LOCAL, "executor", None)
    previous_token = getattr(_EXECUTION_LOCAL, "attempt_token", None)
    previous_base_commit = getattr(_EXECUTION_LOCAL, "base_commit", None)
    previous_preserve_workspace = getattr(_EXECUTION_LOCAL, "preserve_workspace", None)
    _EXECUTION_LOCAL.executor = None
    _EXECUTION_LOCAL.attempt_token = token
    _EXECUTION_LOCAL.base_commit = None
    _EXECUTION_LOCAL.preserve_workspace = False
    verdict: dict | None = None
    pipeline_error: Exception | None = None
    cleanup_error: Exception | None = None
    try:
        verdict = _execute_mission_body(payload, mission)
    except Exception as exc:  # noqa: BLE001 - preserve post-baseline work before cleanup
        pipeline_error = exc
        executor = getattr(_EXECUTION_LOCAL, "executor", None)
        base_commit = str(getattr(_EXECUTION_LOCAL, "base_commit", None) or "")
        if executor is not None and base_commit:
            try:
                verdict = _recoverable_pipeline_verdict(
                    exc,
                    ex=executor,
                    base_commit=base_commit,
                    task_id=task_id,
                    task_memory_id=task_memory_id,
                    root_task_id=root_task_id,
                    parent_task_id=parent_task_id,
                )
                verdict = _finalize_service_verdict(
                    verdict,
                    ex=executor,
                    base_commit=base_commit,
                    task_id=task_id,
                    task_memory_id=task_memory_id,
                    root_task_id=root_task_id,
                    parent_task_id=parent_task_id,
                )
                pipeline_error = None
            except Exception:
                # Keep the original exception as the primary diagnosis when the
                # recovery path itself cannot be completed safely.
                verdict = None
    finally:
        executor = getattr(_EXECUTION_LOCAL, "executor", None)
        preserve_workspace = bool(getattr(_EXECUTION_LOCAL, "preserve_workspace", False))
        if executor is not None and not preserve_workspace:
            try:
                _cleanup_workspace_processes(executor)
            except Exception as exc:  # noqa: BLE001
                cleanup_error = exc
        if mission is not None:
            with _MISSION_EXECUTOR_LOCK:
                if getattr(mission, "executor_attempt", None) == token:
                    mission.executor = None
                    mission.executor_attempt = None
        _EXECUTION_LOCAL.executor = previous_executor
        _EXECUTION_LOCAL.attempt_token = previous_token
        _EXECUTION_LOCAL.base_commit = previous_base_commit
        _EXECUTION_LOCAL.preserve_workspace = previous_preserve_workspace

    if cleanup_error is not None:
        blocked = dict(verdict) if isinstance(verdict, dict) else {}
        blocked.update({
            "status": "blocked", "accepted": False, "task_id": task_id,
            "task_memory_id": task_memory_id, "root_task_id": root_task_id,
            "parent_task_id": parent_task_id,
            "summary": "Sandbox cleanup could not be proven; lifecycle quarantined.",
            "error": f"{type(cleanup_error).__name__}: {cleanup_error}"[:500],
            "cleanup_complete": False, "boundary_quarantined": True, "files": {},
        })
        return blocked
    if pipeline_error is not None:
        return {
            "status": "failed", "accepted": False, "task_id": task_id,
            "task_memory_id": task_memory_id, "root_task_id": root_task_id,
            "parent_task_id": parent_task_id,
            "summary": "Mission pipeline failed before a trustworthy verdict was produced.",
            "error": f"{type(pipeline_error).__name__}: {pipeline_error}"[:500],
            "cleanup_complete": True, "files": {},
        }
    if verdict is not None:
        verdict["task_id"] = task_id
        verdict["task_memory_id"] = task_memory_id
        verdict["root_task_id"] = root_task_id
        verdict["parent_task_id"] = parent_task_id
        verdict.setdefault("cleanup_complete", True)
        return verdict
    return {
        "status": "failed", "accepted": False, "task_id": task_id,
        "task_memory_id": task_memory_id, "root_task_id": root_task_id,
        "parent_task_id": parent_task_id,
        "summary": "Mission pipeline returned no verdict.",
        "cleanup_complete": True, "files": {},
    }


class Handler(BaseHTTPRequestHandler):
    def _request_gate(self, *, require_json: bool = False) -> bool:
        """Reject DNS-rebinding, browser CSRF and optional bearer-auth failures."""
        try:
            peer = ipaddress.ip_address(str(self.client_address[0]))
        except (IndexError, TypeError, ValueError):
            self._send(403, {"error": "loopback client required"})
            return False
        if not peer.is_loopback:
            self._send(403, {"error": "loopback client required"})
            return False

        hosts = self.headers.get_all("Host") or []
        authority = _literal_loopback_authority(hosts[0]) if len(hosts) == 1 else None
        if authority is None:
            self._send(421, {"error": "literal loopback Host required"})
            return False

        fetch_sites = self.headers.get_all("Sec-Fetch-Site") or []
        if len(fetch_sites) > 1 or (
            fetch_sites and fetch_sites[0].strip().lower() not in {"same-origin", "same-site", "none"}
        ):
            self._send(403, {"error": "cross-site request rejected"})
            return False
        origins = self.headers.get_all("Origin") or []
        if len(origins) > 1 or (origins and not _trusted_origin(origins[0].strip(), authority)):
            self._send(403, {"error": "untrusted Origin"})
            return False

        if BEARER_TOKEN:
            auth = self.headers.get_all("Authorization") or []
            expected = f"Bearer {BEARER_TOKEN}"
            if len(auth) != 1 or not hmac.compare_digest(auth[0], expected):
                self._send(401, {"error": "bearer authorization required"})
                return False

        if require_json:
            content_types = self.headers.get_all("Content-Type") or []
            media_type = content_types[0].split(";", 1)[0].strip().lower() if len(content_types) == 1 else ""
            if media_type != "application/json":
                self._send(415, {"error": "Content-Type application/json required"})
                return False
            if self.headers.get_all("Transfer-Encoding"):
                self._send(400, {"error": "Transfer-Encoding is not supported"})
                return False
        return True

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
        if not self._request_gate():
            return
        if _is_health_path(self.path):
            # cheap health: does not block on the VM (that could take seconds over SSH
            # and make a client time out). Report VM reachability only if asked.
            probe = "vm" in (self.path.split("?", 1)[1] if "?" in self.path else "")
            payload = {"status": "ok", "service": "Skitarii", "identity": service_identity()}
            if probe:
                vm = VmExecutor(host="127.0.0.1", port=VM_PORT,
                                user="skitarii", key=VM_KEY)
                payload["vm_alive"] = vm.alive()
                payload["process_boundary_ready"] = (
                    payload["vm_alive"] and vm.boundary_ready()
                )
            self._send(200, payload)
            return
        parts = [p for p in self.path.split("?", 1)[0].split("/") if p]
        if len(parts) >= 2 and parts[0] == "missions":
            m = mission_store.get(parts[1])
            if not m:
                self._send(404, {"error": "mission not found"}); return
            if len(parts) == 3 and parts[2] == "events":   # GET /missions/{id}/events
                self._send(200, {"id": m.id, "events": m.events_snapshot()}); return
            self._send(200, m.snapshot(event_limit=50)); return   # GET /missions/{id}
        self._send(404, {"error": "not found"})

    def _body(self) -> dict:
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise ValueError("exactly one decimal Content-Length is required")
        length = int(lengths[0])
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError(f"request body exceeds {MAX_REQUEST_BYTES} bytes")
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def do_POST(self):
        if not self._request_gate(require_json=True):
            return
        parts = [p for p in self.path.split("?", 1)[0].split("/") if p]
        try:
            payload = self._body()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": f"bad json: {exc}"}); return

        # sync (blocking) — used by the Warmaster bridge / research loop
        if parts == ["mission"]:
            if not str(payload.get("goal") or "").strip():
                self._send(400, {"error": "goal is required"}); return
            if payload.get("task_id") is not None and not _valid_task_id(payload.get("task_id")):
                self._send(400, {"error": "invalid task_id"}); return
            task_id = str(payload.get("task_id") or f"m{int(time.time())}")
            payload = {**payload, "task_id": task_id}
            authorization_error = execution_authorization_error(payload, task_id)
            if authorization_error is not None:
                code, error_payload = authorization_error
                self._send(code, error_payload); return
            self._send(200, execute_mission(payload)); return

        # async lifecycle
        if parts == ["missions"]:              # POST /missions -> start in background
            goal = str(payload.get("goal") or "").strip()
            if not goal:
                self._send(400, {"error": "goal is required"}); return
            mid = str(payload.get("task_id") or f"m{int(time.time()*1000)}")
            if not _valid_task_id(mid):
                self._send(400, {"error": "invalid task_id"}); return
            payload = {**payload, "task_id": mid}
            authorization_error = execution_authorization_error(payload, mid)
            if authorization_error is not None:
                code, error_payload = authorization_error
                self._send(code, error_payload); return
            with _ASYNC_CREATE_LOCK:
                existing = mission_store.get(mid)
                if existing is not None:
                    self._send(409, {
                        "error": "mission already exists", "mission_id": mid,
                        "request_sha256": existing.request_sha256,
                    }); return
                try:
                    # ID reservation, exact resumable payload and adoption hash
                    # become visible atomically under the mission-store lock.
                    m = mission_store.create_and_run(
                        mid,
                        goal,
                        payload,
                        lambda mm: execute_mission(payload, mm),
                        on_created=lambda mission: mission.record(
                            "created", {"goal": goal[:300]}
                        ),
                    )
                except mission_store.MissionExistsError:
                    existing = mission_store.get(mid)
                    self._send(409, {
                        "error": "mission already exists", "mission_id": mid,
                        "request_sha256": (
                            existing.request_sha256 if existing else None
                        ),
                    }); return
                except mission_store.PayloadTooLargeError as exc:
                    self._send(413, {"error": str(exc), "mission_id": mid}); return
                except mission_store.MissionCapacityError as exc:
                    self._send(429, {
                        "error": str(exc), "mission_id": mid, "retryable": True,
                    }); return
                except mission_store.MissionPersistenceError as exc:
                    self._send(507, {"error": str(exc), "mission_id": mid}); return
            self._send(202, {
                "mission_id": mid, "status": m.status,
                "request_sha256": m.request_sha256,
            }); return
        if len(parts) == 3 and parts[0] == "missions":
            m = mission_store.get(parts[1])
            if not m:
                self._send(404, {"error": "mission not found"}); return
            if parts[2] == "answer":           # POST /missions/{id}/answer
                ok = m.provide_answer(str(payload.get("answer") or ""))
                self._send(200 if ok else 409, {"ok": ok, "status": m.status}); return
            if parts[2] == "cancel":           # POST /missions/{id}/cancel
                cancel_error = None
                with _MISSION_EXECUTOR_LOCK:
                    try:
                        ok = mission_store.cancel(parts[1], expected=m)
                    except mission_store.MissionPersistenceError as exc:
                        ok = False
                        cancel_error = exc
                    executor = getattr(m, "executor", None)
                cancel_commands = getattr(executor, "cancel_current_commands", None)
                if (ok or cancel_error is not None) and callable(cancel_commands):
                    cancel_commands()
                if cancel_error is not None:
                    self._send(507, {
                        "ok": False, "status": "blocked", "error": str(cancel_error),
                    }); return
                self._send(200, {"ok": ok, "status": m.status}); return
            if parts[2] == "resume":           # POST /missions/{id}/resume -> retry a stopped mission
                authorization_error = execution_authorization_error(m.payload, m.id)
                if authorization_error is not None:
                    code, error_payload = authorization_error
                    self._send(code, error_payload); return
                with m._lock:
                    prior_result = dict(m.result) if isinstance(m.result, dict) else {}
                restart_salvage = prior_result.get("restart_recovery_required") is True
                checkpoint_commit_pending = (
                    prior_result.get("error_code") == "task_checkpoint_commit_pending"
                )
                if restart_salvage:
                    sweep_executor = None
                    try:
                        sweep_executor = _mission_executor(m.id + "-restart-sweep")
                        _cleanup_workspace_processes(sweep_executor)
                        prepared = mission_store.prepare_restart_salvage(
                            parts[1], expected=m,
                        )
                    except Exception as exc:
                        self._send(409, {
                            "ok": False,
                            "status": "blocked",
                            "error": f"restart boundary sweep failed: {type(exc).__name__}: {exc}"[:500],
                        }); return
                    if not prepared:
                        self._send(409, {
                            "ok": False,
                            "status": "blocked",
                            "error": "restart recovery envelope is not salvageable",
                        }); return
                try:
                    ok = mission_store.resume(
                        parts[1],
                        lambda mm: execute_mission(mm.payload, mm),
                        expected=m,
                        require_payload=True,
                        preserve_result=(restart_salvage or checkpoint_commit_pending),
                    )
                except mission_store.MissionPersistenceError as exc:
                    self._send(507, {
                        "ok": False,
                        "status": m.status,
                        "error": str(exc),
                        "error_code": "mission_resume_persistence_failed",
                    }); return
                except mission_store.MissionCapacityError as exc:
                    self._send(409, {
                        "ok": False,
                        "status": m.status,
                        "error": str(exc),
                        "error_code": "mission_resume_capacity_exhausted",
                        "retryable": True,
                    }); return
                self._send(200 if ok else 409, {"ok": ok, "status": m.status}); return
        self._send(404, {"error": "not found"})


def _source_reload_watcher(interval_sec: float = 4.0) -> None:
    """Self-healing hot reload of the warband's own source.

    The gateway's deep readiness attestation refuses to dispatch when the running
    instance's source SHA disagrees with the checkout on disk — a correct integrity
    gate, but one that otherwise strands the whole code pipeline behind a manual
    restart every time a Skitarii file is edited. Instead of that operator crutch,
    watch the source and re-exec the process the moment it changes AND the single
    worker slot is idle. A live mission is never interrupted: if busy, we wait and
    reload once it drains. Result: edit a file -> the service adopts it on its own,
    its attested SHA matches disk again, and dispatch keeps working with no restart.
    """
    if os.environ.get("SKITARII_SOURCE_HOT_RELOAD", "1") != "1":
        return
    while True:
        time.sleep(max(1.0, interval_sec))
        try:
            current = _service_source_sha256()
        except OSError:
            continue
        if current == SERVICE_SOURCE_SHA256:
            continue
        # Source on disk changed. Only re-exec while idle so no worker is killed
        # mid-flight; if a mission holds the slot, retry on the next tick.
        try:
            with mission_store._GLOCK:
                active = mission_store._active_count_locked()
        except Exception:
            active = 1  # cannot prove idleness -> do not repave
        if active > 0:
            continue
        print(
            "Skitarii source changed on disk while idle — reloading in place: "
            f"{SERVICE_SOURCE_SHA256[:12]} -> {current[:12]}",
            flush=True,
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, *sys.argv])


def main():
    host = os.environ.get("SKITARII_HOST", "127.0.0.1")
    port = int(os.environ.get("SKITARII_PORT", "7200"))
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True          # a crashing request thread never takes the server down
    threading.Thread(
        target=_source_reload_watcher, name="skitarii-source-reload", daemon=True,
    ).start()
    print(f"Skitarii warband listening on http://{host}:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
