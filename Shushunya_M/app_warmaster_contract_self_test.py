#!/usr/bin/env python3
"""Static contract checks for the Android Abaddon UI and compatibility API."""
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
    require("АБАДДОН • WARBANDS" in source, "brigade UI must expose Abaddon as the public commander name")
    require("WARMASTER /" not in source, "legacy commander name must not remain in visible status banners")
    require('clean.equalsIgnoreCase("Warmaster") || clean.equalsIgnoreCase("Abaddon")' in source, "machine commander ids must map to the Abaddon public label")
    require('append(agentBrigadeLabel(revision))' in source, "state revision must not expose the legacy machine identity")
    require("/abaddon " in source and "!абаддон " in source and "абаддон:" in source and "abaddon:" in source, "Abaddon chat command aliases are incomplete")
    require("/warmaster " in source and "!вармастер " in source and "вармастер:" in source and "warmaster:" in source, "legacy command aliases must remain compatible")
    require("/archive/client/chat/completions" in source, "chat completions must use the client server facade")
    require("/archive/client/chat/messages" in source, "chat history must use the client server facade")
    require("/archive/client/agent/" not in source, "Android app must not call legacy client agent endpoints")
    require("/archive/mobile/" not in source, "Android app must not call mobile implementation endpoints directly")
    require("/archive/chat/" not in source, "Android app must not call raw archive chat endpoints directly")
    require("agentInput" not in source, "brigade monitor must not expose a separate task input")
    require("submitAgentTask(" not in source, "brigade monitor must not submit tasks directly")
    require("runAgentTask(" not in source, "brigade monitor must not run a standalone task path")
    require("payload.put(\"message\", task)" in source, "Warmaster task payload must carry the user text as message")
    require("payload.put(\"task\", task)" not in source, "Warmaster launch payload must not use legacy raw task")
    print("[ok] Android Abaddon UI and Warmaster API compatibility")


if __name__ == "__main__":
    main()
