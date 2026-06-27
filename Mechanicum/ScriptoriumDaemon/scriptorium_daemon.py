from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


PHASE_TITLES = {
    "prelude": "Предыстория",
    "arrival": "Прибытие",
    "parley": "Переговоры",
    "parley_collapse": "Срыв переговоров",
    "escalation": "Эскалация",
    "battle": "Битва",
    "turning_point": "Перелом",
    "betrayal": "Предательство",
    "aftermath_boundary": "Граница последствий",
}

KNOWN_RU_SUMMARIES = {
    "ec_claim_system": "Дети Императора первыми нашли Скалатракс и рассматривали его как убежище и опорную точку после бегства из Ока Ужаса.",
    "world_eaters_internal_dispute": "Среди Пожирателей Миров не было единой линии: часть командиров хотела немедленного удара, часть склонялась к отходу или переговорам.",
    "world_eaters_arrival": "Прибытие крупного флота Пожирателей Миров превратило Скалатракс из убежища Детей Императора в спорную добычу двух предательских легионов.",
    "anteus_hedonarch_presence": "На стороне Детей Императора в рассказах о переговорах фигурируют Тиберий Ангеллус Антей и Хедонарх.",
    "moon_parley": "Попытка решить конфликт через встречу на луне Скалатракса стала центральной точкой перед открытым кровопролитием.",
    "dreagher_shoots_anteus": "Выстрел Дреагера в Антея сломал переговоры и дал конфликту точку невозврата.",
    "golden_absolute": "Во время эскалации вокруг отряда Кхарна и корабля Детей Императора Golden Absolute события окончательно ушли от переговоров к насилию.",
    "planetary_battle": "После срыва договоренностей война перекинулась на сам Скалатракс: Пожиратели Миров атаковали, а Дети Императора удерживали свои позиции.",
    "cold_night_shelters": "Смертоносная ночь Скалатракса заставила даже сверхлюдей искать укрытие от холода, на время ломая обычный ход битвы.",
    "kharn_burns_shelters": "Кхарн начал выжигать убежища, убивая не только врагов, но и собственных братьев, прятавшихся от холода.",
    "fratricide_spreads": "После этого бой перестал быть только войной против Детей Императора: Пожиратели Миров стали массово резать друг друга.",
    "legion_fractures": "Итогом стала не просто победа или поражение, а окончательный распад Пожирателей Миров как единого легиона на военные банды.",
}


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def sibling_artifact(output_path: str, filename: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/{filename}"


def load_json_artifact(workspace_root: Path, path: str) -> dict[str, Any]:
    host_path = sandbox_path(workspace_root, path)
    if not host_path.exists():
        raise FileNotFoundError(path)
    payload = json.loads(host_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"artifact must be an object: {path}")
    return payload


def confidence_marker(value: Any) -> str:
    text = str(value or "").lower()
    if text in {"high", "medium-high"}:
        return ""
    if text == "medium":
        return " По этому пункту нужна сверка с первичным текстом."
    return " Уверенность по этому пункту ограничена."


def event_text(event: dict[str, Any], notes_by_id: dict[str, dict[str, Any]]) -> str:
    event_id = str(event.get("event_id") or "")
    note = notes_by_id.get(event_id, {})
    text = str(note.get("narrative_ru") or KNOWN_RU_SUMMARIES.get(event_id) or event.get("summary") or "")
    refs = event.get("source_refs") or note.get("source_refs") or []
    ref_text = ", ".join(str(item) for item in refs if item)
    suffix = confidence_marker(event.get("confidence") or note.get("confidence"))
    if ref_text:
        return f"{text}{suffix} Источники: {ref_text}."
    return f"{text}{suffix}"


def build_reconstruction(source_map: dict[str, Any], notes: dict[str, Any], timeline: dict[str, Any]) -> str:
    topic = str(timeline.get("topic") or notes.get("topic") or source_map.get("topic") or "задача")
    notes_by_id = {
        str(item.get("event_id")): item
        for item in notes.get("events", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    events = [item for item in timeline.get("timeline", []) if isinstance(item, dict)]
    by_phase: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_phase[str(event.get("phase") or "unknown")].append(event)

    lines = [
        f"# Реконструкция: {topic}",
        "",
        "Это рабочая реконструкция, собранная из извлеченных событий. Она отделяет прямой ход событий от последствий и не закрывает пробелы выдуманными деталями.",
        "",
    ]
    for phase, phase_events in by_phase.items():
        lines.append(f"## {PHASE_TITLES.get(phase, phase)}")
        lines.append("")
        for event in phase_events:
            lines.append(event_text(event, notes_by_id))
            lines.append("")
    contradictions = timeline.get("contradictions", [])
    if contradictions:
        lines.append("## Замечания к хронологии")
        lines.append("")
        for item in contradictions:
            if isinstance(item, dict):
                lines.append(f"- {item.get('topic')}: {item.get('note')}")
        lines.append("")
    gaps = list(source_map.get("coverage_gaps", [])) + list(notes.get("gaps", [])) + list(timeline.get("gaps", []))
    if gaps:
        lines.append("## Что еще надо проверить")
        lines.append("")
        for gap in dict.fromkeys(str(item) for item in gaps if item):
            lines.append(f"- {gap}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_coverage_report(source_map: dict[str, Any], notes: dict[str, Any], timeline: dict[str, Any]) -> str:
    sources = [item for item in source_map.get("sources", []) if isinstance(item, dict)]
    events = [item for item in timeline.get("timeline", []) if isinstance(item, dict)]
    gaps = list(source_map.get("coverage_gaps", [])) + list(notes.get("gaps", [])) + list(timeline.get("gaps", []))
    lines = [
        "# Coverage Report",
        "",
        f"- Sources mapped: {len(sources)}",
        f"- Direct events extracted: {len(notes.get('events', []))}",
        f"- Timeline events: {len(events)}",
        "",
        "## Sources",
        "",
    ]
    for source in sources:
        title = source.get("title", "")
        source_class = source.get("source_class", source.get("type", ""))
        reliability = source.get("reliability", "")
        use = source.get("expected_use", "")
        lines.append(f"- {title} | {source_class} | reliability={reliability} | {use}")
    lines.extend(["", "## Gaps", ""])
    for gap in dict.fromkeys(str(item) for item in gaps if item):
        lines.append(f"- {gap}")
    lines.extend(["", "## Event Coverage", ""])
    for event in events:
        refs = ", ".join(str(item) for item in event.get("source_refs", []) if item)
        lines.append(f"- {event.get('event_id')} | phase={event.get('phase')} | confidence={event.get('confidence')} | refs={refs}")
    return "\n".join(lines).rstrip() + "\n"


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    step = request.get("step")
    if not isinstance(step, dict):
        return {"ok": False, "worker": "ScriptoriumDaemon", "error": "request.step must be an object"}
    expected_artifacts = step.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or len(expected_artifacts) < 2:
        return {"ok": False, "worker": "ScriptoriumDaemon", "error": "step.expected_artifacts must contain reconstruction and coverage report"}
    reconstruction_path = str(expected_artifacts[0])
    coverage_path = str(expected_artifacts[1])
    source_path = sibling_artifact(reconstruction_path, "source_map.json")
    notes_path = sibling_artifact(reconstruction_path, "direct_event_notes.json")
    timeline_path = sibling_artifact(reconstruction_path, "timeline.json")
    try:
        source_map = load_json_artifact(workspace_root, source_path)
        notes = load_json_artifact(workspace_root, notes_path)
        timeline = load_json_artifact(workspace_root, timeline_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "worker": "ScriptoriumDaemon", "error": str(exc)}

    reconstruction = build_reconstruction(source_map, notes, timeline)
    coverage_report = build_coverage_report(source_map, notes, timeline)
    for output_path, content in ((reconstruction_path, reconstruction), (coverage_path, coverage_report)):
        host_path = sandbox_path(workspace_root, output_path)
        host_path.parent.mkdir(parents=True, exist_ok=True)
        host_path.write_text(content, encoding="utf-8")
    return {
        "ok": True,
        "worker": "ScriptoriumDaemon",
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Draft reconstruction and coverage report written.",
        "artifacts": [reconstruction_path, coverage_path],
        "gaps": list(dict.fromkeys(str(item) for item in timeline.get("gaps", []) if item)),
        "confidence": "medium",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run ScriptoriumDaemon on a Worker API request JSON.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/scriptorium-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    request = payload.get("request") if isinstance(payload, dict) and isinstance(payload.get("request"), dict) else payload
    result = run(request, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
