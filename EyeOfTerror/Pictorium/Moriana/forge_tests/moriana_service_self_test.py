#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
WARMMASTER_ROOT = PROJECT_ROOT / "EyeOfTerror" / "Warmaster"
if str(WARMMASTER_ROOT) not in sys.path:
    sys.path.insert(0, str(WARMMASTER_ROOT))

from EyeOfTerror.Pictorium.Moriana.moriana_governor import make_handler
from EyeOfTerror.Pictorium.testing.fake_model_server import fake_pictorium_model


def request_json(base: str, method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise AssertionError(f"endpoint returned non-object JSON: {path}")
    return result


def _main() -> int:
    with tempfile.TemporaryDirectory(prefix="moriana-service-self-test-") as tmp:
        run_root = Path(tmp) / "runs"
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            health = request_json(base, "GET", "/health")
            if not health.get("ok") or health.get("governor") != "Moriana":
                raise AssertionError(f"bad health payload: {health}")
            capabilities = request_json(base, "GET", "/capabilities")
            if (
                not capabilities.get("ok")
                or capabilities.get("governor") != "Moriana"
                or "ScenarioScribe" not in capabilities.get("required_workers", [])
                or "Promptwright" not in capabilities.get("required_workers", [])
                or "GET /runs/{run_id}/revision-decision" not in capabilities.get("endpoints", [])
                or "POST /runs/{run_id}/apply_revision" not in capabilities.get("endpoints", [])
            ):
                raise AssertionError(f"bad capabilities payload: {capabilities}")
            image_plan = request_json(base, "POST", "/plan", {"task": "нарисуй картинку 512x512", "task_id": "moriana-http-image"})
            if (
                not image_plan.get("ok")
                or image_plan.get("contract", {}).get("assigned_governor") != "Moriana"
                or image_plan.get("contract", {}).get("worker_plan", [])[0].get("worker") != "Promptwright"
            ):
                raise AssertionError(f"bad image plan payload: {image_plan}")
            comic_plan = request_json(base, "POST", "/plan", {"task": "сделай комикс 3 панели про кузню", "task_id": "moriana-http-comic"})
            if (
                not comic_plan.get("ok")
                or comic_plan.get("contract", {}).get("worker_plan", [])[0].get("worker") != "ScenarioScribe"
            ):
                raise AssertionError(f"bad comic plan payload: {comic_plan}")
            prepared = request_json(base, "POST", "/prepare_run", {"task": "сделай комикс 3 панели про кузню", "task_id": "moriana-http-comic-run"})
            run_dir = Path(str(prepared.get("run_dir") or ""))
            if (
                not prepared.get("ok")
                or prepared.get("governor") != "Moriana"
                or not run_dir.exists()
                or not (run_dir / "contract.json").exists()
                or not (run_dir / "dispatch" / "scenario.json").exists()
                or not (run_dir / "artifact_registry.json").exists()
            ):
                raise AssertionError(f"bad prepare_run payload: {prepared}")
            executed = request_json(
                base,
                "POST",
                "/runs",
                {
                    "task": "нарисуй HTTP smoke картинку 512x512",
                    "task_id": "moriana-http-exec-image",
                    "execute": True,
                    "test_artifact_mode": "good",
                },
            )
            if not executed.get("ok") or executed.get("status", {}).get("status") != "completed":
                raise AssertionError(f"bad /runs execution payload: {executed}")
            status = request_json(base, "GET", "/runs/moriana-http-exec-image/status")
            if status.get("status", {}).get("status") != "completed":
                raise AssertionError(f"bad /runs/{{id}}/status payload: {status}")
            artifacts = request_json(base, "GET", "/runs/moriana-http-exec-image/artifacts")
            if not artifacts.get("artifacts"):
                raise AssertionError(f"bad /runs/{{id}}/artifacts payload: {artifacts}")
            final = request_json(base, "GET", "/runs/moriana-http-exec-image/final")
            if final.get("final", {}).get("status") != "ready":
                raise AssertionError(f"bad /runs/{{id}}/final payload: {final}")
            quality = request_json(base, "GET", "/runs/moriana-http-exec-image/quality")
            if quality.get("quality_report", {}).get("next_action") != "accept_final":
                raise AssertionError(f"bad /runs/{{id}}/quality payload: {quality}")
            decision = request_json(base, "GET", "/runs/moriana-http-exec-image/revision-decision")
            if decision.get("revision_decision", {}).get("action") != "accept_final":
                raise AssertionError(f"bad /runs/{{id}}/revision-decision payload: {decision}")
            audit = request_json(base, "POST", "/runs/moriana-http-exec-image/audit", {})
            if audit.get("quality_report", {}).get("kind") != "pictorium_quality_report" or audit.get("revision_decision", {}).get("kind") != "pictorium_revision_decision":
                raise AssertionError(f"bad /runs/{{id}}/audit payload: {audit}")
            decided = request_json(base, "POST", "/runs/moriana-http-exec-image/decide_revision", {})
            if decided.get("revision_decision", {}).get("action") != "accept_final":
                raise AssertionError(f"bad /runs/{{id}}/decide_revision payload: {decided}")
            failed = request_json(
                base,
                "POST",
                "/runs",
                {
                    "task": "нарисуй HTTP smoke картинку для apply revision 512x512",
                    "task_id": "moriana-http-revision-image",
                    "execute": True,
                },
            )
            if failed.get("ok") or failed.get("revision_decision", {}).get("action") != "wait_or_resubmit_forge_job":
                raise AssertionError(f"bad failed revision fixture payload: {failed}")
            applied = request_json(base, "POST", "/runs/moriana-http-revision-image/apply_revision", {"test_artifact_mode": "revision_good"})
            if not applied.get("ok") or applied.get("revision_decision", {}).get("action") != "accept_final":
                raise AssertionError(f"bad /runs/{{id}}/apply_revision payload: {applied}")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] Moriana HTTP service endpoints")
    return 0


def main() -> int:
    with fake_pictorium_model():
        return _main()


if __name__ == "__main__":
    raise SystemExit(main())
