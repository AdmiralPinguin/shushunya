#!/usr/bin/env python3
from __future__ import annotations

from eye_of_terror.contracts import build_research_writing_contract, classify_research_intent
from eye_of_terror.inner_circle.iskandar import plan_research_writing
from eye_of_terror.pipeline import build_dispatch_packets


def assert_mode(task: str, expected_intent: str, expected_mode: str, needs_timeline: bool, needs_chapters: bool) -> None:
    profile = classify_research_intent(task)
    if (
        profile.get("intent") != expected_intent
        or profile.get("output_mode") != expected_mode
        or profile.get("needs_timeline") is not needs_timeline
        or profile.get("needs_chapters") is not needs_chapters
    ):
        raise AssertionError(f"bad mode classification for {task!r}: {profile}")
    contract = build_research_writing_contract(task, task_id=f"mode-{expected_intent}")
    payload = contract.to_dict()
    workers = [step["worker"] for step in payload["worker_plan"]]
    artifacts = payload["required_artifacts"]
    if "NoosphericExtractor" not in workers or "ScriptoriumArchitect" not in workers or "ScriptoriumDaemon" not in workers:
        raise AssertionError(f"research mode pipeline missing core workers: {payload}")
    if not artifacts or not all(str(path).startswith("/work/") for path in artifacts):
        raise AssertionError(f"research mode artifacts must stay in workspace paths: {payload}")
    if not any(path.endswith("/research_corpus.json") for path in artifacts):
        raise AssertionError(f"research mode missing research_corpus: {payload}")
    if not any(path.endswith("/synthesis_plan.json") for path in artifacts):
        raise AssertionError(f"research mode missing synthesis_plan: {payload}")
    if needs_timeline and not any(path.endswith("/timeline.json") for path in artifacts):
        raise AssertionError(f"timeline mode missing timeline: {payload}")
    if not needs_timeline and any(path.endswith("/timeline.json") for path in artifacts):
        raise AssertionError(f"non-timeline mode should not require timeline artifact: {payload}")
    if expected_intent not in {"qa_answer"} and not any(path.endswith("/structure_map.json") for path in artifacts):
        raise AssertionError(f"structured research mode missing structure_map: {payload}")
    if expected_intent == "qa_answer" and "Chronologis" in workers:
        raise AssertionError(f"short Q&A should not force Chronologis: {payload}")
    if needs_chapters:
        for suffix in ("/book_outline.json", "/chapter_plan.json", "/manuscript_ru.md", "/manuscript.fb2"):
            if not any(path.endswith(suffix) for path in artifacts):
                raise AssertionError(f"book mode missing artifact {suffix}: {payload}")
    plan = plan_research_writing(task, task_id=f"mode-{expected_intent}").to_dict()
    if not plan.get("ok") or plan.get("oversight", {}).get("research_intent", {}).get("intent") != expected_intent:
        raise AssertionError(f"research mode plan failed: {plan}")
    packets = build_dispatch_packets(contract, oversight=plan["oversight"])
    architect_packet = next((packet for packet in packets if packet.worker == "ScriptoriumArchitect"), None)
    if architect_packet is None or architect_packet.request.get("quality_expectations", {}).get("research_intent", {}).get("intent") != expected_intent:
        raise AssertionError(f"dispatch did not carry research intent to Architect: {[packet.to_dict() for packet in packets]}")


def main() -> int:
    assert_mode("Кто такой Искандар Хайон? Дай короткий ответ с источниками.", "qa_answer", "short_answer", False, False)
    assert_mode("Исследуй историю домашних 3D-принтеров и сделай report.", "topic_report", "research_report", False, False)
    assert_mode("Сравни CrewAI и AutoGen для локального агента.", "comparison", "comparative_review", False, False)
    assert_mode("Реконструируй события битвы при Скалатраксе.", "event_reconstruction", "event_reconstruction", True, False)
    assert_mode("Сделай longform article о локальных LLM агентах.", "longform_article", "longform_article", False, False)
    assert_mode("Напиши book на 3 chapters о локальных агентах.", "book", "book_manuscript", False, True)
    print("[ok] research mode contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
