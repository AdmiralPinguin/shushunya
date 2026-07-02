from __future__ import annotations

import sys
from pathlib import Path


MOBILE_GATEWAY_ROOT = Path(__file__).resolve().parents[1] / "MobileGateway" / "ShushunyaAgent"
if str(MOBILE_GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(MOBILE_GATEWAY_ROOT))

from shushunya_agent.server import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
