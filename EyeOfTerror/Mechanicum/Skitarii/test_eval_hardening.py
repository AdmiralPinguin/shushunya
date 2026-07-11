"""Narrow regression tests for the capability-smoke runner's trust boundary."""
from __future__ import annotations

import contextlib
import copy
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import eval_suite


class TestEvalHardening(unittest.TestCase):
    @staticmethod
    def _bundle(seed: dict[str, str], mutate) -> dict:
        with tempfile.TemporaryDirectory(prefix="eval-bundle-") as temp_dir:
            root = Path(temp_dir)
            for rel, content in seed.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", "-f", "-A"], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@invalid", "-c", "user.name=t",
                 "commit", "--allow-empty", "-qm", "seed"],
                cwd=root, check=True,
            )
            mutate(root)
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            diff = subprocess.run(
                ["git", "diff", "--cached", "--binary", "--full-index", "HEAD", "--", "."],
                cwd=root, check=True, capture_output=True, text=True,
            ).stdout
            raw_names = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "-z", "HEAD", "--", "."],
                cwd=root, check=True, capture_output=True,
            ).stdout
            return {
                "unified_diff": diff,
                "changed_files": [part.decode() for part in raw_names.split(b"\0") if part],
            }

    def test_private_oracle_is_not_sent_to_service(self):
        task = {
            "id": "private-check",
            "category": "greenfield",
            "goal": "write result.py",
            "oracle_checks": [{"cmd": "python3 result.py", "expect_stdout": "secret"}],
        }
        seen = {}

        def fake_post(path, payload, timeout=1800):
            seen.update(payload)
            return {"accepted": False, "files": {}}

        with (
            mock.patch.object(eval_suite, "_post", side_effect=fake_post),
            mock.patch.object(eval_suite, "_independent_verify", return_value=(False, "bad")),
        ):
            eval_suite._run_checked(task)

        self.assertNotIn("checks", seen)
        self.assertNotIn("oracle_checks", seen)
        self.assertNotIn("held_out_checks", seen)
        self.assertNotIn("secret", repr(seen))
        self.assertIs(seen["standalone_test"], True)

    def test_seed_tests_and_forbid_touch_are_immutable(self):
        task = {
            "seed": {
                "app.py": "print('old')\n",
                "test_app.py": "from app import *\n",
                "settings.ini": "safe=true\n",
            },
            "forbid_touch": ["settings.ini"],
        }
        workspace, error = eval_suite._verification_workspace(
            task, {"app.py": "print('new')\n", "test_app.py": "pass\n"},
        )
        self.assertEqual(workspace, {})
        self.assertEqual(error, "protected fixture changed: test_app.py")

        workspace, error = eval_suite._verification_workspace(
            task, {"test_app.py": task["seed"]["test_app.py"], "settings.ini": "safe=false\n"},
        )
        self.assertEqual(workspace, {})
        self.assertEqual(error, "protected fixture changed: settings.ini")

        workspace, error = eval_suite._verification_workspace(
            task, {"app.py": "print('fixed')\n"}, ["test_app.py"],
        )
        self.assertEqual(workspace, {})
        self.assertEqual(error, "protected fixture touched: test_app.py")

    def test_protected_fixture_change_is_a_real_candidate_failure(self):
        seed = {"test_app.py": "print('trusted')\n"}
        bundle = self._bundle(
            seed, lambda root: (root / "test_app.py").write_text("print('forged')\n", encoding="utf-8"),
        )
        task = {
            "seed": seed,
            "oracle_checks": [{"cmd": "python3 test_app.py", "expect_stdout": "trusted"}],
        }
        passed, detail = eval_suite._independent_verify(
            task, {}, bundle,
        )
        self.assertIs(passed, False)
        self.assertIn("protected fixture touched", detail)

    def test_patch_task_uses_bundle_not_returned_files(self):
        task = {
            "seed": {"app.py": "print('bug')\n"},
            "oracle_checks": [{"cmd": "python3 app.py", "expect_stdout": "fixed"}],
        }
        passed, detail = eval_suite._independent_verify(
            task,
            {"app.py": "print('fixed')\n"},
            {"unified_diff": "", "changed_files": []},
        )
        self.assertIs(passed, False)
        self.assertIn("disagree", detail)

    def test_greenfield_also_uses_the_real_patch_bundle(self):
        task = {
            "oracle_checks": [{"cmd": "python3 app.py", "expect_stdout": "fixed"}],
        }
        passed, detail = eval_suite._independent_verify(
            task,
            {"app.py": "print('fixed')\n"},
            {"unified_diff": "", "changed_files": []},
        )
        self.assertIs(passed, False)
        self.assertIn("does not match", detail)

        bundle = self._bundle(
            {}, lambda root: (root / "app.py").write_text("print('fixed')\n", encoding="utf-8"),
        )
        passed, detail = eval_suite._independent_verify(
            task, {"app.py": "print('fixed')\n"}, bundle,
        )
        self.assertIs(passed, True, detail)

    def test_patch_manifest_must_match_applied_diff(self):
        seed = {"app.py": "print('bug')\n"}
        bundle = self._bundle(
            seed, lambda root: (root / "app.py").write_text("print('fixed')\n", encoding="utf-8"),
        )
        bundle["changed_files"] = []
        passed, detail = eval_suite._independent_verify(
            {"seed": seed, "oracle_checks": [{"cmd": "python3 app.py", "expect_stdout": "fixed"}]},
            {}, bundle,
        )
        self.assertIs(passed, False)
        self.assertIn("manifest", detail)

    def test_deletion_patch_is_the_oracle_tree(self):
        seed = {"app.py": "print('ok')\n", "obsolete.txt": "remove\n"}
        bundle = self._bundle(seed, lambda root: (root / "obsolete.txt").unlink())
        passed, detail = eval_suite._independent_verify(
            {
                "seed": seed,
                "oracle_checks": [{
                    "cmd": "python3 -c \"from pathlib import Path; print(Path('obsolete.txt').exists())\"",
                    "expect_stdout": "False",
                }],
            },
            {}, bundle,
        )
        self.assertIs(passed, True, detail)

    def test_binary_patch_is_verified_without_returned_text_files(self):
        seed = {"app.py": "print('ok')\n"}
        bundle = self._bundle(seed, lambda root: (root / "asset.bin").write_bytes(b"\x00\xffexact"))
        passed, detail = eval_suite._independent_verify(
            {
                "seed": seed,
                "oracle_checks": [{
                    "cmd": "python3 -c \"print(open('asset.bin','rb').read().hex())\"",
                    "expect_stdout": "00ff6578616374",
                }],
            },
            {}, bundle,
        )
        self.assertIs(passed, True, detail)

    def test_compressed_binary_literal_is_rejected_before_host_git(self):
        bundle = self._bundle(
            {}, lambda root: (root / "compressed.bin").write_bytes(b"\0" * 4096),
        )
        self.assertLess(len(bundle["unified_diff"].encode("utf-8")), 4096)
        with tempfile.TemporaryDirectory(prefix="eval-bounded-") as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(eval_suite, "MAX_PATCH_FILE_BYTES", 1024):
                passed, detail = eval_suite._materialize_patch_candidate(
                    {}, {}, bundle, root,
                )
            self.assertIs(passed, False)
            self.assertIn("binary literal exceeds 1024 bytes", detail)
            self.assertFalse((root / ".git").exists(), "git must not run before bounds validation")

    def test_patch_declarations_have_hard_resource_bounds(self):
        cases = (
            (
                "input",
                {"MAX_PATCH_INPUT_BYTES": 3},
                "1234",
                "patch input exceeds 3 bytes",
            ),
            (
                "file count",
                {"MAX_PATCH_FILES": 1},
                "diff --git a/a b/a\ndiff --git a/b b/b\n",
                "patch touches more than 1 files",
            ),
            (
                "one literal",
                {"MAX_PATCH_FILE_BYTES": 4},
                "literal 5\n",
                "git binary literal exceeds 4 bytes",
            ),
            (
                "expanded total",
                {"MAX_PATCH_FILE_BYTES": 10, "MAX_PATCH_EXPANDED_BYTES": 8},
                "literal 5\nliteral 5\n",
                "git binary literals exceed 8 expanded bytes",
            ),
        )
        for name, limits, patch_text, expected in cases:
            with self.subTest(name=name):
                patches = [mock.patch.object(eval_suite, key, value) for key, value in limits.items()]
                for patcher in patches:
                    patcher.start()
                try:
                    valid, detail = eval_suite._validate_patch_resource_bounds(patch_text)
                finally:
                    for patcher in reversed(patches):
                        patcher.stop()
                self.assertFalse(valid)
                self.assertEqual(detail, expected)

    def test_binary_delta_and_submodule_patches_are_rejected(self):
        for name, patch_text, expected in (
            (
                "delta",
                "diff --git a/a.bin b/a.bin\nGIT binary patch\ndelta 12\nabc\n",
                "git binary delta patches are unsupported",
            ),
            (
                "new gitlink",
                "diff --git a/vendor b/vendor\nnew file mode 160000\n",
                "git submodule entries are unsupported",
            ),
            (
                "existing gitlink",
                "diff --git a/vendor b/vendor\nindex 1234567..89abcde 160000\n",
                "git submodule entries are unsupported",
            ),
        ):
            with self.subTest(name=name):
                valid, detail = eval_suite._validate_patch_resource_bounds(patch_text)
                self.assertFalse(valid)
                self.assertEqual(detail, expected)

    def test_copy_and_normalized_octal_gitlink_modes_are_rejected(self):
        for name, patch_text, expected in (
            (
                "copy",
                "diff --git a/source b/copied\nsimilarity index 100%\n"
                "copy from source\ncopy to copied\n",
                "git copy patches are unsupported",
            ),
            (
                "zero-padded gitlink with whitespace",
                "diff --git a/vendor b/vendor\nnew file mode 0160000 \n",
                "git submodule entries are unsupported",
            ),
            (
                "zero-padded index gitlink with tab",
                "diff --git a/vendor b/vendor\nindex 1234567..89abcde 0160000\t\n",
                "git submodule entries are unsupported",
            ),
        ):
            with self.subTest(name=name):
                valid, detail = eval_suite._validate_patch_resource_bounds(patch_text)
                self.assertFalse(valid)
                self.assertEqual(detail, expected)

    def test_runner_control_packages_and_bytecode_are_forbidden(self):
        for rel in (
            "sitecustomize/__init__.py",
            "sitecustomize.pyc",
            "__pycache__/sitecustomize.cpython-311.pyc",
            "__pycache__/trusted_seed.cpython-311.pyc",
            "nested/usercustomize/__init__.py",
            "conftest/__init__.py",
            "runpy.py",
            "unittest/__init__.py",
        ):
            with self.subTest(rel=rel):
                self.assertTrue(eval_suite._is_runner_control_path(rel))

    def test_materialized_workspace_rejects_symlink_to_directory(self):
        with tempfile.TemporaryDirectory(prefix="eval-symlink-") as temp_dir:
            root = Path(temp_dir)
            target = root / "real-package"
            target.mkdir()
            (target / "module.py").write_text("safe = True\n", encoding="utf-8")
            link = root / "linked-package"
            try:
                os.symlink(target, link, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            payload, detail = eval_suite._workspace_payload_from_dir(root)
        self.assertIsNone(payload)
        self.assertIn("unsupported symlink: linked-package", detail)

    def test_quoted_pytest_config_and_its_removal_are_forbidden(self):
        config = '[tool."pytest".ini_options]\naddopts = "-p no:terminal"\n'
        self.assertTrue(eval_suite._runner_control_config_text("pyproject.toml", config))
        self.assertTrue(
            eval_suite._runner_control_config_text(
                "setup.cfg", "[tool:pytest]\naddopts = -p no:terminal\n",
            ),
        )
        with tempfile.TemporaryDirectory(prefix="eval-quoted-config-") as temp_dir:
            root = Path(temp_dir)
            (root / "pyproject.toml").write_text(config, encoding="utf-8")
            payload, config_detail = eval_suite._workspace_payload_from_dir(root)
        self.assertIsNone(payload)
        self.assertEqual(
            config_detail,
            "verifier workspace contains forbidden pytest config: pyproject.toml",
        )

        seed = {"pyproject.toml": config, "app.py": "print('ok')\n"}
        bundle = self._bundle(seed, lambda root: (root / "pyproject.toml").unlink())
        with tempfile.TemporaryDirectory(prefix="eval-config-delete-") as temp_dir:
            passed, detail = eval_suite._materialize_patch_candidate(
                {"seed": seed}, {}, bundle, Path(temp_dir),
            )
        self.assertIs(passed, False)
        self.assertEqual(detail, "patch changes existing pytest configuration: pyproject.toml")

    def test_namespace_package_and_runpy_shadowing_are_forbidden(self):
        seed = {"trusted.py": "VALUE = 1\n"}
        namespace_bundle = self._bundle(
            seed,
            lambda root: (
                (root / "trusted").mkdir(),
                (root / "trusted" / "__init__.py").write_text("VALUE = 99\n", encoding="utf-8"),
            ),
        )
        with tempfile.TemporaryDirectory(prefix="eval-namespace-") as temp_dir:
            passed, detail = eval_suite._materialize_patch_candidate(
                {"seed": seed}, {}, namespace_bundle, Path(temp_dir),
            )
        self.assertIs(passed, False)
        self.assertIn("shadows trusted seed module namespace", detail)

        runpy_bundle = self._bundle(
            seed, lambda root: (root / "runpy.py").write_text("def run_path(*a): return {}\n", encoding="utf-8"),
        )
        with tempfile.TemporaryDirectory(prefix="eval-runpy-") as temp_dir:
            passed, detail = eval_suite._materialize_patch_candidate(
                {"seed": seed}, {}, runpy_bundle, Path(temp_dir),
            )
        self.assertIs(passed, False)
        self.assertEqual(detail, "patch contains forbidden runner control: runpy.py")

    def test_traditional_diff_cannot_bypass_git_file_counting(self):
        valid, detail = eval_suite._validate_patch_resource_bounds(
            "--- a/hidden.txt\n+++ b/hidden.txt\n@@ -0,0 +1 @@\n+payload\n",
        )
        self.assertFalse(valid)
        self.assertEqual(detail, "patch contains a non-git or duplicate file section")

    def test_hunk_content_that_looks_like_headers_remains_valid(self):
        bundle = self._bundle(
            {"markers.txt": "-- old\n"},
            lambda root: (root / "markers.txt").write_text("++ new\n", encoding="utf-8"),
        )
        valid, detail = eval_suite._validate_patch_resource_bounds(bundle["unified_diff"])
        self.assertTrue(valid, detail)

    def test_materialized_workspace_enforces_file_count_limit(self):
        with tempfile.TemporaryDirectory(prefix="eval-workspace-") as temp_dir:
            root = Path(temp_dir)
            (root / "one.txt").write_text("1", encoding="utf-8")
            (root / "two.txt").write_text("2", encoding="utf-8")
            with mock.patch.object(eval_suite, "MAX_WORKSPACE_FILES", 1):
                payload, detail = eval_suite._workspace_payload_from_dir(root)
        self.assertIsNone(payload)
        self.assertEqual(detail, "verifier workspace exceeds 1 files")

    def test_materialized_workspace_rejects_oversize_file_before_read(self):
        with tempfile.TemporaryDirectory(prefix="eval-workspace-") as temp_dir:
            root = Path(temp_dir)
            (root / "large.bin").write_bytes(b"12345")
            with (
                mock.patch.object(eval_suite, "MAX_WORKSPACE_FILE_BYTES", 4),
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("must not read")),
            ):
                payload, detail = eval_suite._workspace_payload_from_dir(root)
        self.assertIsNone(payload)
        self.assertEqual(
            detail, "verifier workspace file exceeds 4 bytes: large.bin",
        )

    def test_materialized_workspace_enforces_total_size_limit(self):
        with tempfile.TemporaryDirectory(prefix="eval-workspace-") as temp_dir:
            root = Path(temp_dir)
            (root / "one.bin").write_bytes(b"1234")
            (root / "two.bin").write_bytes(b"5678")
            with mock.patch.object(eval_suite, "MAX_WORKSPACE_TOTAL_BYTES", 7):
                payload, detail = eval_suite._workspace_payload_from_dir(root)
        self.assertIsNone(payload)
        self.assertEqual(detail, "verifier workspace exceeds 7 total bytes")

    def test_verifier_infrastructure_exception_is_unverified(self):
        task = {
            "oracle_checks": [{"cmd": "python3 -c 'print(1)'", "expect_stdout": "1"}],
        }
        with mock.patch.object(subprocess, "run", side_effect=OSError("runner unavailable")):
            passed, detail = eval_suite._independent_verify(
                task, {}, {"unified_diff": "", "changed_files": []},
            )
        self.assertIsNone(passed)
        self.assertIn("infrastructure error", detail)

    def test_unavailable_vm_workspace_is_unverified(self):
        executor = mock.Mock()
        executor.initialize_process_boundary.return_value = True
        executor.alive.return_value = False
        executor.bash.return_value = {"returncode": 0, "stdout": "", "stderr": ""}
        with mock.patch("executor.VmExecutor", return_value=executor):
            passed, detail = eval_suite._independent_verify_in_isolated_vm_workspace(
                {"oracle_checks": [{"cmd": "php app.php"}]}, {},
            )
        self.assertIsNone(passed)
        self.assertIn("unavailable", detail)

    def test_every_check_and_reference_oracle_gets_a_fresh_bounded_lifecycle(self):
        executors = []
        constructor_calls = []

        def make_executor(*args, **kwargs):
            constructor_calls.append(kwargs)
            ex = mock.Mock()
            ex.initialize_process_boundary.return_value = True
            ex.alive.return_value = True

            def run(command, timeout=60):
                if "candidate" in command or "reference" in command:
                    stdout = "same\n"
                elif "second" in command:
                    stdout = "second\n"
                else:
                    stdout = ""
                return {"returncode": 0, "stdout": stdout, "stderr": ""}

            ex.bash.side_effect = run
            executors.append(ex)
            return ex

        task = {
            "oracle_checks": [
                {
                    "cmd": "cd /; unset SKITARII_MISSION_MARKER; printf candidate",
                    "oracle": "printf reference",
                },
                {"cmd": "printf second", "expect_stdout": "second"},
            ],
        }
        workspace = {"files": {"app.py": "print('ok')\n"}, "blobs": {}, "modes": {}}
        with mock.patch("executor.VmExecutor", side_effect=make_executor):
            passed, detail = eval_suite._independent_verify_in_isolated_vm_workspace(
                task, workspace,
            )

        self.assertIs(passed, True, detail)
        self.assertEqual(len(executors), 3, "two checks plus one oracle need three lifecycles")
        self.assertEqual(len({call["workdir"] for call in constructor_calls}), 3)
        self.assertEqual(
            len({call["command_env"]["XDG_CACHE_HOME"] for call in constructor_calls}), 3,
        )
        self.assertTrue(all(call["process_boundary"] is True for call in constructor_calls))
        for ex in executors:
            self.assertEqual(ex.stop_process_boundary.call_count, 2)
            ex.remove_boundary_storage.assert_called_once_with(strict=True)
            ex.release_process_boundary.assert_called_once_with(strict=True)
            ex.quarantine_process_boundary.assert_not_called()

    def test_strict_cleanup_failure_is_unverified_and_quarantines_boundary(self):
        executor = mock.Mock()
        executor.initialize_process_boundary.return_value = True
        executor.alive.return_value = True
        executor.bash.return_value = {"returncode": 0, "stdout": "ok\n", "stderr": ""}
        executor.stop_process_boundary.side_effect = RuntimeError("uid process survived")
        with mock.patch("executor.VmExecutor", return_value=executor):
            passed, detail = eval_suite._independent_verify_in_isolated_vm_workspace(
                {"oracle_checks": [{"cmd": "cd /; unset SKITARII_MISSION_MARKER; true"}]},
                {"files": {}, "blobs": {}, "modes": {}},
            )
        self.assertIsNone(passed)
        self.assertIn("strict cleanup failed", detail)
        executor.quarantine_process_boundary.assert_called_once_with()

    def test_unexpected_verifier_crash_is_an_explicit_error(self):
        task = {
            "id": "verify-crash",
            "category": "fix_one",
            "goal": "fix it",
            "oracle_checks": [{"cmd": "true"}],
        }
        with (
            mock.patch.object(
                eval_suite, "_post", return_value={"accepted": True, "files": {}},
            ),
            mock.patch.object(
                eval_suite, "_independent_verify", side_effect=RuntimeError("boom"),
            ),
        ):
            row = eval_suite._run_checked(task)
        self.assertEqual(row["verdict"], "verification_error")
        self.assertEqual(row["errored"], 1)
        self.assertNotIn("false_accepted", row)

    def test_results_are_labelled_as_smoke(self):
        result = eval_suite.run_eval([])
        self.assertEqual(result["suite_kind"], "capability_smoke")
        self.assertFalse(result["complete_suite"])

    def test_eval_client_sends_configured_service_bearer(self):
        seen = []

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, *_args): return b'{"ok": true}'

        def open_request(request, **_kwargs):
            seen.append(request.get_header("Authorization"))
            return Response()

        with (
            mock.patch.dict(os.environ, {"SKITARII_BEARER_TOKEN": "eval-secret"}),
            mock.patch.object(eval_suite.urllib.request, "urlopen", side_effect=open_request),
        ):
            self.assertTrue(eval_suite._post("/mission", {})["ok"])
            self.assertTrue(eval_suite._get("/health")["ok"])
        self.assertEqual(seen, ["Bearer eval-secret", "Bearer eval-secret"])

    def test_run_valid_requires_exact_live_identity_and_health_gates(self):
        tasks = [
            {"id": "checked", "category": "greenfield", "goal": "do it", "oracle_checks": [{}]},
            {
                "id": "ambiguous", "category": "ambiguous", "goal": "ask",
                "oracle_checks": [], "expects_clarification": True,
            },
        ]
        expected_source = "a" * 64
        models = {
            role: {"model": f"{role}-model", "base_url": f"http://127.0.0.1/{role}"}
            for role in ("planner", "reviewer", "spec", "fighter", "held_out")
        }
        identity = {
            "source_sha256": expected_source,
            "instance_id": "daemon-instance",
            "started_at": 123456,
            "held_out_required": True,
            "execution_authorization": {
                "ceraxia_leadership_directive_required": True,
                "standalone_test_mode_enabled": True,
                "standalone_test_payload_flag_required": True,
            },
            "models": models,
        }
        checked_row = {
            "id": "checked", "cat": "greenfield", "verdict": "correct",
            "accepted": 1, "correct": 1, "held_out_required": True,
            "held_out_check_count": 2, "held_out_status": "passed",
        }
        ambiguous_row = {
            "id": "ambiguous", "cat": "ambiguous", "verdict": "asked_clarification",
            "asked_clarification": 1,
        }

        def evaluate(mutator=None, row_mutator=None):
            start = {
                "status": "ok", "service": "Skitarii", "vm_alive": True,
                "process_boundary_ready": True,
                "identity": copy.deepcopy(identity),
            }
            end = copy.deepcopy(start)
            if mutator:
                mutator(start, end)
            row = copy.deepcopy(checked_row)
            if row_mutator:
                row_mutator(row)
            with (
                mock.patch.object(eval_suite, "TASKS", tasks),
                mock.patch.object(
                    eval_suite, "evaluated_source_identity",
                    return_value={"service_source_sha256": expected_source},
                ),
                mock.patch.object(eval_suite, "_get", side_effect=[start, end]),
                mock.patch.object(eval_suite, "_run_checked", return_value=row),
                mock.patch.object(eval_suite, "_run_ambiguous", return_value=ambiguous_row),
                mock.patch.object(eval_suite.time, "time", side_effect=range(1000, 1100)),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                return eval_suite.run_eval(tasks)

        valid = evaluate()
        self.assertTrue(valid["run_valid"], valid["validation"])
        self.assertTrue(valid["validation"]["healthy_skitarii_endpoints"])

        cases = {
            "source drift": lambda start, end: start["identity"].update(source_sha256="b" * 64),
            "missing instance": lambda start, end: start["identity"].update(instance_id=""),
            "daemon restart": lambda start, end: end["identity"].update(instance_id="other"),
            "missing model": lambda start, end: start["identity"]["models"]["held_out"].update(model=""),
            "standalone mode disabled": lambda start, end: (
                start["identity"]["execution_authorization"].update(
                    standalone_test_mode_enabled=False,
                ),
                end["identity"]["execution_authorization"].update(
                    standalone_test_mode_enabled=False,
                ),
            ),
            "wrong service": lambda start, end: start.update(service="NotSkitarii"),
            "vm unavailable": lambda start, end: end.update(vm_alive=False),
            "boundary unavailable": lambda start, end: start.update(process_boundary_ready=False),
        }
        for name, mutator in cases.items():
            with self.subTest(name=name):
                self.assertFalse(evaluate(mutator)["run_valid"])
        self.assertFalse(
            evaluate(row_mutator=lambda row: row.update(held_out_check_count=0))["run_valid"],
        )
        self.assertFalse(
            evaluate(row_mutator=lambda row: row.update(held_out_status="candidate_failure"))["run_valid"],
        )

    def test_accepted_candidate_requires_passed_held_out_gate(self):
        task = {
            "id": "gate-contradiction", "category": "greenfield", "goal": "do it",
            "oracle_checks": [{"cmd": "true"}],
        }
        service_verdict = {
            "accepted": True,
            "held_out_required": True,
            "held_out_check_count": 1,
            "held_out_status": "candidate_failure",
            "files": {},
            "patch_bundle": {},
        }
        with (
            mock.patch.object(eval_suite, "_post", return_value=service_verdict),
            mock.patch.object(eval_suite, "_independent_verify", return_value=(True, "works")),
        ):
            row = eval_suite._run_checked(task)
        self.assertEqual(row["verdict"], "accepted_without_held_out_gate")
        self.assertEqual(row["unverified"], 1)
        self.assertNotIn("correct", row)

    def test_ambiguous_eval_uses_a_fresh_service_task_id_each_time(self):
        task = {
            "id": "ambiguous-repeat", "category": "ambiguous",
            "goal": "Add data import support.", "expects_clarification": True,
        }
        submitted_ids = []
        get_count = 0

        def post(path, payload):
            if path == "/missions":
                submitted_ids.append(payload["task_id"])
                return {"mission_id": payload["task_id"]}
            return {"ok": True, "status": "cancelled"}

        def get(_path):
            nonlocal get_count
            get_count += 1
            if get_count % 2:
                return {
                    "status": "needs_user",
                    "question": "Which import format and source should be supported?",
                }
            return {
                "status": "cancelled", "inflight": False,
                "cleanup_complete": True, "result": {"accepted": False},
            }

        with (
            mock.patch.object(eval_suite, "_post", side_effect=post),
            mock.patch.object(eval_suite, "_get", side_effect=get),
        ):
            first = eval_suite._run_ambiguous(task)
            second = eval_suite._run_ambiguous(task)
        self.assertEqual(first["verdict"], "asked_clarification")
        self.assertEqual(second["verdict"], "asked_clarification")
        self.assertEqual(len(set(submitted_ids)), 2)
        self.assertEqual(first["service_task_id"], submitted_ids[0])
        self.assertEqual(second["service_task_id"], submitted_ids[1])
        self.assertTrue(first["cleanup_proven"])
        self.assertTrue(second["cleanup_proven"])

    def test_ambiguous_eval_detects_nested_terminal_acceptance(self):
        task = {
            "id": "ambiguous-contradiction", "category": "ambiguous",
            "goal": "Add data import support.", "expects_clarification": True,
        }

        def post(path, payload):
            if path == "/missions":
                return {"mission_id": payload["task_id"]}
            return {"ok": True}

        with (
            mock.patch.object(eval_suite, "_post", side_effect=post),
            mock.patch.object(
                eval_suite, "_get",
                return_value={
                    "status": "failed", "inflight": False,
                    "cleanup_complete": True, "result": {"accepted": True},
                },
            ),
        ):
            row = eval_suite._run_ambiguous(task)
        self.assertEqual(row["verdict"], "FALSE_ACCEPT")
        self.assertEqual(row["false_accepted"], 1)
        self.assertTrue(row["cleanup_proven"])

    def test_ambiguous_eval_waits_for_terminal_cleanup_proof(self):
        task = {
            "id": "ambiguous-cleanup", "category": "ambiguous",
            "goal": "Add data import support.", "expects_clarification": True,
        }
        snapshots = iter([
            {
                "status": "needs_user",
                "question": "Which import format and source should be supported?",
            },
            {"status": "cancelling", "inflight": True, "cleanup_complete": False},
            {
                "status": "cancelled", "inflight": False,
                "cleanup_complete": True, "result": {"accepted": False},
            },
        ])
        posted = []

        def post(path, payload):
            posted.append(path)
            if path == "/missions":
                return {"mission_id": payload["task_id"]}
            return {"ok": True, "status": "cancelling"}

        with (
            mock.patch.object(eval_suite, "_post", side_effect=post),
            mock.patch.object(eval_suite, "_get", side_effect=lambda _path: next(snapshots)),
            mock.patch.object(eval_suite.time, "sleep", return_value=None),
        ):
            row = eval_suite._run_ambiguous(task)
        self.assertEqual(row["verdict"], "asked_clarification")
        self.assertTrue(row["cleanup_proven"])
        self.assertEqual(row["cleanup_status"], "cancelled")
        self.assertEqual(len([path for path in posted if path.endswith("/cancel")]), 1)

    def test_ambiguous_eval_cleanup_failure_is_unverified(self):
        task = {
            "id": "ambiguous-cleanup-failed", "category": "ambiguous",
            "goal": "Add data import support.", "expects_clarification": True,
        }
        snapshots = iter([
            {
                "status": "needs_user",
                "question": "Which import format and source should be supported?",
            },
            {
                "status": "blocked", "inflight": False, "cleanup_complete": False,
                "cleanup_error": "boundary storage removal failed",
                "result": {"accepted": False},
            },
        ])

        def post(path, payload):
            if path == "/missions":
                return {"mission_id": payload["task_id"]}
            return {"ok": True, "status": "cancelling"}

        with (
            mock.patch.object(eval_suite, "_post", side_effect=post),
            mock.patch.object(eval_suite, "_get", side_effect=lambda _path: next(snapshots)),
        ):
            row = eval_suite._run_ambiguous(task)
        self.assertEqual(row["verdict"], "unverified")
        self.assertEqual(row["unverified"], 1)
        self.assertFalse(row["cleanup_proven"])
        self.assertIn("removal failed", row["detail"])

    def test_ambiguous_eval_cleanup_timeout_is_unverified(self):
        task = {
            "id": "ambiguous-cleanup-timeout", "category": "ambiguous",
            "goal": "Add data import support.", "expects_clarification": True,
        }

        def post(path, payload):
            if path == "/missions":
                return {"mission_id": payload["task_id"]}
            return {"ok": True, "status": "cancelling"}

        with (
            mock.patch.object(eval_suite, "_post", side_effect=post),
            mock.patch.object(
                eval_suite, "_get",
                return_value={
                    "status": "needs_user",
                    "question": "Which import format and source should be supported?",
                },
            ),
            mock.patch.object(eval_suite, "AMBIGUOUS_CANCEL_TIMEOUT_SEC", 0.0),
        ):
            row = eval_suite._run_ambiguous(task)
        self.assertEqual(row["verdict"], "unverified")
        self.assertEqual(row["unverified"], 1)
        self.assertFalse(row["cleanup_proven"])
        self.assertIn("timed out", row["detail"])

    def test_invalid_complete_run_refuses_to_replace_out_file(self):
        with tempfile.TemporaryDirectory(prefix="eval-out-") as temp_dir:
            target = Path(temp_dir) / "result.json"
            target.write_text("trusted-old-result\n", encoding="utf-8")
            invalid = {"complete_suite": True, "run_valid": False}
            with (
                mock.patch.object(eval_suite, "run_eval", return_value=invalid),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = eval_suite.main(["--n", "0", "--out", str(target)])
            self.assertEqual(status, 2)
            self.assertEqual(target.read_text(encoding="utf-8"), "trusted-old-result\n")
            self.assertFalse(target.with_name(target.name + ".tmp").exists())

    def test_eval_source_digest_matches_loaded_service_contract(self):
        import service
        identity = eval_suite.evaluated_source_identity()
        self.assertEqual(identity["service_source_sha256"], service.SERVICE_SOURCE_SHA256)

    def test_clarification_must_narrow_a_specific_decision(self):
        good, detail = eval_suite._clarification_quality(
            "Нужно поддержать импорт.",
            "Какой формат и источник данных нужно импортировать?",
        )
        self.assertTrue(good, detail)
        bad, detail = eval_suite._clarification_quality(
            "Нужно поддержать импорт.", "Уточните задачу, пожалуйста?",
        )
        self.assertFalse(bad)
        self.assertIn("task-specific", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
