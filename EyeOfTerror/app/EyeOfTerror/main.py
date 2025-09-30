from __future__ import annotations
import os, asyncio
from fastapi import FastAPI, HTTPException
from .schemas import InboundMessage, OrchestratorResult, Plan
from .controller import call_controller_7b
from .tools import TOOL_REGISTRY
from .models import chat_complete

HOST = os.getenv("EYE_HOST", "0.0.0.0")
PORT = int(os.getenv("EYE_PORT", "1488"))

app = FastAPI(title="EyeOfTerror", version="0.3.0")

@app.get("/healthz")
def healthz(): return {"ok": True}

@app.get('/debug/controller')
def debug_controller():
    from .controller import _state, BASE
    return {'base': BASE, 'endpoint': _state.get('endpoint'), 'last_error': _state.get('last_error')}

async def run_plan(plan: Plan, input_payload: dict, ctx: dict|None=None):
    ctx = ctx or {"input": input_payload}; logs=[]; done={}
    for step in plan.steps:
        for dep in step.wait_for:
            if dep not in done:
                raise HTTPException(500, f"dependency {dep} missing for {step.id}")
        if step.kind=="tool" and step.call:
            fn = TOOL_REGISTRY.get(step.call.tool)
            if not fn: raise HTTPException(400,f"unknown tool {step.call.tool}")
            args = dict(step.call.args or {})
            for k,v in list(args.items()):
                if isinstance(v,str) and v.startswith("${") and v.endswith("}"):
                    key=v[2:-1]; cur=ctx
                    for part in key.split('.'): cur = cur.get(part,{}) if isinstance(cur,dict) else {}
                    args[k]=cur if cur!={} else ""
            res = await fn(args)
            if step.emit: ctx[step.emit]=res
            done[step.id]=True; logs.append(f"tool {step.call.tool} -> {step.emit}")
        elif step.kind=="model" and step.route:
            user_text = ctx.get("input",{}).get("text") or ""
            res = await chat_complete(step.route.name, step.route.purpose, user_text)
            if step.emit: ctx[step.emit]=res
            done[step.id]=True; logs.append(f"model {step.route.name}/{step.route.purpose} -> {step.emit}")
        else:
            raise HTTPException(400,f"bad step {step.id}")
    return ctx, logs

@app.post("/route", response_model=OrchestratorResult)
async def route(msg: InboundMessage):
    # 1) план по входу
    plan_in = await call_controller_7b(msg.model_dump())
    ctx, logs1 = await run_plan(plan_in, msg.model_dump())

    # 2) если есть output от модели → разметить снова
    if "reply" in ctx or "full_text" in ctx:
        text_out = ctx.get("reply",{}).get("text") or ctx.get("full_text",{}).get("text") or ""
        plan_out = await call_controller_7b({"text": text_out, "phase":"postprocess"})
        ctx, logs2 = await run_plan(plan_out, {"text": text_out}, ctx)
        logs = logs1+logs2
    else:
        logs=logs1

    # собрать артефакты по deliver
    deliver = plan_out.criteria.deliver if 'plan_out' in locals() else plan_in.criteria.deliver
    artifacts = {k: ctx.get(k) for k in deliver}
    return OrchestratorResult(ok=True, artifacts=artifacts, logs=logs)
