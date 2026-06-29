#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ocularis_renderium import render_snapshots, run, validate_public_url


def main() -> int:
    source_snapshots = {
        "topic": "render test",
        "snapshots": [
            {
                "source_title": "Static",
                "requested_url": "https://example.com/static",
                "final_url": "https://example.com/static",
                "ok": True,
                "render_required": False,
            },
            {
                "source_title": "Scripted",
                "requested_url": "https://example.com/app",
                "final_url": "https://example.com/app",
                "ok": True,
                "render_required": True,
            },
        ],
    }

    def fake_renderer(url: str, timeout_ms: int) -> dict:
        if timeout_ms != 30000:
            raise AssertionError(f"bad timeout: {timeout_ms}")
        return {
            "ok": True,
            "render_available": True,
            "title": "Rendered app",
            "text": "Rendered DOM text with application content",
            "text_chars": 42,
        }

    rendered = render_snapshots(source_snapshots, renderer=fake_renderer)
    if rendered["summary"] != {
        "render_requested": 1,
        "render_ok": 1,
        "render_failed": 0,
        "render_skipped": 1,
        "browser_available": True,
    }:
        raise AssertionError(f"bad render summary: {rendered}")
    if rendered["rendered_snapshots"][0]["text_excerpt"] != "Rendered DOM text with application content":
        raise AssertionError(f"rendered text missing: {rendered}")
    try:
        validate_public_url("http://127.0.0.1/private")
    except ValueError as exc:
        if "public address" not in str(exc):
            raise
    else:
        raise AssertionError("loopback render URL should be rejected")

    request = {
        "task_id": "test:render",
        "step": {"expected_artifacts": ["/work/test/rendered_snapshots.json"]},
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source_path = root / "test" / "source_snapshots.json"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(json.dumps(source_snapshots, ensure_ascii=False), encoding="utf-8")
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"OcularisRenderium failed in diagnostic mode: {result}")
        output = root / "test" / "rendered_snapshots.json"
        if not output.exists():
            raise AssertionError("rendered_snapshots was not written")
        written = json.loads(output.read_text(encoding="utf-8"))
        if written.get("summary", {}).get("render_requested") != 1 or written.get("summary", {}).get("render_ok") != 0:
            raise AssertionError(f"diagnostic render summary is wrong: {written}")
    print("[ok] OcularisRenderium render snapshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
