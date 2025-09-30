from __future__ import annotations
import os, httpx
from typing import List, Dict, Any

MODEL_7B_BASE  = os.getenv("MODEL_7B_BASE")
MODEL_20B_BASE = os.getenv("MODEL_20B_BASE")

async def chat_complete(route_name: str, purpose: str, user_text: str) -> Dict[str, Any]:
    if route_name == "20b":
        base = MODEL_20B_BASE
        model = "shushu-20b"
    elif route_name == "7b":
        base = MODEL_7B_BASE
        model = "shushu-7b"
    else:
        raise RuntimeError(f"unknown route model: {route_name}")

    if not base:
        raise RuntimeError(f"model base not configured for {route_name}")

    payload = {
        "model": model,
        "messages": [
            {"role":"system","content":"Отвечай кратко и по делу. Русский."},
            {"role":"user","content": user_text or ""}
        ],
        "temperature": 0.2,
        "max_tokens": 512
    }
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(f"{base}/chat/completions", json=payload)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    return {"text": content}
