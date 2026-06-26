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

    def state(self) -> dict[str, Any]:
        return self._request("GET", "/forge/state")

    def unload_runtime(self, engine: str | None = None) -> dict[str, Any]:
        suffix = f"?engine={engine}" if engine else ""
        return self._request("POST", f"/forge/runtime/unload{suffix}")

    def checkpoint_runtime(self) -> dict[str, Any]:
        return self._request("POST", "/forge/runtime/checkpoint")

    def pause_queue(self) -> dict[str, Any]:
        return self._request("POST", "/forge/queue/pause")

    def queue(self) -> dict[str, Any]:
        return self._request("GET", "/forge/queue")

    def events(self, limit: int = 100, job_id: str | None = None) -> dict[str, Any]:
        params = {"limit": limit, "job_id": job_id}
        return self._request("GET", "/forge/events", params={k: v for k, v in params.items() if v is not None})

    def reports(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._request("GET", f"/forge/reports?limit={limit}")

    def report_summary(self, limit: int = 100) -> dict[str, Any]:
        return self._request("GET", f"/forge/reports/summary?limit={limit}")

    def resume_queue(self) -> dict[str, Any]:
        return self._request("POST", "/forge/queue/resume")

    def job_schema(self) -> dict[str, Any]:
        return self._request("GET", "/forge/schema/job")

    def models(self) -> list[dict[str, Any]]:
        return self._request("GET", "/forge/models")

    def engines(self) -> dict[str, Any]:
        return self._request("GET", "/forge/engines")

    def loras(self) -> list[dict[str, Any]]:
        return self._request("GET", "/forge/loras")

    def embeddings(self) -> list[dict[str, Any]]:
        return self._request("GET", "/forge/embeddings")

    def samplers(self) -> list[str]:
        return self._request("GET", "/forge/samplers")

    def schedulers(self) -> list[dict[str, Any]]:
        return self._request("GET", "/forge/schedulers")

    def aspect_presets(self) -> dict[str, dict[str, int]]:
        return self._request("GET", "/forge/aspect-presets")

    def planner_thinker(self) -> dict[str, Any]:
        return self._request("GET", "/forge/planner/thinker")

    def refresh_registries(self) -> dict[str, Any]:
        return self._request("POST", "/forge/registries/refresh")

    def asset_downloads(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._request("GET", f"/forge/assets/downloads?limit={limit}")

    def asset_profiles(self) -> dict[str, Any]:
        return self._request("GET", "/forge/assets/profiles")

    def characters(self) -> dict[str, Any]:
        return self._request("GET", "/forge/characters")

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

    def plan(
        self,
        request: str,
        preferred_engine: str | None = None,
        use_memory: bool = True,
        use_thinker: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/forge/plan",
            json={
                "request": request,
                "preferred_engine": preferred_engine,
                "use_memory": use_memory,
                "use_thinker": use_thinker,
            },
        )

    def plan_project(
        self,
        request: str,
        project_type: str = "auto",
        character_id: str | None = None,
        variants: int = 4,
        panels: int = 4,
        width: int | None = None,
        height: int | None = None,
        use_memory: bool = True,
        use_thinker: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/forge/projects/plan",
            json={
                "request": request,
                "project_type": project_type,
                "character_id": character_id,
                "variants": variants,
                "panels": panels,
                "width": width,
                "height": height,
                "use_memory": use_memory,
                "use_thinker": use_thinker,
            },
        )

    def create_project(self, spec: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        suffix = "?dry_run=true" if dry_run else ""
        return self._request("POST", f"/forge/projects{suffix}", json=spec)

    def projects(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._request("GET", "/forge/projects", params={"limit": limit})

    def project(self, project_id: str) -> dict[str, Any]:
        return self._request("GET", f"/forge/projects/{project_id}")

    def create_job(self, spec: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        suffix = "?dry_run=true" if dry_run else ""
        return self._request("POST", f"/forge/jobs{suffix}", json=spec)

    def jobs(
        self,
        status: str | None = None,
        limit: int = 100,
        engine: str | None = None,
        job_type: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {"limit": limit, "status": status, "engine": engine, "job_type": job_type}
        return self._request("GET", "/forge/jobs", params={k: v for k, v in params.items() if v is not None})

    def job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/forge/jobs/{job_id}")

    def job_manifest(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/forge/jobs/{job_id}/manifest")

    def job_spec(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/forge/jobs/{job_id}/spec")

    def job_logs(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/forge/jobs/{job_id}/logs")

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/forge/jobs/{job_id}/cancel")

    def clone_job(
        self,
        job_id: str,
        overrides: dict[str, Any] | None = None,
        reuse_seed: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        suffix = "?dry_run=true" if dry_run else ""
        return self._request(
            "POST",
            f"/forge/jobs/{job_id}/clone{suffix}",
            json={"overrides": overrides or {}, "reuse_seed": reuse_seed},
        )

    def retry_job(self, job_id: str, dry_run: bool = False) -> dict[str, Any]:
        suffix = "?dry_run=true" if dry_run else ""
        return self._request("POST", f"/forge/jobs/{job_id}/retry{suffix}")

    def gallery(
        self,
        limit: int = 100,
        query: str | None = None,
        engine: str | None = None,
        model: str | None = None,
        job_type: str | None = None,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {
            "limit": limit,
            "q": query,
            "engine": engine,
            "model": model,
            "job_type": job_type,
            "kind": kind,
        }
        return self._request("GET", "/forge/gallery", params={k: v for k, v in params.items() if v is not None})

    def artifact(self, artifact_id: str) -> dict[str, Any]:
        return self._request("GET", f"/forge/artifacts/{artifact_id}")

    def artifact_file_url(self, artifact_id: str) -> str:
        return f"{self.base_url}/forge/artifacts/{artifact_id}/file"

    def artifact_thumbnail_url(self, artifact_id: str) -> str:
        return f"{self.base_url}/forge/artifacts/{artifact_id}/thumbnail"

    def artifact_metadata(self, artifact_id: str) -> dict[str, Any]:
        return self._request("GET", f"/forge/artifacts/{artifact_id}/metadata")

    def artifact_verify(self, artifact_id: str) -> dict[str, Any]:
        return self._request("GET", f"/forge/artifacts/{artifact_id}/verify")

    def artifact_evaluation(self, artifact_id: str) -> dict[str, Any]:
        return self._request("GET", f"/forge/artifacts/{artifact_id}/evaluation")
