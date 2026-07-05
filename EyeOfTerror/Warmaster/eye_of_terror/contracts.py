from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


TASK_CONTRACT_FIELDS = {
    "version",
    "task_id",
    "kind",
    "goal",
    "assigned_governor",
    "non_goals",
    "required_artifacts",
    "completion_criteria",
    "quality_gates",
    "worker_plan",
}
TASK_CONTRACT_REQUIRED_FIELDS = {"version", "task_id", "kind", "goal", "assigned_governor", "completion_criteria"}
TASK_KINDS = {"chat", "research", "image_generation", "code", "general"}
WORKER_STEP_FIELDS = {"step_id", "worker", "purpose", "depends_on", "expected_artifacts"}
WORKER_STEP_REQUIRED_FIELDS = {"step_id", "worker", "purpose"}
RESEARCH_INTENTS = {
    "event_reconstruction",
    "topic_report",
    "comparison",
    "qa_answer",
    "investigation",
    "longform_article",
    "book",
}


def slugify(value: str, fallback: str = "task") -> str:
    lowered = value.lower()
    replacements = {
        "skalathrax": "skalathrax",
        "scalathrax": "skalathrax",
        "скалатрак": "skalathrax",
        "кхарн": "kharn",
        "kharn": "kharn",
        "церакс": "ceraxia",
        "ceraxia": "ceraxia",
        "код": "code",
        "прилож": "app",
    }
    for needle, slug in replacements.items():
        if needle in lowered:
            return slug
    words = re.findall(r"[a-zA-Z0-9]+", lowered)
    return "-".join(words[:6]) or fallback


def classify_research_intent(user_task: str) -> dict[str, Any]:
    text = " ".join(user_task.lower().split())
    has_question_mark = "?" in user_task
    event_terms = (
        "событ",
        "битв",
        "сражен",
        "хронолог",
        "реконструкц",
        "timeline",
        "chronology",
        "battle",
        "event",
        "reconstruct",
    )
    comparison_terms = ("сравн", "отлич", "разниц", "против", " vs ", "compare", "comparison", "difference")
    investigation_terms = ("расслед", "выясн", "проверь", "разбер", "докоп", "investigat", "audit", "verify")
    book_terms = ("книг", "роман", "манускрипт", "глав", "fb2", "book", "manuscript", "chapters")
    longform_terms = ("лонгрид", "статья", "эссе", "подробн", "longform", "article", "essay")
    qa_terms = ("что такое", "кто ", "почему", "как ", "зачем", "where ", "what ", "who ", "why ", "how ")

    if any(term in text for term in book_terms):
        intent = "book"
        output_mode = "book_manuscript"
        required_depth = "comprehensive"
        source_policy = "primary_and_secondary_sources_required"
        needs_chapters = True
    elif any(term in text for term in comparison_terms):
        intent = "comparison"
        output_mode = "comparative_review"
        required_depth = "deep"
        source_policy = "balanced_sources_for_each_side"
        needs_chapters = False
    elif any(term in text for term in investigation_terms):
        intent = "investigation"
        output_mode = "investigative_report"
        required_depth = "deep"
        source_policy = "evidence_first_with_contradiction_tracking"
        needs_chapters = False
    elif any(term in text for term in event_terms):
        intent = "event_reconstruction"
        output_mode = "event_reconstruction"
        required_depth = "comprehensive"
        source_policy = "chronological_primary_and_secondary_sources"
        needs_chapters = False
    elif any(term in text for term in longform_terms):
        intent = "longform_article"
        output_mode = "longform_article"
        required_depth = "deep"
        source_policy = "broad_sources_with_evidence_trace"
        needs_chapters = False
    elif has_question_mark or any(term in text for term in qa_terms):
        intent = "qa_answer"
        output_mode = "short_answer"
        required_depth = "standard"
        source_policy = "answer_with_citations"
        needs_chapters = False
    else:
        intent = "topic_report"
        output_mode = "research_report"
        required_depth = "deep"
        source_policy = "broad_sources_with_gaps_disclosed"
        needs_chapters = False

    needs_timeline = intent == "event_reconstruction" or any(term in text for term in event_terms)
    if intent == "book" and needs_timeline:
        output_mode = "book_manuscript_with_timeline"

    return {
        "intent": intent,
        "output_mode": output_mode,
        "required_depth": required_depth,
        "source_policy": source_policy,
        "needs_timeline": needs_timeline,
        "needs_chapters": needs_chapters,
    }


