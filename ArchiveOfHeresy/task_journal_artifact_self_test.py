#!/usr/bin/env python3
"""Focused barrier for accepted-run artifact backfill and idempotency."""
from __future__ import annotations

import hashlib
import io
import json

import task_journal


TASK_ID = "accepted-artifact-backfill"
MISSION_ID = "mission-accepted-artifact-backfill"
PAYLOADS = {
    "/work/result.bin": b"\x00accepted-result\xff",
    "work/native-report.txt": b"native report\n",
}


def accepted_orchestration(task_id):
    return {
        "task_id": task_id,
        "status": "completed",
        "summary": {
            "task_id": task_id,
            "status": "completed",
            "result": {"task_id": task_id},
            "mission_ref": {"mission_id": MISSION_ID},
            "mission_protocol": {
                "mission": {"mission_id": MISSION_ID},
                "commander_order": {"mission_id": MISSION_ID},
                "acceptance_review": {
                    "type": "acceptance_review",
                    "mission_id": MISSION_ID,
                    "reviewer": "Warmaster",
                    "status": "accepted",
                    "accepted": True,
                },
                "final_response": {
                    "type": "final_response",
                    "mission_id": MISSION_ID,
                    "status": "completed",
                    "accepted_by": "Warmaster",
                    "answer": "accepted",
                },
            },
        },
    }


def artifact_listing(_task_id):
    return [
        {
            "path": path,
            "exists": True,
            "bytes": len(payload),
            "source": "result",
        }
        for path, payload in PAYLOADS.items()
    ]


