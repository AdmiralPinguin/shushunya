from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings


class OrganError(RuntimeError):
    def __init__(self, code: str, explanation: str, *, retryable: bool = True, evidence: dict[str, Any] | None = None):
        super().__init__(explanation)
        self.code = code
        self.explanation = explanation
        self.retryable = retryable
        self.evidence = evidence or {}


class Organs:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._health: dict[str, Any] = {}
        self._health_at = 0.0
        self._health_lock = asyncio.Lock()

    async def _health_one(self, name: str, url: str, headers: dict[str, str] | None = None) -> tuple[str, dict[str, Any]]:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(url, headers=headers)
            payload = response.json() if response.content else {}
            return name, {
                "ready": 200 <= response.status_code < 300,
                "status_code": response.status_code,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "detail": payload if isinstance(payload, dict) else {},
            }
        except Exception as exc:  # a dead optional organ must not stop the personality
            return name, {
                "ready": False,
                "status_code": 0,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "error": str(exc)[:500],
            }

    async def refresh_health(self, force: bool = False) -> dict[str, Any]:
        if not force and self._health and time.monotonic() - self._health_at < self.settings.organ_health_ttl_sec:
            return self._health
        async with self._health_lock:
            if not force and self._health and time.monotonic() - self._health_at < self.settings.organ_health_ttl_sec:
                return self._health
            checks = await asyncio.gather(
                self._health_one("llm_dispatcher", f"{self.settings.llm_base_url}/models"),
                self._health_one("archive", f"{self.settings.archive_base_url}/health"),
                self._health_one("abaddon", f"{self.settings.abaddon_base_url}/health"),
                self._health_one("administratum", f"{self.settings.administratum_base_url}/health"),
                self._health_one("vox", f"{self.settings.vox_base_url}/health"),
                self._health_one("warpwails", f"{self.settings.warpwails_base_url}/health"),
            )
            self._health = dict(checks)
            self._health_at = time.monotonic()
            return self._health

    def health_snapshot(self) -> dict[str, Any]:
        return self._health

    def _archive_effect_headers(self) -> dict[str, str]:
        key = str(self.settings.archive_effect_key or "").strip()
        if len(key) < 32:
            raise OrganError(
                "archive_effect_auth_unconfigured",
                "Не настроен отдельный секрет Core->Archive; внутренний effect adapter закрыт.",
                retryable=False,
            )
        return {"X-Shushunya-Core-Key": key}

    async def dispatch_abaddon(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = str(payload.get("message") or "").strip()
        task_id = str(payload.get("task_id") or "").strip()
        if not message or not task_id:
            raise OrganError("invalid_abaddon_effect", "В запросе Абаддону нет message или стабильного task_id.", retryable=False)
        request = {
            "message": message,
            "task_id": task_id,
            "auto_start": True,
            "reuse_existing": True,
            "run_mode": "http",
            "governor_transport": "http",
            "governor_host": "127.0.0.1",
            "host": "127.0.0.1",
            "include_brigade_health": False,
        }
        try:
            async with httpx.AsyncClient(timeout=240.0) as client:
                response = await client.post(
                    f"{self.settings.abaddon_base_url}/orchestrate_run",
                    json=request,
                    headers={"Idempotency-Key": str(payload.get("idempotency_key") or task_id)},
                )
        except Exception as exc:
            raise OrganError(
                "abaddon_unreachable",
                f"Абаддон не подтвердил приём задачи: {exc}",
                retryable=True,
                evidence={"task_id": task_id},
            ) from exc
        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text[:2_000]}
        accepted_task_id = str(body.get("task_id") or "").strip() if isinstance(body, dict) else ""
        phase = str(body.get("phase") or body.get("status") or "").strip().lower() if isinstance(body, dict) else ""
        accepted = (
            response.status_code in {200, 202}
            and isinstance(body, dict)
            and body.get("ok") is True
            and accepted_task_id == task_id
            and bool(phase)
            and phase not in {"blocked", "failed", "cancelled", "rejected", "error"}
        )
        if not accepted:
            retryable = response.status_code >= 500 or response.status_code in {408, 409, 425, 429}
            raise OrganError(
                "abaddon_rejected",
                f"Абаддон вернул HTTP {response.status_code}, но не дал строгого подтверждения запуска.",
                retryable=retryable,
                evidence={"task_id": task_id, "response": body},
            )
        resolved_id = accepted_task_id
        return {
            "ok": True,
            "delegate_ref": resolved_id,
            "status": phase,
            "explanation": f"Абаддон принял задачу {resolved_id}; Core будет сверять фактический прогресс.",
            "evidence": {
                "http_status": response.status_code,
                "task_id": resolved_id,
                "phase": phase,
                "next_action": body.get("next_action"),
                "client_action": body.get("client_action"),
            },
        }

    async def dispatch_archive_adapter(self, effect_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Ask Archive to structure and persist an Administratum effect.

        The stable effect id is also the downstream dedupe key, so a lost HTTP
        acknowledgement can be retried without creating a second reminder.
        """
        # Authentication/configuration failures are factual and non-retryable;
        # resolve the header outside the transport exception wrapper.
        headers = self._archive_effect_headers()
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_sec) as client:
                response = await client.post(
                    f"{self.settings.archive_base_url}/archive/internal/core/administratum-effect",
                    json={"effect_id": effect_id, "payload": payload},
                    headers=headers,
                )
            body = response.json() if response.content else {}
        except Exception as exc:
            raise OrganError(
                "archive_adapter_unreachable",
                f"Archive не подтвердил запись в Administratum: {exc}",
                retryable=True,
                evidence={"effect_id": effect_id},
            ) from exc
        if not isinstance(body, dict) or response.status_code not in {200, 201} or body.get("ok") is not True:
            retryable = response.status_code >= 500 or response.status_code in {408, 425, 429}
            raise OrganError(
                str(body.get("code") or "administratum_not_created") if isinstance(body, dict) else "administratum_invalid_response",
                str(body.get("explanation") or "Archive не подтвердил создание задачи Administratum.")
                if isinstance(body, dict)
                else "Archive вернул ответ неверной формы.",
                retryable=retryable,
                evidence={"effect_id": effect_id, "response": body},
            )
        return {
            "ok": True,
            "delegate_ref": str(body.get("delegate_ref") or ""),
            "status": str(body.get("status") or "created"),
            "explanation": str(body.get("explanation") or "Administratum подтвердил запись задачи."),
            "evidence": body.get("evidence") if isinstance(body.get("evidence"), dict) else body,
        }

    async def dispatch_archive_artifact_adapter(self, effect_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Publish one registered artifact card through Archive exactly once."""
        artifact_id = str(payload.get("artifact_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        if not effect_id or not artifact_id or not session_id:
            raise OrganError(
                "invalid_artifact_effect",
                "В доставке файла нет effect_id, artifact_id или сессии владельца.",
                retryable=False,
            )
        headers = self._archive_effect_headers()
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_sec) as client:
                response = await client.post(
                    f"{self.settings.archive_base_url}/archive/internal/core/artifact-effect",
                    json={"effect_id": effect_id, "payload": payload},
                    headers=headers,
                )
            body = response.json() if response.content else {}
        except Exception as exc:
            raise OrganError(
                "archive_artifact_adapter_unreachable",
                f"Archive не подтвердил публикацию файла: {exc}",
                retryable=True,
                evidence={"effect_id": effect_id, "artifact_id": artifact_id},
            ) from exc
        if not isinstance(body, dict) or response.status_code not in {200, 201} or body.get("ok") is not True:
            retryable = response.status_code >= 500 or response.status_code in {408, 425, 429}
            raise OrganError(
                str(body.get("code") or "artifact_delivery_not_persisted")
                if isinstance(body, dict)
                else "artifact_delivery_invalid_response",
                str(body.get("explanation") or "Archive не подтвердил публикацию файла.")
                if isinstance(body, dict)
                else "Archive вернул ответ неверной формы.",
                retryable=retryable,
                evidence={"effect_id": effect_id, "artifact_id": artifact_id, "response": body},
            )
        returned_artifact_id = str(body.get("artifact_id") or "").strip()
        delegate_ref = str(body.get("delegate_ref") or "").strip()
        if returned_artifact_id != artifact_id or not delegate_ref:
            raise OrganError(
                "artifact_delivery_identity_mismatch",
                "Archive подтвердил публикацию без совпадающего artifact_id или сообщения доставки.",
                retryable=False,
                evidence={"effect_id": effect_id, "artifact_id": artifact_id, "response": body},
            )
        return {
            "ok": True,
            "delegate_ref": delegate_ref,
            "artifact_id": returned_artifact_id,
            "status": str(body.get("status") or "delivered"),
            "explanation": str(body.get("explanation") or "Archive сохранил файл в чате владельца."),
            "evidence": body.get("evidence") if isinstance(body.get("evidence"), dict) else body,
        }

    async def inspect_abaddon(self, task_id: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    f"{self.settings.abaddon_base_url}/runs/{quote(task_id, safe='')}/orchestration",
                    params={"events_after": 0, "event_limit": 20, "max_bytes": 12_000},
                )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise TypeError("orchestration snapshot must be a JSON object")
        except Exception as exc:
            raise OrganError(
                "abaddon_status_unavailable",
                f"Не удалось сверить задачу {task_id} с Абаддоном: {exc}",
                retryable=True,
            ) from exc
        snapshot = body.get("snapshot") if isinstance(body.get("snapshot"), dict) else {}
        summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
        mission_state = body.get("mission_state") if isinstance(body.get("mission_state"), dict) else {}
        if not mission_state and isinstance(snapshot.get("mission_state"), dict):
            mission_state = snapshot["mission_state"]
        status = str(summary.get("status") or mission_state.get("status") or body.get("status") or "unknown").lower()
        phase = str(body.get("phase") or summary.get("phase") or mission_state.get("phase") or "").lower()
        final = body.get("final")
        return {
            "task_id": task_id,
            "status": status,
            "phase": phase,
            "active": bool(body.get("active")),
            "mission_state": mission_state,
            "summary": summary,
            "result": body.get("result") if isinstance(body.get("result"), (dict, str, list)) else None,
            "final": final if isinstance(final, (dict, str)) else None,
            "next_action": body.get("next_action") if isinstance(body.get("next_action"), dict) else None,
            "client_action": body.get("client_action") if isinstance(body.get("client_action"), dict) else None,
        }

    @staticmethod
    def executable_action(snapshot: dict[str, Any]) -> dict[str, Any]:
        for field in ("client_action", "next_action"):
            action = snapshot.get(field)
            if not isinstance(action, dict):
                continue
            method = str(action.get("method") or "").upper().strip()
            path = str(action.get("path") or action.get("endpoint") or "").strip()
            if method and path:
                return dict(action)
        return {}

    @staticmethod
    def _normalize_action(task_id: str, action: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
        method = str(action.get("method") or "").upper().strip()
        raw_path = str(action.get("path") or action.get("endpoint") or "").strip()
        # next_action.endpoint is documented both as a plain path and as
        # ``POST /path``. The HTTP verb remains independently validated.
        if " " in raw_path and raw_path.split(" ", 1)[0].upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            verb, raw_path = raw_path.split(" ", 1)
            if not method:
                method = verb.upper()
        path = raw_path.replace("{task_id}", quote(task_id, safe=""))
        kind = str(action.get("kind") or action.get("action") or "").lower().strip()
        payload = action.get("body") if isinstance(action.get("body"), dict) else {}
        if not payload and isinstance(action.get("payload"), dict):
            # Compatibility only for a hand-authored action; Abaddon's current
            # contract always publishes ``body``.
            payload = action["payload"]
        return kind, method, path, dict(payload)

    async def execute_abaddon_action(self, task_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Execute one narrowly validated continuation published by Abaddon."""
        action = self.executable_action(snapshot)
        kind, method, path, payload = self._normalize_action(task_id, action)
        base = f"/runs/{quote(task_id, safe='')}"
        allowed_task_paths = {
            f"{base}/start_http",
            f"{base}/start_revision_http",
            f"{base}/start_revision_local",
            f"{base}/execute_revision_http",
            f"{base}/execute_revision_local",
            f"{base}/start_resume_http",
            f"{base}/start_resume_local",
            f"{base}/resume_http",
            f"{base}/resume_local",
            f"{base}/apply_patch",
        }
        reprepare_kinds = {
            "reprepare_ceraxia_run",
            "legacy_ceraxia_reprepare_required",
            "reprepare_native_research_run",
        }
        reprepare = path == "/orchestrate_run" and kind in reprepare_kinds
        if method != "POST" or (path not in allowed_task_paths and not reprepare):
            raise OrganError(
                "continuation_action_missing",
                "Абаддон сообщил остановку, но не опубликовал разрешённую исполнимую continuation-команду.",
                retryable=False,
                evidence={"task_id": task_id, "next_action": snapshot.get("next_action"), "client_action": snapshot.get("client_action")},
            )
        if path.endswith("/apply_patch"):
            hashes = (
                payload.get("expected_repository_fingerprint"),
                payload.get("expected_patch_sha256"),
                payload.get("expected_checks_sha256"),
            )
            if payload.get("confirm_apply") is not True or not all(
                re.fullmatch(r"[0-9a-fA-F]{64}", str(value or "")) for value in hashes
            ):
                raise OrganError(
                    "unsafe_apply_action",
                    "Абаддон не дал тройную SHA-фиксацию проверенного патча; Core не будет применять неопределённый результат.",
                    retryable=False,
                    evidence={"task_id": task_id, "action": action},
                )
        try:
            async with httpx.AsyncClient(timeout=240.0) as client:
                response = await client.post(f"{self.settings.abaddon_base_url}{path}", json=payload)
            body = response.json() if response.content else {}
        except Exception as exc:
            raise OrganError("continuation_request_failed", f"Не удалось отправить Абаддону continuation-команду: {exc}", retryable=True) from exc
        pending_phases = {
            "apply_intent",
            "applied_unverified",
            "publishing",
            "push_pending",
            "protocol_finalize_pending",
        }
        response_phase = str(body.get("phase") or body.get("status") or "").lower() if isinstance(body, dict) else ""
        accepted_pending = response.status_code == 202 and response_phase in pending_phases
        if (
            response.status_code not in {200, 202}
            or not isinstance(body, dict)
            or (body.get("ok") is not True and not accepted_pending)
        ):
            raise OrganError(
                "continuation_rejected",
                f"Абаддон не принял continuation-команду (HTTP {response.status_code}).",
                retryable=response.status_code >= 500 or response.status_code in {408, 425, 429},
                evidence={"task_id": task_id, "response": body},
            )
        returned_task_id = str(body.get("task_id") or "").strip()
        if not returned_task_id or (not reprepare and returned_task_id != task_id):
            raise OrganError(
                "continuation_identity_mismatch",
                "Абаддон подтвердил команду без совпадающего task_id; такой ответ нельзя приписать этой миссии.",
                retryable=False,
                evidence={"expected_task_id": task_id, "response": body},
            )
        return {
            **body,
            "task_id": returned_task_id,
            "action_kind": kind,
            "action_path": path,
            "reprepared": reprepare,
        }

    async def request_abaddon_revision(self, task_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Compatibility wrapper for callers that specifically require revision."""
        action = self.executable_action(snapshot)
        kind, _method, path, _payload = self._normalize_action(task_id, action)
        if "revision" not in f"{kind} {path}" and "reprepare" not in f"{kind} {path}":
            raise OrganError(
                "revision_action_missing",
                "Абаддон не опубликовал исполнимую revision-команду.",
                retryable=False,
                evidence={"task_id": task_id, "action": action},
            )
        return await self.execute_abaddon_action(task_id, snapshot)
