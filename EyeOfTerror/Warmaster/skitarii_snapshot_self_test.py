from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
WARM_ROOT = ROOT / "EyeOfTerror" / "Warmaster"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WARM_ROOT) not in sys.path:
    sys.path.insert(0, str(WARM_ROOT))

from eye_of_terror import skitarii_bridge as bridge


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class FullRepositorySnapshotTest(unittest.TestCase):
    def test_large_dirty_and_untracked_assets_are_immutable_manifest_entries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            git(root, "init", "-q")
            (root / "tracked.bin").write_bytes(b"tracked-baseline")
            (root / "code.py").write_text("print('ok')\n", encoding="utf-8")
            git(root, "add", "tracked.bin", "code.py")
            git(
                root,
                "-c",
                "user.name=Snapshot Test",
                "-c",
                "user.email=snapshot@example.invalid",
                "commit",
                "-qm",
                "baseline",
            )
            (root / "tracked.bin").write_bytes(b"tracked-dirty-content")
            (root / "untracked.bin").write_bytes(b"untracked-large-content")

            with patch.object(bridge, "REPO_ROOT", root):
                snapshot = bridge._full_repo_snapshot(
                    max_files=20,
                    max_total_bytes=10_000,
                    max_file_bytes=16,
                    max_external_assets=4,
                    max_external_file_bytes=1_000,
                    max_external_total_bytes=2_000,
                )

            self.assertEqual(snapshot["code.py"], "print('ok')\n")
            self.assertEqual(
                snapshot.external_assets["tracked.bin"]["source_state"],
                "tracked_dirty",
            )
            self.assertEqual(
                snapshot.external_assets["untracked.bin"]["source_state"],
                "untracked",
            )
            self.assertNotIn("tracked.bin", snapshot.blobs)
            self.assertNotIn("untracked.bin", snapshot.blobs)
            self.assertEqual(len(snapshot.fingerprint), 64)


if __name__ == "__main__":
    unittest.main()
