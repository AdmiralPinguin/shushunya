#!/usr/bin/env python3
from __future__ import annotations

from archive_handler import ArchiveHandler


def final_message(payload: dict[str, object]) -> str:
    return ArchiveHandler.warmaster_final_message(None, payload)


def main() -> int:
    accepted = final_message(
        {
            "status": "completed",
            "summary": {
                "status": "completed",
                "mission_protocol": {
                    "final_response": {
                        "type": "final_response",
                        "answer": "Принятый Вармастером финальный ответ.",
                    }
                },
            },
            "display": {"detail": "служебный completed detail"},
            "final": {"deliverable": "fallback deliverable"},
        }
    )
    if accepted != "Принятый Вармастером финальный ответ.":
        raise AssertionError(f"accepted final_response was not preferred: {accepted!r}")
    revision = final_message(
        {
            "status": "revision",
            "summary": {"status": "revision"},
            "display": {"headline": "Финальный отчет: нужна ревизия", "detail": "needs_revision internal detail"},
            "final": {},
        }
    )
    if revision:
        raise AssertionError(f"internal revision leaked to chat final message: {revision!r}")
    blocked = final_message(
        {
            "status": "blocked",
            "summary": {"status": "blocked"},
            "display": {"headline": "Нужна эскалация", "detail": "blocked diagnostic detail"},
            "final": {},
        }
    )
    if blocked:
        raise AssertionError(f"blocked diagnostic leaked to chat final message: {blocked!r}")
    running = final_message(
        {
            "status": "running",
            "summary": {"status": "running"},
            "display": {"headline": "Run is active", "detail": "0/10 steps complete"},
            "final": {},
        }
    )
    if running:
        raise AssertionError(f"running diagnostic leaked to chat final message: {running!r}")
    print("[ok] Archive Warmaster final-message gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
