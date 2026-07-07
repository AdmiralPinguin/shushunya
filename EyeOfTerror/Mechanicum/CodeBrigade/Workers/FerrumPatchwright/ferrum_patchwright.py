from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

WORKERS_ROOT = Path(__file__).resolve().parents[1]
if str(WORKERS_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKERS_ROOT))

from common import codewright_core  # noqa: E402
from common.worker_protocol import strict_worker_request_from_payload  # noqa: E402
from implementation import run_implementation  # noqa: E402


WORKER_NAME = "FerrumPatchwright"
STEP_ID = "implementation"


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    codewright_core.WORKER_NAME = WORKER_NAME
    step = request.get("step") if isinstance(request.get("step"), dict) else {}
    step_id = str(step.get("step_id") or "")
    if step_id != STEP_ID:
        return {"ok": False, "worker": WORKER_NAME, "error": f"unsupported step_id for {WORKER_NAME}: {step_id}"}
    return run_implementation(request, workspace_root, codewright_core.output_path_from_request(request))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FerrumPatchwright implementation worker.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/mechanicum-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    result = run(strict_worker_request_from_payload(payload, WORKER_NAME), Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") or result.get("status") in {"blocked", "needs_revision", "passed_with_warnings"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
