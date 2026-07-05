#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from eye_of_terror.inner_circle.iskandar import oversight_plan, plan_research_writing
from eye_of_terror.pipeline import build_dispatch_packets, write_pipeline_run


REPO_ROOT = Path(__file__).resolve().parents[2]
BRIGADE_ROOT = REPO_ROOT / "EyeOfTerror" / "Scriptorium" / "Brigade"
MODEL_BRAIN = {"ok": True, "status": "answered", "content": "{\"status\":\"ok\"}"}


def load_worker(worker: str, module_name: str) -> Any:
    path = BRIGADE_ROOT / worker / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{worker}_{module_name}", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load worker module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WORKERS = {
    "CorpusIngestor": load_worker("CorpusIngestor", "corpus_ingestor"),
    "Lexmechanic": load_worker("Lexmechanic", "lexmechanic"),
    "AuspexBrowser": load_worker("AuspexBrowser", "auspex_browser"),
    "OcularisRenderium": load_worker("OcularisRenderium", "ocularis_renderium"),
    "NoosphericExtractor": load_worker("NoosphericExtractor", "noospheric_extractor"),
    "Chronologis": load_worker("Chronologis", "chronologis"),
    "ScriptoriumArchitect": load_worker("ScriptoriumArchitect", "scriptorium_architect"),
    "ScriptoriumDaemon": load_worker("ScriptoriumDaemon", "scriptorium_daemon"),
    "ReductorVerifier": load_worker("ReductorVerifier", "reductor_verifier"),
    "FabricatorFinalis": load_worker("FabricatorFinalis", "fabricator_finalis"),
}


def fake_search(query: str, limit: int) -> dict[str, Any]:
    query_digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:10]
    safe_query = (query.lower().replace(" ", "-")[:40] or "topic") + "-" + query_digest
    results = []
    for index in range(1, min(limit, 8) + 1):
        if index % 3 == 1:
            url = f"https://docs.example.test/{safe_query}/official-{index}"
            title = f"Official documentation source {index}"
        elif index % 3 == 2:
            url = f"https://en.wikipedia.org/wiki/{safe_query}-{index}"
            title = f"Encyclopedia source {index}"
        else:
            url = f"https://research.example.test/{safe_query}/analysis-{index}"
            title = f"General research source {index}"
        results.append({"title": title, "url": url, "snippet": f"Evidence summary for {query} source {index}."})
    return {"ok": True, "source": "fake", "results": results}


def fake_fetch(url: str, max_bytes: int) -> dict[str, Any]:
    subject = url.rsplit("/", 1)[-1].replace("-", " ")
    sentence = (
        f"{subject} provides a documented claim about the requested research topic, "
        f"with source-grounded context, comparative implications, implementation details, "
        f"historical background, operational tradeoffs, and explicit limits for later synthesis"
    )
    text = ". ".join([sentence for _ in range(8)]) + "."
    return {
        "ok": True,
        "url": url,
        "status": 200,
        "content_type": "text/html",
        "title": subject.title(),
        "text": text[:max_bytes],
        "bytes_read": min(len(text.encode("utf-8")), max_bytes),
        "truncated": False,
        "is_binary": False,
    }


def run_worker(packet: Any, workspace_root: Path) -> dict[str, Any]:
    request = json.loads(json.dumps(packet.request))
    request["model_brain"] = MODEL_BRAIN
    module = WORKERS[packet.worker]
    if packet.worker == "Lexmechanic":
        return module.run(request, workspace_root, searcher=fake_search)
    if packet.worker == "AuspexBrowser":
        return module.run(request, workspace_root, fetcher=fake_fetch)
    return module.run(request, workspace_root)


def run_mode(task: str, expected_mode: str, expected_intent: str, checks: list[str]) -> None:
    plan = plan_research_writing(task, task_id=f"integration-{expected_intent}")
    payload = plan.to_dict()
    if payload.get("oversight", {}).get("research_intent", {}).get("output_mode") != expected_mode:
        raise AssertionError(f"bad mode for {task!r}: {payload.get('oversight', {}).get('research_intent')}")
    packets = build_dispatch_packets(plan.contract, oversight=payload["oversight"])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        run_dir = root / "run"
        work_dir = root / "work"
        write_pipeline_run(plan.contract, run_dir, oversight=oversight_plan(plan.contract))
        for packet in packets:
            result = run_worker(packet, work_dir)
            if not result.get("ok"):
                raise AssertionError(f"{expected_mode} worker {packet.worker} failed: {result}")
        final_paths = list(work_dir.glob("**/final_manifest.json"))
        if len(final_paths) != 1:
            raise AssertionError(f"{expected_mode} should produce one final manifest: {final_paths}")
        manifest = json.loads(final_paths[0].read_text(encoding="utf-8"))
        if manifest.get("status") != "ready" or manifest.get("output_mode") != expected_mode:
            raise AssertionError(f"{expected_mode} final manifest is not ready: {manifest}")
        manifest_text = json.dumps(manifest, ensure_ascii=False)
        for check in checks:
            if check not in manifest_text:
                raise AssertionError(f"{expected_mode} final manifest missing {check!r}: {manifest}")


def main() -> int:
    old_live = os.environ.get("LEXMECHANIC_LIVE_DISCOVERY")
    os.environ["LEXMECHANIC_LIVE_DISCOVERY"] = "1"
    try:
        run_mode(
            "Сравни CrewAI и AutoGen для локального агента.",
            "comparative_review",
            "comparison",
            ["structure_map.json", "synthesis_plan.json", "quality_gates"],
        )
        run_mode(
            "Сделай longform article о локальных LLM агентах.",
            "longform_article",
            "longform_article",
            ["structure_map.json", "synthesis_plan.json", "quality_gates"],
        )
        run_mode(
            "Напиши book на 3 chapters о локальных агентах.",
            "book_manuscript",
            "book",
            ["book_outline.json", "chapter_plan.json", "manuscript_ru.md", "manuscript.fb2"],
        )
    finally:
        if old_live is None:
            os.environ.pop("LEXMECHANIC_LIVE_DISCOVERY", None)
        else:
            os.environ["LEXMECHANIC_LIVE_DISCOVERY"] = old_live
    print("[ok] research output pipelines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
