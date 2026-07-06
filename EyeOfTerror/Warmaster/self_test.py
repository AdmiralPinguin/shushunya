#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from eye_of_terror.contracts import (
    TASK_CONTRACT_FIELDS,
    TASK_CONTRACT_REQUIRED_FIELDS,
    TASK_KINDS,
    WORKER_STEP_FIELDS,
    WORKER_STEP_REQUIRED_FIELDS,
    build_lore_reconstruction_contract,
    build_research_writing_contract,
    classify_research_intent,
    validate_task_contract_payload,
)
from eye_of_terror.inner_circle.iskandar import plan_lore_reconstruction, plan_research_writing
from eye_of_terror.pipeline import build_dispatch_packets, write_pipeline_run
from eye_of_terror.registry import worker_refs


def documented_iskandar_pipeline() -> list[str]:
    readme = Path(__file__).resolve().parents[1] / "Scriptorium" / "IskandarKhayon" / "README.md"
    text = readme.read_text(encoding="utf-8")
    match = re.search(r"## Default Worker Pipeline\s+```text\n(?P<body>.*?)\n```", text, flags=re.S)
    if not match:
        raise AssertionError("Iskandar README missing Default Worker Pipeline block")
    return [
        line.replace("->", "").strip()
        for line in match.group("body").splitlines()
        if line.replace("->", "").strip()
    ]


