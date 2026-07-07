from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from EyeOfTerror.model_brain import attach_model_brain, request_model_decision

from .ledger import TaskLedger
from .mission_control import record_worker_execution_started, record_worker_protocol_report, worker_report_from_payload
from .pipeline import dispatch_packet_with_worker_order, write_json_atomic


WORKER_COMMANDS = {
    "CorpusIngestor": ("EyeOfTerror/Scriptorium/Brigade/CorpusIngestor", "EyeOfTerror/Scriptorium/Brigade/CorpusIngestor/corpus_ingestor.py"),
    "Lexmechanic": ("EyeOfTerror/Scriptorium/Brigade/Lexmechanic", "EyeOfTerror/Scriptorium/Brigade/Lexmechanic/lexmechanic.py"),
    "AuspexBrowser": ("EyeOfTerror/Scriptorium/Brigade/AuspexBrowser", "EyeOfTerror/Scriptorium/Brigade/AuspexBrowser/auspex_browser.py"),
    "OcularisRenderium": ("EyeOfTerror/Scriptorium/Brigade/OcularisRenderium", "EyeOfTerror/Scriptorium/Brigade/OcularisRenderium/ocularis_renderium.py"),
    "NoosphericExtractor": ("EyeOfTerror/Scriptorium/Brigade/NoosphericExtractor", "EyeOfTerror/Scriptorium/Brigade/NoosphericExtractor/noospheric_extractor.py"),
    "Chronologis": ("EyeOfTerror/Scriptorium/Brigade/Chronologis", "EyeOfTerror/Scriptorium/Brigade/Chronologis/chronologis.py"),
    "ScriptoriumArchitect": ("EyeOfTerror/Scriptorium/Brigade/ScriptoriumArchitect", "EyeOfTerror/Scriptorium/Brigade/ScriptoriumArchitect/scriptorium_architect.py"),
    "ScriptoriumDaemon": ("EyeOfTerror/Scriptorium/Brigade/ScriptoriumDaemon", "EyeOfTerror/Scriptorium/Brigade/ScriptoriumDaemon/scriptorium_daemon.py"),
    "ReductorVerifier": ("EyeOfTerror/Scriptorium/Brigade/ReductorVerifier", "EyeOfTerror/Scriptorium/Brigade/ReductorVerifier/reductor_verifier.py"),
    "FabricatorFinalis": ("EyeOfTerror/Scriptorium/Brigade/FabricatorFinalis", "EyeOfTerror/Scriptorium/Brigade/FabricatorFinalis/fabricator_finalis.py"),
    "CogitatorCodewright": ("EyeOfTerror/Mechanicum/CodeBrigade/Workers/CogitatorCodewright", "EyeOfTerror/Mechanicum/CodeBrigade/Workers/CogitatorCodewright/cogitator_codewright.py"),
    "LogisRepository": ("EyeOfTerror/Mechanicum/CodeBrigade/Workers/LogisRepository", "EyeOfTerror/Mechanicum/CodeBrigade/Workers/LogisRepository/logis_repository.py"),
    "MagosStrategos": ("EyeOfTerror/Mechanicum/CodeBrigade/Workers/MagosStrategos", "EyeOfTerror/Mechanicum/CodeBrigade/Workers/MagosStrategos/magos_strategos.py"),
    "FerrumPatchwright": ("EyeOfTerror/Mechanicum/CodeBrigade/Workers/FerrumPatchwright", "EyeOfTerror/Mechanicum/CodeBrigade/Workers/FerrumPatchwright/ferrum_patchwright.py"),
    "OrdinatusVerifier": ("EyeOfTerror/Mechanicum/CodeBrigade/Workers/OrdinatusVerifier", "EyeOfTerror/Mechanicum/CodeBrigade/Workers/OrdinatusVerifier/ordinatus_verifier.py"),
    "JudicatorCodicis": ("EyeOfTerror/Mechanicum/CodeBrigade/Workers/JudicatorCodicis", "EyeOfTerror/Mechanicum/CodeBrigade/Workers/JudicatorCodicis/judicator_codicis.py"),
    "SealwrightFinalis": ("EyeOfTerror/Mechanicum/CodeBrigade/Workers/SealwrightFinalis", "EyeOfTerror/Mechanicum/CodeBrigade/Workers/SealwrightFinalis/sealwright_finalis.py"),
}


@dataclass
class StepResult:
    step_id: str
    worker: str
    returncode: int
    ok: bool
    payload: dict[str, Any]
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "worker": self.worker,
            "returncode": self.returncode,
            "ok": self.ok,
            "payload": self.payload,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return payload


