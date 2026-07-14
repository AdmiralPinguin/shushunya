from __future__ import annotations

from .companion import CompanionItem, CompanionSnapshot


# This is also the order used by the unattended demo loop.  Keep it explicit:
# the sequence tells a small story instead of sorting states alphabetically.
DEMO_STATES: tuple[str, ...] = (
    "sleep",
    "attention",
    "thinking",
    "forging",
    "waiting",
    "speaking",
    "triumph",
    "wounded",
    "sealing",
)


_DEMO_SNAPSHOTS: dict[str, CompanionSnapshot] = {
    "sleep": CompanionSnapshot(
        name="Шушуня",
        presence="sleep",
        utterance="Я сплю. Ничего не исполняю, но дом остаётся под моим взглядом.",
    ),
    "attention": CompanionSnapshot(
        name="Шушуня",
        presence="attention",
        utterance="Я услышал тебя. Говори.",
    ),
    "thinking": CompanionSnapshot(
        name="Шушуня",
        presence="thinking",
        utterance="Дай мне мгновение. Я разбираю замысел и ещё ничего не утверждаю.",
        current_activity="Разбираю замысел и сверяю возможные пути",
        activities=(
            CompanionItem(
                "demo-thinking",
                "Разбираю замысел и сверяю возможные пути",
                "Это поиск решения, не заявление о готовом результате.",
                "now",
            ),
        ),
        agenda=(
            CompanionItem(
                "demo-thinking-next",
                "Выбрать проверяемый путь и перейти к исполнению",
                "Только после того, как решение станет достаточно определённым.",
                "next",
            ),
        ),
    ),
    "forging": CompanionSnapshot(
        name="Шушуня",
        presence="forging",
        utterance="Кую оболочку. Результат ещё не готов.",
        current_activity="Собираю автономную сцену из подготовленных частей",
        activities=(
            CompanionItem(
                "demo-forging",
                "Собираю автономную сцену из подготовленных частей",
                "Сейчас это незавершённая работа; готовность будет объявлена отдельно.",
                "now",
            ),
        ),
        agenda=(
            CompanionItem(
                "demo-forging-next",
                "Проверить сцену на всех подключённых экранах",
                "После завершения сборки.",
                "next",
            ),
        ),
    ),
    "waiting": CompanionSnapshot(
        name="Шушуня",
        presence="waiting",
        utterance="Я остановился у развилки. Здесь нужен твой выбор.",
        current_activity="Жду решения хозяина; работа не продолжается",
        owner_request="Выбери: сохранить спокойный ритм или сделать сцену агрессивнее.",
        activities=(
            CompanionItem(
                "demo-waiting",
                "Жду решения хозяина; работа не продолжается",
                "Выбери спокойный или агрессивный ритм.",
                "waiting",
            ),
        ),
    ),
    "speaking": CompanionSnapshot(
        name="Шушуня",
        presence="speaking",
        utterance="Смотри на меня, братушонок. Сейчас говорит вся машина, а не окно на ней.",
    ),
    "triumph": CompanionSnapshot(
        name="Шушуня",
        presence="triumph",
        utterance="Готово. Оно сопротивлялось меньше, чем я надеялся.",
        latest_result="Автономная сцена собрана и прошла демонстрационный цикл.",
        results=(
            CompanionItem(
                "demo-triumph",
                "Готово: автономная сцена собрана",
                "Демонстрационный цикл завершён без заявлений о подключении к Core.",
                "done",
            ),
        ),
    ),
    "wounded": CompanionSnapshot(
        name="Шушуня",
        presence="wounded",
        utterance="Я ранен. Исполнение сорвалось, и я не стану изображать победу.",
        latest_result="Сцена не завершена: один из визуальных слоёв не загрузился.",
        results=(
            CompanionItem(
                "demo-wounded",
                "Не завершено: визуальный слой не загрузился",
                "Это демонстрация честного сбоя; никакая работа в фоне не заявлена.",
                "failed",
            ),
        ),
    ),
    "sealing": CompanionSnapshot(
        name="Шушуня",
        presence="sealing",
        utterance="Запечатываю сеанс. Ещё мгновение — и всё лишнее погаснет.",
        current_activity="Закрываю демонстрационную сцену и сохраняю её состояние",
        activities=(
            CompanionItem(
                "demo-sealing",
                "Закрываю демонстрационную сцену и сохраняю её состояние",
                "Процесс ещё идёт; завершение не объявлено.",
                "now",
            ),
        ),
    ),
}


def _require_state(state: str) -> int:
    if not isinstance(state, str):
        raise TypeError("demo state must be a string")
    try:
        return DEMO_STATES.index(state)
    except ValueError:
        choices = ", ".join(DEMO_STATES)
        raise ValueError(f"unknown demo state {state!r}; expected one of: {choices}") from None


def _require_step(step: int) -> int:
    if not isinstance(step, int) or isinstance(step, bool):
        raise TypeError("demo state step must be an integer")
    return step


def demo_state_by_index(index: int) -> str:
    """Return a state from the cyclic order; negative indices wrap naturally."""

    index = _require_step(index)
    return DEMO_STATES[index % len(DEMO_STATES)]


def demo_snapshot_by_index(index: int) -> CompanionSnapshot:
    """Return the immutable fake snapshot at a cyclic index."""

    return demo_snapshot(demo_state_by_index(index))


def demo_snapshot(state: str) -> CompanionSnapshot:
    """Return an honest, self-contained fake snapshot for ``state``."""

    _require_state(state)
    return _DEMO_SNAPSHOTS[state]


def next_demo_state(state: str, step: int = 1) -> str:
    """Move through the demo cycle, wrapping at either end."""

    index = _require_state(state)
    return demo_state_by_index(index + _require_step(step))


def previous_demo_state(state: str, step: int = 1) -> str:
    """Move backwards through the demo cycle, wrapping at either end."""

    return next_demo_state(state, -_require_step(step))

