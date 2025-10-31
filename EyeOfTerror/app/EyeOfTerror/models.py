import httpx

client = httpx.AsyncClient(timeout=None)

ROUTE_MAP = {
    "7b": "http://127.0.0.1:8021",
    "20b": "http://127.0.0.1:8020",
    "warp": "http://127.0.0.1:8009",
}

def _normalize_url(base: str) -> str:
    base = base.strip()
    if base in ROUTE_MAP:
        return ROUTE_MAP[base]
    if not base.startswith("http://") and not base.startswith("https://"):
        return "http://" + base
    return base

async def chat_complete(base: str, purpose: str, user_text: str):
    base = _normalize_url(base)
    payload = {
        "model": "shushunya",
        "messages": [
            {"role": "system", "content": purpose},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0,
    }
    r = await client.post(f"{base}/chat/completions", json=payload)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]
