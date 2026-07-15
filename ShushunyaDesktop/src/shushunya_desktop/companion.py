from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
WAITING_STATES = {"waiting_user", "waiting_external", "retry_wait", "quarantined"}
SOURCE_DENY_MARKERS = ("smoke", "verification", "test", "preview", "idempotency", "codex-live")
PUBLIC_RESULT_KEYS = ("user_message", "answer", "report", "text", "final", "summary")
INTERNAL_TEXT_MARKERS = (
    "127.0.0.1",
    "http://",
    "https://",
    "task_id=",
    "commitment_id",
    "delegate_ref",
    "health endpoint",
    "status_code",
)


@dataclass(frozen=True, slots=True)
class CompanionItem:
    item_id: str
    text: str
    detail: str = ""
    phase: str = ""
    timestamp: str = ""
    steps: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompanionSnapshot:
    name: str = "Шушуня"
    presence: str = "idle"
    utterance: str = "Я здесь."
    current_activity: str = ""
    current_steps: tuple[str, ...] = ()
    owner_request: str = ""
    latest_result: str = ""
    activities: tuple[CompanionItem, ...] = ()
    agenda: tuple[CompanionItem, ...] = ()
    results: tuple[CompanionItem, ...] = ()


class CompanionProvider(Protocol):
    def fetch(self) -> CompanionSnapshot: ...


def idle_snapshot() -> CompanionSnapshot:
    return CompanionSnapshot()


def _text(value: Any, limit: int = 1_400) -> str:
    if not isinstance(value, str):
        return ""
    clean = re.sub(r"\s+", " ", value).strip()
    if not clean:
        return ""
    lowered = clean.casefold()
    if any(marker in lowered for marker in INTERNAL_TEXT_MARKERS):
        return ""
    return clean[:limit].rstrip()


def _goal(item: dict[str, Any]) -> str:
    goal = _text(item.get("goal"), 500)
    if not goal:
        spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
        for key in ("expected_outcome", "user_request", "task", "message", "title"):
            goal = _text(spec.get(key), 500)
            if goal:
                break
    goal = re.sub(r"^Записать в Administratum:\s*", "", goal, flags=re.IGNORECASE)
    return goal or "порученное дело"


def _diagnostic_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("required_action", "question", "explanation", "resume_condition"):
        found = _text(value.get(key), 700)
        if found:
            return found
    return ""


def _result_text(value: Any, depth: int = 0) -> str:
    if depth > 3 or not isinstance(value, dict):
        return ""
    for key in PUBLIC_RESULT_KEYS:
        candidate = value.get(key)
        direct = _text(candidate)
        if direct:
            return direct
        nested = _result_text(candidate, depth + 1)
        if nested:
            return nested
    return ""


def _commitment_items(commitments: list[dict[str, Any]]) -> tuple[tuple[CompanionItem, ...], tuple[CompanionItem, ...], str]:
    activities: list[CompanionItem] = []
    results: list[CompanionItem] = []
    owner_request = ""
    prefixes = {
        "queued": "Собираюсь",
        "working": "Сейчас занимаюсь",
        "revising": "Переделываю",
        "waiting_user": "Мне нужен твой ответ",
        "waiting_external": "Жду возможности продолжить",
        "retry_wait": "Жду возможности продолжить",
        "quarantined": "Разбираюсь с внутренней остановкой",
    }
    for item in commitments:
        state = str(item.get("state") or "queued").lower()
        goal = _goal(item)
        item_id = str(item.get("id") or "")
        timestamp = str(item.get("updated_at") or item.get("created_at") or "")
        if state in TERMINAL_STATES:
            if state == "succeeded":
                text = f"Готово: {goal}"
                detail = _result_text(item.get("result")) or "Я закончил."
                phase = "done"
            elif state == "failed":
                text = f"Не получилось: {goal}"
                detail = _diagnostic_text(item.get("diagnostic")) or "Я не смог честно завершить это."
                phase = "failed"
            else:
                text = f"Отменено: {goal}"
                detail = ""
                phase = "cancelled"
            results.append(CompanionItem(item_id, text, detail, phase, timestamp))
            continue

        prefix = prefixes.get(state, "Держу в работе")
        detail = _diagnostic_text(item.get("diagnostic")) if state in WAITING_STATES else ""
        # The worker's live plain-language steps, surfaced by Core on the
        # commitment so the owner can read what the fighter is actually doing.
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        raw_steps = result.get("activity_steps") if isinstance(result.get("activity_steps"), list) else []
        steps = tuple(
            _text(s.get("text"), 300)
            for s in raw_steps
            if isinstance(s, dict) and str(s.get("text") or "").strip()
        )[-30:]
        if steps and not detail and state in {"working", "revising"}:
            detail = steps[-1]
        if state == "quarantined":
            # Missing internal acknowledgement/recovery proof is Shushunya's
            # repair responsibility, never an implicit owner decision.
            detail = "Я не смог доказать продолжение и разбираюсь с этим внутри."
        if state == "waiting_user":
            phase = "waiting"
        elif state in {"waiting_external", "retry_wait", "quarantined"}:
            phase = "recovering"
        else:
            phase = "now" if state in {"working", "revising"} else "queued"
        activities.append(CompanionItem(item_id, f"{prefix}: {goal}", detail, phase, timestamp, steps))
        if not owner_request and state == "waiting_user":
            owner_request = detail or f"Нужно решить, как продолжить: {goal}"
    return tuple(activities), tuple(results), owner_request


