#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
REPORTS = ROOT / "reports"
WORKSPACES = ROOT / "workspaces"
SANDBOX_ROOT = Path(os.environ.get("SHUSHUNYA_SANDBOX_ROOT", "/media/shushunya/ARCHIVE/shushunya-agent-sandbox"))
SANDBOX_RUNNER = Path(os.environ.get("SHUSHUNYA_AGENT_SANDBOX_RUNNER", str(SANDBOX_ROOT / "profile" / "run-in-sandbox.sh")))
SANDBOX_GROUP = os.environ.get("SHUSHUNYA_AGENT_SANDBOX_GROUP", "shushunya-agent")


@dataclass
class RunResult:
    agent: str
    task_id: str
    ok: bool
    duration_sec: float
    checks: list[dict[str, Any]]
    exit_code: int | None = None
    error: str = ""
    log_path: str = ""
    workspace: str = ""
    orchestration: dict[str, Any] | None = None


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def summarize_results(results: list[RunResult]) -> dict[str, Any]:
    by_agent: dict[str, dict[str, Any]] = {}
    for result in results:
        item = by_agent.setdefault(result.agent, {"total": 0, "passed": 0, "failed": 0, "duration_sec": 0.0})
        item["total"] += 1
        item["passed" if result.ok else "failed"] += 1
        item["duration_sec"] = round(float(item["duration_sec"]) + result.duration_sec, 3)
    for item in by_agent.values():
        total = int(item["total"])
        item["pass_rate"] = round(float(item["passed"]) / total, 3) if total else 0.0
    return {
        "total": len(results),
        "passed": sum(1 for item in results if item.ok),
        "failed": sum(1 for item in results if not item.ok),
        "by_agent": by_agent,
    }


def write_report(path: Path, run_id: str, suite: dict[str, Any], model: dict[str, Any], results: list[RunResult], partial: bool) -> None:
    write_json(
        path,
        {
            "run_id": run_id,
            "suite": suite["suite"],
            "partial": partial,
            "model": model,
            "summary": summarize_results(results),
            "results": [item.__dict__ for item in results],
        },
    )


def prepare_workspace(task: dict[str, Any], agent_name: str, run_id: str) -> Path:
    workspace = WORKSPACES / run_id / agent_name / task["id"]
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    for rel, content in task.get("seed_files", {}).items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(workspace), check=False)
    return workspace


def sandbox_to_host(path: Path | str) -> Path:
    text = str(path)
    if not text.startswith("/work/") and text != "/work":
        raise ValueError(f"unsupported sandbox path for arena: {text}")
    suffix = text.removeprefix("/work").lstrip("/")
    return SANDBOX_ROOT / "work" / suffix


def run_as_sandbox_group(command: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sg", SANDBOX_GROUP, "-c", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )


def prepare_shushunya_workspace(task: dict[str, Any], agent_name: str, run_id: str) -> Path:
    workspace = Path("/work/agent-arena") / run_id / agent_name / task["id"]
    host_workspace = sandbox_to_host(workspace)
    setup_cmd = f"rm -rf -- {shlex.quote(str(host_workspace))} && mkdir -p -- {shlex.quote(str(host_workspace))}"
    completed = run_as_sandbox_group(setup_cmd)
    if completed.returncode != 0:
        raise RuntimeError(f"failed to prepare Shushunya sandbox workspace: {completed.stdout}")
    temp_root = WORKSPACES / run_id / "_seed" / task["id"]
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True)
    for rel, content in task.get("seed_files", {}).items():
        temp = temp_root / rel
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(content, encoding="utf-8")
        target = host_workspace / rel
        install_cmd = (
            f"mkdir -p -- {shlex.quote(str(target.parent))} && "
            f"install -m 660 -- {shlex.quote(str(temp))} {shlex.quote(str(target))}"
        )
        completed = run_as_sandbox_group(install_cmd)
        if completed.returncode != 0:
            raise RuntimeError(f"failed to seed {workspace / rel}: {completed.stdout}")
    return workspace


def run_command(command: list[str], cwd: Path, env: dict[str, str], timeout: int, log_path: Path) -> tuple[int, str]:
    started = time.time()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n")
        log.flush()
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
            return completed.returncode, f"finished in {time.time() - started:.1f}s"
        except subprocess.TimeoutExpired:
            log.write(f"\nTIMEOUT after {timeout}s\n")
            return 124, f"timeout after {timeout}s"


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 30) -> tuple[int, dict[str, Any]]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"error": body}
        return exc.code, parsed


