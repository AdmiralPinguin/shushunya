from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
import requests, os, time, uuid

LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8021")

app = FastAPI()

class ChatMsg(BaseModel):
    role: str
    content: str

class ChatReq(BaseModel):
    model: Optional[str]=None
    messages: List[ChatMsg]
    temperature: Optional[float]=0.7
    max_tokens: Optional[int]=512
    stream: Optional[bool]=False
    top_p: Optional[float]=0.9
    stop: Optional[List[str]]=None

@app.post("/v1/chat/completions")
def chat(req: ChatReq):
    if req.stream:
        raise HTTPException(400, "stream not supported in proxy")
    # simple chat template
    sys = ""
    user_assistant = []
    for m in req.messages:
        if m.role=="system": sys += m.content.strip()+"\n"
        else: user_assistant.append(f"{m.role.upper()}: {m.content.strip()}")
    prompt = (f"{sys}\n" if sys else "") + "\n".join(user_assistant) + "\nASSISTANT:"

    payload = {
        "prompt": prompt,
        "n_predict": req.max_tokens or 512,
        "temperature": req.temperature or 0.7,
        "top_p": req.top_p or 0.9,
    }
    if req.stop: payload["stop"] = req.stop

    r = requests.post(f"{LLAMA_URL}/completion", json=payload, timeout=600)
    if r.status_code!=200:
        raise HTTPException(r.status_code, r.text)
    out = r.json()
    text = out.get("content") or out.get("generation") or out.get("text") or ""
    now = int(time.time())
    return {
        "id": "chatcmpl-"+uuid.uuid4().hex,
        "object": "chat.completion",
        "created": now,
        "model": req.model or "llama-7b",
        "choices": [{
            "index": 0,
            "message": {"role":"assistant","content": text},
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": out.get("prompt_n_tokens", 0),
            "completion_tokens": out.get("n_tokens", 0),
            "total_tokens": out.get("prompt_n_tokens", 0)+out.get("n_tokens", 0),
        }
    }

class CompReq(BaseModel):
    model: Optional[str]=None
    prompt: str
    temperature: Optional[float]=0.7
    max_tokens: Optional[int]=512
    stream: Optional[bool]=False
    top_p: Optional[float]=0.9
    stop: Optional[List[str]]=None

@app.post("/v1/completions")
def completions(req: CompReq):
    if req.stream:
        raise HTTPException(400, "stream not supported in proxy")
    payload = {
        "prompt": req.prompt,
        "n_predict": req.max_tokens or 512,
        "temperature": req.temperature or 0.7,
        "top_p": req.top_p or 0.9,
    }
    if req.stop: payload["stop"] = req.stop
    r = requests.post(f"{LLAMA_URL}/completion", json=payload, timeout=600)
    if r.status_code!=200:
        raise HTTPException(r.status_code, r.text)
    out = r.json()
    text = out.get("content") or out.get("generation") or out.get("text") or ""
    now = int(time.time())
    return {
        "id": "cmpl-"+uuid.uuid4().hex,
        "object": "text_completion",
        "created": now,
        "model": req.model or "llama-7b",
        "choices": [{
            "index": 0,
            "text": text,
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": out.get("prompt_n_tokens", 0),
            "completion_tokens": out.get("n_tokens", 0),
            "total_tokens": out.get("prompt_n_tokens", 0)+out.get("n_tokens", 0),
        }
    }
