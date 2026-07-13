#!/usr/bin/env python3
"""Focused barrier for Iskandar's report/evidence artifact handoff."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = next(
    candidate
    for candidate in Path(__file__).resolve().parents
    if (candidate / "EyeOfTerror" / "model_brain.py").is_file()
)
WARM_ROOT = PROJECT_ROOT / "EyeOfTerror" / "Warmaster"
for entry in (PROJECT_ROOT, WARM_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from eye_of_terror import artifacts as artifact_module
from eye_of_terror import research_warband_bridge as bridge

artifact_status = artifact_module.artifact_status
open_artifact_binary = artifact_module.open_artifact_binary


def main() -> int:
    task_id = "research-artifact-self-test"
    mission_id = "mission-research-artifact-self-test"
    answer = "Verified research answer."
    raw_result = {
        "outcome": "accepted",
        "reason": "accepted",
        "external_evaluator_result": {
            "status": "accepted",
            "accepted": True,
            "final_text": answer,
            "ledger": {
                "sources": [
                    {
                        "source_id": "source-1",
                        "url": "https://example.invalid/evidence",
                        "raw_sha256": "a" * 64,
                    }
                ],
                "spans": [],
                "claims": [],
                "evidence_edges": [],
                "derivations": [],
                "conflicts": [],
                "gaps": [],
                "final_claim_refs": [],
            },
            "search_log": [],
        },
        "pipeline_audit": {
            "verification_report": {
                "accepted": True,
                "integrity_ok": True,
                "issues": [],
            },
            "runtime_attestation_sha256": "b" * 64,
        },
    }
    with tempfile.TemporaryDirectory() as raw_root:
        run_dir = Path(raw_root) / task_id
        run_dir.mkdir()
        result = bridge._terminal_result(
            run_dir,
            task_id,
            mission_id,
            "done",
            raw_result,
        )
        if result.get("artifacts") != [
            bridge.RESEARCH_REPORT_ARTIFACT,
            bridge.RESEARCH_EVIDENCE_ARTIFACT,
        ]:
            raise AssertionError(f"research result did not record its artifacts: {result}")
        ledger = {"status": "completed", "result": result}
        status = artifact_status(ledger)
        items = status.get("artifacts") if isinstance(status.get("artifacts"), list) else []
        catalog = status.get("artifact_catalog")
        if (
            len(items) != 2
            or not all(item.get("exists") for item in items)
            or not isinstance(catalog, dict)
            or catalog.get("complete") is not True
            or catalog.get("truncated") is not False
            or catalog.get("returned") != 2
        ):
            raise AssertionError(f"research artifacts are not resolvable: {status}")
        if any("host_path" in item for item in items):
            raise AssertionError(f"artifact status disclosed a host path: {status}")
        with open_artifact_binary(ledger, bridge.RESEARCH_REPORT_ARTIFACT) as (reader, size):
            report = reader.read()
        if size != len(report) or answer.encode("utf-8") not in report:
            raise AssertionError("research report did not preserve the accepted answer bytes")
        evidence = json.loads(
            (run_dir / "work" / "research" / "research_evidence.json").read_text(
                encoding="utf-8"
            )
        )
        if (
            evidence.get("task_id") != task_id
            or evidence.get("mission_id") != mission_id
            or evidence.get("external_evaluator_result", {}).get("ledger", {}).get("sources", [])[0].get("source_id")
            != "source-1"
        ):
            raise AssertionError(f"research evidence envelope was degraded: {evidence}")

        package_root = run_dir / "catalog-work"
        package_dir = package_root / "pkg"
        package_dir.mkdir(parents=True)
        package_files = []
        for index in range(120):
            logical_path = f"/work/pkg/file-{index:03d}.txt"
            package_files.append({"path": logical_path})
            (package_dir / f"file-{index:03d}.txt").write_text(
                f"artifact {index}\n",
                encoding="utf-8",
            )
        manifest_path = package_dir / "final_manifest.json"
        manifest_path.write_text(
            json.dumps({"files": package_files}),
            encoding="utf-8",
        )
        package_ledger = {
            "status": "completed",
            "result": {
                "status": "completed",
                "workspace_root": str(package_root),
                "artifacts": ["/work/pkg/final_manifest.json"],
            },
        }
        decode_calls = 0
        original_decode = artifact_module._decode_json_object_bounded

        def counted_decode(raw):
            nonlocal decode_calls
            decode_calls += 1
            return original_decode(raw)

        artifact_module._decode_json_object_bounded = counted_decode
        try:
            package_status = artifact_status(package_ledger)
        finally:
            artifact_module._decode_json_object_bounded = original_decode
        package_catalog = package_status.get("artifact_catalog", {})
        if (
            decode_calls != 1
            or package_catalog.get("complete") is not True
            or package_catalog.get("returned") != 121
        ):
            raise AssertionError(
                "manifest catalog was reparsed per file or lost completeness: "
                f"decode_calls={decode_calls}, status={package_status}"
            )

        truncated = artifact_status(package_ledger, max_items=10)
        truncated_catalog = truncated.get("artifact_catalog", {})
        if (
            truncated_catalog.get("complete") is not False
            or truncated_catalog.get("truncated") is not True
            or truncated_catalog.get("returned") != 10
        ):
            raise AssertionError(f"bounded catalog did not declare truncation: {truncated}")

        manifest_path.write_text(
            json.dumps({"files": [{"path": "/work/pkg/file-000.txt"}, "bad-entry"]}),
            encoding="utf-8",
        )
        malformed = artifact_status(package_ledger)
        malformed_catalog = malformed.get("artifact_catalog", {})
        if (
            malformed_catalog.get("complete") is not False
            or malformed_catalog.get("truncated") is not False
            or malformed_catalog.get("error_count", 0) < 1
        ):
            raise AssertionError(
                f"malformed manifest was advertised as a complete catalog: {malformed}"
            )

        oversized_path_ledger = {
            "result": {
                "workspace_root": str(package_root),
                "artifacts": [
                    "/work/" + "x" * artifact_module.MAX_ARTIFACT_CATALOG_PATH_CHARS
                ],
            }
        }
        oversized_path = artifact_status(oversized_path_ledger)
        oversized_catalog = oversized_path.get("artifact_catalog", {})
        if (
            oversized_catalog.get("complete") is not False
            or oversized_catalog.get("error_count", 0) < 1
            or oversized_path.get("artifacts")
        ):
            raise AssertionError(
                f"oversized catalog path was reflected into the response: {oversized_path}"
            )
    print("[ok] ResearchWarband accepted report/evidence artifacts are recorded and streamable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
