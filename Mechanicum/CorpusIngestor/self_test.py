#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path

import corpus_ingestor
from corpus_ingestor import run, scan_corpus


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        corpus_root = root / "Corpus"
        corpus_root.mkdir()
        (corpus_root / "skalathrax-notes.txt").write_text(
            "Skalathrax\n" + ("Kharn and the World Eaters fought in the ruins of Skalathrax. " * 8),
            encoding="utf-8",
        )
        with zipfile.ZipFile(corpus_root / "kharn-eater-of-worlds.epub", "w") as archive:
            archive.writestr(
                "chapter.xhtml",
                "<html><head><title>Kharn</title></head><body><p>"
                + ("Primary EPUB text about Kharn and the World Eaters shelters. " * 6)
                + "</p></body></html>",
            )
        (corpus_root / "irrelevant.txt").write_text("A short note about another topic " * 8, encoding="utf-8")
        for index in range(35):
            (corpus_root / f"irrelevant-{index:02d}.txt").write_text(
                f"Unrelated local archive note {index}. " * 8,
                encoding="utf-8",
            )
        old_root = corpus_ingestor.DEFAULT_CORPUS_ROOT
        corpus_ingestor.DEFAULT_CORPUS_ROOT = corpus_root
        try:
            index = scan_corpus({"goal": "Максимально полно реконструируй события Скалатракса"})
            if index.get("summary", {}).get("sources_matched") != 2:
                raise AssertionError(f"corpus scan should match only relevant local text: {index}")
            if index.get("summary", {}).get("sources_non_matching") != 36 or index.get("summary", {}).get("non_matching_sample_count") != 30:
                raise AssertionError(f"corpus scan should expose non-matching local files: {index}")
            if len(index.get("non_matching", [])) != 30:
                raise AssertionError(f"corpus scan should cap non-matching samples without losing total count: {index}")
            source = next((item for item in index["sources"] if item.get("corpus_relative_path") == "skalathrax-notes.txt"), {})
            if source.get("source_class") != "local_primary_candidate" or not source.get("local_path"):
                raise AssertionError(f"local source metadata is wrong: {source}")
            if "skalathrax" not in source.get("matched_terms", []):
                raise AssertionError(f"local source should expose matched relevance terms: {source}")
            epub_source = next((item for item in index["sources"] if item.get("corpus_relative_path") == "kharn-eater-of-worlds.epub"), {})
            if epub_source.get("type") != "book" or epub_source.get("text_chars", 0) < 50:
                raise AssertionError(f"EPUB corpus source was not extracted as book text: {epub_source}")
            if "kharn" not in epub_source.get("matched_terms", []) or "skalathrax" in epub_source.get("matched_terms", []):
                raise AssertionError(f"EPUB should match through source playbook title terms, not only topic text: {epub_source}")
            request = {
                "task_id": "test:corpus_ingestion",
                "contract": {"goal": "Скалатракс"},
                "step": {"expected_artifacts": ["/work/skalathrax/corpus_index.json"]},
            }
            os.environ["SHUSHUNYA_CORPUS_DIR"] = str(corpus_root)
            result = run(request, root / "work")
            if not result.get("ok"):
                raise AssertionError(f"CorpusIngestor failed: {result}")
            output = root / "work" / "skalathrax" / "corpus_index.json"
            if not output.exists():
                raise AssertionError("corpus index was not written")
            written = json.loads(output.read_text(encoding="utf-8"))
            if written.get("summary", {}).get("sources_matched") != 2:
                raise AssertionError(f"written corpus index is wrong: {written}")
        finally:
            corpus_ingestor.DEFAULT_CORPUS_ROOT = old_root
            os.environ.pop("SHUSHUNYA_CORPUS_DIR", None)
    print("[ok] CorpusIngestor local corpus scan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
