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
TASK_KINDS = {"chat", "research", "image_generation", "image_series_generation", "comic_generation", "code", "general"}
WORKER_STEP_FIELDS = {"step_id", "worker", "purpose", "depends_on", "expected_artifacts"}
WORKER_STEP_REQUIRED_FIELDS = {"step_id", "worker", "purpose"}


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


def artifacts_from_plan(plan: list[WorkerPlanStep]) -> list[str]:
    artifacts: list[str] = []
    for step in plan:
        for artifact in step.expected_artifacts:
            if artifact not in artifacts:
                artifacts.append(artifact)
    return artifacts


def image_required_artifacts(slug: str) -> list[str]:
    base = f"/work/{slug}"
    return [
        f"{base}/image_plan.json",
        f"{base}/resource_report.json",
        f"{base}/forge_jobs.json",
        f"{base}/image_verification.json",
        f"{base}/final_manifest.json",
    ]


def image_worker_plan(slug: str) -> list[WorkerPlanStep]:
    base = f"/work/{slug}"
    return [
        WorkerPlanStep(
            step_id="image_planning",
            worker="Promptwright",
            purpose="Turn the user visual request into a normalized Forge job or visual project plan.",
            expected_artifacts=[f"{base}/image_plan.json"],
        ),
        WorkerPlanStep(
            step_id="resource_readiness",
            worker="ModelQuartermaster",
            purpose="Inspect local models, LoRAs, embeddings, and asset approvals required by the image plan.",
            depends_on=["image_planning"],
            expected_artifacts=[f"{base}/resource_report.json"],
        ),
        WorkerPlanStep(
            step_id="forge_dispatch",
            worker="ForgeDispatcher",
            purpose="Validate the Forge job, surface runtime blockers, and submit a queued job when requested.",
            depends_on=["image_planning", "resource_readiness"],
            expected_artifacts=[f"{base}/forge_jobs.json"],
        ),
        WorkerPlanStep(
            step_id="image_verification",
            worker="ImageVerifier",
            purpose="Verify generated artifacts with deterministic image, metadata, and dimension checks.",
            depends_on=["forge_dispatch"],
            expected_artifacts=[f"{base}/image_verification.json"],
        ),
        WorkerPlanStep(
            step_id="finalize",
            worker="ArtifactFinalis",
            purpose="Package the final image manifest, blockers, generated artifacts, and delivery handoff.",
            depends_on=["image_verification"],
            expected_artifacts=[f"{base}/final_manifest.json"],
        ),
    ]


def build_image_generation_contract(user_task: str, task_id: str | None = None) -> TaskContract:
    slug = slugify(user_task, fallback="image")
    resolved_task_id = task_id or f"moriana-{slug}-image"
    plan = image_worker_plan(slug)
    kind = "image_series_generation" if is_image_series_request(user_task) else "image_generation"
    return TaskContract(
        task_id=resolved_task_id,
        kind=kind,
        goal=user_task.strip(),
        assigned_governor="Moriana",
        non_goals=[
            "Do not call DemonsForge directly from Warmaster; route through Moriana and Image Brigade.",
            "Do not treat a queued job as a delivered artifact before verification and final manifest.",
            "Do not auto-download external assets without explicit approval and source validation.",
            "Do not hide model, LoRA, VRAM, runtime, or artifact blockers.",
        ],
        required_artifacts=artifacts_from_plan(plan),
        completion_criteria=[
            "Promptwright produced a normalized image plan.",
            "ModelQuartermaster reported resource readiness or explicit blockers.",
            "ForgeDispatcher validated the job and recorded dry-run or queued submission evidence.",
            "ImageVerifier checked generated artifacts or recorded that generation is still pending.",
            "ArtifactFinalis produced a final manifest with artifacts, blockers, and handoff status.",
        ],
        quality_gates=[
            "image_plan_created",
            "resource_readiness_checked",
            "forge_validation_or_structured_blocker",
            "artifact_verification_or_pending_generation_recorded",
            "final_manifest_created",
        ],
        worker_plan=plan,
    )


