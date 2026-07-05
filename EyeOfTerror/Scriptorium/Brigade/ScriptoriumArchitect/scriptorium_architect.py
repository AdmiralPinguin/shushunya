from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BRIGADE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIGADE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIGADE_ROOT))

from scriptorium_model import model_unavailable_payload, parsed_model_content, request_required_scriptorium_guidance  # noqa: E402


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def sibling_artifact(output_path: str, filename: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/{filename}"


def load_optional_json(workspace_root: Path, path: str) -> dict[str, Any]:
    host_path = sandbox_path(workspace_root, path)
    if not host_path.exists():
        return {}
    payload = json.loads(host_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def research_intent_from_request(request: dict[str, Any]) -> dict[str, Any]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    intent = expectations.get("research_intent") if isinstance(expectations.get("research_intent"), dict) else {}
    if intent:
        return intent
    quality = expectations.get("step_quality") if isinstance(expectations.get("step_quality"), dict) else {}
    return quality.get("research_intent") if isinstance(quality.get("research_intent"), dict) else {}


def output_mode_sections(output_mode: str, needs_timeline: bool) -> list[dict[str, Any]]:
    common = [
        {
            "section_id": "source_base",
            "title": "Источник и границы уверенности",
            "requires_evidence": True,
            "required_claim_refs": [],
        }
    ]
    if output_mode == "short_answer":
        return common + [
            {"section_id": "answer", "title": "Краткий ответ", "requires_evidence": True, "required_claim_refs": []},
            {"section_id": "limits", "title": "Что не подтверждено", "requires_evidence": False, "required_claim_refs": []},
        ]
    if output_mode == "comparative_review":
        return common + [
            {"section_id": "side_a", "title": "Первая сторона", "requires_evidence": True, "required_claim_refs": []},
            {"section_id": "side_b", "title": "Вторая сторона", "requires_evidence": True, "required_claim_refs": []},
            {"section_id": "comparison", "title": "Сравнение и вывод", "requires_evidence": True, "required_claim_refs": []},
        ]
    if output_mode == "investigative_report":
        return common + [
            {"section_id": "hypotheses", "title": "Версии и проверка", "requires_evidence": True, "required_claim_refs": []},
            {"section_id": "findings", "title": "Выводы расследования", "requires_evidence": True, "required_claim_refs": []},
            {"section_id": "open_questions", "title": "Открытые вопросы", "requires_evidence": False, "required_claim_refs": []},
        ]
    if output_mode in {"book_manuscript", "book_manuscript_with_timeline"}:
        return common + [
            {"section_id": "book_opening", "title": "Вступление", "requires_evidence": True, "required_claim_refs": []},
            {"section_id": "book_body", "title": "Основная часть по главам", "requires_evidence": True, "required_claim_refs": []},
            {"section_id": "book_close", "title": "Итоги и незакрытые вопросы", "requires_evidence": False, "required_claim_refs": []},
        ]
    if output_mode == "longform_article":
        return common + [
            {"section_id": "context", "title": "Контекст", "requires_evidence": True, "required_claim_refs": []},
            {"section_id": "analysis", "title": "Разбор", "requires_evidence": True, "required_claim_refs": []},
            {"section_id": "conclusion", "title": "Вывод", "requires_evidence": True, "required_claim_refs": []},
        ]
    if output_mode == "event_reconstruction" or needs_timeline:
        return common + [
            {"section_id": "chronology", "title": "Хронология событий", "requires_evidence": True, "required_claim_refs": []},
            {"section_id": "turning_points", "title": "Переломные моменты", "requires_evidence": True, "required_claim_refs": []},
            {"section_id": "gaps", "title": "Пробелы реконструкции", "requires_evidence": False, "required_claim_refs": []},
        ]
    return common + [
        {"section_id": "overview", "title": "Обзор", "requires_evidence": True, "required_claim_refs": []},
        {"section_id": "analysis", "title": "Анализ", "requires_evidence": True, "required_claim_refs": []},
        {"section_id": "conclusion", "title": "Выводы", "requires_evidence": True, "required_claim_refs": []},
    ]


def claim_refs(research_corpus: dict[str, Any]) -> list[str]:
    claims = research_corpus.get("claims") if isinstance(research_corpus.get("claims"), list) else []
    return [str(item.get("claim_id") or "") for item in claims if isinstance(item, dict) and item.get("claim_id")]


def unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    if not isinstance(values, list):
        return result
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def claim_refs_per_section(output_mode: str) -> int:
    if output_mode == "short_answer":
        return 3
    if output_mode in {"book_manuscript", "book_manuscript_with_timeline", "longform_article"}:
        return 12
    if output_mode in {"event_reconstruction", "investigative_report", "comparative_review"}:
        return 8
    return 6


def attach_claim_refs(sections: list[dict[str, Any]], refs: list[str], output_mode: str) -> list[dict[str, Any]]:
    if not refs:
        return sections
    if output_mode in {"book_manuscript", "book_manuscript_with_timeline"}:
        for section in sections:
            if not section.get("requires_evidence"):
                continue
            section_id = str(section.get("section_id") or "")
            if section_id == "source_base":
                section["required_claim_refs"] = refs
            elif section_id == "book_opening":
                section["required_claim_refs"] = refs
            elif section_id == "book_body":
                section["required_claim_refs"] = refs
            else:
                section["required_claim_refs"] = refs
        return sections
    per_section = claim_refs_per_section(output_mode)
    for index, section in enumerate(sections):
        if section.get("requires_evidence"):
            section["required_claim_refs"] = refs[index :: max(1, len(sections))][:per_section] or refs[: min(per_section, len(refs))]
    return sections


def balanced_claim_groups(refs: list[str], chapter_count: int = 3) -> list[list[str]]:
    groups: list[list[str]] = [[] for _ in range(chapter_count)]
    for index, ref in enumerate(refs):
        groups[index % chapter_count].append(ref)
    if refs:
        for index, group in enumerate(groups):
            if not group:
                groups[index] = refs[: min(3, len(refs))]
    return groups


def model_chapter_candidates(guidance: dict[str, Any], valid_refs: set[str]) -> list[dict[str, Any]]:
    parsed = parsed_model_content(guidance)
    raw_chapters: Any = []
    if isinstance(parsed.get("book_outline"), dict):
        raw_chapters = parsed["book_outline"].get("chapters", [])
    if not raw_chapters and isinstance(parsed.get("chapter_plan"), dict):
        raw_chapters = parsed["chapter_plan"].get("chapters", [])
    if not raw_chapters:
        raw_chapters = parsed.get("chapters", [])
    chapters: list[dict[str, Any]] = []
    for raw in raw_chapters if isinstance(raw_chapters, list) else []:
        if not isinstance(raw, dict):
            continue
        refs = [ref for ref in unique_strings(raw.get("required_claim_refs")) if ref in valid_refs]
        if not refs:
            continue
        chapter_id = str(raw.get("chapter_id") or f"chapter_{len(chapters) + 1:02d}").strip()
        if not chapter_id.startswith("chapter_"):
            chapter_id = f"chapter_{len(chapters) + 1:02d}"
        chapters.append(
            {
                "chapter_id": chapter_id,
                "title": str(raw.get("title") or f"Глава {len(chapters) + 1}").strip(),
                "section_refs": unique_strings(raw.get("section_refs")),
                "required_claim_refs": refs,
                "planning_source": "model_guidance",
            }
        )
    return chapters


def fallback_chapter_titles(output_mode: str, needs_timeline: bool) -> list[str]:
    if output_mode == "book_manuscript_with_timeline" or needs_timeline:
        return ["Источники и предыстория", "Ход событий", "Итоги и спорные места"]
    return ["Источники и постановка темы", "Основной анализ", "Выводы и открытые вопросы"]


def next_chapter_id(used_ids: set[str], preferred_index: int) -> str:
    index = preferred_index
    while True:
        chapter_id = f"chapter_{index:02d}"
        if chapter_id not in used_ids:
            return chapter_id
        index += 1


def fill_book_chapters(
    model_chapters: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    refs: list[str],
    output_mode: str,
    needs_timeline: bool,
    chapter_count: int = 3,
) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for chapter in model_chapters:
        chapter_id = str(chapter.get("chapter_id") or f"chapter_{len(chapters) + 1:02d}")
        if chapter_id in used_ids:
            chapter_id = next_chapter_id(used_ids, len(chapters) + 1)
        updated = dict(chapter)
        updated["chapter_id"] = chapter_id
        chapters.append(updated)
        used_ids.add(chapter_id)
        if len(chapters) >= chapter_count:
            return chapters
    groups = balanced_claim_groups(refs, chapter_count)
    titles = fallback_chapter_titles(output_mode, needs_timeline)
    evidence_sections = [str(section.get("section_id") or "") for section in sections if section.get("requires_evidence")]
    while len(chapters) < chapter_count:
        index = len(chapters)
        chapter_id = next_chapter_id(used_ids, index + 1)
        refs_for_chapter = groups[index] if index < len(groups) else refs[: min(3, len(refs))]
        section_refs = ["source_base"] if index == 0 else evidence_sections if index == 1 else ["book_close"]
        chapters.append(
            {
                "chapter_id": chapter_id,
                "title": titles[index] if index < len(titles) else f"Глава {index + 1}",
                "section_refs": section_refs,
                "required_claim_refs": refs_for_chapter,
                "planning_source": "evidence_balanced_fallback",
            }
        )
        used_ids.add(chapter_id)
    return chapters


def build_book_outline(
    topic: str,
    sections: list[dict[str, Any]],
    refs: list[str],
    output_mode: str,
    needs_timeline: bool,
    guidance: dict[str, Any],
    chapter_count: int = 3,
) -> dict[str, Any]:
    model_chapters = model_chapter_candidates(guidance, set(refs))
    chapters = fill_book_chapters(model_chapters, sections, refs, output_mode, needs_timeline, chapter_count=chapter_count)
    return {
        "version": 1,
        "title": topic or "Research manuscript",
        "target_language": "ru",
        "planning_method": "model_guided_evidence_outline" if model_chapters else "evidence_balanced_outline",
        "chapter_count": len(chapters),
        "chapters": chapters,
    }


def build_synthesis_plan(request: dict[str, Any], research_corpus: dict[str, Any], structure_map: dict[str, Any]) -> dict[str, Any]:
    intent = research_intent_from_request(request)
    output_mode = str(intent.get("output_mode") or "research_report")
    chapter_count = max(1, min(24, int(intent.get("chapter_count") or 3))) if intent.get("needs_chapters") else 0
    refs = claim_refs(research_corpus)
    sections = attach_claim_refs(output_mode_sections(output_mode, bool(intent.get("needs_timeline"))), refs, output_mode)
    unsupported_sections = [
        {
            "section_id": section.get("section_id", ""),
            "reason": "section requires evidence trace but research_corpus has no claim refs",
        }
        for section in sections
        if section.get("requires_evidence") and not section.get("required_claim_refs")
    ]
    topic = str(research_corpus.get("topic") or structure_map.get("topic") or "")
    return {
        "version": 1,
        "task_id": request.get("task_id"),
        "intent": intent.get("intent", "topic_report"),
        "output_mode": output_mode,
        "required_depth": intent.get("required_depth", "deep"),
        "source_policy": intent.get("source_policy", "broad_sources_with_gaps_disclosed"),
        "needs_timeline": bool(intent.get("needs_timeline")),
        "needs_chapters": bool(intent.get("needs_chapters")),
        "chapter_count": chapter_count,
        "topic": topic,
        "style": {
            "language": "ru",
            "tone": "clear researched narrative",
            "citation_style": "inline source/evidence trace",
        },
        "target_length": {
            "short_answer": "800-1800 chars",
            "research_report": "6000-16000 chars",
            "comparative_review": "7000-18000 chars",
            "investigative_report": "9000-22000 chars",
            "event_reconstruction": "12000+ chars if evidence supports it",
            "longform_article": "18000+ chars if evidence supports it",
            "book_manuscript": "chaptered manuscript; length follows corpus",
            "book_manuscript_with_timeline": "chaptered manuscript with chronology; length follows corpus",
        }.get(output_mode, "6000-16000 chars"),
        "sections": sections,
        "source_requirements": {
            "min_sources": 2 if output_mode == "short_answer" else 6,
            "requires_evidence_trace": True,
            "requires_gap_disclosure": True,
        },
        "unsupported_sections": unsupported_sections,
        "evidence_trace": {
            "claim_refs": refs,
            "source_count": len(research_corpus.get("sources", []) if isinstance(research_corpus.get("sources"), list) else []),
            "quote_count": len(research_corpus.get("evidence_excerpts", []) if isinstance(research_corpus.get("evidence_excerpts"), list) else []),
        },
        "structure_inputs": {
            "has_timeline": bool(structure_map.get("timeline")),
            "source_order_count": len(structure_map.get("source_order", []) if isinstance(structure_map.get("source_order"), list) else []),
            "argument_flow_count": len(structure_map.get("argument_flow", []) if isinstance(structure_map.get("argument_flow"), list) else []),
        },
    }


def write_json(workspace_root: Path, path: str, payload: dict[str, Any]) -> None:
    host_path = sandbox_path(workspace_root, path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    step = request.get("step")
    if not isinstance(step, dict):
        return {"ok": False, "worker": "ScriptoriumArchitect", "error": "request.step must be an object"}
    expected_artifacts = step.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or not expected_artifacts:
        return {"ok": False, "worker": "ScriptoriumArchitect", "error": "step.expected_artifacts is empty"}
    synthesis_path = str(expected_artifacts[0])
    corpus_path = sibling_artifact(synthesis_path, "research_corpus.json")
    structure_path = sibling_artifact(synthesis_path, "structure_map.json")
    research_corpus = load_optional_json(workspace_root, corpus_path)
    if not research_corpus:
        return {"ok": False, "worker": "ScriptoriumArchitect", "error": "research_corpus is missing", "missing": corpus_path}
    structure_map = load_optional_json(workspace_root, structure_path)
    guidance = request_required_scriptorium_guidance(
        "ScriptoriumArchitect",
        request,
        {"task_id": request.get("task_id"), "step": step, "research_corpus": research_corpus, "structure_map": structure_map},
        "Plan the requested research synthesis. Return JSON guidance only; do not invent unsupported sections.",
    )
    if not guidance.get("ok"):
        return model_unavailable_payload("ScriptoriumArchitect", request.get("task_id"), guidance)
    plan = build_synthesis_plan(request, research_corpus, structure_map)
    plan["model_guidance"] = guidance
    write_json(workspace_root, synthesis_path, plan)
    artifacts = [synthesis_path]
    if plan.get("needs_chapters"):
        book_outline_path = next((str(item) for item in expected_artifacts if str(item).endswith("/book_outline.json")), sibling_artifact(synthesis_path, "book_outline.json"))
        chapter_plan_path = next((str(item) for item in expected_artifacts if str(item).endswith("/chapter_plan.json")), sibling_artifact(synthesis_path, "chapter_plan.json"))
        outline = build_book_outline(
            str(plan.get("topic") or ""),
            plan.get("sections", []),
            plan.get("evidence_trace", {}).get("claim_refs", []),
            str(plan.get("output_mode") or ""),
            bool(plan.get("needs_timeline")),
            guidance,
            chapter_count=max(1, min(24, int(plan.get("chapter_count") or 3))),
        )
        chapter_plan = {
            "version": 1,
            "planning_method": outline.get("planning_method", ""),
            "chapter_count": outline.get("chapter_count", len(outline["chapters"])),
            "chapters": outline["chapters"],
            "continuity_requirements": ["preserve source limits", "do not add unsupported scenes", "each chapter must retain its required evidence trace"],
        }
        write_json(workspace_root, book_outline_path, outline)
        write_json(workspace_root, chapter_plan_path, chapter_plan)
        artifacts.extend([book_outline_path, chapter_plan_path])
    return {
        "ok": True,
        "worker": "ScriptoriumArchitect",
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": f"Synthesis plan written for {plan['output_mode']}.",
        "artifacts": artifacts,
        "model_guidance": guidance,
        "gaps": [item.get("reason", "") for item in plan.get("unsupported_sections", [])],
        "confidence": "medium",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run ScriptoriumArchitect on a Worker API request JSON.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/scriptorium-architect-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    request = payload.get("request") if isinstance(payload, dict) and isinstance(payload.get("request"), dict) else payload
    result = run(request, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
