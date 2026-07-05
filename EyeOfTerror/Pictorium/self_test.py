#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PICTORIUM = PROJECT_ROOT / "EyeOfTerror" / "Pictorium"
CONTRACT = PICTORIUM / "Moriana" / "contracts" / "moriana_department.json"
EXPECTED_WORKERS = {
    "Promptwright",
    "ModelQuartermaster",
    "ForgeDispatcher",
    "ImageVerifier",
    "ArtifactFinalis",
}


def main() -> int:
    if not CONTRACT.exists():
        raise AssertionError(f"missing Moriana contract: {CONTRACT}")
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if payload.get("department") != "Pictorium":
        raise AssertionError(f"unexpected department: {payload}")
    governor = payload.get("governor") if isinstance(payload.get("governor"), dict) else {}
    if governor.get("name") != "Moriana" or governor.get("status") != "planned":
        raise AssertionError(f"Moriana must stay planned until service activation: {governor}")
    if governor.get("planned_port") != 7103:
        raise AssertionError(f"Moriana should inherit planned image governor port 7103: {governor}")
    workers = payload.get("workers") if isinstance(payload.get("workers"), list) else []
    names = {str(item.get("name") or "") for item in workers if isinstance(item, dict)}
    if names != EXPECTED_WORKERS:
        raise AssertionError(f"unexpected worker set: {names}")
    for name in EXPECTED_WORKERS:
        readme = PICTORIUM / "Brigade" / name / "README.md"
        if not readme.exists():
            raise AssertionError(f"missing worker README: {readme}")
    for worker in workers:
        for raw_path in worker.get("source_modules", []) if isinstance(worker, dict) else []:
            source = PROJECT_ROOT / str(raw_path)
            if not source.exists():
                raise AssertionError(f"mapped DemonsForge source does not exist: {source}")
    print("[ok] Pictorium Moriana scaffold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