def is_image_series_request(user_task: str) -> bool:
    lowered = user_task.lower()
    if any(term in lowered for term in ("серия", "серию", "серии", "набор картинок", "несколько картинок", "image series", "series of images", "batch of images")):
        return True
    return bool(re.search(r"\b\d{1,2}\s*(?:картин\w*|изображен\w*|images|pictures)\b", lowered))


def comics_required_artifacts(slug: str) -> list[str]:
    return artifacts_from_plan(comics_worker_plan(slug))


def comics_worker_plan(slug: str) -> list[WorkerPlanStep]:
    base = f"/work/{slug}"
    return [
        WorkerPlanStep(
            step_id="scenario",
            worker="ScenarioScribe",
            purpose="Turn the user request into a compact comic scenario with premise, cast, visual style, and beats.",
            expected_artifacts=[f"{base}/scenario.json"],
        ),
        WorkerPlanStep(
            step_id="storyboard",
            worker="StoryboardArchitect",
            purpose="Convert scenario beats into ordered panels with camera, composition, continuity, and panel text constraints.",
            depends_on=["scenario"],
            expected_artifacts=[f"{base}/storyboard.json"],
        ),
        WorkerPlanStep(
            step_id="character_sheet",
            worker="CharacterSheetwright",
            purpose="Prepare character-sheet image plans through Image Brigade so panel continuity has a reusable visual reference.",
            depends_on=["scenario"],
            expected_artifacts=[f"{base}/character_sheet.json"],
        ),
        WorkerPlanStep(
            step_id="panel_generation",
            worker="Panelwright",
            purpose="Prepare per-panel Image Brigade run packages and Forge dry-runs without duplicating Forge runtime logic.",
            depends_on=["storyboard", "character_sheet"],
            expected_artifacts=[f"{base}/panels.json", f"{base}/panel_forge_jobs.json"],
        ),
        WorkerPlanStep(
            step_id="layout_manifest",
            worker="LayoutFinalis",
            purpose="Assemble page layout, panel dependencies, generated or pending artifacts, blockers, and final comic manifest.",
            depends_on=["panel_generation"],
            expected_artifacts=[f"{base}/layout.json", f"{base}/final_manifest.json"],
        ),
    ]


def build_comics_generation_contract(user_task: str, task_id: str | None = None) -> TaskContract:
    slug = slugify(user_task, fallback="comic")
    resolved_task_id = task_id or f"moriana-{slug}-comic"
    plan = comics_worker_plan(slug)
    return TaskContract(
        task_id=resolved_task_id,
        kind="comic_generation",
        goal=user_task.strip(),
        assigned_governor="Moriana",
        non_goals=[
            "Do not bypass Image Brigade for panel image planning, resource checks, dispatch, or verification.",
            "Do not treat a storyboard as generated panels.",
            "Do not hide continuity, lettering, page-layout, or missing-artifact blockers.",
            "Do not auto-download external character/style assets without explicit approval.",
        ],
        required_artifacts=artifacts_from_plan(plan),
        completion_criteria=[
            "ScenarioScribe produced a scenario with cast, style, and ordered beats.",
            "StoryboardArchitect produced ordered panel plans with continuity notes.",
            "CharacterSheetwright produced Image Brigade character-sheet planning evidence.",
            "Panelwright produced per-panel Image Brigade plans and Forge validation evidence.",
            "LayoutFinalis produced layout and final manifest with blockers and delivery status.",
        ],
        quality_gates=[
            "scenario_created",
            "storyboard_created",
            "character_sheet_image_plan_created",
            "panel_image_plans_created",
            "layout_and_final_manifest_created",
            "image_brigade_execution_layer_used",
        ],
        worker_plan=plan,
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