def parse_worker_stdout(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "worker stdout is not JSON"}
    return payload if isinstance(payload, dict) else {"ok": False, "error": "worker stdout JSON is not an object"}


def artifact_host_path(workspace_root: Path, artifact_path: str) -> Path:
    if not artifact_path.startswith("/work/"):
        raise ValueError(f"artifact path must start with /work/: {artifact_path}")
    root = workspace_root.resolve()
    host_path = (root / artifact_path.removeprefix("/work/")).resolve()
    if not host_path.is_relative_to(root):
        raise ValueError(f"artifact path escapes workspace root: {artifact_path}")
    return host_path


def input_artifact_errors(request: dict[str, Any], workspace_root: Path) -> list[dict[str, str]]:
    input_artifacts = request.get("input_artifacts", [])
    if not isinstance(input_artifacts, list):
        return [{"path": "", "error": "input_artifacts must be a list"}]
    errors: list[dict[str, str]] = []
    for artifact in input_artifacts:
        if not isinstance(artifact, str):
            errors.append({"path": repr(artifact), "error": "input artifact path must be a string"})
            continue
        try:
            host_path = artifact_host_path(workspace_root, artifact)
        except ValueError as exc:
            errors.append({"path": artifact, "error": str(exc)})
            continue
        if not host_path.exists():
            errors.append({"path": artifact, "error": "input artifact does not exist"})
    return errors


def quality_expectation_errors(request: dict[str, Any], worker_name: str) -> list[dict[str, str]]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    step_quality = expectations.get("step_quality") if isinstance(expectations.get("step_quality"), dict) else {}
    if not step_quality:
        return []
    errors: list[dict[str, str]] = []
    step = request.get("step") if isinstance(request.get("step"), dict) else {}
    step_id = str(step.get("step_id") or "")
    quality_step_id = str(step_quality.get("step_id") or "")
    if step_id and quality_step_id != step_id:
        errors.append({"field": "step_id", "error": f"expected {step_id}, got {quality_step_id or 'missing'}"})
    quality_worker = str(step_quality.get("worker") or "")
    if quality_worker and quality_worker != worker_name:
        errors.append({"field": "worker", "error": f"expected {worker_name}, got {quality_worker}"})
    expected_artifacts = step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []
    if step_quality.get("expected_artifacts") != expected_artifacts:
        errors.append({"field": "expected_artifacts", "error": "quality expectations do not match request.step.expected_artifacts"})
    for field_name in ("checks", "blockers", "revision_targets"):
        values = step_quality.get(field_name)
        if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item for item in values):
            errors.append({"field": field_name, "error": "must be a non-empty list of strings"})
    revision_policy = expectations.get("revision_policy") if isinstance(expectations.get("revision_policy"), dict) else {}
    if revision_policy:
        if not isinstance(revision_policy.get("source_step"), str) or not revision_policy.get("source_step"):
            errors.append({"field": "revision_policy.source_step", "error": "must be a non-empty string"})
        for field_name in ("final_steps", "allowed_steps"):
            values = revision_policy.get(field_name)
            if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item for item in values):
                errors.append({"field": f"revision_policy.{field_name}", "error": "must be a non-empty list of strings"})
        allowed_steps = revision_policy.get("allowed_steps") if isinstance(revision_policy.get("allowed_steps"), list) else []
        if step_id and allowed_steps and step_id not in allowed_steps:
            errors.append({"field": "revision_policy.allowed_steps", "error": f"does not include current step {step_id}"})
        for field_name in ("requires_downstream_rerun", "requires_focused_context", "requires_gap_disclosure"):
            if not isinstance(revision_policy.get(field_name), bool):
                errors.append({"field": f"revision_policy.{field_name}", "error": "must be a boolean"})
    return errors


def split_revision_values(value: str, separators: tuple[str, ...]) -> list[str]:
    values = [value.strip()]
    for separator in separators:
        next_values: list[str] = []
        for item in values:
            next_values.extend(part.strip() for part in item.split(separator))
        values = next_values
    return [item for item in values if item]


def append_unique(items: list[str], values: list[str]) -> None:
    for value in values:
        if value not in items:
            items.append(value)


