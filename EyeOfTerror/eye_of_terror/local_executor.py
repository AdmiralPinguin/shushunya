from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ledger import TaskLedger
from .pipeline import write_json_atomic


WORKER_COMMANDS = {
    "CorpusIngestor": ("Mechanicum/CorpusIngestor", "Mechanicum/CorpusIngestor/corpus_ingestor.py"),
    "Lexmechanic": ("Mechanicum/Lexmechanic", "Mechanicum/Lexmechanic/lexmechanic.py"),
    "AuspexBrowser": ("Mechanicum/AuspexBrowser", "Mechanicum/AuspexBrowser/auspex_browser.py"),
    "OcularisRenderium": ("Mechanicum/OcularisRenderium", "Mechanicum/OcularisRenderium/ocularis_renderium.py"),
    "NoosphericExtractor": ("Mechanicum/NoosphericExtractor", "Mechanicum/NoosphericExtractor/noospheric_extractor.py"),
    "Chronologis": ("Mechanicum/Chronologis", "Mechanicum/Chronologis/chronologis.py"),
    "ScriptoriumDaemon": ("Mechanicum/ScriptoriumDaemon", "Mechanicum/ScriptoriumDaemon/scriptorium_daemon.py"),
    "ReductorVerifier": ("Mechanicum/ReductorVerifier", "Mechanicum/ReductorVerifier/reductor_verifier.py"),
    "FabricatorFinalis": ("Mechanicum/FabricatorFinalis", "Mechanicum/FabricatorFinalis/fabricator_finalis.py"),
    "CogitatorCodewright": ("Mechanicum/CogitatorCodewright", "Mechanicum/CogitatorCodewright/cogitator_codewright.py"),
    "LogisRepository": ("Mechanicum/LogisRepository", "Mechanicum/LogisRepository/logis_repository.py"),
    "MagosStrategos": ("Mechanicum/MagosStrategos", "Mechanicum/MagosStrategos/magos_strategos.py"),
    "FerrumPatchwright": ("Mechanicum/FerrumPatchwright", "Mechanicum/FerrumPatchwright/ferrum_patchwright.py"),
    "OrdinatusVerifier": ("Mechanicum/OrdinatusVerifier", "Mechanicum/OrdinatusVerifier/ordinatus_verifier.py"),
    "JudicatorCodicis": ("Mechanicum/JudicatorCodicis", "Mechanicum/JudicatorCodicis/judicator_codicis.py"),
    "SealwrightFinalis": ("Mechanicum/SealwrightFinalis", "Mechanicum/SealwrightFinalis/sealwright_finalis.py"),
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
    enriched = dict(packet)
    request = dict(enriched.get("request") if isinstance(enriched.get("request"), dict) else {})
    request["revision_context"] = revision_context
    enriched["request"] = request
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix=f".{dispatch_path.stem}.revision.",
        suffix=".json",
        dir=str(dispatch_path.parent),
        delete=False,
    )
    with handle:
        json.dump(enriched, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return Path(handle.name)


def run_step(
    repo_root: Path,
    dispatch_path: Path,
    workspace_root: Path,
    timeout_sec: int,
    revision_context: dict[str, Any] | None = None,
) -> StepResult:
    try:
        packet = load_json(dispatch_path)
    except Exception as exc:  # noqa: BLE001 - executor should record malformed dispatch as a step failure.
        payload = {"ok": False, "status": "failed", "error": f"dispatch unavailable: {exc}"}
        return StepResult(dispatch_path.stem, "", 2, False, payload, "", payload["error"])
    worker = str(packet.get("worker") or "")
    step_id = str(packet.get("step_id") or dispatch_path.stem)
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
    if revision_context:
        temp_dispatch_path = write_revision_dispatch(dispatch_path, packet, revision_context)
        execution_dispatch_path = temp_dispatch_path
    timed_out: subprocess.TimeoutExpired | None = None
    try:
        completed = subprocess.run(
            [sys.executable, str(repo_root / script), str(execution_dispatch_path), "--workspace-root", str(workspace_root)],
            cwd=repo_root,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        timed_out = exc
    finally:
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
        return StepResult(step_id, worker, 124, False, payload, stdout[-4000:], stderr[-4000:])
    payload = parse_worker_stdout(completed.stdout)
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
        result = run_step(repo_root, dispatch_path, workspace_root, timeout_sec, revision_context=revision_contexts.get(dispatch_path.stem))
        results.append(result)
        ledger.record_step(
            result.step_id,
            result.worker,
            str(result.payload.get("status") or ("completed" if result.ok else "failed")),
            [str(item) for item in result.payload.get("artifacts", [])] if isinstance(result.payload.get("artifacts"), list) else [],
            str(result.payload.get("summary") or result.payload.get("error") or ""),
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
    parser.add_argument("--step-id", action="append", default=[], help="Restrict execution to one or more dispatch step ids")
    args = parser.parse_args()
    summary = execute_run(Path(args.repo_root).resolve(), Path(args.run_dir), Path(args.workspace_root), args.timeout_sec, step_ids=args.step_id or None, execution_mode="restricted" if args.step_id else "full")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
