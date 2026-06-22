from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import config


def _trim(value: str | None, limit: int) -> str:
    if not value:
        return ""
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n...[trimmed]"


@dataclass(frozen=True)
class ArchiveMemoryClient:
    base_url: str
    namespace: str
    requester: str
    api_key: str = ""
    enabled: bool = True
    timeout: float = 5.0

    @classmethod
    def from_config(cls) -> "ArchiveMemoryClient":
        return cls(
            base_url=config.ARCHIVE_BASE_URL,
            namespace=config.MEMORY_NAMESPACE,
            requester=config.MEMORY_REQUESTER,
            api_key=config.ARCHIVE_API_KEY,
            enabled=config.MEMORY_ENABLED,
            timeout=config.MEMORY_TIMEOUT_SECONDS,
        )

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "namespace": self.namespace,
            "requester": self.requester,
            "base_url": self.base_url,
            "api_key_configured": bool(self.api_key),
            "write_policy": "proposal-only",
            "direct_file_access": False,
        }

    def gateway(self) -> dict[str, Any]:
        return self._request("GET", "/archive/memory/gateway")

    def catalog(self, create: bool = False) -> dict[str, Any]:
        return self._request(
            "GET",
            "/archive/memory/catalog",
            params={
                "namespace": self.namespace,
                "requester": self.requester,
                "create": int(create),
            },
        )

    def search(
        self,
        query: str,
        limit: int = 5,
        layers: str = "focus,wiki,vector,graph",
        include_content: bool = False,
        create: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/archive/memory/search",
            params={
                "namespace": self.namespace,
                "requester": self.requester,
                "q": query,
                "limit": max(1, min(int(limit), 20)),
                "layers": layers,
                "include_content": int(include_content),
                "create": int(create),
            },
        )

    def events(
        self,
        limit: int = 20,
        component: str | None = None,
        event_action: str | None = None,
        create: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, object] = {
            "namespace": self.namespace,
            "requester": self.requester,
            "limit": max(1, min(int(limit), 100)),
            "create": int(create),
        }
        if component:
            params["component"] = component
        if event_action:
            params["event_action"] = event_action
        return self._request("GET", "/archive/memory/events", params=params)

    def propose(
        self,
        proposal: str,
        evidence: str = "",
        target: str = "auto",
        importance: int = 3,
    ) -> dict[str, Any]:
        payload = {
            "namespace": self.namespace,
            "requester": self.requester,
            "target": target,
            "importance": max(1, min(int(importance), 5)),
            "proposal": _trim(proposal, config.MEMORY_PROPOSAL_MAX_CHARS),
            "evidence": _trim(evidence, config.MEMORY_EVIDENCE_MAX_CHARS),
        }
        return self._request("POST", "/archive/memory/propose-change", payload=payload)

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, object] | None = None,
        payload: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "forge memory is disabled", "memory": self.status()}
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self.base_url}{path}{query}"
        body = None
        headers = {"accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["content-type"] = "application/json"
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return {"ok": False, "status": exc.code, "error": detail or exc.reason}
        except (OSError, URLError) as exc:
            return {"ok": False, "error": str(exc), "memory": self.status()}
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {"ok": False, "error": "archive returned non-json response", "raw": raw[:500]}
        if isinstance(data, dict):
            return data
        return {"ok": True, "data": data}


def asset_memory_proposal(status: str, asset: dict[str, Any], result: dict[str, Any] | None = None, error: str | None = None) -> tuple[str, str, int]:
    name = asset.get("name") or "unknown asset"
    asset_type = asset.get("asset_type") or "unknown"
    if status == "downloaded":
        path = (result or {}).get("path", "")
        sha256 = (result or {}).get("sha256") or asset.get("sha256") or ""
        proposal = f"DemonsForge has locally available {asset_type} asset '{name}'."
        evidence = (
            f"Asset download succeeded. Path: {path}. SHA256: {sha256}. "
            f"Source: {asset.get('source_url', '')}. License note: {asset.get('license_note') or ''}."
        )
        return proposal, evidence, 4
    proposal = f"DemonsForge rejected or failed {asset_type} asset '{name}'."
    evidence = (
        f"Asset status: {status}. Error: {error or ''}. "
        f"Source: {asset.get('source_url', '')}. License note: {asset.get('license_note') or ''}."
    )
    return proposal, evidence, 3
