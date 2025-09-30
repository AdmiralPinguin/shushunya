from __future__ import annotations
import base64, os, httpx

WARPWAILS_URL = os.getenv("WARPWAILS_URL", "http://127.0.0.1:8009")
TTS_DEFAULT_SPK = os.getenv("TTS_DEFAULT_SPK", "kseniya")

class ToolError(Exception): ...

async def tts_speak(args: dict) -> dict:
    text = args.get("text")
    if not text:
        raise ToolError("tts.speak: missing 'text'")
    speaker = args.get("speaker", TTS_DEFAULT_SPK)
    # Гарантируем utf-8
    safe_text = str(text).encode("utf-8").decode("utf-8")
    payload = {"text": safe_text, "speaker": speaker}
    async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as c:
        r = await c.post(f"{WARPWAILS_URL}/speak_full", json=payload)
        r.raise_for_status()
        wav_bytes = r.content
    return {
        "type": "audio/wav",
        "speaker": speaker,
        "data_b64": base64.b64encode(wav_bytes).decode("ascii"),
    }

async def render_display(args: dict) -> dict:
    return {"ok": True, "text": args.get("text","")}

TOOL_REGISTRY = {
    "tts.speak": tts_speak,
    "render.display": render_display,
}
