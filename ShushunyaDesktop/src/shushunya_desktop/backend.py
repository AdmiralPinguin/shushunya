from __future__ import annotations

import math
import threading
import time
from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    Property,
    Qt,
    QTimer,
    Signal,
    Slot,
)

from .companion import CompanionItem, CompanionProvider, CompanionSnapshot, idle_snapshot


class CompanionListModel(QAbstractListModel):
    ItemIdRole = Qt.UserRole + 1
    TextRole = Qt.UserRole + 2
    DetailRole = Qt.UserRole + 3
    PhaseRole = Qt.UserRole + 4
    TimestampRole = Qt.UserRole + 5

    def __init__(self) -> None:
        super().__init__()
        self._items: tuple[CompanionItem, ...] = ()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        return {
            self.ItemIdRole: item.item_id,
            self.TextRole: item.text,
            self.DetailRole: item.detail,
            self.PhaseRole: item.phase,
            self.TimestampRole: item.timestamp,
        }.get(role)

    def roleNames(self) -> dict[int, bytes]:  # noqa: N802
        return {
            self.ItemIdRole: b"itemId",
            self.TextRole: b"text",
            self.DetailRole: b"detail",
            self.PhaseRole: b"phase",
            self.TimestampRole: b"timestamp",
        }

    def replace(self, items: tuple[CompanionItem, ...]) -> None:
        if items == self._items:
            return
        self.beginResetModel()
        self._items = items
        self.endResetModel()


class CompanionViewModel(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._snapshot = idle_snapshot()
        self._activities = CompanionListModel()
        self._agenda = CompanionListModel()
        self._results = CompanionListModel()

    @Property(QObject, constant=True)
    def activities(self) -> CompanionListModel:
        return self._activities

    @Property(QObject, constant=True)
    def agenda(self) -> CompanionListModel:
        return self._agenda

    @Property(QObject, constant=True)
    def results(self) -> CompanionListModel:
        return self._results

    @Property(str, notify=changed)
    def name(self) -> str:
        return self._snapshot.name

    @Property(str, notify=changed)
    def presence(self) -> str:
        return self._snapshot.presence

    @Property(str, notify=changed)
    def utterance(self) -> str:
        return self._snapshot.utterance

    @Property(str, notify=changed)
    def currentActivity(self) -> str:  # noqa: N802
        return self._snapshot.current_activity

    @Property(str, notify=changed)
    def ownerRequest(self) -> str:  # noqa: N802
        return self._snapshot.owner_request

    @Property(str, notify=changed)
    def latestResult(self) -> str:  # noqa: N802
        return self._snapshot.latest_result

    @Property(bool, notify=changed)
    def hasActivities(self) -> bool:  # noqa: N802
        return bool(self._snapshot.activities)

    @Property(bool, notify=changed)
    def hasAgenda(self) -> bool:  # noqa: N802
        return bool(self._snapshot.agenda)

    @Property(bool, notify=changed)
    def hasResults(self) -> bool:  # noqa: N802
        return bool(self._snapshot.results)

    def apply(self, snapshot: CompanionSnapshot) -> None:
        if snapshot == self._snapshot:
            return
        self._snapshot = snapshot
        self._activities.replace(snapshot.activities)
        self._agenda.replace(snapshot.agenda)
        self._results.replace(snapshot.results)
        self.changed.emit()


class AppBackend(QObject):
    clockChanged = Signal()
    pulseChanged = Signal()
    quitRequested = Signal()
    snapshotRequested = Signal()
    _pollComplete = Signal(object)

    def __init__(self, provider: CompanionProvider) -> None:
        super().__init__()
        self._provider = provider
        self._companion = CompanionViewModel()
        self._clock_text = "--:--"
        self._date_text = ""
        self._pulse = 0.0
        self._polling = False

        self._pollComplete.connect(self._apply_poll)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(100)
        self._clock_timer.timeout.connect(self._tick)
        self._clock_timer.start()

        self._core_timer = QTimer(self)
        self._core_timer.setInterval(4000)
        self._core_timer.timeout.connect(self.refresh)
        self._core_timer.start()
        self._tick()
        QTimer.singleShot(50, self.refresh)

    @Property(QObject, constant=True)
    def companion(self) -> CompanionViewModel:
        return self._companion

    @Property(str, notify=clockChanged)
    def clockText(self) -> str:  # noqa: N802
        return self._clock_text

    @Property(str, notify=clockChanged)
    def dateText(self) -> str:  # noqa: N802
        return self._date_text

    @Property(float, notify=pulseChanged)
    def pulse(self) -> float:
        return self._pulse

    @Slot()
    def requestQuit(self) -> None:  # noqa: N802
        self.quitRequested.emit()

    @Slot()
    def requestSnapshot(self) -> None:  # noqa: N802
        self.snapshotRequested.emit()

    @Slot()
    def refresh(self) -> None:
        if self._polling:
            return
        self._polling = True
        threading.Thread(target=self._poll_worker, name="shushunya-companion", daemon=True).start()

    def _tick(self) -> None:
        now = datetime.now()
        clock_text = now.strftime("%H:%M")
        date_text = now.strftime("%d.%m.%Y")
        if clock_text != self._clock_text or date_text != self._date_text:
            self._clock_text = clock_text
            self._date_text = date_text
            self.clockChanged.emit()
        self._pulse = (math.sin(time.monotonic() * 1.1) + 1.0) / 2.0
        self.pulseChanged.emit()

    def _poll_worker(self) -> None:
        try:
            snapshot = self._provider.fetch()
        except Exception as exc:  # Keep machine diagnostics out of QML.
            print(f"companion state unavailable: {type(exc).__name__}: {exc}")
            snapshot = idle_snapshot()
        self._pollComplete.emit(snapshot)

    @Slot(object)
    def _apply_poll(self, payload: object) -> None:
        self._polling = False
        if isinstance(payload, CompanionSnapshot):
            self._companion.apply(payload)
