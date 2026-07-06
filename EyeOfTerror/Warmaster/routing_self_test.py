#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eye_of_terror.routing import route_message


def assert_live_route(message: str, governor: str, kind: str) -> None:
    route = route_message(message)
    payload = route.to_dict()
    model_brain = payload.get("model_brain") if isinstance(payload.get("model_brain"), dict) else {}
    if model_brain.get("owner") != "WarmasterRouter" or model_brain.get("status") != "answered":
        raise AssertionError(f"route did not use live WarmasterRouter model brain: {payload}")
    if not route.ok or route.governor != governor or route.kind != kind:
        raise AssertionError(payload)


def main() -> int:
    assert_live_route(
        "Собери полную реконструкцию событий Скалатракса по книгам, кодексам и источникам лора",
        "IskandarKhayon",
        "research",
    )
    assert_live_route(
        "Создай новый python CLI проект с тестами и документацией",
        "Ceraxia",
        "code",
    )
    moriana = route_message("Сделай серию изображений через Stable Diffusion про кузню тёмного механикума")
    payload = moriana.to_dict()
    model_brain = payload.get("model_brain") if isinstance(payload.get("model_brain"), dict) else {}
    if model_brain.get("owner") != "WarmasterRouter" or model_brain.get("status") != "answered":
        raise AssertionError(f"Moriana route did not use live model brain: {payload}")
    if not moriana.ok or moriana.governor != "Moriana" or moriana.kind not in {"image_generation", "image_series_generation"}:
        raise AssertionError(payload)
    print("[ok] Warmaster live LLM routing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