def revision_contexts_from_result(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    revision_plan = result.get("revision_plan") if isinstance(result.get("revision_plan"), dict) else {}
    contexts: dict[str, dict[str, Any]] = {}
    for item in revision_plan.get("steps", []) if isinstance(revision_plan.get("steps"), list) else []:
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("step_id") or "").strip()
        if not step_id:
            continue
        contexts.setdefault(step_id, {"reasons": [], "source_steps": []})
        context = contexts[step_id]
        reason = str(item.get("reason") or "").strip()
        if reason:
            append_unique(context["reasons"], split_revision_values(reason, (" | ",)))
        source = str(item.get("source") or "").strip()
        if source:
            append_unique(context["source_steps"], split_revision_values(source, (",",)))
        context["priority"] = str(item.get("priority") or "blocker")
    return contexts


def write_revision_dispatch(dispatch_path: Path, packet: dict[str, Any], revision_context: dict[str, Any]) -> Path:
    return write_execution_dispatch(dispatch_path, dispatch_packet_with_worker_order(packet, revision_context=revision_context))


def write_execution_dispatch(dispatch_path: Path, packet: dict[str, Any]) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix=f".{dispatch_path.stem}.execution.",
        suffix=".json",
        dir=str(dispatch_path.parent),
        delete=False,
    )
    with handle:
        json.dump(packet, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return Path(handle.name)


def run_step(
    repo_root: Path,
    dispatch_path: Path,
    workspace_root: Path,
    timeout_sec: int,
    revision_context: dict[str, Any] | None = None,
    timeout_retries: int = 1,
    retry_timeout_multiplier: int = 2,
) -> StepResult:
    try:
        packet = load_json(dispatch_path)
    except Exception as exc:  # noqa: BLE001 - executor should record malformed dispatch as a step failure.
        payload = {"ok": False, "status": "failed", "error": f"dispatch unavailable: {exc}"}
        return StepResult(dispatch_path.stem, "", 2, False, payload, "", payload["error"])
    worker = str(packet.get("worker") or "")
    step_id = str(packet.get("step_id") or dispatch_path.stem)
    packet = dispatch_packet_with_worker_order(packet, revision_context=revision_context)
    request = packet.get("request") if isinstance(packet.get("request"), dict) else packet
    artifact_errors = input_artifact_errors(request, workspace_root)
    quality_errors = quality_expectation_errors(request, worker)
    if artifact_errors or quality_errors:
        payload = {
            "ok": False,
            "worker": worker,
            "task_id": str(request.get("task_id") or ""),
            "status": "failed",
            "error": "worker request preflight failed" if quality_errors else "input artifact preflight failed",
            "input_artifact_errors": artifact_errors,
            "quality_expectation_errors": quality_errors,
        }
        return StepResult(step_id, worker, 2, False, payload, "", payload["error"])
    if worker not in WORKER_COMMANDS:
        payload = {"ok": False, "error": f"no local command registered for worker: {worker}"}
        return StepResult(step_id, worker, 127, False, payload, "", payload["error"])
    pythonpath, script = WORKER_COMMANDS[worker]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / pythonpath)
    execution_dispatch_path = dispatch_path
    temp_dispatch_path: Path | None = None
    execution_packet = dict(packet)
    execution_request = dict(request)
    worker_order = execution_packet.get("worker_order") if isinstance(execution_packet.get("worker_order"), dict) else {}
    model_decision = request_model_decision(
        worker,
        worker,
        {
            "worker_order": worker_order,
            "legacy_request": execution_request,
            "execution_contract": {
                "primary_input": "worker_order",
                "legacy_request_is_compatibility": True,
            },
        },
        layer="local_executor",
        instructions=(
            "You are the model brain for a local pipeline worker subprocess. Treat worker_order as the primary order. "
            "Use legacy_request only for compatibility fields and artifact context. Stay inside the worker role and return guidance for this exact step."
        ),
    )
    if model_decision.get("status") != "answered":
        payload = {
            "ok": False,
            "worker": worker,
            "task_id": str(request.get("task_id") or ""),
            "status": "failed",
            "error_code": "model_brain_unavailable",
            "error": str(model_decision.get("error") or "model brain did not answer"),
            "summary": f"{worker} cannot run without a live model-brain answer.",
        }
        return StepResult(step_id, worker, 2, False, attach_model_brain(payload, model_decision), "", payload["error"])
    execution_request["model_brain"] = model_decision
    execution_request = dispatch_packet_with_worker_order({**execution_packet, "request": execution_request}).get("request", execution_request)
    execution_packet["request"] = execution_request
    temp_dispatch_path = write_execution_dispatch(dispatch_path, execution_packet)
    execution_dispatch_path = temp_dispatch_path
    timed_out: subprocess.TimeoutExpired | None = None
    completed: subprocess.CompletedProcess[str] | None = None
    attempts = 0
    max_attempts = 1 + max(0, timeout_retries if timeout_sec > 0 else 0)
    while attempts < max_attempts:
        attempts += 1
        attempt_timeout = timeout_sec * (retry_timeout_multiplier ** (attempts - 1))
        try:
            completed = subprocess.run(
                [sys.executable, str(repo_root / script), str(execution_dispatch_path), "--workspace-root", str(workspace_root)],
                cwd=repo_root,
                env=env,
                text=True,
                capture_output=True,
                timeout=attempt_timeout,
                check=False,
            )
            timed_out = None
            break
        except subprocess.TimeoutExpired as exc:
            timed_out = exc
            if attempts >= max_attempts:
                break
    if temp_dispatch_path is not None:
        temp_dispatch_path.unlink(missing_ok=True)
    if timed_out is not None:
        stdout = timed_out.stdout.decode("utf-8", errors="replace") if isinstance(timed_out.stdout, bytes) else str(timed_out.stdout or "")
        stderr = timed_out.stderr.decode("utf-8", errors="replace") if isinstance(timed_out.stderr, bytes) else str(timed_out.stderr or "")
        payload = {
            "ok": False,
            "worker": worker,
            "task_id": str(request.get("task_id") or ""),
            "status": "failed",
            "error_code": "worker_timeout",
            "error": f"worker timed out after {timeout_sec} seconds",
            "summary": f"{worker} timed out after {timeout_sec} seconds.",
            "attempt_count": attempts,
            "timeout_retries": max_attempts - 1,
            "revision_plan": {
                "required": True,
                "steps": [
                    {
                        "step_id": step_id,
                        "worker": worker,
                        "reason": f"Worker timed out after {timeout_sec} seconds",
                        "source": "local_executor_timeout",
                        "priority": "blocker",
                    }
                ],
            },
        }
        return StepResult(step_id, worker, 124, False, attach_model_brain(payload, model_decision), stdout[-4000:], stderr[-4000:])
    if completed is None:
        payload = {"ok": False, "worker": worker, "task_id": str(request.get("task_id") or ""), "status": "failed", "error": "worker process did not start"}
        return StepResult(step_id, worker, 2, False, attach_model_brain(payload, model_decision), "", payload["error"])
    payload = parse_worker_stdout(completed.stdout)
    payload = attach_model_brain(payload, model_decision)
    ok = completed.returncode == 0 and bool(payload.get("ok"))
    return StepResult(step_id, worker, completed.returncode, ok, payload, completed.stdout, completed.stderr)


