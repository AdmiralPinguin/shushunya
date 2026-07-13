#!/usr/bin/env python3
"""Static contract checks for the Android Abaddon UI and compatibility API."""
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MAIN_ACTIVITY = ROOT / "app" / "src" / "main" / "java" / "com" / "shushunya" / "m" / "MainActivity.java"
VOX_SERVICE = ROOT / "app" / "src" / "main" / "java" / "com" / "shushunya" / "m" / "VoxMessagingService.java"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    source = MAIN_ACTIVITY.read_text(encoding="utf-8")
    vox_source = VOX_SERVICE.read_text(encoding="utf-8")
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
    require("streamChatAnswerWithRecovery(" in source, "chat transport must retry a recoverable turn")
    recovery_method = source[
        source.index("private ChatStreamResult streamChatAnswerWithRecovery"):
        source.index("private ArtifactStreamOutcome artifactStreamOutcome")
    ]
    require(
        recovery_method.count("streamChatAnswer(text, imageDataUrl, clientRequestId, liveBubble)") == 2,
        "chat recovery must retry exactly once with the same durable request id",
    )
    require("Thread.sleep(1200)" in recovery_method, "chat recovery must avoid an immediate hot retry")
    require("/archive/client/agent/" not in source, "Android app must not call legacy client agent endpoints")
    require("/archive/mobile/" not in source, "Android app must not call mobile implementation endpoints directly")
    require("/archive/chat/" not in source, "Android app must not call raw archive chat endpoints directly")
    require("agentInput" not in source, "brigade monitor must not expose a separate task input")
    require("submitAgentTask(" not in source, "brigade monitor must not submit tasks directly")
    require("runAgentTask(" not in source, "brigade monitor must not run a standalone task path")
    require("payload.put(\"message\", task)" in source, "Warmaster task payload must carry the user text as message")
    require("payload.put(\"task\", task)" not in source, "Warmaster launch payload must not use legacy raw task")
    require('optString("conversation_message"' in source, "main chat must prefer the server conversation projection")
    route_method = source[source.index("private String routeConversationMessage"):source.index("private void resetAttachImageButton")]
    require('optString("message"' not in route_method, "main chat must fail closed instead of rendering a legacy route message")
    require("warmasterAcceptedChatMessage" not in source, "main chat must not synthesize a dispatcher acknowledgement")
    require("Абаддон пока не подтвердил приём; Core сохранил" not in source, "main chat fallback must not expose internal organs")
    require("Я попробую восстановить его сам" not in source, "rejected route fallback must not invent automatic repair")
    require('"needs_user_decision"' in source and '"internal_repair_required"' in source, "new mission-state labels are not rendered")
    require("conversation_body" in vox_source and "conversation_title" in vox_source, "push must prefer conversational fields")
    require("getNotification().getBody()" not in vox_source, "push must not trust an unprojected notification body")
    require('getData().get("body")' not in vox_source, "push must not fall back to a legacy raw body")
    require("looksLikeOperationalDispatch" in source and "looksLikeOperationalDispatch" in vox_source, "chat and push need an internal-detail guard")
    for marker in ("warmaster", "скитари", "церакси", "искандар", "бригад", "варбанд", "run_id"):
        require(f'lower.contains("{marker}")' in source, f"main chat guard misses {marker}")
        require(f'lower.contains("{marker}")' in vox_source, f"push guard misses {marker}")
    print("[ok] Android Abaddon UI and Warmaster API compatibility")


if __name__ == "__main__":
    main()
