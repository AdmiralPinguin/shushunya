from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

MODEL = "Qwen/Qwen2.5-3B-Instruct"

app = FastAPI()
tokenizer = AutoTokenizer.from_pretrained(MODEL, use_fast=True)

# FP16 + auto placement на GPU
model = AutoModelForCausalLM.from_pretrained(
    MODEL,
    torch_dtype=torch.float16,
    device_map="auto",
    low_cpu_mem_usage=True,
)

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    max_tokens: int | None = 256
    temperature: float | None = 0.7
    top_p: float | None = 0.9

@app.post("/v1/chat/completions")
def chat(req: ChatRequest):
    turns = []
    for m in req.messages:
        role = "user" if m.role == "user" else "assistant"
        turns.append(f"{role}: {m.content}")
    prompt = "\n".join(turns) + "\nassistant:"

    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    output = model.generate(
        **inputs,
        max_new_tokens=req.max_tokens or 256,
        do_sample=True,
        temperature=req.temperature or 0.7,
        top_p=req.top_p or 0.9,
        eos_token_id=tokenizer.eos_token_id,
    )
    text = tokenizer.decode(output[0], skip_special_tokens=True)
    completion = text[len(prompt):].strip()

    return {
        "id": "chatcmpl-mini",
        "object": "chat.completion",
        "model": MODEL,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": completion}}
        ],
    }
