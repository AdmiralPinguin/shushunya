from __future__ import annotations
import os, httpx
from typing import Dict, Any

WARPWAILS_URL   = os.getenv("WARPWAILS_URL", "http://127.0.0.1:8009")
TTS_DEFAULT_SPK = os.getenv("TTS_DEFAULT_SPK", "kseniya")

class ToolError(Exception): ...

async def tts_speak(args: Dict[str, Any]) -> Dict[str, Any]:
    text = args.get("text")
    if not text:
        raise ToolError("tts.speak: missing 'text'")
    speaker = args.get("speaker", TTS_DEFAULT_SPK)
    payload = {"text": str(text).encode("utf-8").decode("utf-8"), "speaker": speaker}
    async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as c:
        async with c.stream("POST", f"{WARPWAILS_URL}/speak_full",
                            json=payload,
                            headers={"Content-Type":"application/json; charset=utf-8"}) as r:
            r.raise_for_status()
            async for _ in r.aiter_raw():
                pass  # полностью вычитаем поток, воспроизведение делает WarpWails
    return {"ok": True}

async def render_display(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "text": args.get("text","")}

TOOL_REGISTRY = {
    "tts.speak": tts_speak,
    "render.display": render_display,
}
