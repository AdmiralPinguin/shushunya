#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.Pictorium.Brigades.Comics.Workers.CharacterSheetwright.worker import build_character_sheet
from EyeOfTerror.Pictorium.Brigades.Comics.Workers.LayoutFinalis.worker import build_layout_manifest
from EyeOfTerror.Pictorium.Brigades.Comics.Workers.Panelwright.worker import build_panel_packages
from EyeOfTerror.Pictorium.Brigades.Comics.Workers.ScenarioScribe.worker import build_scenario
from EyeOfTerror.Pictorium.Brigades.Comics.Workers.StoryboardArchitect.worker import build_storyboard


def assert_execution_packet(payload: dict[str, object], worker: str) -> None:
    packet = payload.get("execution_packet") if isinstance(payload.get("execution_packet"), dict) else {}
    if packet.get("kind") != "pictorium_worker_execution_packet" or packet.get("worker") != worker:
        raise AssertionError(f"{worker} did not return execution_packet: {payload}")


def assert_revision_packet(payload: dict[str, object], worker: str) -> None:
    packet = payload.get("revision_packet") if isinstance(payload.get("revision_packet"), dict) else {}
    if packet.get("kind") != "pictorium_revision_packet" or packet.get("source_worker") != worker:
        raise AssertionError(f"{worker} did not return revision_packet: {payload}")


def main() -> int:
    task = "сделай комикс 4 панели про техножреца который запускает древнюю кузню"
    scenario = build_scenario({"request": task})
    if not scenario.get("ok") or scenario.get("scenario", {}).get("panel_count") != 4:
        raise AssertionError(f"ScenarioScribe failed: {scenario}")
    assert_execution_packet(scenario, "ScenarioScribe")
    storyboard = build_storyboard({"scenario": scenario["scenario"]})
    if not storyboard.get("ok") or len(storyboard.get("storyboard", {}).get("panels", [])) != 4:
        raise AssertionError(f"StoryboardArchitect failed: {storyboard}")
    assert_execution_packet(storyboard, "StoryboardArchitect")
    character_sheet = build_character_sheet({"scenario": scenario["scenario"]})
    if not character_sheet.get("ok") or "Promptwright" not in character_sheet.get("image_brigade_used", []):
        raise AssertionError(f"CharacterSheetwright failed: {character_sheet}")
    assert_execution_packet(character_sheet, "CharacterSheetwright")
    assert_revision_packet(character_sheet, "CharacterSheetwright")
    with tempfile.TemporaryDirectory(prefix="pictorium-comics-self-test-") as tmp:
        panels = build_panel_packages(
            {
                "storyboard": storyboard["storyboard"],
                "character_sheet": character_sheet["character_sheet"],
                "db_path": str(Path(tmp) / "forge.sqlite3"),
                "submit": False,
            }
        )
        if (
            not panels.get("ok")
            or len(panels.get("panels", [])) != 4
            or panels.get("image_brigade_used") != ["Promptwright", "ModelQuartermaster", "ForgeDispatcher"]
        ):
            raise AssertionError(f"Panelwright failed: {panels}")
        assert_execution_packet(panels, "Panelwright")
        assert_revision_packet(panels, "Panelwright")
        final = build_layout_manifest(
            {
                "scenario": scenario["scenario"],
                "storyboard": storyboard["storyboard"],
                "character_sheet": character_sheet,
                "panels": panels,
            }
        )
    if not final.get("ok") or final.get("final_manifest", {}).get("status") != "ready":
        raise AssertionError(f"LayoutFinalis failed: {final}")
    assert_execution_packet(final, "LayoutFinalis")
    assert_revision_packet(final, "LayoutFinalis")
    if not final.get("final_manifest", {}).get("handoff", {}).get("uses_image_brigade_execution_layer"):
        raise AssertionError(f"Comics final manifest did not preserve Image Brigade evidence: {final}")
    print("[ok] Pictorium Comics Brigade workers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
