#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

from eye_of_terror.ledger import TaskLedger


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "ledger.json"
        ledger = TaskLedger.create(path, "task-1", "goal", "IskandarKhayon")
        ledger.request_cancel("test")
        if not TaskLedger.load(path).cancel_requested():
            raise AssertionError("cancel request was not persisted")
        ledger.record_step("source_discovery", "Lexmechanic", "completed", ["/work/x/source_map.json"], "done")
        ledger.set_result({"ok": True, "artifacts": ["/work/x/final_manifest.json"]})
        ledger.set_status("completed")
        if (path.parent / ".ledger.json.tmp").exists():
            raise AssertionError("ledger atomic temp file was left behind")
        loaded = TaskLedger.load(path)
        data = loaded.to_dict()
        if data["status"] != "completed" or data["steps"][0]["worker"] != "Lexmechanic":
            raise AssertionError(data)
        if data["result"]["artifacts"] != ["/work/x/final_manifest.json"]:
            raise AssertionError(data)
        if len(data["events"]) < 3:
            raise AssertionError("ledger did not record events")
        corrupt_path = Path(temp_dir) / "corrupt.json"
        corrupt_path.write_text("{", encoding="utf-8")
        try:
            TaskLedger.load(corrupt_path)
        except Exception:
            pass
        else:
            raise AssertionError("corrupt ledger should not load")
        stale_a = TaskLedger.load(path)
        stale_b = TaskLedger.load(path)
        stale_a.record_event("stale_a_event", {})
        stale_b.request_cancel("stale b")
        merged = TaskLedger.load(path).to_dict()
        event_types = [event.get("type") for event in merged.get("events", [])]
        if "stale_a_event" not in event_types or not merged.get("cancel_requested"):
            raise AssertionError(f"ledger save did not merge stale updates: {merged}")
    print("[ok] task ledger")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