@dataclass
class WorkerPlanStep:
    step_id: str
    worker: str
    purpose: str
    depends_on: list[str] = field(default_factory=list)
    expected_artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "worker": self.worker,
            "purpose": self.purpose,
            "depends_on": self.depends_on,
            "expected_artifacts": self.expected_artifacts,
        }


@dataclass
class TaskContract:
    task_id: str
    kind: str
    goal: str
    assigned_governor: str
    non_goals: list[str]
    required_artifacts: list[str]
    completion_criteria: list[str]
    quality_gates: list[str]
    worker_plan: list[WorkerPlanStep]
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "task_id": self.task_id,
            "kind": self.kind,
            "goal": self.goal,
            "assigned_governor": self.assigned_governor,
            "non_goals": self.non_goals,
            "required_artifacts": self.required_artifacts,
            "completion_criteria": self.completion_criteria,
            "quality_gates": self.quality_gates,
            "worker_plan": [step.to_dict() for step in self.worker_plan],
        }


def lore_required_artifacts(slug: str) -> list[str]:
    base = f"/work/{slug}"
    return [
        f"{base}/corpus_index.json",
        f"{base}/source_map.json",
        f"{base}/source_snapshots.json",
        f"{base}/rendered_snapshots.json",
        f"{base}/direct_event_notes.json",
        f"{base}/timeline.json",
        f"{base}/reconstruction_ru.md",
        f"{base}/coverage_report.md",
        f"{base}/critic_report.json",
        f"{base}/final_manifest.json",
    ]


def lore_worker_plan(slug: str) -> list[WorkerPlanStep]:
    base = f"/work/{slug}"
    return [
        WorkerPlanStep(
            step_id="corpus_ingestion",
            worker="CorpusIngestor",
            purpose="Index user-provided local corpus files and expose matching primary-text candidates before web discovery.",
            expected_artifacts=[f"{base}/corpus_index.json"],
        ),
        WorkerPlanStep(
            step_id="source_discovery",
            worker="Lexmechanic",
            purpose="Discover sources and classify reliability, language, and direct-event usefulness.",
            depends_on=["corpus_ingestion"],
            expected_artifacts=[f"{base}/source_map.json"],
        ),
        WorkerPlanStep(
            step_id="source_acquisition",
            worker="AuspexBrowser",
            purpose="Fetch accessible public source URLs and record blocked or binary sources as coverage data.",
            depends_on=["source_discovery"],
            expected_artifacts=[f"{base}/source_snapshots.json"],
        ),
        WorkerPlanStep(
            step_id="source_rendering",
            worker="OcularisRenderium",
            purpose="Render JavaScript-required source snapshots and record DOM text or render blockers.",
            depends_on=["source_acquisition"],
            expected_artifacts=[f"{base}/rendered_snapshots.json"],
        ),
        WorkerPlanStep(
            step_id="fact_extraction",
            worker="NoosphericExtractor",
            purpose="Extract direct event facts with confidence labels and source references.",
            depends_on=["source_rendering"],
            expected_artifacts=[f"{base}/direct_event_notes.json"],
        ),
        WorkerPlanStep(
            step_id="timeline",
            worker="Chronologis",
            purpose="Build chronological event order and mark contradictions or missing links.",
            depends_on=["fact_extraction"],
            expected_artifacts=[f"{base}/timeline.json"],
        ),
        WorkerPlanStep(
            step_id="draft_reconstruction",
            worker="ScriptoriumDaemon",
            purpose="Write a Russian reconstruction from extracted facts without inventing unsupported details.",
            depends_on=["source_discovery", "fact_extraction", "timeline"],
            expected_artifacts=[f"{base}/reconstruction_ru.md", f"{base}/coverage_report.md"],
        ),
        WorkerPlanStep(
            step_id="critic_review",
            worker="ReductorVerifier",
            purpose="Review the draft against the contract, source coverage, and hallucination risks.",
            depends_on=["draft_reconstruction"],
            expected_artifacts=[f"{base}/critic_report.json"],
        ),
        WorkerPlanStep(
            step_id="finalize",
            worker="FabricatorFinalis",
            purpose="Package final artifacts only after critic approval or explicit blockers.",
            depends_on=["critic_review"],
            expected_artifacts=[f"{base}/final_manifest.json"],
        ),
    ]


