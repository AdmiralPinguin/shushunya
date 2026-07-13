from __future__ import annotations

from typing import Any

from .ledger import Ledger, canonical_json, new_id, utc_now
from .schema import AgendaRequest


def agenda_score(value: float, confidence: float, urgency: float, cost: float, risk: float) -> float:
    return round((value * confidence * 0.6) + (urgency * 0.4) - (cost * 0.25) - (risk * 0.65), 6)


class Agenda:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def add(self, request: AgendaRequest) -> dict[str, Any]:
        item_id = new_id("agenda")
        score = agenda_score(request.value, request.confidence, request.urgency, request.cost, request.risk)
        now = utc_now()
        with self.ledger.write() as db:
            db.execute(
                """
                INSERT INTO agenda_items(
                    id,title,kind,payload_json,state,value,confidence,urgency,cost,risk,score,
                    stop_condition,budget_seconds,max_attempts,created_at,updated_at
                ) VALUES (?,?,?,?,'queued',?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item_id,
                    request.title,
                    request.kind,
                    canonical_json(request.payload),
                    request.value,
                    request.confidence,
                    request.urgency,
                    request.cost,
                    request.risk,
                    score,
                    request.stop_condition,
                    request.budget_seconds,
                    request.max_attempts,
                    now,
                    now,
                ),
            )
        return {"id": item_id, "state": "queued", "score": score, **request.model_dump()}

    def next_useful(self, minimum_score: float = 0.12) -> dict[str, Any] | None:
        with self.ledger.connect() as db:
            row = db.execute(
                """
                SELECT * FROM agenda_items
                WHERE state='queued' AND score>=? AND (next_eligible_at IS NULL OR next_eligible_at<=?)
                ORDER BY score DESC,created_at LIMIT 1
                """,
                (minimum_score, utc_now()),
            ).fetchone()
        if not row:
            return None  # Doing nothing is an intentional, valid outcome.
        item = dict(row)
        item["payload"] = __import__("json").loads(item.pop("payload_json"))
        return item

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.ledger.connect() as db:
            rows = db.execute("SELECT * FROM agenda_items ORDER BY state,score DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = __import__("json").loads(item.pop("payload_json"))
            result.append(item)
        return result
