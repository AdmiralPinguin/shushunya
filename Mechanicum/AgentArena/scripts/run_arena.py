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


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_report(path: Path, run_id: str, suite: dict[str, Any], model: dict[str, Any], results: list[RunResult], partial: bool) -> None:
    write_json(
        path,
        {
            "run_id": run_id,
            "suite": suite["suite"],
            "partial": partial,
            "model": model,
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
        "task_id": f"arena-{run_id}-{task['id']}",
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
    log_path.write_text(json.dumps({"status": status, "response": response}, ensure_ascii=False, indent=2), encoding="utf-8")
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
