from __future__ import annotations
from typing import Dict, Any
from .schemas import Plan, Step, Criteria, TargetModel, ToolCall

def build_plan(input_message: Dict[str, Any]) -> Plan:
    text = (input_message.get("text") or "").strip().lower()
    audio_b64 = input_message.get("audio_b64")
    steps = []; route_parts = {}

    if audio_b64:
        steps.append(Step(id="stt1", kind="tool",
            call=ToolCall(tool="stt.transcribe", args={"audio_b64": audio_b64}), emit="transcript"))
        steps.append(Step(id="tts1", kind="tool",
            call=ToolCall(tool="tts.speak", args={"text": "Принято. Распознал."}),
            wait_for=["stt1"], emit="ack_audio"))
        crit = Criteria(success_when=["transcript.text != ''"], deliver=["ack_audio","transcript"])
        return Plan(steps=steps, criteria=crit, route_parts=route_parts)

    if text.startswith("скажи:") or text.startswith("say:"):
        said = input_message.get("text")[6:].strip()
        steps.append(Step(id="tts1", kind="tool",
            call=ToolCall(tool="tts.speak", args={"text": said, "preset": "imp_light"}), emit="speech"))
        crit = Criteria(success_when=["len(speech.data_b64) > 0"], deliver=["speech"])
        return Plan(steps=steps, criteria=crit, route_parts=route_parts)

    steps.append(Step(id="llm1", kind="model", route=TargetModel(name="20b", purpose="chat"), emit="reply"))
    steps.append(Step(id="tts1", kind="tool",
        call=ToolCall(tool="tts.speak", args={"text": "${reply.text}"}), wait_for=["llm1"], emit="speech"))
    crit = Criteria(success_when=["reply.text != ''"], deliver=["reply","speech"])
    return Plan(steps=steps, criteria=crit, route_parts=route_parts)
