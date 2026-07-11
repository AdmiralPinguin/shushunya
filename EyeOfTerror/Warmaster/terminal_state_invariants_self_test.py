#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


HERE = Path(__file__).resolve()
WARM_ROOT = HERE.parent
REPO_ROOT = next(
    candidate
    for candidate in (HERE.parent, *HERE.parents)
    if (candidate / "ArchiveOfHeresy").is_dir()
)
ARCHIVE_ROOT = REPO_ROOT / "ArchiveOfHeresy"
for import_root in (REPO_ROOT, WARM_ROOT, ARCHIVE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from archive_handler import ArchiveHandler
from eye_of_terror.actions import run_actions
from eye_of_terror.run_state import governor_activity_report


def main() -> int:
    no_revision = run_actions("blocked", {"required": False, "steps": []})
    assert no_revision["can_start"] is False
    assert no_revision["can_research_loop"] is False
    assert no_revision["next_action"]["kind"] == "inspect"

    reprepare = {
        "kind": "reprepare_ceraxia_run",
        "method": "POST",
        "endpoint": "POST /orchestrate_run",
        "body": {
            "message": "same message",
            "governor_transport": "http",
            "run_mode": "http",
            "auto_start": True,
        },
        "reason": "fresh Ceraxia authorization required",
    }
    explicit = run_actions(
        "blocked",
        {"required": False, "steps": []},
        result_next_action=reprepare,
    )
    assert explicit["can_start"] is False
    assert explicit["next_action"] == reprepare

    revision = run_actions(
        "blocked",
        {"required": True, "steps": [{"step_id": "review"}]},
    )
    assert revision["can_start"] is False
    assert revision["can_start_revision"] is True
    assert revision["next_action"]["kind"] == "execute_revision"

    stale_event = {
        "type": "progress_event",
        "protocol_version": 1,
        "mission_id": "mission-stale-terminal",
        "created_at": "2026-07-09T00:00:00Z",
        "actor": "Warmaster",
        "role": "commander",
        "phase": "revising",
        "status": "running",
        "title": "Revision assigned",
        "body": "This is an old event.",
        "visible_to_user": True,
    }
    summary = {
        "task_id": "stale-terminal",
        "governor": "Ceraxia",
        "status": "blocked",
        "mission_protocol": {},
        "mission_progress_events": [stale_event],
        "progress": {"step_states": []},
        "result": {"status": "blocked", "summary": "Directive is missing."},
        "revision_plan": {"required": False, "steps": []},
        "revision_plan_summary": {},
        "final_manifest_summary": {},
    }
    report = governor_activity_report(summary, {"status": "blocked", "governor": "Ceraxia"})
    assert report["progress_events"][-1]["phase"] == "revising"
    assert report["brigade_tabs"]
    assert all(tab["active"] is False for tab in report["brigade_tabs"])
    terminal_headline = report["summary_activity_cards"][-1]["headline"]

    task = ArchiveHandler.warmaster_run_as_agent_task(
        None,
        {
            "task_id": "stale-terminal",
            "status": "blocked",
            "governor": "Ceraxia",
            "mission_state": {"status": "blocked", "active": False},
        },
        active=True,
        activity=report,
    )
    assert task["running"] is False
    assert task["current_step"] == terminal_headline
    assert task["current_step"] != stale_event["title"]
    assert all(tab["active"] is False for tab in task["brigade_tabs"])

    running_task = ArchiveHandler.warmaster_run_as_agent_task(
        None,
        {"task_id": "active", "status": "running", "governor": "Ceraxia"},
        activity={
            "entries": [{"headline": "Live step"}],
            "activity_cards": [{"headline": "Live step"}],
            "summary_activity_cards": [{"headline": "Diagnostic summary"}],
            "brigade_tabs": [],
        },
    )
    assert running_task["current_step"] == "Live step"

    print("[ok] terminal state dominates stale revision activity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
