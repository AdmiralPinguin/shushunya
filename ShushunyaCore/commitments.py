from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Iterator

from .ledger import (
    COMMITMENT_STATES,
    UNHAPPY_STATES,
    InvariantViolation,
    Ledger,
    canonical_json,
    sha256_json,
    utc_now,
)
from .organs import OrganError, Organs


TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
WORKING_STATES = {
    "running",
    "started",
    "accepted",
    "queued",
    "pending",
    "routing",
    "planning",
    "executing",
    "ready",
    "ready_to_preflight",
    "apply_intent",
    "applied_unverified",
    "publishing",
    "push_pending",
    "protocol_finalize_pending",
    "cancelling",
}
REVISION_STATES = {"revising", "revision", "needs_revision", "revision_required"}


def _retry_at(seconds: int = 30) -> str:
    return (datetime.now(UTC) + timedelta(seconds=max(1, seconds))).isoformat()


def _dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _dicts(child)


class Commitments:
    def __init__(self, ledger: Ledger, organs: Organs):
        self.ledger = ledger
        self.organs = organs

    def transition(
        self,
        commitment_id: str,
        state: str,
        *,
        honest_status: str,
        diagnostic: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        delegate_ref: str = "",
        next_attempt_at: str | None = None,
        increment_attempt: bool = False,
    ) -> dict[str, Any]:
        if state not in COMMITMENT_STATES:
            raise InvariantViolation(f"unknown commitment state: {state}")
        if state in UNHAPPY_STATES and not diagnostic:
            raise InvariantViolation(f"{state} requires an actionable diagnostic")
        with self.ledger.write() as db:
            row = db.execute("SELECT * FROM commitments WHERE id=?", (commitment_id,)).fetchone()
            if not row:
                raise KeyError("commitment not found")
            current = str(row["state"])
            if current in TERMINAL_STATES:
                return self._row(row)
            encoded_diagnostic = canonical_json(diagnostic) if diagnostic else None
            encoded_result = canonical_json(result) if result is not None else row["result_json"]
            if (
                current == state
                and str(row["honest_status"] or "") == honest_status
                and (not delegate_ref or str(row["delegate_ref"] or "") == delegate_ref)
                and row["diagnostic_json"] == encoded_diagnostic
                and row["result_json"] == encoded_result
                and row["next_attempt_at"] == next_attempt_at
                and not increment_attempt
            ):
                return self._row(row)
            version = int(row["version"])
            event = self.ledger._append_event(
                db,
                aggregate_type="commitment",
                aggregate_id=commitment_id,
                kind=f"commitment.{state}",
                actor="shushunya-steward",
                correlation_id=commitment_id,
                causation_event_id=None,
                payload={
                    "from": current,
                    "to": state,
                    "honest_status": honest_status,
                    "diagnostic": diagnostic,
                    "result": result,
                    "delegate_ref": delegate_ref or row["delegate_ref"],
                },
            )
            updated = db.execute(
                """
                UPDATE commitments SET state=?,version=version+1,next_attempt_at=?,
                    attempt_count=attempt_count+?,delegate_ref=?,honest_status=?,diagnostic_json=?,
                    result_json=?,last_event_seq=?,updated_at=?
                WHERE id=? AND version=?
                """,
                (
                    state,
                    next_attempt_at,
                    1 if increment_attempt else 0,
                    delegate_ref or row["delegate_ref"],
                    honest_status,
                    encoded_diagnostic,
                    encoded_result,
                    int(event["seq"]),
                    utc_now(),
                    commitment_id,
                    version,
                ),
            )
            if updated.rowcount != 1:
                raise InvariantViolation("stale commitment writer")
            updated_row = db.execute("SELECT * FROM commitments WHERE id=?", (commitment_id,)).fetchone()
            self.ledger.enqueue_quarantine_notification(
                db,
                commitment_row=updated_row,
                previous_state=current,
                diagnostic=diagnostic or {},
                event_seq=int(event["seq"]),
                delegate_ref=delegate_ref,
            )
            return self._row(updated_row)

    @staticmethod
    def _row(row) -> dict[str, Any]:
        import json

        item = dict(row)
        item["spec"] = json.loads(item.pop("spec_json"))
        item["diagnostic"] = json.loads(item.pop("diagnostic_json")) if item.get("diagnostic_json") else None
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
        return item

    @staticmethod
    def _needs_user_diagnostic(snapshot: dict[str, Any]) -> dict[str, Any] | None:
        for node in _dicts(snapshot):
            status = str(node.get("status") or node.get("phase") or "").lower()
            needs_user = node.get("needs_user") is True or status in {"needs_user", "waiting_user"}
            decision_request = (
                node.get("decision_request")
                if isinstance(node.get("decision_request"), dict)
                else {}
            )
            question = str(
                decision_request.get("question")
                or node.get("question")
                or node.get("user_question")
                or node.get("clarification_question")
                or ""
            ).strip()
            if needs_user and question:
                return {
                    "code": "abaddon_needs_user",
                    "explanation": question,
                    "evidence": {"task_id": snapshot.get("task_id"), "detail": node},
                    "required_action": question,
                    "resume_condition": "Ответ будет передан в ту же ожидающую миссию.",
                }
        return None

    @staticmethod
    def _external_diagnostic(snapshot: dict[str, Any]) -> dict[str, Any] | None:
        for node in _dicts(snapshot):
            code = str(node.get("code") or node.get("status") or node.get("phase") or "").lower()
            blocker = (
                node.get("external_blocker")
                or node.get("external_dependency")
                or node.get("dependency")
                or node.get("blocked_by")
            )
            resume = str(node.get("resume_condition") or node.get("required_action") or "").strip()
            explicitly_external = bool(blocker) or code in {"external_dependency", "waiting_external"}
            if not explicitly_external or not resume:
                continue
            return {
                "code": "external_dependency",
                "explanation": str(node.get("explanation") or blocker or "Миссия ждёт внешнюю зависимость."),
                "evidence": {"task_id": snapshot.get("task_id"), "detail": node},
                "required_action": str(node.get("required_action") or "Восстановить внешнюю зависимость."),
                "resume_condition": resume,
            }
        return None

    @staticmethod
    def _nested_state(snapshot: dict[str, Any]) -> str:
        summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
        result = summary.get("result") if isinstance(summary.get("result"), dict) else {}
        return str(result.get("phase") or result.get("status") or "").lower()

    def _bounded_retry(
        self,
        item: dict[str, Any],
        *,
        diagnostic: dict[str, Any],
        result: dict[str, Any] | None = None,
        seconds: int = 30,
    ) -> dict[str, Any]:
        attempt = int(item.get("attempt_count") or 0) + 1
        maximum = int(item.get("max_attempts") or 3)
        if attempt >= maximum:
            diagnostic = {
                **diagnostic,
                "required_action": diagnostic.get("required_action")
                or "Проверить контракт органа и выбрать подтверждаемый путь продолжения.",
                "resume_condition": diagnostic.get("resume_condition")
                or "Появится новый фактический статус или исправленный action contract.",
            }
            return self.transition(
                item["id"],
                "quarantined",
                honest_status=str(diagnostic["explanation"]),
                diagnostic=diagnostic,
                result=result,
                increment_attempt=True,
            )
        return self.transition(
            item["id"],
            "retry_wait",
            honest_status=str(diagnostic["explanation"]),
            diagnostic=diagnostic,
            result=result,
            next_attempt_at=_retry_at(seconds),
            increment_attempt=True,
        )

    async def _execute_continuation(
        self,
        item: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any] | None:
        action = self.organs.executable_action(snapshot)
        kind, method, path, _payload = self.organs._normalize_action(str(item["delegate_ref"]), action)
        if method != "POST" or not path:
            return None
        joined = f"{kind} {path}".lower()
        actionable = any(
            marker in joined
            for marker in ("revision", "resume", "apply_patch", "apply_verified", "retry_publish", "reconcile_mission", "reprepare")
        )
        phase = str(snapshot.get("phase") or "").lower()
        if "/start_http" in path and phase == "ready_to_start":
            actionable = True
        if not actionable:
            return None
        snapshot_hash = sha256_json(snapshot)
        prior = item.get("result") if isinstance(item.get("result"), dict) else {}
        prior_continuation = prior.get("continuation") if isinstance(prior.get("continuation"), dict) else {}
        if prior_continuation.get("snapshot_sha256") == snapshot_hash:
            return item
        if int(item.get("attempt_count") or 0) >= int(item.get("max_attempts") or 3):
            diagnostic = {
                "code": "continuation_budget_exhausted",
                "explanation": "Автоматические продолжения исчерпаны; Core остановил цикл вместо бесконечного перезапуска одной стратегии.",
                "evidence": {"snapshot": snapshot, "action": action},
                "required_action": "Абаддон или владелец должен выбрать новую стратегию, а не повторить тот же action.",
                "resume_condition": "Появится новая директива/миссия с отличающимся проверяемым планом.",
            }
            return self.transition(
                item["id"],
                "quarantined",
                honest_status=diagnostic["explanation"],
                diagnostic=diagnostic,
                result=snapshot,
            )
        try:
            dispatched = await self.organs.execute_abaddon_action(str(item["delegate_ref"]), snapshot)
        except OrganError as exc:
            diagnostic = {
                "code": exc.code,
                "explanation": exc.explanation,
                "evidence": {**exc.evidence, "snapshot": snapshot},
                "required_action": "Исправить опубликованную continuation-команду или повторить её после восстановления органа.",
                "resume_condition": "Абаддон опубликует и примет однозначный POST action.",
            }
            return self._bounded_retry(item, diagnostic=diagnostic, result=snapshot)
        new_ref = str(dispatched.get("task_id") or item["delegate_ref"])
        revision = "revision" in joined or "reprepare" in joined or self._nested_state(snapshot) in REVISION_STATES
        return self.transition(
            item["id"],
            "revising" if revision else "working",
            honest_status=(
                "Абаддон фактически принял команду ревизии; Core продолжает сверять результат."
                if revision
                else "Абаддон фактически принял continuation-команду; Core продолжает сверять результат."
            ),
            result={
                "snapshot": snapshot,
                "continuation": {
                    "snapshot_sha256": snapshot_hash,
                    "kind": kind,
                    "path": path,
                    "response": dispatched,
                },
            },
            delegate_ref=new_ref,
            increment_attempt=True,
        )

    async def reconcile_one(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("delegate_kind") != "abaddon" or not item.get("delegate_ref"):
            return item
        try:
            snapshot = await self.organs.inspect_abaddon(str(item["delegate_ref"]))
        except OrganError as exc:
            diagnostic = {
                "code": exc.code,
                "explanation": exc.explanation,
                "evidence": exc.evidence,
                "required_action": "Повторить сверку, не объявляя работу завершённой.",
                "resume_condition": "Абаддон снова отвечает на orchestration snapshot.",
            }
            return self._bounded_retry(item, diagnostic=diagnostic, seconds=30)

        status = str(snapshot.get("status") or "unknown").lower()
        phase = str(snapshot.get("phase") or "").lower()
        nested = self._nested_state(snapshot)
        needs_user = self._needs_user_diagnostic(snapshot)
        external = self._external_diagnostic(snapshot)
        revision_required = status in REVISION_STATES or phase in REVISION_STATES or nested in REVISION_STATES

        # The outer Abaddon run may be mechanically complete while its native
        # warband result still requires owner input, an external dependency or
        # another revision. Those nested facts outrank the outer wrapper state.
        if (
            (status in {"completed", "succeeded", "done"} or phase == "completed")
            and needs_user is None
            and external is None
            and not revision_required
        ):
            return self.transition(
                item["id"],
                "succeeded",
                honest_status="Абаддон подтвердил терминальное завершение; итог сохранён как факт.",
                result=snapshot,
            )
        if status == "cancelled" or phase == "cancelled":
            return self.transition(item["id"], "cancelled", honest_status="Миссия отменена.", result=snapshot)

        if needs_user:
            return self.transition(
                item["id"],
                "waiting_user",
                honest_status=needs_user["explanation"],
                diagnostic=needs_user,
                result=snapshot,
            )
        if external:
            return self.transition(
                item["id"],
                "waiting_external",
                honest_status=external["explanation"],
                diagnostic=external,
                result=snapshot,
            )

        # Publication phases mean the action already happened and verification
        # is ongoing; never resend apply merely because its idempotent action is
        # still visible in the snapshot.
        if status in {"apply_intent", "applied_unverified", "publishing", "push_pending", "protocol_finalize_pending", "cancelling"}:
            return self.transition(
                item["id"],
                "working",
                honest_status=f"Абаддон подтверждает фазу {status}; терминальный результат ещё не доказан.",
                result=snapshot,
            )

        # A terminal failure outranks any stale start/resume affordance left on
        # the wrapper snapshot. Replaying such an action only produces a 409 and
        # launders a proven failure into an apparently live retry loop.
        if status in {"failed", "corrupt", "preflight_failed"}:
            diagnostic = {
                "code": f"abaddon_{status}",
                "explanation": (
                    "Работа завершилась неуспешно и не опубликовала доказуемый путь ревизии."
                ),
                "evidence": snapshot,
                "required_action": "Сформировать новую стратегию, если цель всё ещё нужна.",
                "resume_condition": "Новая миссия с исправленной стратегией и явными критериями.",
            }
            return self.transition(
                item["id"],
                "failed",
                honest_status=diagnostic["explanation"],
                diagnostic=diagnostic,
                result=snapshot,
            )

        continuation = await self._execute_continuation(item, snapshot)
        if continuation is not None:
            return continuation

        if revision_required:
            return self.transition(
                item["id"],
                "revising",
                honest_status=f"Абаддон подтверждает внутреннюю ревизию ({nested or phase or status}); завершение ещё не доказано.",
                result=snapshot,
            )

        if status in {"blocked", "interrupted", "resume_required"} or phase in {"blocked", "resume_required", "inspect", "needs_attention"}:
            diagnostic = {
                "code": "abaddon_continuation_not_executable",
                "explanation": "Абаддон остановил миссию, но не дал исполнимую команду продолжения; Core не выдаёт это ни за работу, ни за терминальный провал.",
                "evidence": snapshot,
                "required_action": "Абаддон должен опубликовать конкретный POST action с причиной и телом команды.",
                "resume_condition": "В orchestration snapshot появится исполнимая revision/resume/apply/reprepare команда.",
            }
            return self._bounded_retry(item, diagnostic=diagnostic, result=snapshot, seconds=30)

        if status not in WORKING_STATES and phase not in WORKING_STATES:
            diagnostic = {
                "code": "unknown_abaddon_status",
                "explanation": f"Абаддон вернул неизвестное состояние {status or '<empty>'}/{phase or '<empty>'}; Core не будет выдавать его за живую работу.",
                "evidence": snapshot,
                "required_action": "Исправить orchestration snapshot или вернуть документированное состояние.",
                "resume_condition": "Абаддон вернёт однозначный status и фактические доказательства прогресса.",
            }
            return self._bounded_retry(item, diagnostic=diagnostic, result=snapshot, seconds=30)

        return self.transition(
            item["id"],
            "working",
            honest_status=f"Абаддон сообщает состояние {phase or status}; завершение ещё не подтверждено.",
            result=snapshot,
        )

    async def reconcile_all(self) -> dict[str, int]:
        items = self.ledger.list_commitments(include_terminal=False, limit=100)
        checked = 0
        changed = 0
        now = utc_now()
        for item in items:
            if item.get("delegate_kind") != "abaddon" or not item.get("delegate_ref"):
                continue
            # A waiting-user mission must remain observable: after Archive
            # delivers the answer directly to Abaddon, its next snapshot is
            # the durable evidence that this commitment resumed. Quarantined
            # work still requires an explicit recovery path.
            if item.get("state") == "quarantined":
                continue
            if item.get("state") == "retry_wait" and str(item.get("next_attempt_at") or "") > now:
                continue
            checked += 1
            before = (item["state"], item.get("honest_status"), item.get("version"))
            current = await self.reconcile_one(item)
            after = (current["state"], current.get("honest_status"), current.get("version"))
            changed += int(before != after)
        return {"checked": checked, "changed": changed}
