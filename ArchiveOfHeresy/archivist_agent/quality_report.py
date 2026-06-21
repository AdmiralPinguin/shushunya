#!/usr/bin/env python3
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from archivist_agent.agent import LIBRARIAN_MODEL, LIBRARIAN_SYSTEM_PROMPT, extract_json, trim_text


REPORT_TASK_PROMPT = (
    "Ты изолированный архивариус ArchiveOfHeresy и проверяешь качество памяти за сутки. "
    "Оцени, помогала ли память, где она шумела, какие focus/wiki/vector/graph данные выглядят устаревшими, "
    "какие решения были сохранены хорошо или плохо, что надо исправить вручную. "
    "Отвечай только валидным JSON без markdown."
)


def report_date_from_value(value=None):
    if value:
        return date.fromisoformat(str(value))
    return datetime.now().astimezone().date() - timedelta(days=1)


def daily_path(root, report_date):
    return Path(root) / f"{report_date.year:04d}" / f"{report_date.month:02d}" / f"{report_date.isoformat()}.jsonl"


def load_jsonl(path, limit=None):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-limit:] if limit else rows


def latest_user_message(messages):
    for message in reversed(messages or []):
        if message.get("role") == "user":
            return str(message.get("content") or "").strip()
    return ""


def summarize_turn(record):
    magos_result = record.get("magos_result") if isinstance(record.get("magos_result"), dict) else {}
    return {
        "turn_id": record.get("turn_id"),
        "created_at": record.get("created_at"),
        "conversation_id": record.get("conversation_id"),
        "memory_namespace": record.get("memory_namespace") or "default",
        "status": record.get("status"),
        "focus_enabled": record.get("focus_enabled"),
        "vector_enabled": record.get("vector_enabled"),
        "graph_enabled": record.get("graph_enabled"),
        "magos_enabled": record.get("magos_enabled"),
        "magos": {
            "action": magos_result.get("action"),
            "focus_id": magos_result.get("focus_id"),
            "context_sources": magos_result.get("context_sources"),
            "memory_context_chars": magos_result.get("memory_context_chars"),
            "error": magos_result.get("error"),
        },
        "user_excerpt": trim_text(latest_user_message((record.get("request") or {}).get("messages", [])), 320),
        "assistant_excerpt": trim_text(((record.get("assistant_message") or {}).get("content") or ""), 320),
        "error": record.get("error"),
    }


def summarize_event(event):
    body = event.get("event") if isinstance(event.get("event"), dict) else {}
    return {
        "created_at": event.get("created_at"),
        "turn_id": event.get("turn_id"),
        "memory_namespace": event.get("memory_namespace"),
        "component": body.get("component"),
        "action": body.get("action"),
        "status": body.get("status"),
        "requester": body.get("requester"),
        "result": body.get("result"),
        "error": body.get("error"),
    }


def build_report_payload(report_date, jsonl_root, memory_events_root, catalogs=None, max_turns=24, max_events=50):
    turns = load_jsonl(daily_path(jsonl_root, report_date), limit=max_turns)
    events = load_jsonl(daily_path(memory_events_root, report_date), limit=max_events)
    statuses = {}
    namespaces = {}
    for turn in turns:
        statuses[turn.get("status") or "unknown"] = statuses.get(turn.get("status") or "unknown", 0) + 1
        namespace = turn.get("memory_namespace") or "default"
        namespaces[namespace] = namespaces.get(namespace, 0) + 1
    return {
        "date": report_date.isoformat(),
        "turn_count": len(turns),
        "status_counts": statuses,
        "namespace_counts": namespaces,
        "catalogs": catalogs or {},
        "turns": [summarize_turn(turn) for turn in turns],
        "memory_events": [summarize_event(event) for event in events],
        "output_schema": {
            "summary": "short overall quality assessment",
            "score": "integer 1..5",
            "what_worked": ["list"],
            "noise_or_risks": ["list"],
            "stale_or_conflicting_memory": ["list"],
            "retrieval_notes": ["list"],
            "recommended_actions": ["list"],
        },
    }


def ask_librarian_for_report(proxy_json, payload, model=None):
    request = {
        "model": model or LIBRARIAN_MODEL,
        "user": "archive-memory-quality",
        "messages": [
            {"role": "system", "content": LIBRARIAN_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"task": REPORT_TASK_PROMPT, "payload": payload}, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "max_tokens": 1800,
    }
    _status, response = proxy_json("POST", "/v1/chat/completions", payload=request, timeout=240)
    return extract_json(response["choices"][0]["message"].get("content", ""))


def report_paths(report_root, report_date):
    root = Path(report_root) / "memory_quality" / f"{report_date.year:04d}" / f"{report_date.month:02d}"
    root.mkdir(parents=True, exist_ok=True)
    stem = report_date.isoformat()
    return root / f"{stem}.json", root / f"{stem}.md"


def write_report(report_root, report_date, payload, assessment):
    json_path, md_path = report_paths(report_root, report_date)
    output = {"date": report_date.isoformat(), "payload": payload, "assessment": assessment}
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        f"# Memory Quality Report {report_date.isoformat()}",
        "",
        f"Score: {assessment.get('score', 'n/a')}/5",
        "",
        "## Summary",
        "",
        str(assessment.get("summary") or "").strip(),
        "",
    ]
    for key, title in (
        ("what_worked", "What Worked"),
        ("noise_or_risks", "Noise Or Risks"),
        ("stale_or_conflicting_memory", "Stale Or Conflicting Memory"),
        ("retrieval_notes", "Retrieval Notes"),
        ("recommended_actions", "Recommended Actions"),
    ):
        lines.extend([f"## {title}", ""])
        values = assessment.get(key) if isinstance(assessment.get(key), list) else []
        if values:
            lines.extend(f"- {item}" for item in values)
        else:
            lines.append("- none")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def generate_quality_report(proxy_json, jsonl_root, memory_events_root, report_root, report_date=None, catalogs=None, model=None):
    report_date = report_date_from_value(report_date)
    payload = build_report_payload(report_date, jsonl_root, memory_events_root, catalogs=catalogs)
    try:
        assessment = ask_librarian_for_report(proxy_json, payload, model=model)
    except Exception as exc:
        compact_payload = build_report_payload(
            report_date,
            jsonl_root,
            memory_events_root,
            catalogs=catalogs,
            max_turns=12,
            max_events=25,
        )
        try:
            payload = compact_payload
            assessment = ask_librarian_for_report(proxy_json, payload, model=model)
        except Exception as retry_exc:
            assessment = {
                "summary": f"Memory quality report failed: {retry_exc}",
                "score": 1,
                "what_worked": [],
                "noise_or_risks": [str(exc), str(retry_exc)],
                "stale_or_conflicting_memory": [],
                "retrieval_notes": [],
                "recommended_actions": ["Check ArchiveOfHeresy runtime log and rerun the report manually."],
            }
    paths = write_report(report_root, report_date, payload, assessment)
    return {"date": report_date.isoformat(), "paths": paths, "assessment": assessment}
