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
        loaded = TaskLedger.load(path)
        data = loaded.to_dict()
        if data["status"] != "completed" or data["steps"][0]["worker"] != "Lexmechanic":
            raise AssertionError(data)
        if data["result"]["artifacts"] != ["/work/x/final_manifest.json"]:
            raise AssertionError(data)
        if len(data["events"]) < 3:
            raise AssertionError("ledger did not record events")
    print("[ok] task ledger")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