def main() -> int:
    schema = json.loads((Path(__file__).resolve().parent / "contracts" / "task_contract.schema.json").read_text(encoding="utf-8"))
    schema_required = set(schema.get("required", []))
    schema_fields = set(schema.get("properties", {}))
    schema_kinds = set(schema.get("properties", {}).get("kind", {}).get("enum", []))
    step_schema = schema.get("properties", {}).get("worker_plan", {}).get("items", {})
    step_required = set(step_schema.get("required", []))
    step_fields = set(step_schema.get("properties", {}))
    if schema_required != TASK_CONTRACT_REQUIRED_FIELDS or schema_fields != TASK_CONTRACT_FIELDS or schema_kinds != TASK_KINDS:
        raise AssertionError("task contract schema and runtime validator constants disagree")
    if step_required != WORKER_STEP_REQUIRED_FIELDS or step_fields != WORKER_STEP_FIELDS:
        raise AssertionError("worker step schema and runtime validator constants disagree")
    if schema.get("properties", {}).get("required_artifacts", {}).get("items", {}).get("pattern") != "^/work/":
        raise AssertionError("task contract schema should require /work required_artifacts")
    if schema.get("properties", {}).get("required_artifacts", {}).get("uniqueItems") is not True:
        raise AssertionError("task contract schema should reject duplicate required_artifacts")
    if step_schema.get("properties", {}).get("expected_artifacts", {}).get("items", {}).get("pattern") != "^/work/":
        raise AssertionError("task contract schema should require /work expected_artifacts")
    if step_schema.get("properties", {}).get("expected_artifacts", {}).get("uniqueItems") is not True:
        raise AssertionError("task contract schema should reject duplicate expected_artifacts")
    if step_schema.get("properties", {}).get("depends_on", {}).get("uniqueItems") is not True:
        raise AssertionError("task contract schema should reject duplicate step dependencies")
    if step_schema.get("properties", {}).get("worker", {}).get("minLength") != 1:
        raise AssertionError("task contract schema should require non-empty worker names")
    print("[ok] task contract schema")

    workers = worker_refs()
    ports = [worker.port for worker in workers]
    if ports != sorted(ports) or any(port < 7001 for port in ports):
        raise AssertionError(f"worker ports are not stable 7001+ order: {ports}")
    names = {worker.name for worker in workers}
    required = {
        "Lexmechanic",
        "AuspexBrowser",
        "NoosphericExtractor",
        "Chronologis",
        "ScriptoriumArchitect",
        "ScriptoriumDaemon",
        "ReductorVerifier",
        "FabricatorFinalis",
        "ForgeRelay",
    }
    if not required.issubset(names):
        raise AssertionError(f"worker registry missing expected workers: {required - names}")
    print("[ok] worker registry")

    classifier_cases = {
        "Собери реконструкцию событий Скалатракса по хронологии.": ("event_reconstruction", "event_reconstruction", True, False),
        "Собери реконструкцию событий Скалатракса по книгам, кодексам и wiki.": ("event_reconstruction", "event_reconstruction", True, False),
        "Сравни CrewAI и AutoGen для локального агента.": ("comparison", "comparative_review", False, False),
        "Что такое квантование модели?": ("qa_answer", "short_answer", False, False),
        "Напиши книгу на 3 главы о падении легиона.": ("book", "book_manuscript", False, True),
        "Проверь и выясни, почему пайплайн падает.": ("investigation", "investigative_report", False, False),
        "Сделай лонгрид о домашних 3D-принтерах.": ("longform_article", "longform_article", False, False),
    }
    for classifier_task, expected in classifier_cases.items():
        profile = classify_research_intent(classifier_task)
        actual = (profile["intent"], profile["output_mode"], profile["needs_timeline"], profile["needs_chapters"])
        if actual != expected:
            raise AssertionError(f"bad research intent classification for {classifier_task!r}: {profile}")
    if classify_research_intent("Напиши книгу на 5 глав о локальных агентах.").get("chapter_count") != 5:
        raise AssertionError("research intent classifier should preserve requested book chapter count")
    print("[ok] research intent classifier")

    task = "Собери все известное о событиях Скалатракса и сделай реконструкцию."
    contract = build_lore_reconstruction_contract(task, task_id="test-skalathrax")
    payload = contract.to_dict()
    if payload["assigned_governor"] != "IskandarKhayon" or payload["kind"] != "research":
        raise AssertionError(f"bad lore contract routing: {payload}")
    validation_errors = validate_task_contract_payload(payload)
    if validation_errors:
        raise AssertionError(f"valid lore contract failed validation: {validation_errors}")
    broken_payload = json.loads(json.dumps(payload))
    broken_payload["worker_plan"][0]["depends_on"] = ["missing-step"]
    if not validate_task_contract_payload(broken_payload):
        raise AssertionError("broken lore contract should fail validation")
    broken_text_list = json.loads(json.dumps(payload))
    broken_text_list["quality_gates"] = ["source_map_created", 7]
    if not any("quality_gates[1]" in error for error in validate_task_contract_payload(broken_text_list)):
        raise AssertionError("task contract validator should reject non-string quality gates")
    broken_required_artifact = json.loads(json.dumps(payload))
    broken_required_artifact["required_artifacts"].append("/work/skalathrax/unproduced.json")
    if not any("not produced" in error for error in validate_task_contract_payload(broken_required_artifact)):
        raise AssertionError("task contract validator should reject required artifacts without producers")
    broken_duplicate_output = json.loads(json.dumps(payload))
    broken_duplicate_output["worker_plan"][1]["expected_artifacts"] = broken_duplicate_output["worker_plan"][0]["expected_artifacts"]
    if not any("multiple producer" in error for error in validate_task_contract_payload(broken_duplicate_output)):
        raise AssertionError("task contract validator should reject duplicate artifact producers")
    broken_duplicate_required = json.loads(json.dumps(payload))
    broken_duplicate_required["required_artifacts"].append(broken_duplicate_required["required_artifacts"][0])
    if not any("duplicate required artifact" in error for error in validate_task_contract_payload(broken_duplicate_required)):
        raise AssertionError("task contract validator should reject duplicate required artifacts")
    broken_duplicate_dependency = json.loads(json.dumps(payload))
    broken_duplicate_dependency["worker_plan"][1]["depends_on"] = ["source_discovery", "source_discovery"]
    if not any("depends_on contains duplicates" in error for error in validate_task_contract_payload(broken_duplicate_dependency)):
        raise AssertionError("task contract validator should reject duplicate dependencies")
    broken_duplicate_expected = json.loads(json.dumps(payload))
    broken_duplicate_expected["worker_plan"][0]["expected_artifacts"].append(broken_duplicate_expected["worker_plan"][0]["expected_artifacts"][0])
    if not any("expected_artifacts contains duplicates" in error for error in validate_task_contract_payload(broken_duplicate_expected)):
        raise AssertionError("task contract validator should reject duplicate expected artifacts")
    if "/work/skalathrax/source_map.json" not in payload["required_artifacts"]:
        raise AssertionError(f"skalathrax artifacts not derived: {payload['required_artifacts']}")
    if "/work/skalathrax/source_snapshots.json" not in payload["required_artifacts"]:
        raise AssertionError(f"source snapshots missing: {payload['required_artifacts']}")
    step_workers = [step["worker"] for step in payload["worker_plan"]]
    if not all(step.get("step_id") for step in payload["worker_plan"]):
        raise AssertionError(f"worker steps must expose stable step_id: {payload['worker_plan']}")
    expected_order = [
        "CorpusIngestor",
        "Lexmechanic",
        "AuspexBrowser",
        "OcularisRenderium",
        "NoosphericExtractor",
        "Chronologis",
        "ScriptoriumArchitect",
        "ScriptoriumDaemon",
        "ReductorVerifier",
        "FabricatorFinalis",
    ]
    if step_workers != expected_order:
        raise AssertionError(f"wrong Iskandar worker order: {step_workers}")
    if documented_iskandar_pipeline() != expected_order:
        raise AssertionError(f"Iskandar README worker pipeline is out of sync: {documented_iskandar_pipeline()}")
    for artifact in [
        "/work/skalathrax/research_corpus.json",
        "/work/skalathrax/structure_map.json",
        "/work/skalathrax/synthesis_plan.json",
    ]:
        if artifact not in payload["required_artifacts"]:
            raise AssertionError(f"lore reconstruction must use new research pipeline artifact {artifact}: {payload['required_artifacts']}")
    print("[ok] lore reconstruction contract")

    generic_task = "Исследуй историю развития домашних 3D-принтеров и собери связный русский обзор с источниками."
    generic_contract = build_research_writing_contract(generic_task, task_id="test-generic-research")
    generic_payload = generic_contract.to_dict()
    if generic_payload["assigned_governor"] != "IskandarKhayon" or generic_payload["kind"] != "research":
        raise AssertionError(f"bad research/writing contract routing: {generic_payload}")
    if validate_task_contract_payload(generic_payload):
        raise AssertionError(f"valid research/writing contract failed validation: {validate_task_contract_payload(generic_payload)}")
    if generic_payload["task_id"] != "test-generic-research":
        raise AssertionError(f"generic research task id not preserved: {generic_payload}")
    if "Do not answer from a single convenient source" not in " ".join(generic_payload["non_goals"]):
        raise AssertionError(f"generic research contract lacks broad-source guardrail: {generic_payload}")
    generic_plan = plan_research_writing(generic_task, task_id="test-generic-research").to_dict()
    if (
        not generic_plan["ok"]
        or generic_plan["oversight"]["kind"] != "research_writing_oversight"
        or generic_plan["oversight"]["research_intent"]["intent"] != "topic_report"
        or generic_plan["oversight"]["pipeline_plan"]["intent"] != "topic_report"
        or generic_plan["oversight"]["pipeline_plan"]["required_depth"] != "deep"
        or not generic_plan["oversight"]["pipeline_plan"]["source_policy"]
        or generic_plan["contract"]["required_artifacts"][0] != "/work/3d/corpus_index.json"
        or "/work/3d/research_corpus.json" not in generic_plan["contract"]["required_artifacts"]
        or "/work/3d/structure_map.json" not in generic_plan["contract"]["required_artifacts"]
        or "/work/3d/synthesis_plan.json" not in generic_plan["contract"]["required_artifacts"]
        or generic_plan["contract"]["worker_plan"][4]["purpose"].find("claims, events, arguments") < 0
    ):
        raise AssertionError(f"bad generic research/writing plan: {generic_plan}")
    qa_contract = build_research_writing_contract("Что такое llama.cpp?", task_id="test-qa")
    qa_workers = [step.worker for step in qa_contract.worker_plan]
    if "Chronologis" in qa_workers or any(artifact.endswith("/timeline.json") for artifact in qa_contract.required_artifacts):
        raise AssertionError(f"short Q&A should not force Chronologis/timeline: {qa_contract.to_dict()}")
    event_contract = build_research_writing_contract("Реконструируй события битвы при Скалатраксе.", task_id="test-event-research")
    event_payload = event_contract.to_dict()
    if (
        "Chronologis" not in [step["worker"] for step in event_payload["worker_plan"]]
        or "ScriptoriumArchitect" not in [step["worker"] for step in event_payload["worker_plan"]]
        or "/work/skalathrax/timeline.json" not in event_payload["required_artifacts"]
        or "/work/skalathrax/structure_map.json" not in event_payload["required_artifacts"]
    ):
        raise AssertionError(f"event research should include timeline and structure map: {event_payload}")
    book_contract = build_research_writing_contract("Напиши book на 3 chapters о локальных агентах.", task_id="test-book")
    book_payload = book_contract.to_dict()
    for artifact in [
        "/work/book-3-chapters/book_outline.json",
        "/work/book-3-chapters/chapter_plan.json",
        "/work/book-3-chapters/chapters/chapter_01.md",
        "/work/book-3-chapters/chapters/chapter_02.md",
        "/work/book-3-chapters/chapters/chapter_03.md",
        "/work/book-3-chapters/manuscript_ru.md",
        "/work/book-3-chapters/manuscript.fb2",
    ]:
        if artifact not in book_payload["required_artifacts"]:
            raise AssertionError(f"book contract missing required artifact {artifact}: {book_payload}")
    five_chapter_contract = build_research_writing_contract("Напиши book на 5 chapters о локальных агентах.", task_id="test-book-five")
    five_chapter_artifacts = five_chapter_contract.to_dict()["required_artifacts"]
    if "/work/book-5-chapters/chapters/chapter_05.md" not in five_chapter_artifacts or "/work/book-5-chapters/chapters/chapter_06.md" in five_chapter_artifacts:
        raise AssertionError(f"book contract should create exactly the requested chapter artifacts: {five_chapter_artifacts}")
    book_plan = plan_research_writing("Напиши book на 3 chapters о локальных агентах.", task_id="test-book").to_dict()
    if (
        book_plan.get("oversight", {}).get("final_review", {}).get("deliverable_role") != "fb2"
        or book_plan.get("oversight", {}).get("final_review", {}).get("deliverable_artifacts") != ["/work/book-3-chapters/manuscript.fb2"]
        or book_plan.get("oversight", {}).get("pipeline_plan", {}).get("intent") != "book"
    ):
        raise AssertionError(f"book oversight should expose fb2 deliverable and selected intent: {book_plan}")
    print("[ok] research/writing contract")

    plan = plan_lore_reconstruction(task, task_id="test-skalathrax").to_dict()
    if not plan["ok"] or plan["missing_workers"] or plan.get("unavailable_workers"):
        raise AssertionError(f"Iskandar plan did not resolve workers: {json.dumps(plan, ensure_ascii=False)}")
    if plan.get("resolved_workers", {}).get("Lexmechanic", {}).get("status") != "prototype":
        raise AssertionError(f"Iskandar plan should expose worker metadata: {plan.get('resolved_workers')}")
    if not plan.get("validation", {}).get("ok"):
        raise AssertionError(f"Iskandar plan failed contract validation: {plan.get('validation')}")
    if "Do not deliver a shallow wiki summary" not in " ".join(plan["contract"]["non_goals"]):
        raise AssertionError("Iskandar contract does not guard against shallow wiki summaries")
    print("[ok] Iskandar worker plan")

    packets = build_dispatch_packets(contract)
    if [packet.step_id for packet in packets] != [
        "corpus_ingestion",
        "source_discovery",
        "source_acquisition",
        "source_rendering",
        "fact_extraction",
        "structure_mapping",
        "synthesis_planning",
        "draft_reconstruction",
        "critic_review",
        "finalize",
    ]:
        raise AssertionError(f"wrong dispatch packet sequence: {[packet.step_id for packet in packets]}")
    if packets[0].port != 7013 or packets[-1].port != 7007:
        raise AssertionError(f"dispatch packets target wrong ports: {[packet.port for packet in packets]}")
    if packets[4].request["task_id"] != "test-skalathrax:fact_extraction":
        raise AssertionError(f"dispatch task id is not stable: {packets[4].request}")
    if packets[1].request["input_artifacts"] != ["/work/skalathrax/corpus_index.json"]:
        raise AssertionError(f"corpus input artifact was not propagated: {packets[1].request}")
    if packets[3].request["input_artifacts"] != ["/work/skalathrax/source_snapshots.json"]:
        raise AssertionError(f"render dependency input artifact was not propagated: {packets[3].request}")
    if packets[4].request["input_artifacts"] != ["/work/skalathrax/rendered_snapshots.json"]:
        raise AssertionError(f"dependency input artifacts were not propagated: {packets[4].request}")
    expected_draft_inputs = [
        "/work/skalathrax/source_map.json",
        "/work/skalathrax/direct_event_notes.json",
        "/work/skalathrax/research_corpus.json",
        "/work/skalathrax/timeline.json",
        "/work/skalathrax/structure_map.json",
        "/work/skalathrax/synthesis_plan.json",
    ]
    if packets[7].input_artifacts != expected_draft_inputs:
        raise AssertionError(f"multi-dependency input artifacts were not propagated: {packets[7].to_dict()}")
    print("[ok] Iskandar dispatch packets")

    with tempfile.TemporaryDirectory() as temp_dir:
        oversight = plan_lore_reconstruction(task, task_id="test-skalathrax").to_dict()["oversight"]
        stale_dispatch = Path(temp_dir) / "dispatch" / "stale_step.json"
        stale_dispatch.parent.mkdir(parents=True, exist_ok=True)
        stale_dispatch.write_text("{}", encoding="utf-8")
        status = write_pipeline_run(contract, Path(temp_dir), oversight=oversight)
        if not status["ok"]:
            raise AssertionError(f"pipeline status failed: {status}")
        fact_status = next((item for item in status.get("steps", []) if item.get("step_id") == "fact_extraction"), {})
        if (
            fact_status.get("quality_hints", {}).get("check_count", 0) < 1
            or "critic_review" not in fact_status.get("quality_hints", {}).get("revision_targets", [])
        ):
            raise AssertionError(f"pipeline status did not expose quality hints: {status}")
        if not status.get("oversight_path"):
            raise AssertionError(f"pipeline status did not expose oversight path: {status}")
        expected_files = [
            "contract.json",
            "oversight.json",
            "status.json",
            "dispatch/corpus_ingestion.json",
            "dispatch/source_discovery.json",
            "dispatch/source_acquisition.json",
            "dispatch/source_rendering.json",
            "dispatch/fact_extraction.json",
            "dispatch/structure_mapping.json",
            "dispatch/synthesis_planning.json",
            "dispatch/draft_reconstruction.json",
            "dispatch/critic_review.json",
            "dispatch/finalize.json",
        ]
        missing = [name for name in expected_files if not (Path(temp_dir) / name).exists()]
        if missing:
            raise AssertionError(f"pipeline run did not write expected files: {missing}")
        if stale_dispatch.exists():
            raise AssertionError(f"pipeline run left stale dispatch packet: {stale_dispatch}")
        leftovers = list(Path(temp_dir).glob("**/*.tmp"))
        if leftovers:
            raise AssertionError(f"pipeline run left atomic temp files: {leftovers}")
        written_oversight = json.loads((Path(temp_dir) / "oversight.json").read_text(encoding="utf-8"))
        if written_oversight.get("final_review", {}).get("final_artifact") != "/work/skalathrax/final_manifest.json":
            raise AssertionError(f"pipeline run wrote bad oversight: {written_oversight}")
        fact_dispatch = json.loads((Path(temp_dir) / "dispatch" / "fact_extraction.json").read_text(encoding="utf-8"))
        fact_expectations = fact_dispatch.get("request", {}).get("quality_expectations", {})
        if (
            fact_expectations.get("step_quality", {}).get("step_id") != "fact_extraction"
            or fact_expectations.get("step_quality", {}).get("worker") != "NoosphericExtractor"
            or fact_expectations.get("final_review", {}).get("critic_step") != "critic_review"
            or fact_expectations.get("revision_policy", {}).get("source_step") != "critic_review"
        ):
            raise AssertionError(f"dispatch packet did not include step quality expectations: {fact_dispatch}")
        if (
            written_oversight.get("revision_policy", {}).get("source_step") != "critic_review"
            or written_oversight.get("revision_policy", {}).get("final_steps") != ["critic_review", "finalize"]
            or written_oversight.get("revision_policy", {}).get("requires_downstream_rerun") is not True
            or written_oversight.get("iteration_policy", {}).get("recommended_endpoint") != "POST /runs/{task_id}/start_research_loop_http"
            or written_oversight.get("iteration_policy", {}).get("max_revision_cycles") != 3
        ):
            raise AssertionError(f"pipeline run wrote bad revision/iteration policy: {written_oversight}")
        quality_matrix = written_oversight.get("step_quality_matrix", [])
        fact_quality = next((item for item in quality_matrix if item.get("step_id") == "fact_extraction"), {})
        if (
            len(quality_matrix) != len(contract.worker_plan)
            or fact_quality.get("worker") != "NoosphericExtractor"
            or "/work/skalathrax/rendered_snapshots.json" not in fact_quality.get("required_inputs", [])
            or "research corpus exists and includes claims, events, arguments, evidence excerpts, confidence, and gaps" not in fact_quality.get("checks", [])
            or "critic_review" not in fact_quality.get("revision_targets", [])
            or "finalize" not in fact_quality.get("revision_targets", [])
        ):
            raise AssertionError(f"pipeline run wrote bad step quality matrix: {written_oversight}")
    print("[ok] Iskandar pipeline run package")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
