from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable


SHUSHUNYA_AGENT_DIR = Path(__file__).resolve().parents[1] / "ShushunyaAgent"
if str(SHUSHUNYA_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(SHUSHUNYA_AGENT_DIR))

from shushunya_agent.web_tools import web_fetch  # noqa: E402


class FetchConfig:
    max_tool_output_chars = 12000


FetchFn = Callable[[str, int], dict[str, Any]]


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def source_map_path_for_output(output_path: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/source_map.json"


def default_fetch(url: str, max_bytes: int) -> dict[str, Any]:
    return web_fetch(FetchConfig(), url, max_bytes)


def compact_snapshot(source: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    text = str(result.get("text") or "")
    return {
        "source_title": source.get("title", ""),
        "source_class": source.get("source_class", source.get("type", "")),
        "requested_url": source.get("url", ""),
        "ok": bool(result.get("ok")),
        "final_url": result.get("url", ""),
        "status": result.get("status"),
        "content_type": result.get("content_type", ""),
        "title": result.get("title", ""),
        "is_binary": bool(result.get("is_binary")),
        "truncated": bool(result.get("truncated")),
        "bytes_read": result.get("bytes_read", 0),
        "text_excerpt": text[:6000],
        "render_required": bool(result.get("render_required")),
        "render_reason": result.get("render_reason", ""),
        "error": result.get("error", ""),
    }


def collect_snapshots(source_map: dict[str, Any], fetcher: FetchFn = default_fetch, max_bytes: int = 200000) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for source in source_map.get("sources", []):
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        if not url:
            skipped.append({"source_title": source.get("title", ""), "reason": "no public URL in source map"})
            continue
        try:
            result = fetcher(url, max_bytes)
        except Exception as exc:  # noqa: BLE001 - network failures are data for this worker.
            result = {"ok": False, "error": str(exc)}
        snapshots.append(compact_snapshot(source, result))
    return {
        "topic": source_map.get("topic", ""),
        "snapshots": snapshots,
        "skipped": skipped,
        "summary": {
            "sources_with_url": len(snapshots),
            "sources_without_url": len(skipped),
            "fetched_ok": sum(1 for item in snapshots if item.get("ok")),
            "failed": sum(1 for item in snapshots if not item.get("ok")),
            "render_required": sum(1 for item in snapshots if item.get("render_required")),
        },
    }


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    step = request.get("step")
    if not isinstance(step, dict):
        return {"ok": False, "worker": "AuspexBrowser", "error": "request.step must be an object"}
    expected_artifacts = step.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or not expected_artifacts:
        return {"ok": False, "worker": "AuspexBrowser", "error": "step.expected_artifacts is empty"}
    output_path = str(expected_artifacts[0])
    source_path = source_map_path_for_output(output_path)
    source_host_path = sandbox_path(workspace_root, source_path)
    if not source_host_path.exists():
        return {"ok": False, "worker": "AuspexBrowser", "error": "source_map is missing", "missing": source_path}
    source_map = json.loads(source_host_path.read_text(encoding="utf-8"))
    snapshots = collect_snapshots(source_map)
    host_path = sandbox_path(workspace_root, output_path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "worker": "AuspexBrowser",
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": f"Fetched {snapshots['summary']['fetched_ok']} source URLs; {snapshots['summary']['failed']} failed.",
        "artifacts": [output_path],
        "gaps": [item["source_title"] for item in snapshots["skipped"]],
        "confidence": "medium",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run AuspexBrowser on a Worker API request JSON.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/auspex-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    request = payload.get("request") if isinstance(payload, dict) and isinstance(payload.get("request"), dict) else payload
    result = run(request, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