def main() -> int:
    imported = []
    saved_states = []

    accepted, _summary, _protocol = task_journal._accepted_completed_orchestration(
        accepted_orchestration(TASK_ID),
        TASK_ID,
    )
    if not accepted:
        raise AssertionError("strict accepted-final fixture did not pass its own gate")
    contradictory = []
    for field, value in (
        ("status", "blocked"),
        ("accepted_by", "Nobody"),
        ("mission_id", "different-mission"),
    ):
        payload = json.loads(json.dumps(accepted_orchestration(TASK_ID)))
        payload["summary"]["mission_protocol"]["final_response"][field] = value
        contradictory.append(payload)
    wrong_task = json.loads(json.dumps(accepted_orchestration(TASK_ID)))
    wrong_task["summary"]["task_id"] = "different-task"
    contradictory.append(wrong_task)
    if any(
        task_journal._accepted_completed_orchestration(payload, TASK_ID)[0]
        for payload in contradictory
    ):
        raise AssertionError("contradictory final status/authority/identity passed acceptance")

    original_proxy = task_journal.proxy_json_url
    valid_listing = {
        "ok": True,
        "artifacts": artifact_listing(TASK_ID),
        "artifact_catalog": {
            "schema_version": 1,
            "complete": True,
            "truncated": False,
            "limit": 32,
            "returned": len(PAYLOADS),
            "errors": [],
            "error_count": 0,
        },
    }
    try:
        task_journal.proxy_json_url = lambda *_args, **_kwargs: (200, valid_listing)
        if task_journal.fetch_artifacts(TASK_ID) != artifact_listing(TASK_ID):
            raise AssertionError("complete bounded catalog was not accepted")
        for bad_catalog in (
            {},
            {**valid_listing, "artifact_catalog": {}},
            {
                **valid_listing,
                "artifact_catalog": {
                    **valid_listing["artifact_catalog"],
                    "complete": False,
                    "truncated": True,
                    "errors": ["catalog limit exceeded"],
                },
            },
        ):
            task_journal.proxy_json_url = (
                lambda *_args, payload=bad_catalog, **_kwargs: (200, payload)
            )
            try:
                task_journal.fetch_artifacts(TASK_ID)
            except ValueError:
                pass
            else:
                raise AssertionError("missing/incomplete artifact catalog was accepted")
    finally:
        task_journal.proxy_json_url = original_proxy

    def open_artifact(_task_id, path, expected_size):
        payload = PAYLOADS[path]
        if len(payload) != expected_size:
            raise AssertionError("producer requested the wrong expected_size")
        return io.BytesIO(payload), "application/octet-stream"

    def import_stream(reader, **fields):
        payload = reader.read()
        if len(payload) != fields.get("expected_size"):
            raise AssertionError("artifact stream size was not enforced")
        imported.append({"payload": payload, "fields": fields})
        digest = hashlib.sha256(
            (fields["dedupe_key"] + fields["logical_path"]).encode("utf-8")
        ).hexdigest()[:32]
        return {"artifact_id": f"art_{digest}"}

    originals = {
        "fetch_runs": task_journal.fetch_runs,
        "load_state": task_journal.load_state,
        "save_state": task_journal.save_state,
        "fetch_orchestration": task_journal.fetch_orchestration,
        "fetch_artifacts": task_journal.fetch_artifacts,
        "open_artifact": task_journal._open_warmaster_artifact,
        "import_stream": task_journal.trusted_import_stream,
        "remember_entry": task_journal.remember_entry,
        "deliver_final": task_journal.deliver_final_to_chat,
        "deliver_escalation": task_journal.deliver_escalation_to_chat,
    }
    current_state = {}
    task_journal.fetch_runs = lambda: [
        {"task_id": TASK_ID, "status": "completed", "governor": "IskandarKhayon"}
    ]
    task_journal.load_state = lambda: json.loads(json.dumps(current_state))

    def save_state(state):
        nonlocal current_state
        current_state = json.loads(json.dumps(state))
        saved_states.append(current_state)

    task_journal.save_state = save_state
    task_journal.fetch_orchestration = accepted_orchestration
    task_journal.fetch_artifacts = artifact_listing
    task_journal._open_warmaster_artifact = open_artifact
    task_journal.trusted_import_stream = import_stream
    task_journal.remember_entry = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("baseline artifact backfill replayed a journal event")
    )
    task_journal.deliver_final_to_chat = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("baseline artifact backfill replayed a chat report")
    )
    task_journal.deliver_escalation_to_chat = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("baseline artifact backfill replayed an escalation")
    )
    try:
        first = task_journal.poll_once()
        second = task_journal.poll_once()

        # One failed stream remains retryable but does not prevent the following
        # recorded artifact from being catalogued in the same pass.
        mixed_payload = b"good-after-error"
        task_journal.fetch_artifacts = lambda _task_id: [
            {"path": "/work/bad.bin", "exists": True, "bytes": 3, "source": "result"},
            {
                "path": "/work/good.bin",
                "exists": True,
                "bytes": len(mixed_payload),
                "source": "result",
            },
        ]

        def mixed_open(_task_id, path, _expected_size):
            if path == "/work/bad.bin":
                raise OSError("synthetic stream failure")
            return io.BytesIO(mixed_payload), "application/octet-stream"

        task_journal._open_warmaster_artifact = mixed_open
        mixed_publications = {}
        mixed = task_journal.publish_completed_artifacts(
            "mixed-artifact-errors",
            mixed_publications,
            byte_budget=1024,
            file_budget=4,
        )
        if (
            mixed.get("published") != 1
            or mixed.get("attempted") != 2
            or mixed.get("complete") is not False
            or not mixed.get("notices")
        ):
            raise AssertionError(f"one artifact error broke or disappeared from the pass: {mixed}")

        # A file larger than the configured poll budget is never downloaded
        # silently; it stays pending with a durable explanation.
        task_journal.fetch_artifacts = lambda _task_id: [
            {
                "path": "/work/too-large-for-one-poll.bin",
                "exists": True,
                "bytes": task_journal.TASK_JOURNAL_ARTIFACT_BYTES_PER_POLL + 1,
                "source": "result",
            }
        ]
        task_journal._open_warmaster_artifact = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("over-budget artifact was opened")
        )
        budget_publications = {}
        budgeted = task_journal.publish_completed_artifacts(
            "over-budget-artifact",
            budget_publications,
            byte_budget=task_journal.TASK_JOURNAL_ARTIFACT_BYTES_PER_POLL,
            file_budget=4,
        )
        if (
            budgeted.get("attempted") != 0
            or budgeted.get("complete") is not False
            or "per-poll byte budget" not in " ".join(budgeted.get("notices") or [])
        ):
            raise AssertionError(f"over-budget artifact was not explained and retained: {budgeted}")
    finally:
        task_journal.fetch_runs = originals["fetch_runs"]
        task_journal.load_state = originals["load_state"]
        task_journal.save_state = originals["save_state"]
        task_journal.fetch_orchestration = originals["fetch_orchestration"]
        task_journal.fetch_artifacts = originals["fetch_artifacts"]
        task_journal._open_warmaster_artifact = originals["open_artifact"]
        task_journal.trusted_import_stream = originals["import_stream"]
        task_journal.remember_entry = originals["remember_entry"]
        task_journal.deliver_final_to_chat = originals["deliver_final"]
        task_journal.deliver_escalation_to_chat = originals["deliver_escalation"]

    if first.get("baseline") is not True or first.get("artifacts_published") != 2:
        raise AssertionError(f"baseline did not publish accepted artifacts: {first}")
    if second.get("baseline") is not False or second.get("artifacts_published") != 0:
        raise AssertionError(f"repeat poll was not idempotent: {second}")
    baseline_imports = [
        item for item in imported if item["fields"].get("task_id") == TASK_ID
    ]
    if len(baseline_imports) != 2 or len(saved_states) != 1:
        raise AssertionError(
            "unexpected baseline import/state counts: "
            f"imports={len(baseline_imports)}, saves={len(saved_states)}"
        )
    logical_paths = {item["fields"]["logical_path"] for item in baseline_imports}
    if logical_paths != {"work/result.bin", "work/native-report.txt"}:
        raise AssertionError(f"logical paths were not normalized safely: {logical_paths}")
    for item in baseline_imports:
        fields = item["fields"]
        if (
            fields.get("source") != "warmaster"
            or fields.get("session_id") != task_journal.SHARED_CHAT_SESSION_ID
            or fields.get("task_id") != TASK_ID
            or fields.get("mission_id") != MISSION_ID
            or not str(fields.get("dedupe_key") or "").startswith("warmaster-artifact:")
            or "artifact_root" in json.dumps(fields, ensure_ascii=False)
        ):
            raise AssertionError(f"unsafe or unstable publication fields: {fields}")
    publication = current_state[task_journal.ARTIFACT_PUBLICATIONS_STATE_KEY][TASK_ID]
    if publication.get("complete") is not True or len(publication.get("published", {})) != 2:
        raise AssertionError(f"publication checkpoint is incomplete: {publication}")
    print("[ok] task journal accepted-artifact baseline backfill is streaming and idempotent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
