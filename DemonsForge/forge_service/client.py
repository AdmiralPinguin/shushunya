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
