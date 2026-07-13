from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from .commitments import Commitments
from .config import Settings
from .ledger import Ledger
from .organs import OrganError, Organs


LOG = logging.getLogger("shushunya.steward")


class Steward:
    """Continuous low-priority custodian.

    It advances only durable, already-authorized work. It never invents work to
    look busy, and it never sends an unknown effect merely because it is queued.
    """

    def __init__(self, settings: Settings, ledger: Ledger, organs: Organs, commitments: Commitments):
        self.settings = settings
        self.ledger = ledger
        self.organs = organs
        self.commitments = commitments
        self.worker_id = f"steward-{uuid.uuid4().hex[:12]}"
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._cycle_lock = asyncio.Lock()
        self.last_cycle: dict[str, Any] = {}

    async def dispatch_effect(self, effect_id: str = "") -> dict[str, Any] | None:
        claim = self.ledger.claim_outbox(
            self.worker_id,
            self.settings.effect_lease_sec,
            message_id=effect_id,
            destination="",
        )
        if not claim:
            if not effect_id:
                return None
            # An explicit foreground request may race the background steward.
            # Wait for the current fenced owner instead of reporting a false
            # failure while the delivery is still in progress.
            for _ in range(600):
                current = self.ledger.get_effect(effect_id)
                if not current or current.get("state") != "leased":
                    return current
                await asyncio.sleep(0.1)
            return self.ledger.get_effect(effect_id)
        try:
            if claim["destination"] == "abaddon":
                result = await self.organs.dispatch_abaddon(claim["payload"])
            elif claim["destination"] == "archive_adapter":
                result = await self.organs.dispatch_archive_adapter(str(claim["message_id"]), claim["payload"])
            elif claim["destination"] == "archive_artifact_adapter":
                result = await self.organs.dispatch_archive_artifact_adapter(
                    str(claim["message_id"]), claim["payload"],
                )
            else:
                raise OrganError(
                    "unknown_effect_destination",
                    f"Core не знает исполнителя {claim['destination']}.",
                    retryable=False,
                    evidence={"destination": claim["destination"]},
                )
        except OrganError as exc:
            outcome_type = str(exc.evidence.get("outcome_type") or "").strip()
            decision_request = (
                exc.evidence.get("decision_request")
                if isinstance(exc.evidence.get("decision_request"), dict)
                else {}
            )
            clarification = outcome_type == "needs_user_decision" or exc.code in {
                "administratum_needs_clarification",
                "clarification_required",
                "confirmation_required",
            }
            repair_required = outcome_type == "repair_required"
            organ_name = {
                "archive_adapter": "Archive/Administratum",
                "archive_artifact_adapter": "Archive/Artifacts",
                "abaddon": "Абаддон",
            }.get(str(claim["destination"]), str(claim["destination"]))
            return self.ledger.finish_effect(
                effect_id=str(claim["message_id"]),
                lease_token=str(claim["lease_token"]),
                ok=False,
                result={
                    "code": exc.code,
                    "explanation": exc.explanation,
                    "evidence": exc.evidence,
                    "required_action": (
                        str(decision_request.get("question") or "Дать недостающие параметры или подтверждение.")
                        if clarification
                        else str(exc.evidence.get("required_action") or "Выполнить внутреннее восстановление.")
                        if repair_required
                        else "Повторить тем же idempotency key или выбрать новый подтверждаемый путь."
                    ),
                    "resume_condition": (
                        str(decision_request.get("resume_condition") or "Получен ответ на запрос решения.")
                        if clarification
                        else str(
                            exc.evidence.get("resume_condition")
                            or "Внутреннее восстановление выполнено и запуск снова подтверждён."
                        )
                        if repair_required
                        else f"{organ_name} снова отвечает и даёт однозначный фактический результат."
                    ),
                },
                retryable=exc.retryable,
            )
        return self.ledger.finish_effect(
            effect_id=str(claim["message_id"]),
            lease_token=str(claim["lease_token"]),
            ok=True,
            result=result,
        )

    async def cycle(self) -> dict[str, Any]:
        # The HTTP maintenance endpoint and the background heartbeat share this
        # object. Serialize the complete inspect/dispatch/commit sequence so two
        # cycles cannot execute the same continuation before either records it.
        async with self._cycle_lock:
            return await self._cycle_once()

    async def _cycle_once(self) -> dict[str, Any]:
        health = await self.organs.refresh_health()
        dispatched = 0
        # Bound work per heartbeat. The interactive dispatcher remains free and
        # an accidental backlog cannot monopolize the event loop.
        for _ in range(3):
            effect = await self.dispatch_effect()
            if not effect:
                break
            dispatched += 1
            if effect.get("state") == "retry_wait":
                break
        reconciled = await self.commitments.reconcile_all()
        self.last_cycle = {
            "health": health,
            "effects_dispatched": dispatched,
            "commitments": reconciled,
        }
        return self.last_cycle

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.cycle()
            except Exception:
                LOG.exception("steward cycle failed; the foreground core remains alive")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.steward_interval_sec)
            except TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="shushunya-steward")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
