from __future__ import annotations

from typing import Any

import requests


class DemonsForgeClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8110", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            timeout=self.timeout,
            **kwargs,
        )
        response.raise_for_status()
        return response.json()

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def capabilities(self) -> dict[str, Any]:
        return self._request("GET", "/forge/capabilities")

    def runtime(self) -> dict[str, Any]:
        return self._request("GET", "/forge/runtime")

    def job_schema(self) -> dict[str, Any]:
        return self._request("GET", "/forge/schema/job")

    def models(self) -> list[dict[str, Any]]:
        return self._request("GET", "/forge/models")

    def loras(self) -> list[dict[str, Any]]:
        return self._request("GET", "/forge/loras")

    def asset_downloads(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._request("GET", f"/forge/assets/downloads?limit={limit}")

    def memory_status(self) -> dict[str, Any]:
        return self._request("GET", "/forge/memory/status")

    def memory_policy(self) -> dict[str, Any]:
        return self._request("GET", "/forge/memory/policy")

    def memory_gateway(self) -> dict[str, Any]:
        return self._request("GET", "/forge/memory/gateway")

    def memory_catalog(self, create: bool = False) -> dict[str, Any]:
        return self._request("GET", "/forge/memory/catalog", params={"create": create})

    def memory_search(
        self,
        query: str,
        layers: str = "focus,wiki,vector,graph",
        limit: int = 5,
        include_content: bool = False,
        create: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/forge/memory/search",
            params={
                "q": query,
                "layers": layers,
                "limit": limit,
                "include_content": include_content,
                "create": create,
            },
        )

    def memory_events(self, limit: int = 20) -> dict[str, Any]:
        return self._request("GET", f"/forge/memory/events?limit={limit}")

    def memory_proposals(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._request("GET", f"/forge/memory/proposals?limit={limit}")

    def memory_propose(
        self,
        proposal: str,
        evidence: str = "",
        target: str = "auto",
        importance: int = 3,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        suffix = "?dry_run=true" if dry_run else ""
        return self._request(
            "POST",
            f"/forge/memory/propose{suffix}",
            json={
                "proposal": proposal,
                "evidence": evidence,
                "target": target,
                "importance": importance,
            },
        )

    def plan(self, request: str, preferred_engine: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/forge/plan",
            json={"request": request, "preferred_engine": preferred_engine},
        )

    def create_job(self, spec: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        suffix = "?dry_run=true" if dry_run else ""
        return self._request("POST", f"/forge/jobs{suffix}", json=spec)

    def jobs(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = f"?limit={limit}"
        if status:
            query += f"&status={status}"
        return self._request("GET", f"/forge/jobs{query}")

    def job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/forge/jobs/{job_id}")

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/forge/jobs/{job_id}/cancel")

    def gallery(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._request("GET", f"/forge/gallery?limit={limit}")

    def artifact(self, artifact_id: str) -> dict[str, Any]:
        return self._request("GET", f"/forge/artifacts/{artifact_id}")
