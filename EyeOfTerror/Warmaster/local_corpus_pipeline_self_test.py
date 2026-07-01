#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path

from eye_of_terror.inner_circle.iskandar import plan_lore_reconstruction
from eye_of_terror.local_executor import execute_run
from eye_of_terror.pipeline import write_pipeline_run


def write_epub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = " ".join(
        [
            "Goghur Argus Brond Solax argued before the battle.",
            "Tiberius Angellus Anteus stood with the Hedonarch.",
            "Kharn joined a parlay on a moon of Skalathrax.",
            "Dreagher saw Anteus open fire.",
            "The Golden Absolute became part of the escalation.",
            "World Eaters fell upon Skalathrax and Lucius was named in the wider account.",
            "The extremely cold night forced warriors to seek shelter.",
            "Kharn took a flamer and began burning his fellow warriors in the shelters.",
            "The World Eaters turned against their comrades.",
            "Afterwards the Legion shattered and no longer fought as a unified Legion.",
        ]
        * 4
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("chapter.xhtml", f"<html><head><title>Kharn</title></head><body><p>{body}</p></body></html>")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    old_corpus_dir = os.environ.get("SHUSHUNYA_CORPUS_DIR")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        corpus_root = root / "Corpus"
        write_epub(corpus_root / "kharn-eater-of-worlds.epub")
        os.environ["SHUSHUNYA_CORPUS_DIR"] = str(corpus_root)
        try:
            plan = plan_lore_reconstruction("Собери все известное о событиях Скалатракса.", task_id="local-corpus-pipeline")
            run_dir = root / "run"
            work_dir = root / "work"
            write_pipeline_run(plan.contract, run_dir, oversight=plan.to_dict()["oversight"])
            summary = execute_run(repo_root, run_dir, work_dir, timeout_sec=60)
        finally:
            if old_corpus_dir is None:
                os.environ.pop("SHUSHUNYA_CORPUS_DIR", None)
            else:
                os.environ["SHUSHUNYA_CORPUS_DIR"] = old_corpus_dir
        if not summary.get("ok"):
            raise AssertionError(f"local corpus pipeline should complete standard task: {summary}")
        base = work_dir / "skalathrax"
        source_map = json.loads((base / "source_map.json").read_text(encoding="utf-8"))
        local_sources = [source for source in source_map.get("sources", []) if source.get("discovery_method") == "local_corpus"]
        if not local_sources or local_sources[0].get("source_type") != "local_primary":
            raise AssertionError(f"local corpus source was not promoted into source map: {source_map.get('sources')}")
        snapshots = json.loads((base / "source_snapshots.json").read_text(encoding="utf-8"))
        local_snapshot = next((item for item in snapshots.get("snapshots", []) if item.get("local_path")), {})
        if not local_snapshot.get("ok") or "parlay on a moon of Skalathrax" not in local_snapshot.get("text_excerpt", ""):
            raise AssertionError(f"local corpus text was not fetched into snapshots: {local_snapshot}")
        notes = json.loads((base / "direct_event_notes.json").read_text(encoding="utf-8"))
        local_evidence_events = [
            event
            for event in notes.get("events", [])
            if any(
                isinstance(item, dict) and item.get("source_title") == local_snapshot.get("source_title")
                for item in event.get("evidence_snapshots", [])
            )
        ]
        event_ids = {event.get("event_id") for event in local_evidence_events}
        if "moon_parley" not in event_ids or "kharn_burns_shelters" not in event_ids:
            raise AssertionError(f"local primary source did not become direct event evidence: {local_evidence_events}")
        final_manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if final_manifest.get("status") != "ready":
            raise AssertionError(f"standard local corpus run should produce a ready manifest: {final_manifest}")
    print("[ok] local corpus pipeline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