def artifacts_from_plan(plan: list[WorkerPlanStep]) -> list[str]:
    artifacts: list[str] = []
    for step in plan:
        for artifact in step.expected_artifacts:
            if artifact not in artifacts:
                artifacts.append(artifact)
    return artifacts


def research_writing_worker_plan(slug: str, intent_profile: dict[str, Any] | None = None) -> list[WorkerPlanStep]:
    base = f"/work/{slug}"
    profile = intent_profile or classify_research_intent(slug)
    needs_structure = profile.get("intent") != "qa_answer"
    plan = [
        WorkerPlanStep(
            step_id="corpus_ingestion",
            worker="CorpusIngestor",
            purpose="Index user-provided local documents and expose relevant primary or reference-text candidates before web discovery.",
            expected_artifacts=[f"{base}/corpus_index.json"],
        ),
        WorkerPlanStep(
            step_id="source_discovery",
            worker="Lexmechanic",
            purpose="Discover and classify sources for the requested research/writing task by reliability, language, and usefulness.",
            depends_on=["corpus_ingestion"],
            expected_artifacts=[f"{base}/source_map.json"],
        ),
        WorkerPlanStep(
            step_id="source_acquisition",
            worker="AuspexBrowser",
            purpose="Fetch accessible public source URLs and record blocked, binary, or unavailable sources as coverage data.",
            depends_on=["source_discovery"],
            expected_artifacts=[f"{base}/source_snapshots.json"],
        ),
        WorkerPlanStep(
            step_id="source_rendering",
            worker="OcularisRenderium",
            purpose="Render JavaScript-required source snapshots and record DOM text or render blockers.",
            depends_on=["source_acquisition"],
            expected_artifacts=[f"{base}/rendered_snapshots.json"],
        ),
        WorkerPlanStep(
            step_id="fact_extraction",
            worker="NoosphericExtractor",
            purpose="Extract direct claims, events, arguments, or evidence notes with confidence labels and source references.",
            depends_on=["source_rendering"],
            expected_artifacts=[f"{base}/direct_event_notes.json", f"{base}/research_corpus.json"],
        ),
    ]
    draft_dependencies = ["source_discovery", "fact_extraction"]
    if needs_structure:
        structure_artifacts = [f"{base}/structure_map.json"]
        if profile.get("needs_timeline"):
            structure_artifacts = [f"{base}/timeline.json", f"{base}/structure_map.json"]
        plan.append(
            WorkerPlanStep(
                step_id="structure_mapping",
                worker="Chronologis",
                purpose="Build timeline for event tasks or source order, argument flow, and topic structure for analytical synthesis.",
                depends_on=["fact_extraction"],
                expected_artifacts=structure_artifacts,
            )
        )
        draft_dependencies.append("structure_mapping")
    synthesis_artifacts = [f"{base}/synthesis_plan.json"]
    if profile.get("needs_chapters"):
        synthesis_artifacts.extend([f"{base}/book_outline.json", f"{base}/chapter_plan.json"])
    plan.append(
        WorkerPlanStep(
            step_id="synthesis_planning",
            worker="ScriptoriumArchitect",
            purpose="Plan the requested output structure, style, length, source requirements, evidence trace, and unsupported sections before writing.",
            depends_on=["fact_extraction"] + (["structure_mapping"] if needs_structure else []),
            expected_artifacts=synthesis_artifacts,
        )
    )
    draft_dependencies.append("synthesis_planning")
    draft_artifacts = [f"{base}/reconstruction_ru.md", f"{base}/coverage_report.md"]
    if profile.get("needs_chapters"):
        draft_artifacts.extend(
            [
                f"{base}/chapters/chapter_01.md",
                f"{base}/chapters/chapter_02.md",
                f"{base}/chapters/chapter_03.md",
                f"{base}/continuity_report.json",
                f"{base}/editor_report.json",
                f"{base}/manuscript_ru.md",
                f"{base}/manuscript.fb2",
            ]
        )
    plan.extend(
        [
            WorkerPlanStep(
                step_id="draft_reconstruction",
                worker="ScriptoriumDaemon",
                purpose="Write the requested Russian output from research_corpus, synthesis_plan, output_mode, and evidence trace without unsupported sections.",
                depends_on=draft_dependencies,
                expected_artifacts=draft_artifacts,
            ),
            WorkerPlanStep(
                step_id="critic_review",
                worker="ReductorVerifier",
                purpose="Review the draft against the user task, source coverage, extracted evidence, and hallucination risks.",
                depends_on=["draft_reconstruction"],
                expected_artifacts=[f"{base}/critic_report.json"],
            ),
            WorkerPlanStep(
                step_id="finalize",
                worker="FabricatorFinalis",
                purpose="Package final artifacts only after critic approval or explicit blockers.",
                depends_on=["critic_review"],
                expected_artifacts=[f"{base}/final_manifest.json"],
            ),
        ]
    )
    return plan


