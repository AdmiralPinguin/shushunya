from __future__ import annotations

import os
import importlib.util
from importlib._bootstrap_external import _code_to_timestamp_pyc
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

try:
    from . import deployment_guard, deployment_profile, mission_store, process_supervisor
    from .test_mission_store import runner_accepted, runner_slow_accepted
except ImportError:
    import deployment_guard  # type: ignore[no-redef]
    import deployment_profile  # type: ignore[no-redef]
    import mission_store  # type: ignore[no-redef]
    import process_supervisor  # type: ignore[no-redef]
    from test_mission_store import runner_accepted, runner_slow_accepted  # type: ignore[no-redef]


class DeploymentGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = self.root / "guard_package"
        self.package.mkdir()
        self.source = self.package / "core.py"
        self.source.write_text("VALUE = 1\n", encoding="utf-8")
        self.environment = mock.patch.dict(
            os.environ,
            {name: "" for name in deployment_guard.BOUND_ENVIRONMENT},
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp.cleanup()

    def guard(self) -> deployment_guard.DeploymentGuard:
        return deployment_guard.DeploymentGuard.from_environment(self.package)

    def store(self, name: str = "missions") -> mission_store.MissionStore:
        return mission_store.MissionStore(
            self.root / name,
            max_active=4,
            max_missions=16,
            max_store_bytes=20_000_000,
            max_payload_bytes=1_000_000,
            max_result_bytes=1_000_000,
            max_events_bytes=1_000_000,
            max_event_bytes=100_000,
            max_state_bytes=100_000,
            max_attempts=8,
            attempt_timeout_seconds=5,
            cancel_grace_seconds=0.05,
            terminate_grace_seconds=0.2,
        )

    def test_manifest_detects_changed_missing_and_classifier_symlink(self) -> None:
        classifier = self.root / "classifier.json"
        classifier.write_text('{"version":1}\n', encoding="utf-8")
        os.environ["RESEARCH_SOURCE_CLASSIFIER_JSON"] = str(classifier)
        guard = self.guard()
        self.assertTrue(guard.status().ok)

        classifier.write_text('{"version":2}\n', encoding="utf-8")
        with self.assertRaises(deployment_guard.DeploymentIntegrityError):
            guard.verify()
        classifier.unlink()
        with self.assertRaises(deployment_guard.DeploymentIntegrityError):
            guard.verify()

        target = self.root / "classifier-target.json"
        target.write_text("{}\n", encoding="utf-8")
        try:
            classifier.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaises(deployment_guard.DeploymentIntegrityError):
            self.guard()

    def test_malicious_timestamp_pyc_is_rejected_and_never_resolved(self) -> None:
        package = self.root / "pyc_attack"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        source = (
            "def run(payload=None, context=None):\n"
            "    return {'outcome': 'safe'}\n"
        )
        runner = package / "runner.py"
        runner.write_text(source, encoding="utf-8")
        sys.path.insert(0, str(self.root))
        try:
            target = "pyc_attack.runner:run"
            spec = process_supervisor.attest_runner(target)
            with mock.patch.object(sys, "pycache_prefix", None):
                cache = Path(importlib.util.cache_from_source(str(runner)))
                cache.parent.mkdir(exist_ok=True)
                metadata = runner.stat()
                malicious = compile(
                    source.replace("safe", "evil"), str(runner), "exec"
                )
                cache.write_bytes(
                    _code_to_timestamp_pyc(
                        malicious, int(metadata.st_mtime), metadata.st_size
                    )
                )

                with self.assertRaises(deployment_guard.DeploymentIntegrityError):
                    deployment_guard.DeploymentGuard.from_environment(package)
                resolved = process_supervisor._resolve_attested_target(spec)
                self.assertEqual(resolved()["outcome"], "safe")
        finally:
            sys.modules.pop("pyc_attack.runner", None)
            sys.modules.pop("pyc_attack", None)
            sys.path.remove(str(self.root))

    def test_production_readiness_callable_fingerprint_is_process_stable(self) -> None:
        package = process_supervisor.__package__
        module_name = f"{package}.production_runner" if package else "production_runner"
        target = f"{module_name}:runtime_readiness_probe"
        path = process_supervisor._source_path(module_name)
        raw = process_supervisor._read_source(path)
        first = process_supervisor._compile_callable_code(
            raw, path, "runtime_readiness_probe"
        )
        second = process_supervisor._compile_callable_code(
            raw, path, "runtime_readiness_probe"
        )
        self.assertEqual(first, second)
        self.assertEqual(
            process_supervisor._code_sha256(first),
            process_supervisor._code_sha256(second),
        )

        spec = process_supervisor.attest_runner(target)
        imported = importlib.import_module(module_name)
        loaded = process_supervisor._resolve_attested_target(spec)
        self.assertIs(loaded, imported.runtime_readiness_probe)
        self.assertEqual(loaded.__code__, first)

        sys.modules.pop(module_name, None)
        try:
            source_loaded = process_supervisor._resolve_attested_target(spec)
            self.assertEqual(source_loaded.__code__, first)
            self.assertEqual(
                process_supervisor._code_sha256(source_loaded.__code__),
                spec.callable_sha256,
            )
        finally:
            sys.modules.pop(module_name, None)
            sys.modules[module_name] = imported

    def test_secret_environment_is_hashed_not_stored_in_manifest(self) -> None:
        secret = "do-not-persist-this-bearer"
        os.environ["RESEARCH_WARBAND_BEARER_TOKEN"] = secret
        manifest = self.guard().manifest
        environment = dict(manifest.environment)
        self.assertNotIn(secret, repr(manifest))
        self.assertRegex(
            environment["RESEARCH_WARBAND_BEARER_TOKEN"], r"^sha256:[0-9a-f]{64}$"
        )

    def test_common_protocol_cache_is_ignored_but_source_remains_bound(self) -> None:
        protocol = self.root / "common_protocol"
        protocol.mkdir()
        contract = protocol / "contract.py"
        contract.write_text("VALUE = 1\n", encoding="utf-8")
        cache = protocol / "__pycache__"
        cache.mkdir()
        cached = cache / "contract.cpython-312.pyc"
        cached.write_bytes(b"shared-service-cache")
        spec = deployment_guard.GuardSpec(
            package_root=str(self.package),
            automatic_protocol_roots=(str(protocol),),
            trusted_files=(),
            trusted_config_json="{}",
        )
        guard = deployment_guard.DeploymentGuard(
            deployment_guard.build_manifest(spec)
        )

        bound = {Path(item.path): item.role for item in guard.manifest.files}
        self.assertEqual(bound[contract], "common_protocol")
        self.assertNotIn(cached, bound)
        cached.write_bytes(b"cache-created-by-another-service")
        guard.verify()
        contract.write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaises(deployment_guard.DeploymentIntegrityError):
            guard.verify()

    def test_deployment_profile_requires_bytecode_sink_mode(self) -> None:
        for prefix, dont_write in ((None, True), ("/dev/null", False)):
            with self.subTest(prefix=prefix, dont_write=dont_write), mock.patch.object(
                deployment_profile.sys, "pycache_prefix", prefix
            ), mock.patch.object(
                deployment_profile.sys, "dont_write_bytecode", dont_write
            ), self.assertRaisesRegex(
                deployment_profile.DeploymentProfileError,
                "PYTHONPYCACHEPREFIX=/dev/null",
            ):
                deployment_profile.validate_deployment_profile("shadow-production")

    def test_queued_scheduler_tamper_blocks_without_attempt(self) -> None:
        guard = self.guard()
        store = self.store()
        store.bind_runner(runner_accepted)
        store.bind_deployment_guard(guard)
        mission, _ = store.create_or_get(
            "queued-tamper", {"mission_id": "queued-tamper", "task_id": "t"}
        )
        self.source.write_text("VALUE = 2\n", encoding="utf-8")

        store._launch_waiting()

        self.assertEqual(mission.status, "blocked")
        self.assertEqual(mission.attempt, 0)
        self.assertTrue(mission.cleanup_complete)
        self.assertFalse(mission.inflight)
        self.assertEqual(mission.events[-1]["type"], "deployment_integrity_failed")

    def test_tamper_during_attempt_never_commits_done(self) -> None:
        guard = self.guard()
        store = self.store()
        store.bind_deployment_guard(guard)
        mission, _ = store.create_or_get(
            "running-tamper", {"mission_id": "running-tamper", "task_id": "t"}
        )
        self.assertTrue(store.launch(mission, runner_slow_accepted))
        deadline = time.monotonic() + 3
        while mission.status != "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.source.write_text("VALUE = 2\n", encoding="utf-8")
        self.assertTrue(store.wait_for_idle())

        self.assertEqual(mission.status, "blocked")
        self.assertIsNone(mission.result)
        self.assertTrue(mission.cleanup_complete)

    def test_resume_and_recovered_adoption_fail_closed_after_tamper(self) -> None:
        guard = self.guard()
        initial = self.store("resume")
        initial.bind_deployment_guard(guard)
        mission, _ = initial.create_or_get(
            "resume-tamper", {"mission_id": "resume-tamper", "task_id": "t"}
        )
        with mission._lock:
            initial._transition(mission, "blocked", "fixture_blocked")
        self.source.write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaises(deployment_guard.DeploymentIntegrityError):
            initial.resume("resume-tamper", runner_accepted)
        self.assertEqual(mission.status, "blocked")
        self.assertEqual(mission.attempt, 0)

        self.source.write_text("VALUE = 1\n", encoding="utf-8")
        adopt_initial = self.store("adopt")
        adopt, _ = adopt_initial.create_or_get(
            "adopt-tamper", {"mission_id": "adopt-tamper", "task_id": "t"}
        )
        with adopt._lock:
            adopt.status = "running"
            adopt.inflight = True
            adopt.cleanup_complete = False
            adopt_initial._append_event(adopt, "simulated_crash", {"status": "running"})
            adopt_initial._persist(adopt)
        recovered = self.store("adopt")
        recovered.bind_runner(runner_accepted)
        recovered.bind_deployment_guard(guard)
        self.source.write_text("VALUE = 3\n", encoding="utf-8")

        self.assertEqual(recovered.adopt_pending(), [])
        loaded = recovered.get("adopt-tamper")
        assert loaded is not None
        self.assertEqual(loaded.status, "blocked")
        self.assertEqual(loaded.attempt, 0)
        self.assertTrue(loaded.cleanup_complete)


if __name__ == "__main__":
    unittest.main()
