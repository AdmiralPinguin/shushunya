from __future__ import annotations

import ipaddress
import json
import os
import socket
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


RenderFn = Callable[[str, int], dict[str, Any]]


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def source_snapshots_path_for_output(output_path: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/source_snapshots.json"


def is_loopback_or_private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        raw_address = info[4][0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            return True
        if address.is_loopback or address.is_private or address.is_link_local or address.is_multicast:
            return True
    return False


def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("render URL must use http or https")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("render URL host is missing")
    if parsed.username or parsed.password:
        raise ValueError("render URL must not contain credentials")
    if is_loopback_or_private(host):
        raise ValueError("render URL must resolve to a public address")
    return url


def playwright_render(url: str, timeout_ms: int) -> dict[str, Any]:
    if os.environ.get("OCULARIS_ENABLE_PLAYWRIGHT", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return {"ok": False, "render_available": False, "error": "Playwright rendering disabled; set OCULARIS_ENABLE_PLAYWRIGHT=1"}
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional local browser tooling.
        return {"ok": False, "render_available": False, "error": f"Playwright unavailable: {exc}"}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1365, "height": 768})
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                title = page.title()
                text = page.locator("body").inner_text(timeout=timeout_ms)
                return {
                    "ok": True,
                    "render_available": True,
                    "title": title,
                    "text": text,
                    "text_chars": len(text),
                    "screenshot": "",
                }
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - render failures are worker diagnostics.
        return {"ok": False, "render_available": True, "error": str(exc)}


def render_source(snapshot: dict[str, Any], renderer: RenderFn = playwright_render, timeout_ms: int = 30000) -> dict[str, Any]:
    url = str(snapshot.get("final_url") or snapshot.get("requested_url") or "").strip()
    title = str(snapshot.get("source_title") or snapshot.get("title") or url)
    if not url:
        return {"source_title": title, "ok": False, "error": "render source has no URL", "text_excerpt": ""}
    try:
        safe_url = validate_public_url(url)
        rendered = renderer(safe_url, timeout_ms)
    except Exception as exc:  # noqa: BLE001 - validation/render errors are diagnostics.
        rendered = {"ok": False, "render_available": False, "error": str(exc)}
    text = str(rendered.get("text") or "")
    return {
        "source_title": title,
        "requested_url": snapshot.get("requested_url", ""),
        "final_url": url,
        "ok": bool(rendered.get("ok")),
        "render_available": bool(rendered.get("render_available")),
        "title": rendered.get("title", ""),
        "text_excerpt": text[:6000],
        "text_chars": int(rendered.get("text_chars") or len(text)),
        "screenshot": rendered.get("screenshot", ""),
        "error": rendered.get("error", ""),
    }


def render_snapshots(source_snapshots: dict[str, Any], renderer: RenderFn = playwright_render, timeout_ms: int = 30000) -> dict[str, Any]:
    rendered: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for snapshot in source_snapshots.get("snapshots", []):
        if not isinstance(snapshot, dict):
            continue
        if not snapshot.get("render_required"):
            skipped.append({"source_title": snapshot.get("source_title", ""), "reason": "render_not_required"})
            continue
        rendered.append(render_source(snapshot, renderer=renderer, timeout_ms=timeout_ms))
    return {
        "topic": source_snapshots.get("topic", ""),
        "rendered_snapshots": rendered,
        "skipped": skipped,
        "summary": {
            "render_requested": len(rendered),
            "render_ok": sum(1 for item in rendered if item.get("ok")),
            "render_failed": sum(1 for item in rendered if not item.get("ok")),
            "render_skipped": len(skipped),
            "browser_available": any(item.get("render_available") for item in rendered),
        },
    }


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    step = request.get("step")
    if not isinstance(step, dict):
        return {"ok": False, "worker": "OcularisRenderium", "error": "request.step must be an object"}
    expected_artifacts = step.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or not expected_artifacts:
        return {"ok": False, "worker": "OcularisRenderium", "error": "step.expected_artifacts is empty"}
    output_path = str(expected_artifacts[0])
    source_path = source_snapshots_path_for_output(output_path)
    source_host_path = sandbox_path(workspace_root, source_path)
    if not source_host_path.exists():
        return {"ok": False, "worker": "OcularisRenderium", "error": "source_snapshots is missing", "missing": source_path}
    source_snapshots = json.loads(source_host_path.read_text(encoding="utf-8"))
    timeout_ms = max(1000, min(int(request.get("render_timeout_ms") or 30000), 120000))
    rendered = render_snapshots(source_snapshots, timeout_ms=timeout_ms)
    host_path = sandbox_path(workspace_root, output_path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(rendered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = rendered["summary"]
    return {
        "ok": True,
        "worker": "OcularisRenderium",
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": f"Rendered {summary['render_ok']} of {summary['render_requested']} render-required sources.",
        "artifacts": [output_path],
        "gaps": [item["source_title"] for item in rendered["rendered_snapshots"] if not item.get("ok")],
        "confidence": "medium" if summary["render_ok"] else "low",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run OcularisRenderium on a Worker API request JSON.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/ocularis-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    request = payload.get("request") if isinstance(payload, dict) and isinstance(payload.get("request"), dict) else payload
    result = run(request, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
