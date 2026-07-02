#!/usr/bin/env python3
from __future__ import annotations

from eye_of_terror.routing import route_message


def main() -> int:
    lore = route_message("Собери события Скалатракса")
    if not lore.ok or lore.governor != "IskandarKhayon":
        raise AssertionError(lore)
    code = route_message("почини баг в python приложении")
    if not code.ok or code.kind != "code" or code.governor != "Ceraxia":
        raise AssertionError(code)
    code_with_research_word = route_message("кодовая задача: python тесты падают, источник ошибки не указан, почини приложение")
    if not code_with_research_word.ok or code_with_research_word.kind != "code" or code_with_research_word.governor != "Ceraxia":
        raise AssertionError(code_with_research_word)
    image = route_message("сделай рисовалку stable diffusion")
    if image.ok or image.kind != "image_generation" or image.governor != "ForgeMasterGovernor":
        raise AssertionError(image)
    unknown = route_message("сделай что-нибудь")
    if unknown.ok or unknown.kind != "general":
        raise AssertionError(unknown)
    # Incidental infixes must not match: "source" inside "resource", "app" inside
    # "happiness", "test" inside "latest", "repo" inside "report".
    for phrase in ("resource planning meeting", "happiness metrics dashboard", "latest quarterly report"):
        incidental = route_message(phrase)
        if incidental.ok or incidental.governor:
            raise AssertionError(f"incidental infix routed: {phrase} -> {incidental}")
    # Word-initial stems must still match (Russian morphology).
    if not route_message("кодовая задача").ok:
        raise AssertionError("word-initial stem 'код' should match 'кодовая'")
    # A stronger match on an inactive governor must fall back to an active one
    # rather than dead-ending: this hits image (planned) and code (active) terms.
    mixed = route_message("нарисуй картинку и почини python код в репозитории")
    if not mixed.ok or mixed.governor != "Ceraxia":
        raise AssertionError(f"inactive-governor tie should fall back to active: {mixed}")
    print("[ok] Warmaster routing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