def ordered_dispatch_paths(run_dir: Path, step_ids: list[str] | None = None) -> list[Path]:
    status = load_json(run_dir / "status.json")
    dispatch_dir = Path(str(status.get("dispatch_dir") or run_dir / "dispatch"))
    if not dispatch_dir.is_absolute():
        candidates = [dispatch_dir, run_dir / "dispatch", run_dir.parent / dispatch_dir]
        dispatch_dir = next((candidate for candidate in candidates if candidate.exists()), run_dir / "dispatch")
    allowed = set(step_ids or [])
    paths: list[Path] = []
    for step in status.get("steps", []):
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("step_id") or "")
        if step_id and (not allowed or step_id in allowed):
            paths.append(dispatch_dir / f"{step_id}.json")
    return paths


def terminal_payload_allows_completion(payload: dict[str, Any]) -> bool:
    if not payload.get("ok"):
        return False
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"ready", "completed", "passed", "passed_with_warnings"}:
        return False
    if status in {"blocked", "needs_revision", "failed", "preflight_failed", "cancelled"}:
        return False
    revision_plan = payload.get("revision_plan")
    if isinstance(revision_plan, dict) and revision_plan.get("required"):
        return False
    return True


def execute_run(
    repo_root: Path,
    run_dir: Path,
    workspace_root: Path,
    timeout_sec: int = 1800,
    step_ids: list[str] | None = None,
    execution_mode: str = "full",
    timeout_retries: int = 1,
) -> dict[str, Any]:
    contract = load_json(run_dir / "contract.json") if (run_dir / "contract.json").exists() else {}
    ledger_path = run_dir / "task_ledger.json"
    ledger = (
        TaskLedger.load(ledger_path)
        if ledger_path.exists()
        else TaskLedger.create(
            ledger_path,
            str(contract.get("task_id") or run_dir.name),
            str(contract.get("goal") or ""),
            str(contract.get("assigned_governor") or ""),
        )
    )
    ledger.set_status("running")
    revision_contexts = revision_contexts_from_result(ledger.data.get("result", {}) if isinstance(ledger.data.get("result"), dict) else {})
    if step_ids:
        event_type = f"{execution_mode}_execution_started" if execution_mode in {"revision", "resume"} else "restricted_execution_started"
        ledger.record_event(event_type, {"step_ids": step_ids, "mode": "local"})
    all_dispatch_paths = ordered_dispatch_paths(run_dir)
    selected_dispatch_paths = ordered_dispatch_paths(run_dir, step_ids=step_ids)
    partial_execution = bool(step_ids) and [path.stem for path in selected_dispatch_paths] != [path.stem for path in all_dispatch_paths]
    results: list[StepResult] = []
    for dispatch_path in selected_dispatch_paths:
        ledger = TaskLedger.load(ledger_path)
        if ledger.cancel_requested():
            break
        try:
            record_worker_execution_started(run_dir, load_json(dispatch_path))
        except Exception:  # noqa: BLE001 - progress reporting must not hide the worker result.
            pass
        result = run_step(repo_root, dispatch_path, workspace_root, timeout_sec, revision_context=revision_contexts.get(dispatch_path.stem), timeout_retries=timeout_retries)
        results.append(result)
        step_details: dict[str, Any] = {}
        try:
            packet = load_json(dispatch_path)
            order = packet.get("worker_order") if isinstance(packet.get("worker_order"), dict) else {}
            report = worker_report_from_payload(str(order.get("mission_id") or f"mission-{contract.get('task_id') or run_dir.name}"), result.step_id, result.worker, result.payload, result.ok)
            record_worker_protocol_report(run_dir, report)
            step_details["worker_report"] = report
        except Exception as exc:  # noqa: BLE001 - protocol reporting must not hide the worker result.
            step_details["worker_report_error"] = str(exc)
        ledger.record_step(
            result.step_id,
            result.worker,
            str(result.payload.get("status") or ("completed" if result.ok else "failed")),
            [str(item) for item in result.payload.get("artifacts", [])] if isinstance(result.payload.get("artifacts"), list) else [],
            str(result.payload.get("summary") or result.payload.get("error") or ""),
            step_details,
        )
        if not result.ok:
            break
    cancelled = TaskLedger.load(ledger_path).cancel_requested()
    final_payload = results[-1].payload if results else {}
    terminal_ok = terminal_payload_allows_completion(final_payload) if isinstance(final_payload, dict) else False
    summary = {
        "ok": bool(results) and all(item.ok for item in results) and terminal_ok and not cancelled,
        "run_dir": str(run_dir),
        "workspace_root": str(workspace_root),
        "steps": [item.to_dict() for item in results],
        "cancelled": cancelled,
    }
    if step_ids:
        summary["step_ids"] = step_ids
        summary["execution_mode"] = execution_mode
        summary["partial_execution"] = partial_execution
        if execution_mode == "revision":
            summary["revision_execution"] = True
        if execution_mode == "resume":
            summary["resume_execution"] = True
    if isinstance(final_payload, dict) and isinstance(final_payload.get("revision_plan"), dict):
        summary["revision_plan"] = final_payload["revision_plan"]
    report_path = run_dir / "execution_report.json"
    write_json_atomic(report_path, summary)
    if isinstance(final_payload, dict):
        ledger.set_result(
            {
                "ok": summary["ok"],
                "final_step": results[-1].step_id if results else "",
                "artifacts": final_payload.get("artifacts", []),
                "workspace_root": str(workspace_root),
                "status": "cancelled" if cancelled else ("interrupted" if summary["ok"] and partial_execution else final_payload.get("status", "")),
                "summary": "Execution cancelled before next step." if cancelled else ("Partial execution completed; pending steps remain." if summary["ok"] and partial_execution else final_payload.get("summary", "")),
                "revision_plan": final_payload.get("revision_plan", {}),
            }
        )
    ledger.set_status("interrupted" if summary["ok"] and partial_execution else ("completed" if summary["ok"] else ("cancelled" if cancelled else "failed")))
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Execute a local EyeOfTerror pipeline run package.")
    parser.add_argument("run_dir")
    parser.add_argument("--workspace-root", default="runtime/eye-local-work")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--timeout-retries", type=int, default=1)
    parser.add_argument("--step-id", action="append", default=[], help="Restrict execution to one or more dispatch step ids")
    args = parser.parse_args()
    summary = execute_run(Path(args.repo_root).resolve(), Path(args.run_dir), Path(args.workspace_root), args.timeout_sec, step_ids=args.step_id or None, execution_mode="restricted" if args.step_id else "full", timeout_retries=args.timeout_retries)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
