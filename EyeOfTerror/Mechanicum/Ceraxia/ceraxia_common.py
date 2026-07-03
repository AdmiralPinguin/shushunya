"""Shared roots, sys.path setup, and domain constants for the Ceraxia engineering
orchestrator. Importing this module wires the PlanningBrigade and CodeBrigade
paths so the split ceraxia_* modules can import their sibling contracts."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

CERAXIA_ROOT = Path(__file__).resolve().parent
MECHANICUM_ROOT = CERAXIA_ROOT.parent
EYE_ROOT = MECHANICUM_ROOT.parent
PROJECT_ROOT = EYE_ROOT.parent
RUNS_ROOT = CERAXIA_ROOT / "runs"

PLANNING_PATH = str(MECHANICUM_ROOT / "PlanningBrigade")
if PLANNING_PATH not in sys.path:
    sys.path.insert(0, PLANNING_PATH)
CODE_BRIGADE_PATH = str(MECHANICUM_ROOT / "CodeBrigade")
if CODE_BRIGADE_PATH not in sys.path:
    sys.path.insert(0, CODE_BRIGADE_PATH)

CONTRACT_VERSION = "eye-mechanicum.v1"
EXECUTION_MODES = {"dry_run", "guarded_patch", "repo_engineer", "review_only", "project_creation"}
DIAGNOSTIC_REPAIR_MAX_ATTEMPTS = 3


LIFECYCLE = [
    "received",
    "planned",
    "surveyed",
    "implementation_ready",
    "implemented",
    "verified",
    "reviewed",
    "finalized",
]

REQUIRED_RUN_ARTIFACTS = [
    "task.json",
    "planning_packet.json",
    "repo_survey.json",
    "planning_department.json",
    "implementation_brief.json",
    "worker_report.json",
    "verification_report.json",
    "review_gate.json",
    "diagnostic_repair_request.json",
    "planning_feedback_request.json",
    "status.json",
    "final_report.md",
    "execution_readiness.json",
    "run_summary.json",
    "evidence_matrix.json",
    "engineering_memory_update.json",
]


@dataclass(frozen=True)
class CeraxiaInput:
    task: str
    repo_path: str
    execution_mode: str = ""
    dry_run: bool = True
    execute_verification: bool = False
    execute_diagnostic_repair: bool = False
    constraints: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()
    runs_root: Path = RUNS_ROOT
