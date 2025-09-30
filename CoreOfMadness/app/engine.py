from __future__ import annotations
import os
from typing import List, Dict, Any, Optional
import yaml
import httpx

_tf = None
_tokenizer = None
_model = None

class Engine:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.backend = cfg["engine"]["backend"].lower().strip()
        if self.backend not in ("lmstudio", "transformers"):
            raise ValueError(f"Unsupported backend: {self.backend}")
        if self.backend == "transformers":
            self._load_transformers()

    async def _lmstudio_chat(self, messages: List[Dict[str, str]], **gen_kwargs) -> str:
        base = self.cfg["engine"]["lmstudio_base_url"].rstrip("/")
        url = f"{base}/chat/completions"
        model = self.cfg["engine"].get("lmstudio_model", "gpt-neox-20b")
        payload = {
            "model": model,
            "messages": messages,
            "temperature": gen_kwargs.get("temperature", self.cfg["engine"]["temperature"]),
            "top_p": gen_kwargs.get("top_p", self.cfg["engine"]["top_p"]),
            "max_tokens": gen_kwargs.get("max_new_tokens", self.cfg["engine"]["max_new_tokens"]),
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
        return data["choices"][0]["message"]["content"]

    def _load_transformers(self):
        global _tf, _tokenizer, _model
        if _model is not None:
            return
        from transformers import AutoModelForCausalLM, AutoTokenizer, TextStreamer
        import torch

        model_id = self.cfg["engine"]["model_id"]
        quant = self.cfg["engine"]["quantization"]
        device_map = self.cfg["engine"]["device_map"]

        kwargs = {"device_map": device_map, "torch_dtype": torch.float16}

        if quant == "gptq":
            from auto_gptq import AutoGPTQForCausalLM
            _tokenizer = AutoTokenizer.from_pretrained(model_id, use_fast=False)
            _model = AutoGPTQForCausalLM.from_quantized(
                model_id,
                device_map=device_map,
                use_safetensors=True,
                trust_remote_code=True,
            )
        else:
            _tokenizer = AutoTokenizer.from_pretrained(model_id, use_fast=False)
            _model = AutoModelForCausalLM.from_pretrained(
                model_id, trust_remote_code=True, **kwargs
            )
        _tf = {"TextStreamer": TextStreamer}

    async def _transformers_chat(self, messages: List[Dict[str, str]], **gen_kwargs) -> str:
        global _tokenizer, _model
        from transformers import StoppingCriteria, StoppingCriteriaList
        import torch

        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = []
        if system:
            convo.append(f"[SYSTEM]\n{system}\n")
        for m in messages:
            if m["role"] == "user":
                convo.append(f"[USER]\n{m['content']}\n")
            elif m["role"] == "assistant":
                convo.append(f"[ASSISTANT]\n{m['content']}\n")
        convo.append("[ASSISTANT]\n")
        prompt = "\n".join(convo)

        inputs = _tokenizer(prompt, return_tensors="pt")
        inputs = {k: v.to(_model.device) for k, v in inputs.items()}

        max_new = int(gen_kwargs.get("max_new_tokens", self.cfg["engine"]["max_new_tokens"]))
        temperature = float(gen_kwargs.get("temperature", self.cfg["engine"]["temperature"]))
        top_p = float(gen_kwargs.get("top_p", self.cfg["engine"]["top_p"]))

        with torch.no_grad():
            output_ids = _model.generate(
                **inputs,
                do_sample=True,
                temperature=temperature,
                top_p=top_p,
                max_new_tokens=max_new,
                pad_token_id=_tokenizer.eos_token_id,
                eos_token_id=_tokenizer.eos_token_id,
            )
        text = _tokenizer.decode(output_ids[0], skip_special_tokens=True)
        if "[ASSISTANT]" in text:
            text = text.split("[ASSISTANT]")[-1].strip()
        return text

    async def generate(self, messages: List[Dict[str, str]], **gen_kwargs) -> str:
        if self.backend == "lmstudio":
            return await self._lmstudio_chat(messages, **gen_kwargs)
        return await self._transformers_chat(messages, **gen_kwargs)

def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or os.environ.get("COREMAD_CONFIG", "configs/default.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
