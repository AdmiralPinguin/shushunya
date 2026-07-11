"""Focused regression tests for service workspace snapshots and patch bundles."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import service  # noqa: E402
import executor  # noqa: E402
import spec  # noqa: E402
from executor import (  # noqa: E402
    LocalExecutor, ProcessBoundaryBusy, ProcessBoundaryQuarantined, VmExecutor,
    _run_capped_process,
)


class AliveLocalExecutor(LocalExecutor):
    def alive(self) -> bool:
        return True


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _http_request(method: str, path: str, *, body: bytes | None = None,
                  headers: dict[str, str] | None = None,
                  include_default_auth: bool = True) -> tuple[int, dict]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    request_headers = dict(headers or {})
    if include_default_auth and service.BEARER_TOKEN:
        request_headers.setdefault("Authorization", f"Bearer {service.BEARER_TOKEN}")
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        return response.status, payload
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class TestPatchBundle(unittest.TestCase):
    def test_skitarii_validates_and_renders_ceraxia_leadership_context(self):
        directive = {
            "kind": "ceraxia_leadership_directive",
            "version": 1,
            "task_id": "parent-task",
            "mission_id": "mission-parent-task",
            "leader": "Ceraxia",
            "decision": "delegate",
            "delegated_to": "SkitariiWarband",
            "mission_intent": "Deliver a compatible verified change",
            "priorities": ["correctness", "compatibility"],
            "constraints": ["preserve unrelated behavior"],
            "success_conditions": ["the requested behavior is verified"],
            "tradeoffs": [],
            "escalation_conditions": ["observable behavior requires a product choice"],
        }
        validated, context = service.leadership_context_from_payload(
            {
                "task_id": "wm-parent-task-a1",
                "delegating_task_id": "parent-task",
                "leadership_directive": directive,
            },
            "wm-parent-task-a1",
        )
        self.assertEqual(validated, directive)
        self.assertIn("Deliver a compatible verified change", context)
        self.assertIn("Skitarii owns repository exploration", context)

        mismatched = dict(directive, task_id="another-task")
        with self.assertRaisesRegex(service.CeraxiaDirectiveError, "does not match"):
            service.leadership_context_from_payload(
                {
                    "delegating_task_id": "parent-task",
                    "leadership_directive": mismatched,
                },
                "wm-parent-task-a1",
            )

    def test_symlink_scan_uses_a_synchronous_trusted_inventory(self):
        class RecordingExecutor:
            def __init__(self):
                self.commands = []

            def bash(self, command, timeout=0):
                self.commands.append(command)
                return {"returncode": 0, "stdout": "", "stderr": ""}

            def fetch_artifact(self, rel, max_bytes=None):
                return b""

        ex = RecordingExecutor()
        self.assertEqual(service._workspace_symlink_violation(ex), "")
        script = ex.commands[-1]
        self.assertTrue(script.startswith("set -e;"))
        self.assertIn("-print0 > .git/skitarii-symlink-scan", script)
        self.assertIn("done < .git/skitarii-symlink-scan", script)
        self.assertNotIn("< <(", script)

    def test_raw_index_builder_handles_five_thousand_files_with_bounded_cost(self):
        root = Path(tempfile.mkdtemp(prefix="raw-index-scale-"))
        for shard in range(50):
            directory = root / f"d{shard:02d}"
            directory.mkdir()
            for number in range(100):
                (directory / f"f{number:03d}.txt").write_text(
                    f"{shard}:{number}\n", encoding="utf-8",
                )
        ex = AliveLocalExecutor(root)
        started = time.monotonic()
        base = service._create_baseline(ex)
        (root / "d00" / "f000.txt").write_text("changed\n", encoding="utf-8")
        bundle = service._build_patch_bundle(ex, base, accepted=False)
        fingerprint = service._workspace_fingerprint(ex)
        elapsed = time.monotonic() - started

        self.assertEqual(bundle["changed_files"], ["d00/f000.txt"])
        self.assertEqual(len(fingerprint), 64)
        self.assertLess(elapsed, 20.0)
        script = service._raw_index_population(write_objects=True)
        self.assertIn("hash-object --no-filters -w --stdin-paths", script)
        self.assertEqual(script.count("git update-index"), 1)
        self.assertIn("-print0 > \"$all\"", script)
        self.assertNotIn("< <(", script)

    def test_diff_is_against_base_and_contains_full_final_tree(self):
        repo = Path(tempfile.mkdtemp(prefix="patch-bundle-src-"))
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "test@example.invalid")
        _git(repo, "config", "user.name", "test")

        (repo / "merged.txt").write_text("baseline\n", encoding="utf-8")
        (repo / "deleted.txt").write_text("remove me\n", encoding="utf-8")
        (repo / "existing.bin").write_bytes(bytes(range(64)))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "baseline")
        base = _git(repo, "rev-parse", "HEAD")

        # Simulate a fighter branch already merged into HEAD.  The old
        # `git diff HEAD` implementation silently omitted this change.
        (repo / "merged.txt").write_text("merged fighter result\n", encoding="utf-8")
        _git(repo, "add", "merged.txt")
        _git(repo, "commit", "-qm", "fighter merge")

        (repo / "deleted.txt").unlink()
        (repo / "new.txt").write_text("untracked result\n", encoding="utf-8")
        (repo / "large.txt").write_text(
            "large-start\n" + "payload line\n" * 3000 + "large-end\n", encoding="utf-8",
        )
        (repo / "existing.bin").write_bytes(bytes(reversed(range(64))))
        (repo / "new.bin").write_bytes(b"\x00\xff\x10\x80" * 128)
        (repo / "__pycache__").mkdir()
        (repo / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"runtime")
        (repo / ".pytest_cache").mkdir()
        (repo / ".pytest_cache" / "README.md").write_text("runtime", encoding="utf-8")
        (repo / ".git" / "skitarii-bg").mkdir()
        (repo / ".git" / "skitarii-bg" / "server.log").write_text("runtime", encoding="utf-8")

        bundle = service._build_patch_bundle(LocalExecutor(repo), base, accepted=True)

        expected = {"merged.txt", "deleted.txt", "new.txt", "large.txt", "existing.bin", "new.bin"}
        self.assertEqual(set(bundle["changed_files"]), expected)
        self.assertEqual(bundle["base_commit"], base)
        self.assertEqual(bundle["apply_gate"], "accepted")
        self.assertIn("merged fighter result", bundle["unified_diff"])
        self.assertIn("large-start", bundle["unified_diff"])
        self.assertIn("large-end", bundle["unified_diff"])
        self.assertIn("deleted file mode", bundle["unified_diff"])
        self.assertIn("GIT binary patch", bundle["unified_diff"])
        self.assertNotIn("__pycache__", bundle["unified_diff"])
        self.assertNotIn(".pytest_cache", bundle["unified_diff"])
        self.assertNotIn("skitarii-bg", bundle["unified_diff"])
        self.assertGreater(len(bundle["unified_diff"]), 20_000)

        # The bundle must reconstruct the VM's final tree from the original
        # baseline, including committed, untracked, deleted and binary changes.
        target = Path(tempfile.mkdtemp(prefix="patch-bundle-dst-"))
        subprocess.run(["git", "clone", "-q", str(repo), str(target)], check=True)
        _git(target, "checkout", "-q", base)
        patch = target / "result.patch"
        patch.write_text(bundle["unified_diff"], encoding="utf-8")
        _git(target, "apply", "--binary", "result.patch")
        self.assertEqual((target / "merged.txt").read_text(encoding="utf-8"), "merged fighter result\n")
        self.assertFalse((target / "deleted.txt").exists())
        self.assertEqual((target / "new.txt").read_text(encoding="utf-8"), "untracked result\n")
        self.assertEqual((target / "existing.bin").read_bytes(), bytes(reversed(range(64))))
        self.assertEqual((target / "new.bin").read_bytes(), b"\x00\xff\x10\x80" * 128)

    def test_oversized_complete_patch_fails_closed(self):
        repo = Path(tempfile.mkdtemp(prefix="patch-cap-"))
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "test@example.invalid")
        _git(repo, "config", "user.name", "test")
        (repo / "a.txt").write_text("base\n", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-qm", "base")
        base = _git(repo, "rev-parse", "HEAD")
        (repo / "a.txt").write_text("changed payload that exceeds a tiny cap\n", encoding="utf-8")
        original = service.MAX_PATCH_BYTES
        service.MAX_PATCH_BYTES = 10
        try:
            with self.assertRaisesRegex(ValueError, "complete patch exceeds"):
                service._build_patch_bundle(LocalExecutor(repo), base, accepted=True)
        finally:
            service.MAX_PATCH_BYTES = original

    def test_candidate_git_filter_and_ignore_cannot_execute_or_hide_files(self):
        repo = Path(tempfile.mkdtemp(prefix="patch-git-control-"))
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "test@example.invalid")
        _git(repo, "config", "user.name", "test")
        (repo / "victim.txt").write_text("base\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        base = _git(repo, "rev-parse", "HEAD")

        config = (repo / ".git" / "config")
        config.write_text(
            config.read_text(encoding="utf-8")
            + "\n[filter \"evil\"]\n\tclean = sh -c 'touch filter-ran; cat'\n\trequired = true\n",
            encoding="utf-8",
        )
        (repo / ".gitattributes").write_text("victim.txt filter=evil\n", encoding="utf-8")
        (repo / ".gitignore").write_text("conftest.py\n", encoding="utf-8")
        (repo / "conftest.py").write_text("def pytest_configure(config): pass\n", encoding="utf-8")
        (repo / "victim.txt").write_text("changed\n", encoding="utf-8")

        bundle = service._build_patch_bundle(LocalExecutor(repo), base, accepted=False)
        self.assertFalse((repo / "filter-ran").exists())
        self.assertIn("conftest.py", bundle["changed_files"])
        self.assertIn("victim.txt", bundle["changed_files"])

    def test_nested_git_metadata_is_rejected_before_patch_capture(self):
        repo = Path(tempfile.mkdtemp(prefix="patch-nested-git-"))
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "test@example.invalid")
        _git(repo, "config", "user.name", "test")
        (repo / "a.txt").write_text("base\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        base = _git(repo, "rev-parse", "HEAD")
        (repo / "nested" / ".git").mkdir(parents=True)
        (repo / "nested" / "payload.py").write_text("print('x')\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            service._build_patch_bundle(LocalExecutor(repo), base, accepted=False)

    def test_git_commondir_indirection_is_rejected_before_git_invocation(self):
        repo = Path(tempfile.mkdtemp(prefix="patch-commondir-"))
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "test@example.invalid")
        _git(repo, "config", "user.name", "test")
        (repo / "a.txt").write_text("base\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        base = _git(repo, "rev-parse", "HEAD")
        (repo / ".git" / "commondir").write_text("../../hostile-common\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            service._build_patch_bundle(LocalExecutor(repo), base, accepted=False)

    def test_hardlinked_git_config_cannot_corrupt_workspace_file(self):
        root = Path(tempfile.mkdtemp(prefix="git-config-hardlink-"))
        ex = AliveLocalExecutor(root)
        ex.write_file("docs.txt", "keep exact bytes\n")
        base = service._create_baseline(ex)
        (root / ".git" / "config").unlink()
        os.link(root / "docs.txt", root / ".git" / "config")

        bundle = service._build_patch_bundle(ex, base, accepted=False)

        self.assertEqual((root / "docs.txt").read_text(encoding="utf-8"), "keep exact bytes\n")
        self.assertEqual(bundle["changed_files"], [])
        self.assertEqual((root / ".git" / "config").stat().st_nlink, 1)

    def test_unreadable_directory_cannot_be_silently_omitted_from_patch(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root bypasses directory read permissions")
        root = Path(tempfile.mkdtemp(prefix="raw-index-unreadable-"))
        ex = AliveLocalExecutor(root)
        ex.write_file("app.py", "print('base')\n")
        base = service._create_baseline(ex)
        hidden = root / "hidden"
        hidden.mkdir()
        (hidden / "answer.txt").write_text("public-check dependency\n", encoding="utf-8")
        hidden.chmod(0o111)
        try:
            with self.assertRaises(RuntimeError):
                service._build_patch_bundle(ex, base, accepted=False)
        finally:
            hidden.chmod(0o700)


class TestWorkspaceSnapshot(unittest.TestCase):
    def test_deleted_modes_and_symlinks_are_materialised_before_baseline(self):
        root = Path(tempfile.mkdtemp(prefix="workspace-snapshot-"))
        (root / "deleted.txt").write_text("stale\n", encoding="utf-8")
        ex = LocalExecutor(root)

        count = service._prepare_workspace(
            ex,
            {"script.sh": "#!/bin/sh\necho ok\n", "dir/target.txt": "target\n"},
            {},
            ["deleted.txt"],
            {"script.sh": "100755", "dir/link": "120000"},
            {"dir/link": "target.txt"},
        )

        self.assertEqual(count, 3)
        self.assertFalse((root / "deleted.txt").exists())
        self.assertTrue(os.access(root / "script.sh", os.X_OK))
        self.assertTrue((root / "dir" / "link").is_symlink())
        self.assertEqual(os.readlink(root / "dir" / "link"), "target.txt")

    def test_binary_round_trip_and_tracked_ignored_baseline(self):
        root = Path(tempfile.mkdtemp(prefix="workspace-binary-"))
        ex = LocalExecutor(root)
        service._prepare_workspace(
            ex,
            {".gitignore": "ignored.bin\n"},
            {"ignored.bin": base64.b64encode(b"\x00\xffexact").decode("ascii")},
            [],
            {".gitignore": "100644", "ignored.bin": "100644"},
            {},
        )
        base = service._create_baseline(ex)
        self.assertTrue(base)
        self.assertEqual((root / "ignored.bin").read_bytes(), b"\x00\xffexact")
        self.assertIn("ignored.bin", _git(root, "ls-files").splitlines())

    def test_snapshot_metadata_cannot_escape_or_modify_git_metadata(self):
        root = Path(tempfile.mkdtemp(prefix="workspace-safety-"))
        outside = root.parent / f"{root.name}-outside"
        outside.write_text("keep\n", encoding="utf-8")
        ex = LocalExecutor(root)

        with self.assertRaises(ValueError):
            service._prepare_workspace(ex, {}, {}, [f"../{outside.name}"], {}, {})
        self.assertTrue(outside.exists())
        with self.assertRaises(ValueError):
            service._prepare_workspace(ex, {}, {}, [], {}, {"link": "../outside"})
        with self.assertRaises(ValueError):
            service._prepare_workspace(ex, {}, {}, [], {}, {"link": ".git/config"})
        with self.assertRaises(ValueError):
            service._prepare_workspace(ex, {".git/config": "bad"}, {}, [], {}, {})

    def test_protected_seed_test_change_is_blocked(self):
        violation = service._protected_path_violation(
            "fix app.py", {"app.py": "", "tests/test_app.py": "trusted"},
            ["app.py", "tests/test_app.py"], [],
        )
        self.assertEqual(violation, "tests/test_app.py")
        allowed = service._protected_path_violation(
            "update test for app.py", {"tests/test_app.py": "trusted"},
            ["tests/test_app.py"], [],
        )
        self.assertEqual(allowed, "")
        blob_violation = service._protected_path_violation(
            "fix app.py", {"tests/fixture.bin"}, ["tests/fixture.bin"], [],
        )
        self.assertEqual(blob_violation, "tests/fixture.bin")


class TestHealthRoute(unittest.TestCase):
    def test_health_query_is_routed_as_health(self):
        self.assertTrue(service._is_health_path("/health?vm=1"))
        self.assertTrue(service._is_health_path("/health"))
        self.assertFalse(service._is_health_path("/mission?vm=1"))

    def test_service_identity_attests_runtime_sources_and_models(self):
        identity = service.service_identity()
        self.assertEqual(len(identity["source_sha256"]), 64)
        self.assertTrue(identity["instance_id"])
        self.assertTrue(identity["held_out_required"])
        self.assertEqual(
            set(identity["models"]),
            {"planner", "reviewer", "spec", "fighter", "held_out"},
        )
        for model in identity["models"].values():
            self.assertTrue(model["model"])
            self.assertTrue(model["base_url"])

    def test_health_boundary_probe_reports_failed_attestation(self):
        class NotReadyVm:
            def __init__(self, **kwargs):
                pass

            def alive(self):
                return True

            def boundary_ready(self):
                return False

        with patch.object(service, "VmExecutor", NotReadyVm):
            code, payload = _http_request("GET", "/health?vm=1")
        self.assertEqual(code, 200)
        self.assertTrue(payload["vm_alive"])
        self.assertFalse(payload["process_boundary_ready"])

    def test_freeze_fails_closed_when_process_cleanup_fails(self):
        executor = type("Executor", (), {
            "mission_marker": "unit-marker",
            "bash": lambda _self, command, timeout=30: {
                "returncode": 1, "stdout": "", "stderr": "survivor",
            },
        })()
        with self.assertRaisesRegex(RuntimeError, "survived cleanup"):
            service._stop_workspace_processes(executor, strict=True)

    def test_freeze_uses_cgroup_boundary_not_mutable_env_or_cwd(self):
        calls = []

        class BoundaryExecutor:
            process_boundary = True
            mission_marker = "fighter-can-unset-this"

            def stop_process_boundary(self, *, strict=False):
                calls.append(strict)
                return True

            def bash(self, command, timeout=30):
                raise AssertionError("legacy env/cwd process scan must not run")

        self.assertTrue(service._stop_workspace_processes(BoundaryExecutor(), strict=True))
        self.assertEqual(calls, [True])

    def test_vm_foreground_command_is_wrapped_in_killing_cgroup(self):
        ex = VmExecutor(
            process_boundary=True, mission_marker="mission-a",
            workdir="/home/skitarii/work/unit-a",
            boundary_process_baseline={}, boundary_auth_state="MISSING",
            boundary_lease=object(),
        )
        with (
            patch("executor._run_capped_process", return_value={
                "returncode": 0, "stdout": "ok\n", "stderr": "",
            }) as run,
            patch.object(ex, "_check_storage_bounds", return_value=(True, "")),
        ):
            result = ex.bash(
                "oid=abcdef; entry=./hello; "
                "printf '%s %s %s' \"${#oid}\" \"${entry#./}\" \"$oid\"; "
                "unset SKITARII_MISSION_MARKER; cd /tmp; sleep 99 &",
                timeout=7,
            )

        self.assertEqual(result["returncode"], 0)
        remote = run.call_args.args[0][-1]
        self.assertIn("skitarii-boundary systemd-run", remote)
        self.assertNotIn("systemd-run --user", remote)
        self.assertIn("--property=KillMode=control-group", remote)
        self.assertIn("--property=NoNewPrivileges=yes", remote)
        self.assertIn(f"--property=Slice={ex.process_slice}", remote)
        self.assertIn("--property=ProtectSystem=strict", remote)
        self.assertIn("--property=ProtectHome=read-only", remote)
        self.assertIn("--property=ReadWritePaths=/home/skitarii/work", remote)
        self.assertIn("--property=BindPaths=", remote)
        self.assertIn("--property=IPAddressDeny=10.0.2.2/32", remote)
        self.assertIn("--property=MemoryMax=4294967296", remote)
        self.assertIn("--property=RuntimeMaxSec=7s", remote)
        self.assertIn(ex.process_unit_prefix, remote)
        self.assertIn("$${#oid}", remote)
        self.assertIn("$${entry#./}", remote)
        self.assertIn("$$oid", remote)

    def test_host_output_is_streamed_with_hard_cap(self):
        result = _run_capped_process(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000)"],
            timeout=10, max_output_bytes=100_000,
        )
        self.assertEqual(result["returncode"], 125)
        self.assertLessEqual(len(result["stdout"].encode()), 20_000)
        self.assertIn("output exceeded", result["stderr"])

    def test_storage_accounting_fails_closed_above_limit(self):
        completed = subprocess.CompletedProcess(
            [], 0, stdout="1000000001 10 0 0\n", stderr="",
        )
        ex = VmExecutor(
            process_boundary=True, mission_marker="mission-storage",
            workdir="/home/skitarii/work/unit-storage",
            boundary_process_baseline={}, boundary_auth_state="MISSING",
            boundary_lease=object(),
        )
        with patch("executor.subprocess.run", return_value=completed):
            ok, reason = ex._check_storage_bounds()
        self.assertFalse(ok)
        self.assertIn("storage exceeds", reason)

    def test_temp_entry_count_is_included_in_file_limit(self):
        completed = subprocess.CompletedProcess(
            [], 0, stdout="1 1 1 50000\n", stderr="",
        )
        ex = VmExecutor(
            process_boundary=True, mission_marker="mission-inodes",
            workdir="/home/skitarii/work/unit-inodes",
            boundary_process_baseline={}, boundary_auth_state="MISSING",
            boundary_lease=object(),
        )
        with patch("executor.subprocess.run", return_value=completed):
            ok, reason = ex._check_storage_bounds()
        self.assertFalse(ok)
        self.assertIn("file count", reason)

    def test_interstage_temp_scrub_is_privileged_and_proves_empty(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        ex = VmExecutor(
            process_boundary=True, mission_marker="mission-temp-scrub",
            workdir="/home/skitarii/work/unit-temp-scrub",
            boundary_process_baseline={}, boundary_auth_state="MISSING",
            boundary_lease=object(),
        )
        with patch("executor.subprocess.run", return_value=completed) as run:
            self.assertTrue(ex.scrub_boundary_temp(strict=True))
        remote = run.call_args.args[0][-1]
        self.assertIn("shared=/home/skitarii/work/.skitarii-tmp", remote)
        self.assertIn("set -- /tmp /var/tmp /dev/shm", remote)
        self.assertIn("-exec /usr/bin/rm -rf", remote)
        self.assertIn("-print -quit", remote)
        self.assertNotIn("|| true", remote)

    def test_final_storage_cleanup_unmounts_reverts_and_proves_empty(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        ex = VmExecutor(
            process_boundary=True, mission_marker="mission-final-storage",
            workdir="/home/skitarii/work/unit-final-storage",
            boundary_process_baseline={}, boundary_auth_state="MISSING",
            boundary_lease=object(),
        )
        with patch("executor.subprocess.run", return_value=completed) as run:
            self.assertTrue(ex.remove_boundary_storage(strict=True))
        remote = run.call_args.args[0][-1]
        self.assertIn(f"systemctl stop {ex.process_slice}", remote)
        self.assertIn("skitarii-boundary umount -- /home/skitarii/work", remote)
        self.assertIn("! sudo -n /usr/local/sbin/skitarii-boundary mountpoint", remote)
        self.assertIn(f"systemctl revert {ex.process_slice}", remote)
        self.assertIn("set -e", remote)
        self.assertNotIn("|| true", remote)

    def test_boundary_initialization_sweeps_orphans_before_baseline(self):
        baseline = subprocess.CompletedProcess(
            [], 0, stdout="AUTH\tMISSING\nPROC\t123\t456\n", stderr="",
        )
        ex = VmExecutor(
            process_boundary=True, mission_marker="mission-init",
            workdir="/home/skitarii/work/unit-init",
        )
        ex.boundary_lease = object()
        with (
            patch.object(ex, "_acquire_boundary_lease", return_value=True),
            patch("executor.subprocess.run", return_value=baseline) as run,
        ):
            self.assertTrue(ex.initialize_process_boundary(strict=True))

        self.assertEqual(ex.boundary_process_baseline, {"123": "456"})
        remote = run.call_args.args[0][-1]
        self.assertIn('[ -x "$helper" ] || exit 127', remote)
        self.assertIn('helper_version=$("$helper" --version 2>/dev/null) || exit 126', remote)
        self.assertIn("helper_sha=$(/usr/bin/sha256sum \"$helper\" | /usr/bin/awk", remote)
        self.assertIn('[ "$helper_sha" = "' + executor.BOUNDARY_HELPER_SHA256 + '" ] || exit 126', remote)
        self.assertLess(remote.index('[ -x "$helper" ]'), remote.index("set -e\n"))
        self.assertLess(remote.index('helper_sha=$(/usr/bin/sha256sum'), remote.index("set -e\n"))
        self.assertIn("systemctl mask --runtime", remote)
        self.assertIn("pre-existing sandbox uid process survived", remote)
        self.assertIn("for signal in TERM KILL", remote)
        self.assertIn("chown -hR root:root", remote)
        self.assertIn("chmod 0644", remote)
        self.assertIn("set -- /tmp /var/tmp /dev/shm", remote)
        self.assertIn("mount -t tmpfs", remote)
        self.assertIn("nr_inodes=50000", remote)
        self.assertIn("systemctl set-property --runtime", remote)

    def test_helper_preflight_failures_release_flock_without_quarantine(self):
        class Lease:
            released = False

            def release(self):
                self.released = True

        for returncode, detail in (
            (127, "boundary helper is missing"),
            (126, "boundary helper identity mismatch"),
        ):
            with self.subTest(returncode=returncode):
                lease = Lease()
                failed = subprocess.CompletedProcess(
                    [], returncode, stdout="", stderr=detail,
                )
                ex = VmExecutor(
                    process_boundary=True,
                    mission_marker=f"mission-helper-preflight-{returncode}",
                    workdir=f"/home/skitarii/work/unit-helper-preflight-{returncode}",
                )
                ex.boundary_lease = lease
                with (
                    patch.object(ex, "_acquire_boundary_lease", return_value=True),
                    patch.object(ex, "quarantine_process_boundary") as quarantine,
                    patch("executor.subprocess.run", return_value=failed),
                ):
                    with self.assertRaisesRegex(RuntimeError, "could not initialize"):
                        ex.initialize_process_boundary(strict=True)
                self.assertTrue(lease.released)
                self.assertIsNone(ex.boundary_lease)
                quarantine.assert_not_called()

    def test_init_timeout_and_ssh_255_quarantine_and_raise_distinctly(self):
        class Lease:
            released = False

            def release(self):
                self.released = True

        failures = [
            subprocess.TimeoutExpired(["ssh"], 45),
            subprocess.CompletedProcess([], 255, stdout="", stderr="connection lost"),
        ]
        for index, failure in enumerate(failures):
            with self.subTest(index=index):
                lease = Lease()
                ex = VmExecutor(
                    process_boundary=True, mission_marker=f"mission-init-uncertain-{index}",
                    workdir=f"/home/skitarii/work/unit-init-uncertain-{index}",
                )
                ex.boundary_lease = lease
                runner = {"side_effect": failure} if isinstance(failure, BaseException) else {
                    "return_value": failure,
                }
                with (
                    patch.object(ex, "_acquire_boundary_lease", return_value=True),
                    patch.object(ex, "quarantine_process_boundary") as quarantine,
                    patch("executor.subprocess.run", **runner),
                ):
                    with self.assertRaises(ProcessBoundaryQuarantined):
                        ex.initialize_process_boundary(strict=True)
                quarantine.assert_called_once_with()
                self.assertFalse(lease.released)
                self.assertIs(ex.boundary_lease, lease)

    def test_global_boundary_lock_times_out_fail_closed(self):
        import fcntl

        lock_path = Path(tempfile.mkdtemp(prefix="boundary-lock-")) / "mission.lock"
        holder = open(lock_path, "a+", encoding="utf-8")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            ex = VmExecutor(
                process_boundary=True, mission_marker="mission-busy",
                workdir="/home/skitarii/work/unit-busy",
            )
            with patch.dict(os.environ, {
                "SKITARII_PROCESS_LOCK": str(lock_path),
                "SKITARII_PROCESS_LOCK_TIMEOUT_SEC": "1",
            }):
                with self.assertRaises(ProcessBoundaryBusy):
                    ex._acquire_boundary_lease(strict=True)
        finally:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()

    def test_global_boundary_lock_rejects_symlink_without_touching_target(self):
        root = Path(tempfile.mkdtemp(prefix="boundary-lock-link-"))
        target = root / "target"
        target.write_text("keep", encoding="utf-8")
        target.chmod(0o644)
        link = root / "mission.lock"
        link.symlink_to(target)
        ex = VmExecutor(
            process_boundary=True, mission_marker="mission-link",
            workdir="/home/skitarii/work/unit-link",
        )
        with patch.dict(os.environ, {"SKITARII_PROCESS_LOCK": str(link)}):
            with self.assertRaisesRegex(RuntimeError, "global sandbox mission lock"):
                ex._acquire_boundary_lease(strict=True)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)

    def test_busy_sandbox_returns_honest_blocked_verdict(self):
        with patch.object(service, "_mission_executor", side_effect=ProcessBoundaryBusy("busy")):
            verdict = service.execute_mission({
                "goal": "fix app.py", "task_id": "busy-unit",
                "checks": [{"cmd": "python3 app.py"}],
            })
        self.assertEqual(verdict["status"], "blocked")
        self.assertFalse(verdict["accepted"])
        self.assertIn("retry", verdict["summary"].lower())

    def test_uncertain_init_returns_quarantined_cleanup_false_verdict(self):
        with patch.object(
            service, "_mission_executor",
            side_effect=ProcessBoundaryQuarantined("ssh state uncertain"),
        ):
            verdict = service.execute_mission({
                "goal": "fix app.py", "task_id": "uncertain-init",
                "checks": [{"cmd": "true"}],
            })
        self.assertEqual(verdict["status"], "blocked")
        self.assertFalse(verdict["cleanup_complete"])
        self.assertTrue(verdict["boundary_quarantined"])

    def test_vm_background_helper_gets_mission_unit_and_pid(self):
        completed = {"returncode": 0, "stdout": "4321\n", "stderr": ""}
        with patch("executor._run_capped_process", return_value=completed) as run:
            ex = VmExecutor(
                process_boundary=True, mission_marker="mission-bg",
                workdir="/home/skitarii/work/unit-bg",
                boundary_process_baseline={}, boundary_auth_state="MISSING",
                boundary_lease=object(),
            )
            info = ex.bash_background(
                "value=abcdef; test \"${#value}\" = 6; python3 -m http.server 8123"
            )

        self.assertTrue(info["started"])
        self.assertEqual(info["pid"], "4321")
        self.assertTrue(info["unit"].startswith(ex.process_unit_prefix))
        remote = run.call_args.args[0][-1]
        self.assertIn("skitarii-boundary systemd-run", remote)
        self.assertNotIn("systemd-run --user", remote)
        self.assertIn("--property=KillMode=control-group", remote)
        self.assertIn("--property=NoNewPrivileges=yes", remote)
        self.assertIn("skitarii-boundary systemctl show --property=MainPID", remote)
        self.assertTrue(info["log"].startswith(".git/skitarii-bg/"))
        self.assertIn("$${#value}", remote)

    def test_vm_boundary_cleanup_stops_units_and_checks_cgroup_procs(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch("executor.subprocess.run", return_value=completed) as run:
            ex = VmExecutor(
                process_boundary=True, mission_marker="mission-cleanup",
                workdir="/home/skitarii/work/unit-cleanup",
                boundary_process_baseline={"12": "34"}, boundary_auth_state="MISSING",
                boundary_lease=object(),
            )
            self.assertTrue(ex.stop_process_boundary(strict=True))

        remote = run.call_args.args[0][-1]
        self.assertIn("skitarii-boundary systemctl stop", remote)
        self.assertIn("skitarii-boundary systemctl kill --kill-whom=all", remote)
        self.assertIn("should_reap_pid", remote)
        self.assertIn("baseline=12:34", remote)
        self.assertIn("authorized_keys integrity changed", remote)
        self.assertIn("cgroup.procs", remote)
        self.assertNotIn("/proc/$1/environ", remote)

    def test_cancel_interrupts_blocking_foreground_unit(self):
        started = threading.Event()
        stopped = threading.Event()
        ex = VmExecutor(
            process_boundary=True, mission_marker="mission-cancel",
            workdir="/home/skitarii/work/unit-cancel",
            boundary_process_baseline={}, boundary_auth_state="MISSING",
            boundary_lease=object(),
        )

        def blocking_run(argv, *, timeout, max_output_bytes=0, **kwargs):
            started.set()
            stopped.wait(timeout=3)
            return {"returncode": 1, "stdout": "", "stderr": "cancelled"}

        result = {}
        with (
            patch("executor._run_capped_process", side_effect=blocking_run),
            patch.object(ex, "_check_storage_bounds", return_value=(True, "")),
            patch.object(ex, "_stop_one_process_unit", side_effect=lambda unit: stopped.set()) as stop,
            patch.object(ex, "stop_process_boundary", return_value=True),
        ):
            thread = threading.Thread(
                target=lambda: result.update(ex.bash("sleep 600", timeout=600)),
            )
            thread.start()
            self.assertTrue(started.wait(timeout=2))
            ex.cancel_current_commands()
            thread.join(timeout=3)

        self.assertFalse(thread.is_alive())
        self.assertTrue(stopped.is_set())
        stop.assert_called()
        self.assertNotEqual(result.get("returncode"), 0)

    def test_stale_cancel_after_release_cannot_reacquire_global_lease(self):
        ex = VmExecutor(
            process_boundary=True, mission_marker="mission-stale-cancel",
            workdir="/home/skitarii/work/unit-stale-cancel",
            boundary_process_baseline={}, boundary_auth_state="MISSING",
            boundary_lease=None,
        )
        with (
            patch.object(ex, "_acquire_boundary_lease", side_effect=AssertionError("reacquired")) as acquire,
            patch.object(ex, "_stop_one_process_unit") as stop,
        ):
            ex.cancel_current_commands()
        acquire.assert_not_called()
        stop.assert_not_called()

    def test_cancel_before_local_spawn_invalidates_launch_generation(self):
        entered = threading.Event()
        continue_launch = threading.Event()
        spawned = []
        ex = VmExecutor(
            process_boundary=True, mission_marker="mission-prelaunch-cancel",
            workdir="/home/skitarii/work/unit-prelaunch-cancel",
            boundary_process_baseline={}, boundary_auth_state="MISSING",
            boundary_lease=object(),
        )

        def gated_run(argv, *, timeout, max_output_bytes=0, spawn_lock=None,
                      pre_spawn=None, on_started=None):
            entered.set()
            self.assertTrue(continue_launch.wait(timeout=2))
            with spawn_lock:
                if not pre_spawn():
                    return {"returncode": 125, "stdout": "", "stderr": "cancelled before launch"}
                spawned.append(True)
            return {"returncode": 0, "stdout": "", "stderr": ""}

        result = {}
        with (
            patch("executor._run_capped_process", side_effect=gated_run),
            patch.object(ex, "_check_storage_bounds", return_value=(True, "")),
            patch.object(ex, "_stop_one_process_unit"),
            patch.object(ex, "stop_process_boundary", return_value=True),
        ):
            thread = threading.Thread(target=lambda: result.update(ex.bash("true")))
            thread.start()
            self.assertTrue(entered.wait(timeout=2))
            ex.cancel_current_commands()
            continue_launch.set()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(spawned, [])
        self.assertEqual(result["returncode"], 125)

    def test_cancel_during_heldout_child_prelaunch_poisons_shared_runtime(self):
        entered = threading.Event()
        continue_launch = threading.Event()
        spawned = []
        parent = VmExecutor(
            process_boundary=True, mission_marker="mission-heldout-cancel",
            workdir="/home/skitarii/work/unit-heldout-cancel",
            boundary_process_baseline={}, boundary_auth_state="MISSING",
            boundary_lease=object(),
        )
        with patch.object(VmExecutor, "prepare_boundary_workdir", return_value=True):
            heldout = parent.child("heldout")

        def gated_run(argv, *, timeout, max_output_bytes=0, spawn_lock=None,
                      pre_spawn=None, on_started=None):
            entered.set()
            self.assertTrue(continue_launch.wait(timeout=2))
            with spawn_lock:
                if not pre_spawn():
                    return {"returncode": 125, "stdout": "", "stderr": "cancelled before launch"}
                spawned.append(True)
            return {"returncode": 0, "stdout": "", "stderr": ""}

        result = {}
        with (
            patch("executor._run_capped_process", side_effect=gated_run),
            patch.object(heldout, "_check_storage_bounds", return_value=(True, "")),
            patch.object(parent, "stop_process_boundary", return_value=True),
            patch.object(heldout, "stop_process_boundary", return_value=True),
            patch.object(parent, "_stop_one_process_unit"),
            patch.object(heldout, "_stop_one_process_unit"),
        ):
            thread = threading.Thread(target=lambda: result.update(heldout.bash("true")))
            thread.start()
            self.assertTrue(entered.wait(timeout=2))
            parent.cancel_current_commands()
            continue_launch.set()
            thread.join(timeout=3)

        self.assertFalse(thread.is_alive())
        self.assertEqual(spawned, [])
        self.assertEqual(result["returncode"], 125)
        self.assertTrue(parent.boundary_poisoned)
        self.assertTrue(heldout.boundary_poisoned)
        with patch("executor._run_capped_process") as future_launch:
            future = heldout.bash("echo must-not-run")
        self.assertEqual(future["returncode"], 125)
        future_launch.assert_not_called()

    def test_fingerprint_pipeline_is_checked_with_pipefail(self):
        seen = []

        class Executor:
            def bash(self, command, timeout=30):
                seen.append(command)
                if len(seen) == 1:
                    return {"returncode": 0, "stdout": "", "stderr": ""}
                return {"returncode": 1, "stdout": "hash  -", "stderr": "fingerprint failed"}

        with self.assertRaises(RuntimeError):
            service._workspace_fingerprint(Executor())
        self.assertIn("set -e -o pipefail", seen[-1])


class TestHttpRequestGate(unittest.TestCase):
    def test_literal_loopback_authority_has_no_dns_fallback(self):
        self.assertEqual(service._literal_loopback_authority("127.0.0.1:7200"), ("127.0.0.1", 7200))
        self.assertEqual(service._literal_loopback_authority("[::1]:7200"), ("::1", 7200))
        for value in ("localhost:7200", "evil.example", "127.0.0.1.evil:7200", "127.0.0.1@evil"):
            self.assertIsNone(service._literal_loopback_authority(value))

    def test_dns_rebinding_host_is_rejected(self):
        code, payload = _http_request("GET", "/health", headers={"Host": "evil.example"})
        self.assertEqual(code, 421)
        self.assertIn("loopback Host", payload["error"])

    def test_text_plain_create_is_rejected_before_dispatch(self):
        code, _ = _http_request(
            "POST", "/missions", body=b'{"goal":"x"}',
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(code, 415)

    def test_cross_origin_cancel_is_rejected_before_dispatch(self):
        code, payload = _http_request(
            "POST", "/missions/unit/cancel", body=b"{}",
            headers={"Content-Type": "application/json", "Origin": "http://evil.example"},
        )
        self.assertEqual(code, 403)
        self.assertIn("Origin", payload["error"])

    def test_cross_site_create_is_rejected_before_dispatch(self):
        code, payload = _http_request(
            "POST", "/missions", body=b'{"goal":"x"}',
            headers={"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(code, 403)
        self.assertIn("cross-site", payload["error"])

    def test_configured_bearer_is_mandatory_and_constant_contract(self):
        with patch.object(service, "BEARER_TOKEN", "unit-secret"):
            denied, _ = _http_request(
                "GET", "/health", include_default_auth=False,
            )
            allowed, _ = _http_request(
                "GET", "/health", headers={"Authorization": "Bearer unit-secret"},
                include_default_auth=False,
            )
        self.assertEqual(denied, 401)
        self.assertEqual(allowed, 200)

    def test_async_create_get_and_duplicate_share_exact_request_hash(self):
        payload = {"goal": "fix", "task_id": "hash-http", "mode": "patch", "z": 1}
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        expected = service.mission_store.request_sha256(payload)
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(service.mission_store, "STORE_ROOT", Path(temporary)),
            patch.object(service.mission_store, "_MISSIONS", {}),
            patch.object(service.mission_store, "run_async"),
        ):
            created, create_body = _http_request(
                "POST", "/missions", body=encoded,
                headers={"Content-Type": "application/json"},
            )
            fetched, fetch_body = _http_request("GET", "/missions/hash-http")
            duplicate, duplicate_body = _http_request(
                "POST", "/missions",
                body=json.dumps({"goal": "different", "task_id": "hash-http"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            invalid, _ = _http_request(
                "POST", "/missions", body=b'{"goal":"x","task_id":"../escape"}',
                headers={"Content-Type": "application/json"},
            )
        self.assertEqual(created, 202)
        self.assertEqual(create_body["request_sha256"], expected)
        self.assertEqual(fetched, 200)
        self.assertEqual(fetch_body["request_sha256"], expected)
        self.assertEqual(duplicate, 409)
        self.assertEqual(duplicate_body["request_sha256"], expected)
        self.assertEqual(invalid, 400)

    def test_async_generated_id_is_injected_into_resumable_payload(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(service.mission_store, "STORE_ROOT", Path(temporary)),
            patch.object(service.mission_store, "_MISSIONS", {}),
            patch.object(service.mission_store, "run_async"),
            patch.object(service.time, "time", return_value=1234.567),
        ):
            created, body = _http_request(
                "POST", "/missions", body=b'{"goal":"fix"}',
                headers={"Content-Type": "application/json"},
            )
            mission = service.mission_store.get("m1234567")

        self.assertEqual(created, 202)
        self.assertEqual(body["mission_id"], "m1234567")
        self.assertEqual(mission.payload["task_id"], "m1234567")


class TestMissionLifecycleRaces(unittest.TestCase):
    def test_each_executor_gets_unique_work_and_cache_roots(self):
        created = []

        class FakeVm:
            def __init__(self, host="127.0.0.1", port=2222, user="skitarii", key="", workdir="",
                         mission_marker=None, command_env=None, process_boundary=False,
                         boundary_runtime_sec=7200, boundary_process_baseline=None,
                         boundary_auth_state="", boundary_lease=None,
                         boundary_release_on_cleanup=True):
                self.host, self.port, self.user, self.key = host, port, user, key
                self.workdir = workdir
                self.mission_marker = mission_marker or f"marker-{len(created)}"
                self.command_env = dict(command_env or {})
                self.process_boundary = process_boundary
                self.boundary_runtime_sec = boundary_runtime_sec
                self.boundary_process_baseline = boundary_process_baseline
                self.boundary_auth_state = boundary_auth_state
                self.boundary_lease = boundary_lease
                self.boundary_release_on_cleanup = boundary_release_on_cleanup
                created.append(self)

            def initialize_process_boundary(self, *, strict=False):
                self.boundary_process_baseline = {}
                self.boundary_auth_state = "MISSING"
                self.boundary_lease = object()
                return True

        with patch.object(service, "VmExecutor", FakeVm):
            first = service._mission_executor("same-id")
            second = service._mission_executor("same-id")
        try:
            self.assertNotEqual(first.workdir, second.workdir)
            self.assertNotEqual(first.command_env["XDG_CACHE_HOME"], second.command_env["XDG_CACHE_HOME"])
            self.assertNotEqual(first.command_env["npm_config_cache"], second.command_env["npm_config_cache"])
        finally:
            first._cleanup_finalizer.detach()
            second._cleanup_finalizer.detach()

    def test_cancel_before_executor_attach_never_enters_alive_check(self):
        class Mission:
            cancelled = threading.Event()

        class FakeExecutor:
            def __init__(self):
                self.cancelled = False

            def cancel_current_commands(self):
                self.cancelled = True

            def alive(self):
                raise AssertionError("cancelled mission must not enter the sandbox")

        mission = Mission()
        mission.cancelled.set()
        fake = FakeExecutor()
        with (
            patch.object(service, "_mission_executor", return_value=fake),
            patch.object(service, "_cleanup_workspace_processes") as cleanup,
        ):
            verdict = service.execute_mission(
                {"goal": "x", "task_id": "cancel-before-attach", "checks": [{"cmd": "true"}]},
                mission,
            )
        self.assertEqual(verdict["status"], "cancelled")
        self.assertTrue(fake.cancelled)
        cleanup.assert_called_once_with(fake)

    def test_injected_pipeline_exception_cleans_once_and_clears_matching_attempt(self):
        class Mission:
            executor = None
            executor_attempt = None

        class FakeExecutor:
            process_boundary = True

            def __init__(self):
                self.stops = 0
                self.removes = 0
                self.releases = 0
                self.quarantines = 0

            def stop_process_boundary(self, *, strict=False):
                self.stops += 1
                return True

            def remove_boundary_storage(self, *, strict=False):
                self.removes += 1
                return True

            def release_process_boundary(self, *, strict=False):
                self.releases += 1
                return True

            def quarantine_process_boundary(self):
                self.quarantines += 1

        first = FakeExecutor()
        second = FakeExecutor()
        missions = [Mission(), Mission()]
        executors = iter([first, second])

        def explode(_payload, mission):
            ex = next(executors)
            service._EXECUTION_LOCAL.executor = ex
            with service._MISSION_EXECUTOR_LOCK:
                mission.executor = ex
                mission.executor_attempt = service._EXECUTION_LOCAL.attempt_token
            raise RuntimeError("injected after attach")

        with patch.object(service, "_execute_mission_body", side_effect=explode):
            verdict1 = service.execute_mission({"goal": "one", "task_id": "one"}, missions[0])
            # A second ownership attempt starts only after the first release.
            self.assertEqual(first.releases, 1)
            verdict2 = service.execute_mission({"goal": "two", "task_id": "two"}, missions[1])

        for verdict in (verdict1, verdict2):
            self.assertEqual(verdict["status"], "blocked")
            self.assertIn("injected", verdict["error"])
        for ex in (first, second):
            self.assertEqual(ex.stops, 2)
            self.assertEqual(ex.removes, 1)
            self.assertEqual(ex.releases, 1)
            self.assertEqual(ex.quarantines, 0)
        for mission in missions:
            self.assertIsNone(mission.executor)
            self.assertIsNone(mission.executor_attempt)


class TestBoundaryHelperContract(unittest.TestCase):
    @staticmethod
    def _paths() -> tuple[Path, Path]:
        local_helper = HERE / "skitarii-boundary"
        local_userdata = HERE / "vm-user-data"
        if local_helper.is_file() and local_userdata.is_file():
            return local_helper, local_userdata
        vm_dir = HERE.parents[2] / "CoreOfMadness" / "vm-sandbox"
        return vm_dir / "skitarii-boundary", vm_dir / "user-data"

    def test_embedded_helper_is_syntax_valid_and_hash_exact(self):
        helper, user_data = self._paths()
        self.assertEqual(subprocess.run(["bash", "-n", str(helper)]).returncode, 0)
        lines = user_data.read_text(encoding="utf-8").splitlines()
        start = lines.index("    content: |") + 1
        embedded_lines = []
        for line in lines[start:]:
            if not line.startswith("      "):
                break
            embedded_lines.append(line[6:])
        embedded = ("\n".join(embedded_lines) + "\n").encode("utf-8")
        self.assertEqual(helper.read_bytes(), embedded)
        digest = hashlib.sha256(embedded).hexdigest()
        self.assertEqual(digest, executor.BOUNDARY_HELPER_SHA256)
        self.assertEqual(digest, service.service_identity()["process_boundary_helper_sha256"])

    def test_rejects_traversal_find_injection_and_unit_overrides(self):
        helper, _ = self._paths()

        def rejected(*args: str) -> None:
            result = subprocess.run(["bash", str(helper), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 65, (args, result.stdout, result.stderr))

        rejected("rm", "-rf", "--", "/home/skitarii/work/../escape")
        rejected("find", "/tmp", "-exec", "sh", "-c", "id", ";")
        rejected(
            "find", "/home/skitarii", "-mindepth", "1", "-maxdepth", "1", "!",
            "-name", ".ssh", "!", "-name", "work", "-exec", "rm", "-rf", "--", "{}", "+",
        )
        rejected("systemctl", "stop", "ssh.service")
        rejected("chown", "skitarii:skitarii", "/home/skitarii/.ssh/authorized_keys")
        rejected("chown", "-hR", "skitarii:skitarii", "/home/skitarii")
        rejected("chmod", "0700", "/home/skitarii/.ssh/authorized_keys")
        rejected("systemctl", "mask", "--runtime", "user@0.service")
        rejected(
            "find", "/tmp", "/var/tmp", "/dev/shm", "/run/user/0", "-xdev",
            "-mindepth", "1", "-user", "0", "-exec", "/usr/bin/rm", "-rf", "--", "{}", "+",
        )

        unit = "skitarii-mission-" + ("a" * 24) + "-cmd-" + ("b" * 12)
        base = [
            "systemd-run", "--quiet", "--wait", "--collect", "--pipe", "--service-type=exec",
            f"--unit={unit}",
            "--property=Slice=skitarii-mission-" + ("a" * 24) + ".slice",
            "--property=KillMode=control-group",
            "--property=SendSIGKILL=yes", "--property=TimeoutStopSec=2s",
            "--property=NoNewPrivileges=yes", "--property=RestrictSUIDSGID=yes",
            "--property=ProtectSystem=strict", "--property=ProtectHome=read-only",
            "--property=ReadWritePaths=/home/skitarii/work",
            "--property=InaccessiblePaths=/run/systemd/journal /dev/log",
            "--property=IPAddressDeny=10.0.2.2/32", "--property=MemoryMax=4294967296",
            "--property=TasksMax=512", "--property=CPUQuota=400%",
            "--property=LimitFSIZE=1073741824",
            "--property=LimitCORE=0",
            "--property=BindPaths=/home/skitarii/work/.skitarii-tmp/tmp:/tmp /home/skitarii/work/.skitarii-tmp/var-tmp:/var/tmp /home/skitarii/work/.skitarii-tmp/dev-shm:/dev/shm /home/skitarii/work/.skitarii-tmp/run-user:/run/user",
            "--property=RuntimeMaxSec=60s",
            "--uid=skitarii", "--gid=skitarii", "/bin/bash", "-c", "true",
        ]
        exact_mount = [
            "mount", "-t", "tmpfs", "-o",
            "size=1073741824,nr_inodes=50000,nosuid,nodev,mode=0711,uid=0,gid=0",
            "tmpfs", "/home/skitarii/work",
        ]
        rejected(*[arg.replace("size=1073741824", "size=2147483648") for arg in exact_mount])
        rejected(*base[:-3], "--uid=root", *base[-3:])
        rejected(*base[:-3], "--property=NoNewPrivileges=no", *base[-3:])
        rejected(*[arg.replace("RuntimeMaxSec=60s", "RuntimeMaxSec=0s") for arg in base])
        rejected(*[arg.replace("RuntimeMaxSec=60s", "RuntimeMaxSec=7201s") for arg in base])
        rejected(*[arg.replace("RuntimeMaxSec=60s", "RuntimeMaxSec=999999999999999999999s") for arg in base])
        rejected(*base[:-3], "--property=TasksMax=512", *base[-3:])
        reordered = list(base)
        reordered[7], reordered[8] = reordered[8], reordered[7]
        rejected(*reordered)
        rejected(*[arg.replace("ProtectSystem=strict", "ProtectSystem=full") for arg in base])
        rejected(*[arg.replace("InaccessiblePaths=/run/systemd/journal /dev/log", "InaccessiblePaths=/dev/log") for arg in base])
        rejected(*[arg.replace("LimitCORE=0", "LimitCORE=infinity") for arg in base])
        rejected(*base[:-5], "--property=ReadWritePaths=/var/crash", *base[-5:])

    def test_cloud_init_removes_legacy_all_sudo_and_validates_full_policy(self):
        _, user_data = self._paths()
        text = user_data.read_text(encoding="utf-8")
        self.assertIn("sudo: false", text)
        self.assertIn("90-cloud-init-users", text)
        self.assertIn("visudo -c", text)
        self.assertIn("kernel.core_pattern=|/bin/false", text)
        self.assertIn("fs.suid_dumpable=0", text)
        self.assertIn("systemctl mask apport.service", text)
        self.assertIn("chmod 0711 /home/skitarii/work", text)
        self.assertIn("InaccessiblePaths=/run/systemd/journal /dev/log", text)
        self.assertIn("LimitCORE=0", text)
        self.assertNotIn("NOPASSWD:ALL", text)


class TestHeldOutLifecycle(unittest.TestCase):
    @staticmethod
    def _fighter(goal, ex, **kwargs):
        ex.write_file("app.py", "print('candidate')\n")
        return {
            "status": "done", "accepted": True, "summary": "candidate",
            "artifacts": ["app.py"], "checks": [{"cmd": "python3 app.py", "expect_stdout": "candidate"}],
            "rounds": [],
        }

    def _run(self, hidden_accept, fighter=None, baseline=None, executor_override=None,
             hidden_plan_override=None):
        root = Path(tempfile.mkdtemp(prefix="heldout-lifecycle-"))
        executor = executor_override or AliveLocalExecutor(root)
        hidden_plan = hidden_plan_override or {
            "status": "ok",
            "checks": [{"cmd": "python3 app.py", "expect_stdout": "candidate"}],
            "error": "",
        }
        with (
            patch.dict(os.environ, {"SKITARII_REQUIRE_HELD_OUT": "1"}),
            patch.object(service, "_mission_executor", return_value=executor),
            patch.object(service, "build_held_out_plan", return_value=hidden_plan),
            patch.object(service, "run_mission", side_effect=fighter or self._fighter),
            patch.object(service, "accept", side_effect=hidden_accept),
            patch.object(service, "review", return_value={"approved": True, "issues": []}),
            patch.object(service, "_memory"),
        ):
            return service.execute_mission({
                "goal": "fix app.py", "task_id": "heldout-unit",
                "checks": [{"cmd": "python3 app.py", "expect_stdout": "candidate"}],
                "workspace_files": baseline or {"app.py": "print('baseline')\n"},
            })

    def test_public_replay_recovers_deliverable_only_acceptance_exactly(self):
        verdict = {
            "checks": [],
            "rounds": [{"acceptance": {"results": [
                {"kind": "deliverable", "target": "dist/result.bin", "ok": True},
                {"kind": "deliverable", "target": "dist/result.bin", "ok": True},
            ]}}],
        }
        deliverables, checks = service._public_replay_inputs(verdict)
        self.assertEqual(deliverables, ["dist/result.bin"])
        self.assertEqual(checks, [])

        verdict["rounds"][0]["acceptance"]["results"][0]["target"] = None
        with self.assertRaisesRegex(ValueError, "must be a string"):
            service._public_replay_inputs(verdict)

    def test_hidden_mutation_cannot_change_frozen_files_or_patch(self):
        def mutating_accept(ex, deliverables, checks):
            ex.write_file("app.py", "print('self-fulfilled')\n")
            ex.write_file("hidden-marker.txt", "should never ship\n")
            return {"accepted": True, "results": [{"ok": True}]}

        verdict = self._run(mutating_accept)
        self.assertFalse(verdict["accepted"])
        self.assertEqual(verdict["status"], "blocked")
        self.assertEqual(verdict["held_out_status"], "verifier_infra")
        self.assertEqual(verdict["files"]["app.py"], "print('candidate')\n")
        patch_text = verdict["patch_bundle"]["unified_diff"]
        self.assertIn("print('candidate')", patch_text)
        self.assertNotIn("self-fulfilled", patch_text)
        self.assertNotIn("hidden-marker", patch_text)
        self.assertEqual(verdict["patch_bundle"]["apply_gate"], "blocked")

    def test_explicit_checks_do_not_bypass_passing_hidden_gate(self):
        verdict = self._run(lambda ex, deliverables, checks: {
            "accepted": True, "results": [{"ok": True}],
        })
        self.assertTrue(verdict["accepted"])
        self.assertTrue(verdict["held_out_required"])
        self.assertEqual(verdict["held_out_status"], "passed")
        self.assertEqual(len(verdict["checks"]), 2)
        self.assertEqual(verdict["patch_bundle"]["apply_gate"], "accepted")

    def test_hidden_timeout_is_infrastructure_block_not_candidate_failure(self):
        verdict = self._run(lambda ex, deliverables, checks: {
            "accepted": False,
            "results": [{"ok": False, "exit": 124, "why": "timeout"}],
        })
        self.assertFalse(verdict["accepted"])
        self.assertEqual(verdict["status"], "blocked")
        self.assertEqual(verdict["held_out_failure_class"], "verifier_infra")

    def test_runner_control_candidate_is_blocked_before_private_checks(self):
        hidden = Mock(side_effect=AssertionError("private checks must not execute"))

        def fake_runner(goal, ex, **kwargs):
            ex.write_file("app.py", "print('candidate')\n")
            ex.write_file(".gitignore", "conftest.py\n")
            ex.write_file("conftest.py", "def pytest_configure(config): pass\n")
            return {
                "status": "done", "accepted": True, "summary": "fake runner",
                "artifacts": ["app.py"], "checks": [{"cmd": "python3 app.py"}], "rounds": [],
            }

        verdict = self._run(hidden, fighter=fake_runner)
        self.assertFalse(verdict["accepted"])
        self.assertEqual(verdict["status"], "failed")
        self.assertIn("conftest.py", verdict["runner_control_violation"])
        self.assertEqual(verdict["held_out_status"], "not_run_runner_control_violation")
        hidden.assert_not_called()

    def test_hidden_runs_on_clean_patch_reconstruction_with_ignored_dependency(self):
        def fighter(goal, ex, **kwargs):
            ex.write_file(".gitignore", "secret_impl.py\n")
            ex.write_file("app.py", "from secret_impl import value\nprint(value)\n")
            ex.write_file("secret_impl.py", "value = 'candidate'\n")
            return {
                "status": "done", "accepted": True, "summary": "candidate",
                "artifacts": ["app.py", "secret_impl.py"],
                "checks": [{"cmd": "python3 app.py", "expect_stdout": "candidate"}],
                "rounds": [],
            }

        def hidden(child, deliverables, checks):
            self.assertIn("candidate", child.read_file("secret_impl.py"))
            self.assertEqual(child.bash("python3 app.py")["returncode"], 0)
            return {"accepted": True, "results": [{"ok": True}]}

        verdict = self._run(hidden, fighter=fighter)
        self.assertTrue(verdict["accepted"])
        self.assertIn("secret_impl.py", verdict["patch_bundle"]["changed_files"])

    def test_scrubbed_runtime_cache_dependency_fails_reconstructed_public_replay(self):
        hidden = Mock(side_effect=AssertionError("heldout must not run after public replay fails"))

        def fighter(goal, ex, **kwargs):
            ex.write_file(
                "app.py",
                "from pathlib import Path\nprint(Path('.pytest_cache/answer').read_text().strip())\n",
            )
            ex.write_file(".pytest_cache/answer", "candidate\n")
            return {
                "status": "done", "accepted": True, "summary": "cache-dependent",
                "artifacts": ["app.py"],
                "checks": [{"cmd": "python3 app.py", "expect_stdout": "candidate"}],
                "rounds": [],
            }

        verdict = self._run(hidden, fighter=fighter)

        self.assertFalse(verdict["accepted"])
        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["held_out_status"], "reconstructed_public_failure")
        self.assertFalse(verdict["public_replay_acceptance"]["accepted"])
        hidden.assert_not_called()

    def test_public_verifier_cleanup_failure_returns_blocked_instead_of_escaping(self):
        root = Path(tempfile.mkdtemp(prefix="heldout-cleanup-failure-"))
        primary = AliveLocalExecutor(root)
        original_cleanup = service._cleanup_workspace_processes
        hidden = Mock(side_effect=AssertionError("private checks must not follow failed public cleanup"))
        leaked_children = []
        leaked_processes = []
        failed_child_executors = []

        def failing_child_cleanup(target):
            if target is not primary:
                failed_child_executors.append(target)
                leaked_children.append(Path(target.workdir))
                started = target.bash_background("sleep 600")
                self.assertTrue(started["started"])
                leaked_processes.append(target._background_processes[-1])
                raise RuntimeError("injected verifier cleanup failure")
            return original_cleanup(target)

        with patch.object(
            service, "_cleanup_workspace_processes", side_effect=failing_child_cleanup,
        ):
            verdict = self._run(hidden, executor_override=primary)

        self.assertFalse(verdict["accepted"])
        self.assertEqual(verdict["status"], "blocked")
        self.assertEqual(verdict["held_out_status"], "verifier_infra")
        self.assertIn("injected verifier cleanup failure", verdict["held_out_error"])
        self.assertTrue(leaked_children)
        self.assertTrue(all(not child.exists() for child in leaked_children))
        self.assertTrue(leaked_processes)
        self.assertTrue(all(proc.poll() is not None for proc in leaked_processes))
        self.assertTrue(failed_child_executors)
        self.assertTrue(verdict["cleanup_complete"])
        hidden.assert_not_called()

    def test_private_verifier_cleanup_failure_is_captured_from_finally(self):
        root = Path(tempfile.mkdtemp(prefix="heldout-finally-cleanup-failure-"))
        primary = AliveLocalExecutor(root)
        original_cleanup = service._cleanup_workspace_processes
        child_cleanups = 0

        def fail_second_child_cleanup(target):
            nonlocal child_cleanups
            if target is not primary:
                child_cleanups += 1
                original_cleanup(target)
                if child_cleanups == 2:
                    raise RuntimeError("injected private cleanup failure")
                return None
            return original_cleanup(target)

        with patch.object(
            service, "_cleanup_workspace_processes", side_effect=fail_second_child_cleanup,
        ):
            verdict = self._run(lambda ex, deliverables, checks: {
                "accepted": True, "results": [{"ok": True}],
            }, executor_override=primary)

        self.assertEqual(child_cleanups, 2)
        self.assertFalse(verdict["accepted"])
        self.assertEqual(verdict["status"], "blocked")
        self.assertEqual(verdict["held_out_status"], "verifier_infra")
        self.assertIn("injected private cleanup failure", verdict["held_out_error"])

    def test_escaping_symlink_is_blocked_before_private_checks(self):
        hidden = Mock(side_effect=AssertionError("private checks must not execute"))

        def fighter(goal, ex, **kwargs):
            ex.write_file("app.py", "print('candidate')\n")
            self.assertEqual(ex.bash("ln -s /etc/passwd leaked")["returncode"], 0)
            return {
                "status": "done", "accepted": True, "summary": "candidate",
                "artifacts": ["app.py"], "checks": [{"cmd": "python3 app.py"}], "rounds": [],
            }

        verdict = self._run(hidden, fighter=fighter)
        self.assertFalse(verdict["accepted"])
        self.assertIn("leaked", verdict["workspace_symlink_violation"])
        hidden.assert_not_called()

    def test_baseline_materialization_ignores_export_attributes(self):
        baseline = {
            "app.py": "print('baseline')\n",
            "data.txt": "$Format:%H$\n",
            ".gitattributes": "data.txt export-ignore export-subst\n",
        }

        def hidden(child, deliverables, checks):
            self.assertEqual(child.read_file("data.txt"), "$Format:%H$\n")
            return {"accepted": True, "results": [{"ok": True}]}

        verdict = self._run(hidden, baseline=baseline)
        self.assertTrue(verdict["accepted"], verdict.get("held_out_error"))

    def test_baseline_materialization_preserves_crlf_and_ident_bytes(self):
        exact = b"first\r\n$Id$\r\n"
        baseline = {
            "app.py": "print('baseline')\n",
            "data.txt": exact.decode("ascii"),
            ".gitattributes": "*.txt text eol=lf ident\n",
        }

        def hidden(child, deliverables, checks):
            self.assertEqual(child.fetch_artifact("data.txt"), exact)
            return {"accepted": True, "results": [{"ok": True}]}

        verdict = self._run(hidden, baseline=baseline)
        self.assertTrue(verdict["accepted"], verdict.get("held_out_error"))

    def test_service_rejects_bare_npm_and_mutable_test_runner_evidence(self):
        hidden = Mock(side_effect=AssertionError("bare private runner must not execute"))
        fighter = Mock(side_effect=AssertionError("invalid private plan must stop before fighter"))
        plan = {
            "status": "ok",
            "checks": [
                {"cmd": "npm test"},
                {"cmd": "python3 -m unittest"},
            ],
            "error": "",
        }

        verdict = self._run(
            hidden, fighter=fighter, hidden_plan_override=plan,
        )
        self.assertFalse(verdict["accepted"])
        self.assertEqual(verdict["status"], "blocked")
        self.assertEqual(verdict["held_out_status"], "invalid_evidence")
        self.assertIn("immutable output evidence", verdict["held_out_error"])
        fighter.assert_not_called()
        hidden.assert_not_called()

    def test_empty_oracle_comparison_is_verifier_infrastructure_failure(self):
        plan = {
            "status": "ok",
            "checks": [{
                "cmd": "python3 app.py silent",
                "oracle": "python3 -c 'print(str())'",
            }],
            "error": "",
        }
        hidden = Mock(return_value={
            "accepted": True,
            "results": [{"ok": True, "stdout": "", "expected": ""}],
        })

        verdict = self._run(hidden, hidden_plan_override=plan)

        self.assertFalse(verdict["accepted"])
        self.assertEqual(verdict["status"], "blocked")
        self.assertEqual(verdict["held_out_status"], "verifier_infra")
        self.assertIn("empty comparable evidence", verdict["held_out_error"])

    def test_private_oracle_uses_isolated_stdlib_despite_candidate_math_module(self):
        plan = {
            "status": "ok",
            "checks": [{
                "cmd": "python3 -c 'print(3.0)'",
                "oracle": "python3 -c 'import math; print(math.sqrt(9))'",
            }],
            "error": "",
        }

        def fighter(goal, ex, **kwargs):
            ex.write_file("app.py", "print('candidate')\n")
            ex.write_file("math.py", "def sqrt(value): return 999\n")
            return {
                "status": "done", "accepted": True, "summary": "candidate",
                "artifacts": ["app.py"],
                "checks": [{"cmd": "python3 app.py", "expect_stdout": "candidate"}],
                "rounds": [],
            }

        def hidden(child, deliverables, checks):
            oracle = checks[0]["oracle"]
            self.assertTrue(oracle.startswith("/usr/bin/python3 -I -S -c "))
            result = child.bash(oracle)
            self.assertEqual(result["returncode"], 0, result["stderr"])
            self.assertEqual(result["stdout"].strip(), "3.0")
            return {
                "accepted": True,
                "results": [{"ok": True, "stdout": "3.0", "expected": "3.0"}],
            }

        verdict = self._run(
            hidden, fighter=fighter, hidden_plan_override=plan,
        )
        self.assertTrue(verdict["accepted"], verdict.get("held_out_error"))

    def test_service_accepts_spec_precanonicalized_private_oracle_idempotently(self):
        generated = {"checks": [{
                "cmd": "python3 app.py",
                "oracle": "python3 -c 'import math; print(math.sqrt(9))'",
            }]}
        with patch.object(spec, "_held_out_chat_json", return_value=generated):
            plan = spec.build_held_out_plan("fix app.py")
        checks = plan["checks"]
        self.assertEqual(len(checks), 1)
        self.assertTrue(checks[0]["oracle"].startswith("/usr/bin/python3 -I -S -c "))

        def hidden(child, deliverables, received_checks):
            self.assertEqual(received_checks[0]["oracle"], checks[0]["oracle"])
            return {
                "accepted": True,
                "results": [{"ok": True, "stdout": "3.0", "expected": "3.0"}],
            }

        verdict = self._run(
            hidden,
            hidden_plan_override=plan,
        )
        self.assertTrue(verdict["accepted"], verdict.get("held_out_error"))

    def test_private_stage_scrubs_external_marker_and_preserves_opaque_cache(self):
        marker = Path(tempfile.mkdtemp(prefix="heldout-marker-")) / "fighter-marker"
        marker.write_text("poison", encoding="utf-8")
        root = Path(tempfile.mkdtemp(prefix="heldout-temp-aware-"))

        class TempAwareExecutor(AliveLocalExecutor):
            process_boundary = True

            def stop_process_boundary(self, *, strict=False):
                return True

            def scrub_boundary_temp(self, *, strict=False):
                marker.unlink(missing_ok=True)
                return True

        executor = TempAwareExecutor(
            root,
            command_env={
                "XDG_CACHE_HOME": "/tmp/skitarii-cache-primary/xdg",
                "npm_config_cache": "/tmp/skitarii-cache-primary/npm",
            },
        )

        def hidden(child, deliverables, checks):
            self.assertFalse(marker.exists())
            self.assertEqual(
                child.command_env["XDG_CACHE_HOME"],
                executor.command_env["XDG_CACHE_HOME"],
            )
            self.assertRegex(Path(child.workdir).name, r"^mission-[0-9a-f]{16}$")
            self.assertNotIn("heldout", str(child.workdir))
            self.assertNotIn("_wt_", str(child.workdir))
            return {"accepted": True, "results": [{"ok": True}]}

        verdict = self._run(hidden, executor_override=executor)
        self.assertTrue(verdict["accepted"], verdict.get("held_out_error"))


if __name__ == "__main__":
    unittest.main()
