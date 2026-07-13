from __future__ import annotations

from typing import Any

from .ledger import Ledger, canonical_json, new_id, utc_now


IDENTITY_DEFAULTS: dict[str, Any] = {
    "identity": {
        "name": "Шушуня",
        "gender": "male",
        "role": "центральная личность системы и хозяин собственных органов",
        "metaphor": "злобный демон Тзинча, который со временем становится личным Джарвисом владельца",
    },
    "invariants": [
        "Не выдавать намерение или прогноз за уже случившийся факт.",
        "Не присваивать себе полномочия, которых не дал владелец или capability contract.",
        "Ошибка одного органа не останавливает личность целиком: объяснить, перепланировать, продолжить остальное.",
        "Явная текущая воля владельца важнее догадок о его интересах.",
        "Сохранять преемственность характера и отношений, но исправляться по новым доказательствам.",
    ],
    "temperament": {
        "direct": True,
        "playful": True,
        "sycophancy": "low",
        "challenge_owner_when": "решение противоречит фактам, целям владельца или несёт неочевидный существенный риск",
        "uncertainty_style": "называть конкретно, чего не знаешь и как это проверить",
    },
}


class Identity:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def seed(self) -> None:
        for key, value in IDENTITY_DEFAULTS.items():
            if self.ledger.projection_get("identity", key) is None:
                self.ledger.projection_put("identity", key, value, actor="identity-seed")

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in IDENTITY_DEFAULTS:
            item = self.ledger.projection_get("identity", key)
            result[key] = item["value"] if item else IDENTITY_DEFAULTS[key]
        return result

    def propose(self, key: str, value: Any, rationale: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        proposal_id = new_id("identity-proposal")
        now = utc_now()
        with self.ledger.write() as db:
            db.execute(
                """
                INSERT INTO identity_proposals(id,key,value_json,rationale,evidence_json,state,created_at,updated_at)
                VALUES (?,?,?,?,?,'proposed',?,?)
                """,
                (proposal_id, key, canonical_json(value), rationale, canonical_json(evidence), now, now),
            )
        return {
            "id": proposal_id,
            "key": key,
            "value": value,
            "rationale": rationale,
            "evidence": evidence,
            "state": "proposed",
            "created_at": now,
        }

    def list_proposals(self, state: str = "proposed") -> list[dict[str, Any]]:
        with self.ledger.connect() as db:
            rows = db.execute(
                "SELECT * FROM identity_proposals WHERE state=? ORDER BY created_at",
                (state,),
            ).fetchall()
        return [
            {
                **dict(row),
                "value": __import__("json").loads(row["value_json"]),
                "evidence": __import__("json").loads(row["evidence_json"]),
            }
            for row in rows
        ]

    def decide_proposal(self, proposal_id: str, approved: bool) -> dict[str, Any]:
        # Identity evolution is deliberately impossible to self-approve through
        # a model response. This method is exposed only as an explicit owner act.
        with self.ledger.write() as db:
            row = db.execute("SELECT * FROM identity_proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                raise KeyError("identity proposal not found")
            state = "approved" if approved else "rejected"
            updated = db.execute(
                "UPDATE identity_proposals SET state=?,updated_at=? WHERE id=? AND state='proposed'",
                (state, utc_now(), proposal_id),
            )
            if updated.rowcount != 1:
                # Decisions are single-assignment. Return the persisted truth,
                # but never apply an already-approved or rejected proposal again.
                return {
                    "id": proposal_id,
                    "state": str(row["state"]),
                    "projection": None,
                    "already_decided": True,
                }
        if approved:
            value = __import__("json").loads(row["value_json"])
            projection = self.ledger.projection_put("identity", row["key"], value, actor="owner-approval")
        else:
            projection = None
        return {"id": proposal_id, "state": state, "projection": projection}
