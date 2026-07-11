#!/usr/bin/env python3
"""Качает короткие скрипы/стоны/шорохи из архива BBC Sound Effects (личное использование)."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "sfx" / "raw_bbc"
API = "https://sound-effects-api.bbcrewind.co.uk/api/sfx/search"
MEDIA = "https://sound-effects-media.bbcrewind.co.uk/mp3/{id}.mp3"

QUERIES = {
    "creak_wood": "wood creak",
    "creak_door": "door creak slow",
    "creak_metal": "metal groan",
    "creak_ice": "ice creaking",
    "creak_ship": "ship timber creak",
    "rattle": "chain rattle short",
}
PER_QUERY = 4
MAX_MS = 9000
MIN_MS = 700


def search(query: str) -> list[dict]:
    body = json.dumps({"criteria": {"from": 0, "size": 20, "query": query}}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)["results"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    picked = []
    for tag, query in QUERIES.items():
        results = [r for r in search(query) if MIN_MS <= int(r.get("duration", 0)) <= MAX_MS]
        for item in results[:PER_QUERY]:
            sfx_id = item["id"]
            dest = OUT / f"{tag}_{sfx_id}.mp3"
            if not dest.exists():
                urllib.request.urlretrieve(MEDIA.format(id=sfx_id), dest)
            picked.append(
                {"file": dest.name, "tag": tag, "ms": item["duration"], "desc": item["description"]}
            )
            print(f"{tag}: {sfx_id} {item['duration']}ms {item['description'][:60]}", flush=True)
    (OUT / "picked.json").write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
