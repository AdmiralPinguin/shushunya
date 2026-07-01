from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "Mechanicum" / "ShushunyaAgent").exists())
SHUSHUNYA_AGENT_DIR = REPO_ROOT / "Mechanicum" / "ShushunyaAgent"
if str(SHUSHUNYA_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(SHUSHUNYA_AGENT_DIR))

from shushunya_agent.web_tools import web_fetch  # noqa: E402

CORPUS_INGESTOR_DIR = Path(__file__).resolve().parents[1] / "CorpusIngestor"
if str(CORPUS_INGESTOR_DIR) not in sys.path:
    sys.path.insert(0, str(CORPUS_INGESTOR_DIR))

from corpus_ingestor import DEFAULT_CORPUS_ROOT, read_corpus_text  # noqa: E402


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


def configured_corpus_root() -> Path:
    raw = os.environ.get("SHUSHUNYA_CORPUS_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else DEFAULT_CORPUS_ROOT.resolve()


def local_source_result(source: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    raw_path = str(source.get("local_path") or "").strip()
    if not raw_path:
        return {"ok": False, "error": "local_path is empty"}
    path = Path(raw_path).expanduser().resolve()
    corpus_root = configured_corpus_root()
    try:
        path.relative_to(corpus_root)
    except ValueError:
        return {"ok": False, "error": f"local source path is outside configured corpus root: {path}"}
    if not path.exists() or not path.is_file():
        return {"ok": False, "error": f"local source file is missing: {path}"}
    text, source_kind = read_corpus_text(path)
    truncated = len(text.encode("utf-8")) > max_bytes
    return {
        "ok": True,
        "url": "",
        "status": 200,
        "content_type": source_kind,
        "title": source.get("title") or path.name,
        "text": text[:max_bytes],
        "bytes_read": min(len(text.encode("utf-8")), max_bytes),
        "truncated": truncated,
        "is_binary": False,
        "local_path": str(path),
    }


def reddit_old_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host.endswith("reddit.com"):
        return ""
    return urlunparse((parsed.scheme or "https", "old.reddit.com", parsed.path, parsed.params, parsed.query, parsed.fragment))


def source_fetch_limit(source: dict[str, Any], source_map: dict[str, Any], default_max_bytes: int) -> int:
    url = str(source.get("url") or "").lower()
    depth_profile = source_map.get("depth_profile") if isinstance(source_map.get("depth_profile"), dict) else {}
    if url.endswith(".epub") or "application/epub" in str(source.get("content_type") or "").lower():
        return max(default_max_bytes, 1000000)
    if depth_profile.get("mode") == "comprehensive":
        return max(default_max_bytes, 1000000)
    return default_max_bytes


def fetch_with_fallbacks(source: dict[str, Any], source_map: dict[str, Any], fetcher: FetchFn, max_bytes: int) -> dict[str, Any]:
    url = str(source.get("url") or "").strip()
    limit = source_fetch_limit(source, source_map, max_bytes)
    result = fetcher(url, limit)
    text = str(result.get("text") or "")
    old_url = reddit_old_url(url)
    if old_url and result.get("ok") and len(text.strip()) < 200 and "reddit" in text.lower() and "verification" in text.lower():
        fallback = fetcher(old_url, limit)
        if fallback.get("ok") and len(str(fallback.get("text") or "")) > len(text):
            fallback["fallback_from_url"] = url
            fallback["fallback_reason"] = "reddit verification page"
            return fallback
    return result


def compact_snapshot(source: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    text = str(result.get("text") or "")
    return {
        "source_title": source.get("title", ""),
        "source_class": source.get("source_class", source.get("type", "")),
        "requested_url": source.get("url", ""),
        "local_path": source.get("local_path", result.get("local_path", "")),
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
        "fallback_from_url": result.get("fallback_from_url", ""),
        "fallback_reason": result.get("fallback_reason", ""),
    }


def collect_snapshots(source_map: dict[str, Any], fetcher: FetchFn = default_fetch, max_bytes: int = 200000) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for source in source_map.get("sources", []):
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        local_path = str(source.get("local_path") or "").strip()
        if not url and not local_path:
            skipped.append({"source_title": source.get("title", ""), "reason": "no public URL in source map"})
            continue
        try:
            if local_path:
                result = local_source_result(source, source_fetch_limit(source, source_map, max_bytes))
            else:
                result = fetch_with_fallbacks(source, source_map, fetcher, max_bytes)
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