def run_shushunya(agent: dict[str, Any], task: dict[str, Any], workspace: Path, run_id: str, log_path: Path) -> tuple[int, str]:
    base_url = agent["base_url"].rstrip("/")
    task_id = f"arena-{run_id}-{task['id']}"
    required_paths = [str(workspace / item["path"]) for item in task.get("checks", []) if "path" in item]
    required_block = "\n".join(f"- {path}" for path in required_paths)
    prompt = (
        task["prompt"]
        + "\n\nРабочий каталог для этой задачи: "
        + str(workspace)
        + "\nВсе файлы создавай, читай, исправляй и проверяй внутри этого каталога."
    )
    if required_block:
        prompt += "\n\nОбязательные артефакты должны быть именно по этим абсолютным путям:\n" + required_block
    payload = {
        "task": prompt,
        "task_id": task_id,
        "technical": True,
        "shell_enabled": True,
        "archive_task": False,
        "task_memory": False,
        "skip_previous_task_context": True,
        "max_auto_cycles": 1
    }
    if agent.get("max_steps"):
        payload["max_steps"] = int(agent["max_steps"])
    if agent.get("max_runtime_sec"):
        payload["max_runtime_sec"] = int(agent["max_runtime_sec"])
    started = time.time()
    status, response = http_json("POST", f"{base_url}/run", payload, timeout=int(agent.get("timeout_sec", 1800)))
    journal_status, journal_response = http_json("GET", f"{base_url}/task-journal?task_id={task_id}&limit=800", timeout=30)
    log_path.write_text(
        json.dumps(
            {
                "status": status,
                "response": response,
                "journal_status": journal_status,
                "journal": journal_response,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if status >= 400:
        return status, f"http {status}"
    return int(response.get("exit_code", 0) or 0), f"finished in {time.time() - started:.1f}s"


def run_aider(agent: dict[str, Any], model: dict[str, Any], task: dict[str, Any], workspace: Path, log_path: Path) -> tuple[int, str]:
    env = os.environ.copy()
    env.update({
        "OPENAI_API_BASE": model["base_url"],
        "OPENAI_API_KEY": model.get("api_key", "local-key"),
        "AIDER_ANALYTICS_DISABLE": "1",
        "NO_COLOR": "1",
    })
    venv_bin = ROOT / agent.get("venv", ".venv") / "bin"
    command = [
        str(venv_bin / "aider"),
        "--model", f"openai/{model['model']}",
        "--yes-always",
        "--no-auto-commits",
        "--no-git",
        "--no-show-model-warnings",
        "--message", task["prompt"],
    ]
    command.extend(task.get("seed_files", {}).keys())
    return run_command(command, workspace, env, int(agent.get("timeout_sec", 1800)), log_path)


def run_mini_swe(agent: dict[str, Any], model: dict[str, Any], task: dict[str, Any], workspace: Path, log_path: Path) -> tuple[int, str]:
    env = os.environ.copy()
    env.update({
        "OPENAI_API_KEY": model.get("api_key", "local-key"),
        "OPENAI_API_BASE": model["base_url"],
        "OPENAI_BASE_URL": model["base_url"],
        "LITELLM_LOG": "ERROR",
        "MSWEA_CONFIGURED": "true",
        "MSWEA_SILENT_STARTUP": "1",
        "MSWEA_COST_TRACKING": "ignore_errors",
        "NO_COLOR": "1",
    })
    venv_bin = ROOT / agent.get("venv", ".venv") / "bin"
    candidates = [venv_bin / "mini", venv_bin / "mini-swe-agent"]
    binary = next((item for item in candidates if item.exists()), candidates[0])
    command = [
        str(binary),
        "--model", f"openai/{model['model']}",
        "--config", "mini.yaml",
        "--config", "model.cost_tracking=ignore_errors",
        "--config", "model.model_kwargs.api_base=" + model["base_url"],
        "--config", "model.model_kwargs.api_key=" + model.get("api_key", "local-key"),
        "--task", task["prompt"],
        "--yolo",
        "--cost-limit", "0",
        "--exit-immediately",
    ]
    return run_command(command, workspace, env, int(agent.get("timeout_sec", 1800)), log_path)


def run_openhands(agent: dict[str, Any], workspace: Path, log_path: Path) -> tuple[int, str]:
    docker = shutil.which("docker") or shutil.which("podman")
    if not docker:
        log_path.write_text("OpenHands skipped: Docker/Podman is not installed on this machine.\n", encoding="utf-8")
        return 127, "missing Docker/Podman"
    log_path.write_text("OpenHands adapter is intentionally not auto-started yet; runtime config needs a container policy.\n", encoding="utf-8")
    return 78, "adapter not configured"


def task_looks_like_code_repair(task: dict[str, Any]) -> bool:
    if any("/test_" in f"/{path}" or path.startswith("tests/") for path in task.get("seed_files", {})):
        return True
    prompt = str(task.get("prompt") or "").lower()
    return any(marker in prompt for marker in ("pytest", "тест", "tests/", "python-проект", "python project"))


def shushunya_steps_from_journal(payload: dict[str, Any]) -> list[dict[str, Any]]:
    journal = payload.get("journal") if isinstance(payload.get("journal"), dict) else {}
    events = journal.get("events") if isinstance(journal.get("events"), list) else []
    steps: list[dict[str, Any]] = []
    repair_mode_steps: set[int] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        step = int(event.get("step") or 0)
        if event_type == "swe_repair_mode" and step:
            repair_mode_steps.add(step)
            continue
        if event_type == "action" and isinstance(event.get("action"), dict):
            item: dict[str, Any] = {
                "step": step,
                "_seq": len(steps) + 1,
                "action": event["action"],
            }
            if step in repair_mode_steps:
                item["mode"] = "swe_repair"
                item["mode_source_path"] = event.get("source_path")
            steps.append(item)
            continue
        if event_type == "tool_result" and steps:
            steps[-1]["result"] = event.get("result") if isinstance(event.get("result"), dict) else {}
    return steps


def analyze_shushunya_orchestration(log_path: Path, task: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": "log is not readable JSON"}
    steps = shushunya_steps_from_journal(payload)
    source = "task_journal" if steps else "response"
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    if not steps:
        steps = response.get("steps") if isinstance(response.get("steps"), list) else []
    if not task_looks_like_code_repair(task):
        return {
            "kind": "general_or_artifact",
            "ok": None,
            "steps": len(steps),
            "source": source,
            "note": "Code-repair orchestration chain is not required for this task type.",
        }
    edit_steps: list[int] = []
    failing_diagnostic_steps: list[int] = []
    passing_verification_steps: list[int] = []
    repair_mode_steps: list[int] = []
    verified_after_last_edit = False
    for item in steps:
        if not isinstance(item, dict):
            continue
        step = int(item.get("step") or 0)
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        action_type = str(action.get("action") or "")
        seq = int(item.get("_seq") or step)
        if item.get("mode") == "swe_repair":
            repair_mode_steps.append(seq)
        if action_type in {"write_file", "append_file", "replace_in_file"} and result.get("ok") is True:
            edit_steps.append(seq)
        failing_tests = result.get("failing_tests") if isinstance(result.get("failing_tests"), list) else []
        passing_tests = result.get("passing_tests") if isinstance(result.get("passing_tests"), list) else []
        if failing_tests:
            failing_diagnostic_steps.append(seq)
        if passing_tests and not failing_tests:
            passing_verification_steps.append(seq)
    if edit_steps:
        last_edit_step = edit_steps[-1]
        verified_after_last_edit = any(step > last_edit_step for step in passing_verification_steps)
    ok = bool(failing_diagnostic_steps and edit_steps and verified_after_last_edit)
    return {
        "ok": ok,
        "style": "main_agent_orchestrates_repair_function_then_verifies",
        "steps": len(steps),
        "source": source,
        "failing_diagnostic_steps": failing_diagnostic_steps,
        "edit_steps": edit_steps,
        "repair_mode_steps": repair_mode_steps,
        "passing_verification_steps": passing_verification_steps,
        "verified_after_last_edit": verified_after_last_edit,
    }


def evaluate_check(workspace: Path, check: dict[str, Any]) -> dict[str, Any]:
    kind = check["type"]
    sandbox_workspace = str(workspace) == "/work" or str(workspace).startswith("/work/")
    if kind == "file_exists":
        path = workspace / check["path"]
        if sandbox_workspace:
            host_path = sandbox_to_host(path)
            completed = run_as_sandbox_group(f"test -f {shlex.quote(str(host_path))}")
            return {**check, "ok": completed.returncode == 0, "sandbox_path": str(path)}
        return {**check, "ok": path.exists()}
    if kind == "file_contains":
        path = workspace / check["path"]
        if sandbox_workspace:
            host_path = sandbox_to_host(path)
            command = f"grep -F -- {shlex.quote(check['text'])} {shlex.quote(str(host_path))}"
            completed = run_as_sandbox_group(command)
            return {**check, "ok": completed.returncode == 0, "sandbox_path": str(path)}
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        return {**check, "ok": check["text"] in text}
    if kind == "command":
        command = check["command"]
        if sandbox_workspace:
            command = command.replace("__PYTHON__", "python3")
            inner = f"cd {shlex.quote(str(workspace))} && {command}"
            completed = run_as_sandbox_group(
                f"{shlex.quote(str(SANDBOX_RUNNER))} /usr/bin/bash -lc {shlex.quote(inner)}",
                timeout=int(check.get("timeout_sec", 60)),
            )
        else:
            command = command.replace("__PYTHON__", str(ROOT / ".venv" / "bin" / "python"))
            completed = subprocess.run(
                command,
                cwd=str(workspace),
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=int(check.get("timeout_sec", 60)),
            )
        return {**check, "command": command, "ok": completed.returncode == 0, "exit_code": completed.returncode, "output": completed.stdout[-4000:]}
    return {**check, "ok": False, "error": f"unknown check type {kind}"}


def run_one(agent_name: str, agent: dict[str, Any], model: dict[str, Any], task: dict[str, Any], run_id: str) -> RunResult:
    if agent["type"] == "shushunya":
        workspace = prepare_shushunya_workspace(task, agent_name, run_id)
    else:
        workspace = prepare_workspace(task, agent_name, run_id)
    log_path = RUNS / run_id / agent_name / f"{task['id']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    error = ""
    exit_code: int | None = None
    try:
        if agent["type"] == "shushunya":
            exit_code, error = run_shushunya(agent, task, workspace, run_id, log_path)
        elif agent["type"] == "aider":
            exit_code, error = run_aider(agent, model, task, workspace, log_path)
        elif agent["type"] == "mini_swe":
            exit_code, error = run_mini_swe(agent, model, task, workspace, log_path)
        elif agent["type"] == "openhands":
            exit_code, error = run_openhands(agent, workspace, log_path)
        else:
            exit_code, error = 2, f"unknown agent type {agent['type']}"
            log_path.write_text(error + "\n", encoding="utf-8")
    except Exception as exc:
        exit_code, error = 1, repr(exc)
        log_path.write_text(error + "\n", encoding="utf-8")
    checks = [evaluate_check(workspace, item) for item in task.get("checks", [])]
    orchestration = analyze_shushunya_orchestration(log_path, task) if agent["type"] == "shushunya" else None
    ok = exit_code == 0 and all(item.get("ok") for item in checks)
    return RunResult(
        agent=agent_name,
        task_id=task["id"],
        ok=ok,
        duration_sec=round(time.time() - started, 3),
        checks=checks,
        exit_code=exit_code,
        error="" if ok else error,
        log_path=str(log_path),
        workspace=str(workspace),
        orchestration=orchestration,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="smoke")
    parser.add_argument("--agents", default="", help="Comma-separated agent names. Defaults to enabled agents.")
    parser.add_argument("--tasks", default="", help="Comma-separated task ids. Defaults to all tasks in the suite.")
    args = parser.parse_args()

    config = load_json(ROOT / "agents.json")
    suite = load_json(ROOT / "benchmarks" / f"{args.suite}.json")
    selected = [item.strip() for item in args.agents.split(",") if item.strip()]
    selected_tasks = {item.strip() for item in args.tasks.split(",") if item.strip()}
    run_id = time.strftime("%Y%m%d-%H%M%S")
    report_path = REPORTS / f"{run_id}-{suite['suite']}.json"
    results: list[RunResult] = []

    for task in suite["tasks"]:
        if selected_tasks and task["id"] not in selected_tasks:
            continue
        for agent_name, agent in config["agents"].items():
            if selected and agent_name not in selected:
                continue
            if not agent.get("enabled", True):
                continue
            print(f"[arena] {agent_name} -> {task['id']}", flush=True)
            result = run_one(agent_name, agent, config["model"], task, run_id)
            print(f"[arena] {agent_name} -> {task['id']} ok={result.ok} exit={result.exit_code}", flush=True)
            results.append(result)
            write_report(report_path, run_id, suite, config["model"], results, partial=True)

    write_report(report_path, run_id, suite, config["model"], results, partial=False)
    print(f"[arena] report: {report_path}")
    return 0 if all(item.ok for item in results if item.agent != "openhands") else 1


if __name__ == "__main__":
    raise SystemExit(main())
