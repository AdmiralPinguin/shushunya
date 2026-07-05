#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from CorpusIngestor.corpus_ingestor import run as run_corpus
from Lexmechanic.lexmechanic import run as run_lex


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def assert_model_required(result: dict, worker: str) -> None:
    if result.get("ok") or result.get("error_code") != "model_brain_unavailable":
        raise AssertionError(f"{worker} should block without model_brain: {result}")


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        old_base_url = os.environ.get("EYE_MODEL_BASE_URL")
        old_timeout = os.environ.get("EYE_MODEL_TIMEOUT_SEC")
        os.environ["EYE_MODEL_BASE_URL"] = "http://127.0.0.1:9/v1"
        os.environ["EYE_MODEL_TIMEOUT_SEC"] = "1"
        try:
            corpus_request = {
                "task_id": "model-required:corpus",
                "contract": {"goal": "test corpus model requirement"},
                "step": {"expected_artifacts": ["/work/model/corpus_index.json"]},
            }
            assert_model_required(run_corpus(corpus_request, root), "CorpusIngestor")

            lex_request = {
                "task_id": "model-required:lex",
                "contract": {"goal": "Собери события Скалатракса"},
                "step": {"expected_artifacts": ["/work/model/source_map.json"]},
            }
            write_json(root / "model" / "corpus_index.json", {"summary": {}, "sources": []})
            assert_model_required(run_lex(lex_request, root, searcher=False), "Lexmechanic")
        finally:
            if old_base_url is None:
                os.environ.pop("EYE_MODEL_BASE_URL", None)
            else:
                os.environ["EYE_MODEL_BASE_URL"] = old_base_url
            if old_timeout is None:
                os.environ.pop("EYE_MODEL_TIMEOUT_SEC", None)
            else:
                os.environ["EYE_MODEL_TIMEOUT_SEC"] = old_timeout
    print("[ok] Scriptorium model brain contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
