#!/usr/bin/env python3
"""Static contract checks for the Android Warmaster/brigade UI."""
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MAIN_ACTIVITY = ROOT / "app" / "src" / "main" / "java" / "com" / "shushunya" / "m" / "MainActivity.java"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    source = MAIN_ACTIVITY.read_text(encoding="utf-8")
    require("/archive/client/warmaster/start" in source, "main chat task launch must use the Warmaster client endpoint")
    require("/archive/client/warmaster/task" in source, "task polling must use the Warmaster client endpoint")
    require("/archive/client/warmaster/tasks" in source, "brigade monitor history must use the Warmaster client endpoint")
    require("/archive/client/warmaster/cancel" in source, "task cancellation must use the Warmaster client endpoint")
    require("/archive/client/warmaster/state" in source, "state checks must use the Warmaster client endpoint")
    require("/archive/client/agent/" not in source, "Android app must not call legacy client agent endpoints")
    require("agentInput" not in source, "brigade monitor must not expose a separate task input")
    require("submitAgentTask(" not in source, "brigade monitor must not submit tasks directly")
    require("runAgentTask(" not in source, "brigade monitor must not run a standalone task path")
    require("payload.put(\"message\", task)" in source, "Warmaster task payload must carry the user text as message")
    require("payload.put(\"task\", task)" not in source, "Warmaster launch payload must not use legacy raw task")
    print("[ok] Android Warmaster client contract")


if __name__ == "__main__":
    main()
