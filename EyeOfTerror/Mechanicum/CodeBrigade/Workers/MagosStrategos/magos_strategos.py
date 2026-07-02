from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "CogitatorCodewright"))

import codewright_core  # noqa: E402
import cogitator_codewright as entrypoint  # noqa: E402


codewright_core.WORKER_NAME = "MagosStrategos"
run = entrypoint.run
main = entrypoint.main


if __name__ == "__main__":
    raise SystemExit(main())