def _agenda_items(payload: dict[str, Any]) -> tuple[CompanionItem, ...]:
    raw_items = payload.get("items") if isinstance(payload, dict) else []
    raw_items = raw_items if isinstance(raw_items, list) else []
    next_useful = payload.get("next_useful") if isinstance(payload.get("next_useful"), dict) else {}
    next_id = str(next_useful.get("id") or "")
    items: list[CompanionItem] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        state = str(item.get("state") or "queued").lower()
        if state in TERMINAL_STATES:
            continue
        item_id = str(item.get("id") or "")
        title = _text(item.get("title"), 500)
        if not title:
            continue
        phase = "now" if state == "working" else "next" if item_id and item_id == next_id else "later"
        detail = _text(item.get("stop_condition"), 500)
        timestamp = str(item.get("updated_at") or item.get("created_at") or "")
        items.append(CompanionItem(item_id, title, detail, phase, timestamp))
    return tuple(items)


def build_snapshot(
    *,
    name: str,
    commitments_payload: dict[str, Any],
    agenda_payload: dict[str, Any],
    utterance: str = "",
    latest_action: str = "",
    utterance_recent: bool = False,
) -> CompanionSnapshot:
    raw_commitments = commitments_payload.get("commitments") if isinstance(commitments_payload, dict) else []
    commitments = [item for item in raw_commitments if isinstance(item, dict)] if isinstance(raw_commitments, list) else []
    activities, results, owner_request = _commitment_items(commitments)
    agenda = _agenda_items(agenda_payload)
    visible_utterance = _text(utterance, 1_800) or "Я здесь."

    waiting = bool(owner_request) or (latest_action == "ask_clarification" and utterance_recent)
    active = any(item.phase in {"now", "queued"} for item in activities)
    planning = any(item.phase in {"now", "next"} for item in agenda)
    presence = "waiting" if waiting else "speaking" if utterance and utterance_recent else "thinking" if active or planning else "idle"
    current_activity = activities[0].text if activities else (f"Думаю о следующем: {agenda[0].text}" if agenda else "")
    current_steps = activities[0].steps if activities else ()
    latest_result = results[0].text if results else ""
    return CompanionSnapshot(
        name=_text(name, 120) or "Шушуня",
        presence=presence,
        utterance=visible_utterance,
        current_activity=current_activity,
        owner_request=owner_request,
        latest_result=latest_result,
        activities=activities,
        agenda=agenda,
        results=results,
    )