def research_writing_required_artifacts(slug: str, intent_profile: dict[str, Any] | None = None) -> list[str]:
    return artifacts_from_plan(research_writing_worker_plan(slug, intent_profile=intent_profile))


def build_research_writing_contract(user_task: str, task_id: str | None = None) -> TaskContract:
    slug = slugify(user_task, fallback="research")
    resolved_task_id = task_id or f"iskandar-{slug}-research-writing"
    intent_profile = classify_research_intent(user_task)
    return TaskContract(
        task_id=resolved_task_id,
        kind="research",
        goal=user_task.strip(),
        assigned_governor="IskandarKhayon",
        non_goals=[
            "Do not answer from a single convenient source when the task asks for broad research.",
            "Do not hide weak source coverage, inaccessible primary texts, or uncertain claims.",
            "Do not let the writer invent facts absent from extraction outputs.",
            "Do not treat a short summary as complete when the task asks for full coverage.",
        ],
        required_artifacts=research_writing_required_artifacts(slug, intent_profile=intent_profile),
        completion_criteria=[
            "All required artifacts exist and are structurally valid.",
            "Source coverage separates primary, official, secondary, community, unavailable, and uncertain sources where applicable.",
            "Extracted notes separate direct evidence from interpretation and synthesis.",
            "Research corpus captures sources, snapshots, claims, events, arguments, confidence, and gaps.",
            "The draft addresses the user's requested form and language while preserving source limitations.",
            "Critic report passes or lists explicit blockers and required revisions.",
        ],
        quality_gates=[
            f"intent:{intent_profile['intent']}",
            f"output_mode:{intent_profile['output_mode']}",
            "source_map_created",
            "research_corpus_created",
            "claims_or_events_non_empty",
            "source_order_or_timeline_present" if intent_profile.get("intent") != "qa_answer" else "short_answer_evidence_present",
            "writer_uses_only_extracted_evidence",
            "coverage_report_names_gaps",
            "critic_review_passed_or_blocked",
        ],
        worker_plan=research_writing_worker_plan(slug, intent_profile=intent_profile),
    )


def build_lore_reconstruction_contract(user_task: str, task_id: str | None = None) -> TaskContract:
    slug = slugify(user_task)
    resolved_task_id = task_id or f"iskandar-{slug}-lore-reconstruction"
    return TaskContract(
        task_id=resolved_task_id,
        kind="research",
        goal=user_task.strip(),
        assigned_governor="IskandarKhayon",
        non_goals=[
            "Do not deliver a shallow wiki summary when the task asks for full event coverage.",
            "Do not hide weak source coverage or inaccessible primary sources.",
            "Do not let the writer invent facts absent from extraction outputs.",
        ],
        required_artifacts=lore_required_artifacts(slug),
        completion_criteria=[
            "All required artifacts exist and are structurally valid.",
            "Source coverage separates official, wiki, community, unavailable, and uncertain sources.",
            "Direct events are separated from aftermath, interpretation, and reconstruction.",
            "Critic report passes or lists explicit blockers and required revisions.",
        ],
        quality_gates=[
            "source_map_created",
            "direct_event_notes_non_empty",
            "timeline_orders_direct_events",
            "writer_uses_only_extracted_facts",
            "coverage_report_names_gaps",
            "critic_review_passed_or_blocked",
        ],
        worker_plan=lore_worker_plan(slug),
    )


