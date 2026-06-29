#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from auspex_browser import collect_snapshots, run


def main() -> int:
    source_map = {
        "topic": "test",
        "sources": [
            {"title": "Example", "source_class": "secondary", "url": "https://example.com/page"},
            {"title": "Reddit", "source_class": "community_excerpt", "url": "https://www.reddit.com/r/test/comments/abc/post/"},
            {"title": "Book Only", "source_class": "official_primary_narrative", "url": ""},
        ],
    }

    def fake_fetch(url: str, max_bytes: int) -> dict:
        if "www.reddit.com" in url:
            return {
                "ok": True,
                "url": url,
                "status": 200,
                "content_type": "text/html",
                "title": "Reddit - Please wait for verification",
                "text": "Reddit - Please wait for verification",
                "bytes_read": 37,
                "truncated": False,
                "is_binary": False,
            }
        if "old.reddit.com" in url:
            return {
                "ok": True,
                "url": url,
                "status": 200,
                "content_type": "text/html",
                "title": "Old Reddit",
                "text": "Old reddit source text with useful excerpt details",
                "bytes_read": 47,
                "truncated": False,
                "is_binary": False,
            }
        return {
            "ok": True,
            "url": url,
            "status": 200,
            "content_type": "text/html",
            "title": "Example",
            "text": "Fetched source text",
            "bytes_read": 19,
            "truncated": False,
            "is_binary": False,
            "render_required": True,
            "render_reason": "low extracted text with SPA/runtime markers",
        }

    snapshots = collect_snapshots(source_map, fake_fetch)
    if snapshots["summary"] != {"sources_with_url": 2, "sources_without_url": 1, "fetched_ok": 2, "failed": 0, "render_required": 1}:
        raise AssertionError(f"snapshot summary is wrong: {snapshots['summary']}")
    if snapshots["snapshots"][0]["text_excerpt"] != "Fetched source text":
        raise AssertionError("text excerpt missing")
    if snapshots["snapshots"][0].get("render_required") is not True:
        raise AssertionError("render_required flag missing from snapshot")
    if snapshots["snapshots"][1].get("final_url") != "https://old.reddit.com/r/test/comments/abc/post/":
        raise AssertionError(f"reddit fallback did not use old reddit: {snapshots['snapshots'][1]}")
    if snapshots["snapshots"][1].get("fallback_reason") != "reddit verification page":
        raise AssertionError(f"reddit fallback reason missing: {snapshots['snapshots'][1]}")

    request = {
        "task_id": "test:source_acquisition",
        "step": {"expected_artifacts": ["/work/test/source_snapshots.json"]},
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source_path = root / "test" / "source_map.json"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(json.dumps({"sources": []}), encoding="utf-8")
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"AuspexBrowser failed: {result}")
        if not (root / "test" / "source_snapshots.json").exists():
            raise AssertionError("source_snapshots was not written")
    print("[ok] AuspexBrowser snapshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
