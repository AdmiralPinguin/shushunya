#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ceraxia import CeraxiaInput, run_ceraxia
from run_report import audit_run_package, build_artifact_manifest


class CeraxiaRunReportTests(unittest.TestCase):
    def test_audit_blocks_artifact_manifest_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="review package evidence for `app.py`",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            self.assertEqual(result["audit_decision"], "passed", result)
            run_dir = Path(result["run_dir"])
            final_report = run_dir / "final_report.md"
            final_report.write_text(final_report.read_text(encoding="utf-8") + "\nManual drift after manifest.\n", encoding="utf-8")
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertTrue(
                any(
                    "artifact_manifest.json final_report.md sha256 disagrees" in item["finding"]
                    for item in audit["findings"]
                ),
                audit,
            )

    def test_audit_blocks_final_report_semantic_drift_even_with_fresh_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="review package evidence for `app.py`",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            self.assertEqual(result["audit_decision"], "passed", result)
            run_dir = Path(result["run_dir"])
            final_report = run_dir / "final_report.md"
            final_report.write_text(
                final_report.read_text(encoding="utf-8").replace("Review decision: dry_run_ready", "Review decision: fake_ready"),
                encoding="utf-8",
            )
            (run_dir / "artifact_manifest.json").write_text(
                json_dumps(build_artifact_manifest(run_dir)),
                encoding="utf-8",
            )
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertEqual(audit["artifact_semantic_index"]["kind"], "ceraxia_artifact_semantic_index")
            self.assertTrue(
                any("final_report.md semantic review decision disagrees" in item["finding"] for item in audit["findings"]),
                audit,
            )


def json_dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
