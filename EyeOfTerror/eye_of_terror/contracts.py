from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def slugify(value: str, fallback: str = "task") -> str:
    lowered = value.lower()
    replacements = {
        "skalathrax": "skalathrax",
        "scalathrax": "skalathrax",
        "скалатрак": "skalathrax",
        "кхарн": "kharn",
        "kharn": "kharn",
    }
    for needle, slug in replacements.items():
        if needle in lowered:
            return slug
    words = re.findall(r"[a-zA-Z0-9]+", lowered)
    return "-".join(words[:6]) or fallback


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
        f"{base}/source_map.json",
        f"{base}/source_snapshots.json",
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
            step_id="source_discovery",
            worker="Lexmechanic",
            purpose="Discover sources and classify reliability, language, and direct-event usefulness.",
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
            step_id="fact_extraction",
            worker="NoosphericExtractor",
            purpose="Extract direct event facts with confidence labels and source references.",
            depends_on=["source_acquisition"],
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