def code_required_artifacts(slug: str) -> list[str]:
    base = f"/work/{slug}"
    return [
        f"{base}/repo_survey.json",
        f"{base}/change_plan.md",
        f"{base}/patch_manifest.json",
        f"{base}/verification_report.json",
        f"{base}/repair_loop_state.json",
        f"{base}/code_review.json",
        f"{base}/final_manifest.json",
    ]


def code_worker_plan(slug: str) -> list[WorkerPlanStep]:
    base = f"/work/{slug}"
    return [
        WorkerPlanStep(
            step_id="repository_survey",
            worker="LogisRepository",
            purpose="Inspect repository shape, likely ownership boundaries, risky files, and available test surfaces.",
            expected_artifacts=[f"{base}/repo_survey.json"],
        ),
        WorkerPlanStep(
            step_id="change_planning",
            worker="MagosStrategos",
            purpose="Turn the user task and repository survey into a scoped implementation plan.",
            depends_on=["repository_survey"],
            expected_artifacts=[f"{base}/change_plan.md"],
        ),
        WorkerPlanStep(
            step_id="implementation",
            worker="FerrumPatchwright",
            purpose="Prepare an auditable patch manifest and implementation handoff for code changes.",
            depends_on=["change_planning"],
            expected_artifacts=[f"{base}/patch_manifest.json"],
        ),
        WorkerPlanStep(
            step_id="verification",
            worker="OrdinatusVerifier",
            purpose="Define and record the verification commands or blockers for the implementation.",
            depends_on=["implementation"],
            expected_artifacts=[f"{base}/verification_report.json", f"{base}/repair_loop_state.json"],
        ),
        WorkerPlanStep(
            step_id="code_review",
            worker="JudicatorCodicis",
            purpose="Review the implementation package for scope, test evidence, and unsafe assumptions.",
            depends_on=["verification"],
            expected_artifacts=[f"{base}/code_review.json"],
        ),
        WorkerPlanStep(
            step_id="finalize",
            worker="SealwrightFinalis",
            purpose="Package the coding task result, blockers, changed-file intent, and next actions.",
            depends_on=["code_review"],
            expected_artifacts=[f"{base}/final_manifest.json"],
        ),
    ]


def build_code_task_contract(user_task: str, task_id: str | None = None) -> TaskContract:
    slug = slugify(user_task)
    resolved_task_id = task_id or f"ceraxia-{slug}-code-task"
    return TaskContract(
        task_id=resolved_task_id,
        kind="code",
        goal=user_task.strip(),
        assigned_governor="Ceraxia",
        non_goals=[
            "Do not make broad unrelated refactors while solving a narrow code task.",
            "Do not hide missing test evidence or unsafe assumptions.",
            "Do not overwrite user changes without an explicit reviewable handoff.",
        ],
        required_artifacts=code_required_artifacts(slug),
        completion_criteria=[
            "Repository survey, change plan, patch manifest, verification report, repair loop state, review, and final manifest exist.",
            "The plan names files or modules likely to be touched and records test commands or blockers.",
            "The review records whether the implementation package is ready, blocked, or needs a stronger worker.",
            "Final manifest exposes the next safe action for Warmaster or a human/code agent.",
        ],
        quality_gates=[
            "repo_survey_created",
            "change_plan_scoped",
            "patch_manifest_auditable",
            "verification_report_present",
            "repair_loop_state_present",
            "code_review_passed_or_blocked",
            "final_manifest_created",
        ],
        worker_plan=code_worker_plan(slug),
    )


