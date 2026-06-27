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
            {"title": "Book Only", "source_class": "official_primary_narrative", "url": ""},
        ],
    }

    def fake_fetch(url: str, max_bytes: int) -> dict:
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
        }

    snapshots = collect_snapshots(source_map, fake_fetch)
    if snapshots["summary"] != {"sources_with_url": 1, "sources_without_url": 1, "fetched_ok": 1, "failed": 0}:
        raise AssertionError(f"snapshot summary is wrong: {snapshots['summary']}")
    if snapshots["snapshots"][0]["text_excerpt"] != "Fetched source text":
        raise AssertionError("text excerpt missing")

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
