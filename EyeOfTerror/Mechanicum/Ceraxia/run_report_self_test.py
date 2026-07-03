#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ceraxia import CeraxiaInput, run_ceraxia
from run_report import audit_run_package


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


if __name__ == "__main__":
    unittest.main()
