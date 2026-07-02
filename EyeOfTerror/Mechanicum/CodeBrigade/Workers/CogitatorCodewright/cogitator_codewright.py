from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

WORKERS_ROOT = Path(__file__).resolve().parents[1]
if str(WORKERS_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKERS_ROOT))
for worker_dir in [
    "LogisRepository",
    "MagosStrategos",
    "FerrumPatchwright",
    "OrdinatusVerifier",
    "JudicatorCodicis",
    "SealwrightFinalis",
]:
    worker_path = WORKERS_ROOT / worker_dir
    if str(worker_path) not in sys.path:
        sys.path.insert(0, str(worker_path))

from common.codewright_core import *  # noqa: F403,E402 - compatibility surface for existing worker tests/imports.
from common.codewright_core import output_path_from_request, worker_name  # noqa: E402
from change_planning import run_change_planning  # noqa: E402
from code_review import run_code_review  # noqa: E402
from finalize import run_finalize  # noqa: E402
from implementation import run_implementation  # noqa: E402
from repository_survey import run_repository_survey  # noqa: E402
from verification import run_verification  # noqa: E402


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
