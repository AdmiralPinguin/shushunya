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


# A failed delegate run is evidence about one attempt, not the death of the
# durable goal. Only success or an explicit cancellation closes a commitment.
TERMINAL_STATES = {"succeeded", "cancelled"}
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
TASK_MEMORY_LINEAGE_ERRORS = {
    "invalid_abaddon_continuation",
    "invalid_task_memory_identity",
    "legacy_mission_lineage_migration_required",
    "mission_identity_conflict",
    "task_memory_auth_invalid",
    "task_memory_identity_conflict",
    "task_memory_identity_invalid",
    "task_memory_mission_missing",
    "task_memory_parent_conflict",
    "task_memory_read_rejected",
    "task_memory_reference_invalid",
    "task_memory_reference_missing",
    "task_memory_rejected",
}


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


def _task_memory_lineage_error(evidence: Any) -> str:
    """Return a durable-lineage rejection hidden in a downstream envelope."""
    for node in _dicts(evidence):
        for field in ("error_code", "code"):
            code = str(node.get(field) or "").strip().lower()
            if code in TASK_MEMORY_LINEAGE_ERRORS:
                return code
    return ""


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

    @staticmethod
    def _recovery_payload(
        item: dict[str, Any],
        snapshot: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        """Build one crash-stable child run for a failed immutable parent."""
        spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
        parent_task_id = str(item.get("delegate_ref") or "").strip()
        task_memory_id = str(
            spec.get("task_memory_id")
            or spec.get("goal_id")
            or spec.get("root_task_id")
            or spec.get("task_id")
            or item.get("id")
        ).strip()
        root_task_id = str(
            spec.get("root_task_id") or task_memory_id or spec.get("task_id")
        ).strip()
        prior_result = item.get("result") if isinstance(item.get("result"), dict) else {}
        recovery_generation = max(0, int(prior_result.get("recovery_generation") or 0))
        # Do not include attempt_count: a lost acknowledgement increments the
        # transport counter, but retry must reattach to this exact child run.
        recovery_digest = sha256_json(
            {
                "commitment_id": item.get("id"),
                "parent_task_id": parent_task_id,
                "task_memory_id": task_memory_id,
                "root_task_id": root_task_id,
                "recovery_generation": recovery_generation,
            }
        )[:24]
        task_id = f"core-recovery-{recovery_digest}"
        failure_guidance = {
            "code": f"abaddon_{status}",
            "explanation": (
                f"Неизменяемая попытка {parent_task_id} завершилась в состоянии {status}."
            ),
            "required_action": (
                "Разобрать доказательства провала и выбрать отличающуюся стратегию; "
                "не повторять stale action или тот же план."
            ),
            "resume_condition": (
                "Новая связанная попытка реализует изменённый план и заново проверяет исходные критерии успеха."
            ),
            "snapshot_sha256": sha256_json(snapshot),
        }
        original_message = str(spec.get("message") or "").strip()
        original_goal = str(item.get("goal") or "").strip()
        summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
        evidence_summary = canonical_json(summary)[:4_000] if summary else ""
        message_parts = [
            "Автономная recovery-миссия для всё ещё активной цели Шушуни.",
            f"Неизменяемая родительская попытка: {parent_task_id}",
            f"Корневая задача: {root_task_id}",
            f"Поколение recovery-стратегии: {recovery_generation}",
            f"Исходная цель: {original_goal}",
            failure_guidance["explanation"],
            f"Обязательное изменение стратегии: {failure_guidance['required_action']}",
        ]
        if original_message:
            message_parts.append("Исходная спецификация:\n" + original_message)
        if evidence_summary:
            message_parts.append("Краткие доказательства предыдущей попытки:\n" + evidence_summary)
        payload = {
            "message": "\n\n".join(message_parts),
            "task_id": task_id,
            "goal_id": task_memory_id,
            "task_memory_id": task_memory_id,
            "root_task_id": root_task_id,
            "parent_task_id": parent_task_id,
            "continuation_of": parent_task_id,
            "recovery_of": parent_task_id,
            "failure_guidance": failure_guidance,
            "recovery_generation": recovery_generation,
            "idempotency_key": f"recovery-{recovery_digest}",
        }
        if isinstance(spec.get("warmaster_request"), dict):
            payload["warmaster_request"] = dict(spec["warmaster_request"])
        return payload

    def _bounded_retry(
        self,
        item: dict[str, Any],
        *,
        diagnostic: dict[str, Any],
        result: dict[str, Any] | None = None,
        seconds: int = 30,
    ) -> dict[str, Any]:
        attempt = int(item.get("attempt_count") or 0) + 1
        maximum = max(1, int(item.get("max_attempts") or 3))
        delay = min(3_600, max(1, int(seconds)) * (2 ** min(max(0, attempt - 1), 7)))
        if attempt >= maximum:
            existing_action = str(diagnostic.get("required_action") or "").strip()
            strategy_action = (
                "Не повторять ту же транспортную попытку или тот же план. "
                "Сформировать отличающуюся проверяемую стратегию продолжения и новую попытку."
            )
            diagnostic = {
                **diagnostic,
                "strategy_review_required": True,
                "requires_user": False,
                "required_action": (
                    f"{existing_action} {strategy_action}".strip()
                    if existing_action
                    else strategy_action
                ),
                "resume_condition": diagnostic.get("resume_condition")
                or "Будет опубликована новая стратегия с отличающимся планом или исправленный action contract.",
            }
        return self.transition(
            item["id"],
            "retry_wait",
            honest_status=str(diagnostic["explanation"]),
            diagnostic=diagnostic,
            result=result,
            next_attempt_at=_retry_at(delay),
            increment_attempt=True,
        )

    async def _dispatch_recovery_attempt(
        self,
        item: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        status: str,
        diagnostic: dict[str, Any],
    ) -> dict[str, Any]:
        """Replace one inert immutable run with a linked autonomous attempt."""
        recovery_payload = self._recovery_payload(item, snapshot, status)
        try:
            dispatched = await self.organs.dispatch_abaddon(recovery_payload)
        except OrganError as exc:
            decision_request = (
                exc.evidence.get("decision_request")
                if isinstance(exc.evidence, dict)
                and isinstance(exc.evidence.get("decision_request"), dict)
                else {}
            )
            question = str(decision_request.get("question") or "").strip()
            if exc.code == "clarification_required" and question:
                needs_user = {
                    "code": "abaddon_needs_user",
                    "explanation": question,
                    "evidence": {
                        "previous_attempt": snapshot,
                        "recovery_task_id": recovery_payload["task_id"],
                        "detail": exc.evidence,
                    },
                    "required_action": question,
                    "resume_condition": "Ответ будет передан в связанную recovery-миссию.",
                }
                return self.transition(
                    item["id"],
                    "waiting_user",
                    honest_status=question,
                    diagnostic=needs_user,
                    result={"previous_attempt": snapshot, "recovery": recovery_payload},
                )
            lineage_error = _task_memory_lineage_error(exc.evidence)
            if lineage_error:
                lineage_repair = {
                    "code": "task_memory_lineage_repair_required",
                    "explanation": (
                        "Abaddon rejected the recovery attempt because its immutable "
                        "task-memory ancestry is inconsistent. The goal and prior "
                        "evidence remain preserved; generating more child ids cannot "
                        "repair provenance."
                    ),
                    "evidence": {
                        "downstream_error_code": lineage_error,
                        "previous_attempt": snapshot,
                        "recovery_payload": recovery_payload,
                        "dispatch_error": exc.evidence,
                    },
                    "external_dependency": "internal task-memory lineage reconciliation",
                    "required_action": (
                        "Reconcile the existing parent run, mission record, and Archive "
                        "page identity without rebinding or deleting their evidence."
                    ),
                    "resume_condition": (
                        "The parent task_memory.json, mission lineage, and Archive "
                        "root identity agree, after which this same recovery can be retried."
                    ),
                    "requires_user": False,
                }
                return self.transition(
                    item["id"],
                    "waiting_external",
                    honest_status=lineage_repair["explanation"],
                    diagnostic=lineage_repair,
                    result={"previous_attempt": snapshot, "recovery": recovery_payload},
                )
            external = self._external_diagnostic(exc.evidence)
            if external:
                return self.transition(
                    item["id"],
                    "waiting_external",
                    honest_status=external["explanation"],
                    diagnostic=external,
                    result={"previous_attempt": snapshot, "recovery": recovery_payload},
                )
            recovery_error = {
                **diagnostic,
                "code": exc.code,
                "explanation": (
                    f"Новая recovery-попытка {recovery_payload['task_id']} пока не подтверждена: "
                    f"{exc.explanation} Цель остаётся активной."
                ),
                "evidence": {
                    "previous_attempt": snapshot,
                    "recovery_payload": recovery_payload,
                    "dispatch_error": exc.evidence,
                },
            }
            if not exc.retryable:
                next_generation = int(recovery_payload.get("recovery_generation") or 0) + 1
                recovery_error.update(
                    {
                        "strategy_review_required": True,
                        "requires_user": False,
                        "required_action": (
                            "Доказанно отвергнутую recovery-попытку не повторять. "
                            "Сформировать следующее поколение стратегии с новым task_id."
                        ),
                        "resume_condition": (
                            "Абаддон примет новое поколение recovery-стратегии либо вернёт "
                            "конкретную внешнюю зависимость/решение владельца."
                        ),
                    }
                )
                return self._bounded_retry(
                    item,
                    diagnostic=recovery_error,
                    result={
                        "previous_attempt": snapshot,
                        "rejected_recovery": recovery_payload,
                        "recovery_generation": next_generation,
                    },
                    seconds=30,
                )
            return self._bounded_retry(
                item,
                diagnostic=recovery_error,
                result={
                    "previous_attempt": snapshot,
                    "recovery": recovery_payload,
                    "recovery_generation": int(
                        recovery_payload.get("recovery_generation") or 0
                    ),
                },
                seconds=30,
            )
        new_ref = str(
            dispatched.get("delegate_ref")
            or dispatched.get("task_id")
            or recovery_payload["task_id"]
        ).strip()
        return self.transition(
            item["id"],
            "working",
            honest_status=(
                f"Абаддон принял новую recovery-попытку {new_ref}; Core продолжает исходную цель."
            ),
            result={
                "previous_attempt": snapshot,
                "recovery": {
                    "payload": recovery_payload,
                    "response": dispatched,
                },
            },
            delegate_ref=new_ref,
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
                "explanation": "Лимит повторов одной continuation-стратегии исчерпан; цель остаётся активной и ждёт внутреннего пересмотра стратегии.",
                "evidence": {"snapshot": snapshot, "action": action},
                "required_action": "Абаддон должен выбрать новую проверяемую стратегию, а не повторить тот же action.",
                "resume_condition": "Появится новая внутренняя стратегия/миссия с отличающимся проверяемым планом.",
            }
            return await self._dispatch_recovery_attempt(
                item,
                snapshot,
                status="continuation_budget_exhausted",
                diagnostic=diagnostic,
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
            if exc.code == "abaddon_run_not_found":
                missing_snapshot = {
                    "task_id": str(item["delegate_ref"]),
                    "status": "failed",
                    "phase": "delegation_not_created",
                    "summary": {
                        "diagnostic": item.get("diagnostic") or {},
                        "last_result": item.get("result") or {},
                        "inspection": exc.evidence,
                    },
                }
                diagnostic = {
                    "code": "abaddon_attempt_missing",
                    "explanation": exc.explanation,
                    "evidence": missing_snapshot,
                    "strategy_review_required": True,
                    "requires_user": False,
                    "required_action": "Создать новую связанную попытку той же задачи; не ждать статус несуществующего run.",
                    "resume_condition": "Абаддон подтвердит новую попытку с той же страницей задачи.",
                }
                return await self._dispatch_recovery_attempt(
                    item,
                    missing_snapshot,
                    status="delegation_not_created",
                    diagnostic=diagnostic,
                )
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
        failure_states = {"failed", "corrupt", "preflight_failed"}
        failure_status = (
            status
            if status in failure_states
            else phase
            if phase in failure_states
            else nested
            if nested in failure_states
            else ""
        )

        # The outer Abaddon run may be mechanically complete while its native
        # warband result still requires owner input, an external dependency or
        # another revision. Those nested facts outrank the outer wrapper state.
        if (
            (status in {"completed", "succeeded", "done"} or phase == "completed")
            and needs_user is None
            and external is None
            and not revision_required
            and not failure_status
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

        # A failed Abaddon run is an immutable failed attempt, not a failed
        # durable goal. Never replay its stale start affordance; schedule an
        # internal strategy review that must produce a different attempt.
        if failure_status:
            diagnostic = {
                "code": f"abaddon_{failure_status}",
                "explanation": (
                    f"Попытка Абаддона завершилась в состоянии {failure_status}; цель остаётся активной и переведена на внутренний пересмотр стратегии."
                ),
                "evidence": snapshot,
                "strategy_review_required": True,
                "requires_user": False,
                "required_action": "Не запускать stale action этого run. Сформировать отличающуюся стратегию и создать новую связанную попытку с теми же критериями цели.",
                "resume_condition": "Core или Абаддон опубликует новую связанную попытку с исправленной стратегией и явными критериями.",
            }
            return await self._dispatch_recovery_attempt(
                item,
                snapshot,
                status=failure_status,
                diagnostic=diagnostic,
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
                "explanation": "Абаддон остановил попытку и не дал исполнимую команду продолжения; Core сохраняет её диагностику и меняет стратегию в новой попытке.",
                "evidence": snapshot,
                "strategy_review_required": True,
                "requires_user": False,
                "required_action": "Не ждать пустого status-блока и не повторять stale action. Создать новую связанную попытку с исправленной стратегией и конкретным исполнимым планом.",
                "resume_condition": "Новая связанная попытка принята Абаддоном и продолжает ту же долговечную цель.",
            }
            return await self._dispatch_recovery_attempt(
                item,
                snapshot,
                status="blocked_no_action",
                diagnostic=diagnostic,
            )

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
            # the durable evidence that this commitment resumed. Legacy
            # quarantined rows are reconciled too: quarantine must not make a
            # still-authorized durable goal disappear from the steward.
            if item.get("state") == "retry_wait" and str(item.get("next_attempt_at") or "") > now:
                continue
            checked += 1
            before = (item["state"], item.get("honest_status"), item.get("version"))
            current = await self.reconcile_one(item)
            after = (current["state"], current.get("honest_status"), current.get("version"))
            changed += int(before != after)
        return {"checked": checked, "changed": changed}
