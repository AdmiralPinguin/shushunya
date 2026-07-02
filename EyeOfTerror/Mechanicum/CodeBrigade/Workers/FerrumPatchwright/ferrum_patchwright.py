from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "CogitatorCodewright"))

import cogitator_codewright as core  # noqa: E402


core.WORKER_NAME = "FerrumPatchwright"


if __name__ == "__main__":
    raise SystemExit(core.main())
