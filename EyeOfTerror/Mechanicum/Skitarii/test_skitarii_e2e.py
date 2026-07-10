"""Real end-to-end test: preload an existing buggy file into the sandbox VM, run the
warband to fix it, and verify the fix by execution — the bridge→VM→patch→verify path.

Slow (needs the VM and Qwen). Gated behind RUN_E2E=1:
    RUN_E2E=1 python3 test_skitarii_e2e.py
"""
from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

VM_KEY = os.environ.get("SKITARII_VM_KEY",
                        "/media/shushunya/SHUSHUNYA/shushunya/CoreOfMadness/vm-sandbox/skitarii_key")


@unittest.skipUnless(os.environ.get("RUN_E2E") == "1", "needs sandbox VM + Qwen; set RUN_E2E=1")
class TestE2EPatchInVM(unittest.TestCase):
    def test_fix_existing_file_by_execution(self):
        from executor import VmExecutor
        from warband import run_mission

        wd = f"/home/skitarii/work/e2e-{uuid.uuid4().hex[:8]}"
        ex = VmExecutor(host="127.0.0.1", port=2222, user="skitarii", key=VM_KEY, workdir=wd)
        self.assertTrue(ex.alive(), "sandbox VM must be reachable")
        ex.bash(f"rm -rf {wd}; mkdir -p {wd}")

        # a real existing file with a bug is preloaded (as the bridge would do)
        ex.write_file("calc.py", "def add(a, b):\n    return a - b\n\ndef mul(a, b):\n    return a * b\n")

        res = run_mission(
            "Исправь баг в calc.py: add(a,b) возвращает a-b, должна a+b. mul не трогай.",
            ex, task_id="e2e-patch",
            checks=[{"cmd": "python3 -c 'import calc; print(calc.add(2,3))'", "expect_stdout": "5"},
                    {"cmd": "python3 -c 'import calc; print(calc.mul(2,3))'", "expect_stdout": "6"}],
            max_fighter_rounds=2, max_wall_sec=600)

        self.assertTrue(res["accepted"], f"patch must pass verification: {res.get('summary')}")
        final = ex.read_file("calc.py")
        self.assertIn("a + b", final.replace(" ", " "))   # add was actually fixed
        self.assertIn("mul", final)                        # existing function preserved


if __name__ == "__main__":
    unittest.main(verbosity=2)
