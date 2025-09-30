from __future__ import annotations
import os, httpx, json
from typing import Dict, Any
from pydantic import TypeAdapter
from .schemas import Plan

BASE = os.getenv("MODEL_7B_BASE","http://127.0.0.1:8021").rstrip("/")
MODEL = os.getenv("VLLM_MODEL","controller-3b")

SYS = ("Ты — контроллер-оркестратор. Верни только один JSON-план по схеме Plan "
       "(version, route_parts, steps[], criteria). Никаких пояснений.")
HINT = '{"version":"1.0","route_parts":{},"steps":[{"id":"s1","kind":"tool","call":{"tool":"tts.speak","args":{"text":"..."}},"wait_for":[],"emit":"speech"}],"criteria":{"success_when":["..."],"deliver":["speech"]}}'

def _json_from(text:str)->Dict[str,Any]:
    # берём только первый JSON в тексте
    start = text.find('{'); end = text.rfind('}')
    if start==-1 or end==-1: raise ValueError("no JSON found")
    return json.loads(text[start:end+1])

async def call_controller_7b(inp: Dict[str,Any]) -> Plan:
    url = f"{BASE}/v1/chat/completions"
    body = {
        "model": MODEL,
        "messages": [
            {"role":"system","content": SYS},
            {"role":"user","content": f"Вход: {inp}. Верни только JSON. Шаблон: {HINT}"},
        ],
        "temperature": 0.0,
        "max_tokens": 128,
        "response_format": {"type":"json_object"}
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as c:
        r = await c.post(url, json=body)
        r.raise_for_status()
        data = r.json()
    content = data["choices"][0]["message"]["content"]
    plan_dict = _json_from(content)
    return TypeAdapter(Plan).validate_python(plan_dict)
