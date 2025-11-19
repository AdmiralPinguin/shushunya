import json, httpx
from pydantic import TypeAdapter
from .schemas import Plan

CTRL_BASE = "http://127.0.0.1:8021"  # адрес контроллера 7B

system_prompt = """Ты — контроллер EyeOfTerror.
Всегда отвечай строго одним JSON-объектом без лишнего текста.
Формат ответа:
{
  "version": "1.0",
  "route_parts": {},
  "steps": [
    {"id":"m1","kind":"model","route":{"name":"20b","purpose":"chat"},"wait_for":[],"emit":"reply"}
  ],
  "criteria": {
    "success_when": ["reply"],
    "deliver": ["reply"]
  }
}
Никаких пояснений, никакого текста вне JSON.
"""

def _json_from(text: str):
    start = text.find('{')
    end = text.rfind('}')
    js = text[start:end+1]
    return json.loads(js)

async def call_controller_7b(body: dict):
    async with httpx.AsyncClient(timeout=None) as c:
        payload = {
            "model": "7b",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": body["text"]}
            ]
        }
        r = await c.post(f"{CTRL_BASE}/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        plan = TypeAdapter(Plan).validate_python(_json_from(content))
        return plan
