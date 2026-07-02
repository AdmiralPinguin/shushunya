from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

MECHANICUM_ROOT = Path(__file__).resolve().parents[1]
if str(MECHANICUM_ROOT) not in sys.path:
    sys.path.insert(0, str(MECHANICUM_ROOT))

from codewright_core import *  # noqa: F403,E402 - compatibility surface for existing worker tests/imports.
from codewright_core import output_path_from_request, worker_name  # noqa: E402
from roles import (  # noqa: E402
    run_change_planning,
    run_code_review,
    run_finalize,
    run_implementation,
    run_repository_survey,
    run_verification,
)


STEP_HANDLERS = {
    "repository_survey": run_repository_survey,
    "change_planning": run_change_planning,
    "implementation": run_implementation,
    "verification": run_verification,
    "code_review": run_code_review,
    "finalize": run_finalize,
}


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    step = request.get("step") if isinstance(request.get("step"), dict) else {}
    step_id = str(step.get("step_id") or "")
    output_path = output_path_from_request(request)
    handler = STEP_HANDLERS.get(step_id)
    if handler is None:
        return {"ok": False, "worker": worker_name(), "error": f"unsupported step_id: {step_id}"}
    return handler(request, workspace_root, output_path)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run CogitatorCodewright code worker.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/mechanicum-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    result = run(payload.get("request") if isinstance(payload.get("request"), dict) else payload, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") or result.get("status") in {"blocked", "needs_revision", "passed_with_warnings"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