class CoreCompanionProvider:
    def __init__(self, base_url: str = "http://127.0.0.1:7600", timeout: float = 2.5) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.event_cursor = 0
        self.turn_sources: dict[str, str] = {}
        self.latest_utterance = ""
        self.latest_action = ""
        self.latest_utterance_at = ""

    def _get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(f"{self.base_url}{path}", headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read(4_000_000).decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("ok") is False:
            raise RuntimeError("ShushunyaCore returned an invalid read model")
        return payload

    @staticmethod
    def _source_is_public(source: str) -> bool:
        lowered = source.casefold()
        return bool(lowered) and not any(marker in lowered for marker in SOURCE_DENY_MARKERS)

    def _events(self) -> None:
        for _page in range(4):
            payload = self._get(f"/v1/events?after={self.event_cursor}&limit=500")
            batch = payload.get("events") if isinstance(payload.get("events"), list) else []
            if not batch:
                return
            for event in batch:
                if not isinstance(event, dict):
                    continue
                seq = int(event.get("seq") or 0)
                self.event_cursor = max(self.event_cursor, seq)
                turn_id = str(event.get("aggregate_id") or "")
                kind = str(event.get("kind") or "")
                body = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if kind == "turn.received":
                    self.turn_sources[turn_id] = str(body.get("source") or "")
                    continue
                if kind != "turn.resolved" or not self._source_is_public(self.turn_sources.get(turn_id, "")):
                    continue
                decision = body.get("decision") if isinstance(body.get("decision"), dict) else {}
                reply = _text(decision.get("reply"), 1_800)
                action = str(decision.get("action") or "")
                if reply and action in {"answer_in_chat", "ask_clarification"}:
                    self.latest_utterance = reply
                    self.latest_action = action
                    self.latest_utterance_at = str(event.get("occurred_at") or "")
            if len(batch) < 500:
                return

    def fetch(self) -> CompanionSnapshot:
        self_payload = self._get("/v1/self")
        commitments = self._get("/v1/commitments?include_terminal=true&limit=100")
        agenda = self._get("/v1/agenda?limit=100")
        self._events()
        identity = self_payload.get("identity") if isinstance(self_payload.get("identity"), dict) else {}
        identity = identity.get("identity") if isinstance(identity.get("identity"), dict) else {}
        utterance_recent = False
        try:
            occurred = datetime.fromisoformat(self.latest_utterance_at.replace("Z", "+00:00"))
            utterance_recent = (datetime.now(UTC) - occurred.astimezone(UTC)).total_seconds() <= 18
        except (TypeError, ValueError):
            pass
        return build_snapshot(
            name=str(identity.get("name") or "Шушуня"),
            commitments_payload=commitments,
            agenda_payload=agenda,
            utterance=self.latest_utterance,
            latest_action=self.latest_action,
            utterance_recent=utterance_recent,
        )


class DemoCompanionProvider:
    def __init__(self, scenario: str = "demo") -> None:
        self.scenario = scenario

    def fetch(self) -> CompanionSnapshot:
        if self.scenario == "empty":
            return CompanionSnapshot(
                presence="idle",
                utterance="Я рядом.",
            )
        if self.scenario == "stress":
            activities = tuple(
                CompanionItem(
                    f"stress-now-{index}",
                    f"Сейчас занимаюсь: проверяю длинную мысль номер {index} во всех форматах экрана",
                    "Оставляю только человеческий смысл, не показывая порты, идентификаторы, протоколы и внутреннюю машинную кухню.",
                    "now" if index == 1 else "queued",
                )
                for index in range(1, 5)
            )
            agenda = tuple(
                CompanionItem(
                    f"stress-next-{index}",
                    f"Следующий замысел номер {index}: продолжить превращение компьютера в единое живое присутствие",
                    "Остановлюсь, когда результат можно будет увидеть и понять без технического отчёта.",
                    "next" if index == 1 else "later",
                )
                for index in range(1, 6)
            )
            results = tuple(
                CompanionItem(
                    f"stress-done-{index}",
                    f"Готово: проверочный результат номер {index} сохранил смысл и не разорвал композицию",
                    "Это намеренно длинное пояснение проверяет перенос строк, ограничение высоты и прокрутку на маленьком горизонтальном и узком портретном экране.",
                    "done",
                )
                for index in range(1, 5)
            )
            return CompanionSnapshot(
                presence="waiting",
                utterance="Я всё вижу и ничего не потерял, даже когда мыслей стало слишком много для одного взгляда.",
                current_activity=activities[0].text,
                owner_request="Мне нужен твой ответ на очень длинный вопрос: какой из нескольких равноправных путей продолжать, если каждый меняет будущий характер Шушуни?",
                latest_result=results[0].text,
                activities=activities,
                agenda=agenda,
                results=results,
            )
        return CompanionSnapshot(
            presence="speaking",
            utterance="Я здесь. Машина больше не стоит между нами.",
            current_activity="Сейчас занимаюсь: собираю своё новое визуальное тело",
            owner_request="",
            latest_result="Готово: два экрана стали гранями одного разума",
            activities=(
                CompanionItem("preview-now", "Сейчас занимаюсь: собираю своё новое визуальное тело", "", "now"),
            ),
            agenda=(
                CompanionItem("preview-next", "Научиться показывать планы без машинной кухни", "", "next"),
                CompanionItem("preview-later", "Обрести голос и реагировать на твоё присутствие", "", "later"),
            ),
            results=(
                CompanionItem("preview-done", "Готово: два экрана стали гранями одного разума", "", "done"),
            ),
        )
