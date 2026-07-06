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
    if not image.ok or image.kind != "image_generation" or image.governor != "Moriana":
        raise AssertionError(image)
    comic = route_message("сделай комикс 4 панели про техножреца")
    if not comic.ok or comic.kind != "image_generation" or comic.governor != "Moriana":
        raise AssertionError(comic)
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
    # A mixed active-governor request should not be collapsed into one brigade.
    mixed = route_message("нарисуй картинку и почини python код в репозитории")
    if not mixed.ok or mixed.governor not in {"Moriana", "Ceraxia"} or not mixed.requires_decomposition:
        raise AssertionError(f"active image+code task should require decomposition: {mixed}")
    code_investigation = route_message("исследуй источник ошибки и почини python код в приложении")
    if not code_investigation.ok or code_investigation.governor != "Ceraxia" or code_investigation.requires_decomposition:
        raise AssertionError(f"code investigation should stay with Ceraxia: {code_investigation}")
    active_mixed = route_message("собери обзор источников по RISC-V и реализуй python демо код")
    if (
        not active_mixed.ok
        or not active_mixed.requires_decomposition
        or {item.get("name") for item in active_mixed.matched_governors if item.get("active")} != {"IskandarKhayon", "Ceraxia"}
        or not active_mixed.supporting_governors
    ):
        raise AssertionError(f"active mixed-governor task should require decomposition: {active_mixed}")
    route_payload = active_mixed.to_dict()
    if not route_payload.get("matched_governors") or route_payload.get("requires_decomposition") is not True:
        raise AssertionError(f"route payload should expose strategic routing diagnostics: {route_payload}")
    print("[ok] Warmaster routing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