def validate_task_contract_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = sorted(TASK_CONTRACT_REQUIRED_FIELDS - set(payload))
    if missing:
        errors.append(f"missing required fields: {missing}")
    extra = sorted(set(payload) - TASK_CONTRACT_FIELDS)
    if extra:
        errors.append(f"unknown fields: {extra}")
    if payload.get("version") != 1:
        errors.append("version must be 1")
    if not isinstance(payload.get("task_id"), str) or not payload.get("task_id"):
        errors.append("task_id must be a non-empty string")
    if payload.get("kind") not in TASK_KINDS:
        errors.append(f"kind must be one of {sorted(TASK_KINDS)}")
    for field_name in ("goal", "assigned_governor"):
        if not isinstance(payload.get(field_name), str) or not payload.get(field_name):
            errors.append(f"{field_name} must be a non-empty string")
    for field_name in ("non_goals", "required_artifacts", "completion_criteria", "quality_gates", "worker_plan"):
        if field_name in payload and not isinstance(payload[field_name], list):
            errors.append(f"{field_name} must be a list")
    for field_name in ("non_goals", "completion_criteria", "quality_gates"):
        values = payload.get(field_name, [])
        if isinstance(values, list):
            for index, item in enumerate(values):
                if not isinstance(item, str) or not item:
                    errors.append(f"{field_name}[{index}] must be a non-empty string")
    if not payload.get("completion_criteria"):
        errors.append("completion_criteria must not be empty")
    required_artifacts = payload.get("required_artifacts", [])
    if isinstance(required_artifacts, list):
        seen_required_artifacts: set[str] = set()
        for artifact in required_artifacts:
            if not isinstance(artifact, str) or not artifact.startswith("/work/"):
                errors.append(f"required artifact must be a /work path: {artifact!r}")
                continue
            if artifact in seen_required_artifacts:
                errors.append(f"duplicate required artifact: {artifact}")
            seen_required_artifacts.add(artifact)
    worker_plan = payload.get("worker_plan", [])
    if not isinstance(worker_plan, list) or not worker_plan:
        errors.append("worker_plan must be a non-empty list")
        return errors
    seen_steps: set[str] = set()
    expected_artifact_producers: dict[str, str] = {}
    for index, step in enumerate(worker_plan):
        if not isinstance(step, dict):
            errors.append(f"worker_plan[{index}] must be an object")
            continue
        missing_step_fields = sorted(WORKER_STEP_REQUIRED_FIELDS - set(step))
        if missing_step_fields:
            errors.append(f"worker_plan[{index}] missing required fields: {missing_step_fields}")
        extra_step_fields = sorted(set(step) - WORKER_STEP_FIELDS)
        if extra_step_fields:
            errors.append(f"worker_plan[{index}] unknown fields: {extra_step_fields}")
        step_id = step.get("step_id")
        if not isinstance(step_id, str) or not step_id:
            errors.append(f"worker_plan[{index}].step_id must be a non-empty string")
            continue
        if step_id in seen_steps:
            errors.append(f"duplicate worker step_id: {step_id}")
        seen_steps.add(step_id)
        for field_name in ("worker", "purpose"):
            if not isinstance(step.get(field_name), str) or not step.get(field_name):
                errors.append(f"worker_plan[{index}].{field_name} must be a non-empty string")
        depends_on = step.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(isinstance(item, str) for item in depends_on):
            errors.append(f"worker_plan[{index}].depends_on must be a list of strings")
        elif len(set(depends_on)) != len(depends_on):
            errors.append(f"worker_plan[{index}].depends_on contains duplicates")
        expected_artifacts = step.get("expected_artifacts", [])
        if not isinstance(expected_artifacts, list):
            errors.append(f"worker_plan[{index}].expected_artifacts must be a list")
        elif any(not isinstance(item, str) or not item.startswith("/work/") for item in expected_artifacts):
            errors.append(f"worker_plan[{index}].expected_artifacts must contain /work paths")
        elif len(set(expected_artifacts)) != len(expected_artifacts):
            errors.append(f"worker_plan[{index}].expected_artifacts contains duplicates")
        elif isinstance(step_id, str):
            for artifact in expected_artifacts:
                owner = expected_artifact_producers.get(artifact)
                if owner and owner != step_id:
                    errors.append(f"expected artifact has multiple producer steps: {artifact}")
                expected_artifact_producers[artifact] = step_id
    for index, step in enumerate(worker_plan):
        if not isinstance(step, dict):
            continue
        depends_on = step.get("depends_on", [])
        if isinstance(depends_on, list):
            for dependency in depends_on:
                if isinstance(dependency, str) and dependency not in seen_steps:
                    errors.append(f"worker_plan[{index}] depends on unknown step: {dependency}")
    if isinstance(required_artifacts, list):
        for artifact in required_artifacts:
            if isinstance(artifact, str) and artifact.startswith("/work/") and artifact not in expected_artifact_producers:
                errors.append(f"required artifact is not produced by worker_plan: {artifact}")
    return errors
