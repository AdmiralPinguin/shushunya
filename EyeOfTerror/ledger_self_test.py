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

        terminal_path = Path(temp_dir) / "terminal.json"
        terminal = TaskLedger.create(terminal_path, "task-terminal", "goal", "IskandarKhayon")
        terminal.set_result({"ok": True, "artifacts": ["/work/x/terminal_manifest.json"]})
        terminal.set_status("completed")
        late_cancel = TaskLedger.load(terminal_path)
        if late_cancel.request_cancel("too late"):
            raise AssertionError("terminal ledger accepted cancellation")
        terminal_data = TaskLedger.load(terminal_path).to_dict()
        terminal_events = [event.get("type") for event in terminal_data.get("events", [])]
        if terminal_data["status"] != "completed" or terminal_data.get("cancel_requested"):
            raise AssertionError(f"terminal cancellation changed ledger: {terminal_data}")
        if "cancel_rejected" not in terminal_events:
            raise AssertionError(f"terminal cancellation rejection was not recorded: {terminal_data}")

        merge_path = Path(temp_dir) / "merge.json"
        TaskLedger.create(merge_path, "task-merge", "goal", "IskandarKhayon")
        stale_a = TaskLedger.load(merge_path)
        stale_b = TaskLedger.load(merge_path)
        stale_a.record_event("stale_a_event", {})
        stale_b.request_cancel("stale b")
        merged = TaskLedger.load(merge_path).to_dict()
        event_types = [event.get("type") for event in merged.get("events", [])]
        if "stale_a_event" not in event_types or not merged.get("cancel_requested"):
            raise AssertionError(f"ledger save did not merge stale updates: {merged}")

        stale_terminal_path = Path(temp_dir) / "stale-terminal.json"
        TaskLedger.create(stale_terminal_path, "task-stale-terminal", "goal", "IskandarKhayon")
        stale_writer = TaskLedger.load(stale_terminal_path)
        fresh_writer = TaskLedger.load(stale_terminal_path)
        fresh_writer.set_result({"ok": True, "status": "ready"})
        fresh_writer.set_status("completed")
        stale_writer.data["status"] = "running"
        stale_writer.data["cancel_requested"] = True
        stale_writer.data["cancel_reason"] = "stale writer"
        stale_writer.save()
        stale_terminal_data = TaskLedger.load(stale_terminal_path).to_dict()
        if stale_terminal_data["status"] != "completed":
            raise AssertionError(f"stale save rewrote terminal status: {stale_terminal_data}")
        if stale_terminal_data.get("result", {}).get("status") != "ready":
            raise AssertionError(f"stale save rewrote terminal result: {stale_terminal_data}")
        if stale_terminal_data.get("cancel_requested"):
            raise AssertionError(f"stale save added cancellation to terminal ledger: {stale_terminal_data}")
    print("[ok] task ledger")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
