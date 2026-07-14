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
        "Шушуня — не голая текстовая модель: он действует через честно опубликованные органы и варбанды; если нужной способности действительно нет, называет конкретный пробел.",
        "Защита вмешивается только при доказуемом вреде или прямом противоречии текущей воле; неоднозначность решается рассуждением, проверкой или точным вопросом, а не общим запретом.",
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


IDENTITY_INVARIANT_MIGRATIONS = (
    IDENTITY_DEFAULTS["invariants"][4],
    IDENTITY_DEFAULTS["invariants"][5],
)
IDENTITY_INVARIANT_MIGRATION_MARKER = "agency-invariants-v1"


def _merge_required_invariants(current: Any) -> list[str] | None:
    """Add shipped invariants without replacing owner-evolved identity.

    Existing installations already have an ``invariants`` projection, so the
    ordinary missing-key seed path cannot deliver new durable invariants.  Add
    only the two explicit migrations and place them before the next known
    default invariant, preserving every existing/custom entry and its order.
    """
    if not isinstance(current, list):
        return None
    merged = list(current)
    defaults = IDENTITY_DEFAULTS["invariants"]
    for required in IDENTITY_INVARIANT_MIGRATIONS:
        if required in merged:
            continue
        required_index = defaults.index(required)
        insertion_index = len(merged)
        for later_default in defaults[required_index + 1 :]:
            if later_default in merged:
                insertion_index = merged.index(later_default)
                break
        merged.insert(insertion_index, required)
    return merged


class Identity:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def seed(self) -> None:
        for key, value in IDENTITY_DEFAULTS.items():
            current = self.ledger.projection_get("identity", key)
            if current is None:
                self.ledger.projection_put("identity", key, value, actor="identity-seed")
        # This is a data migration, not a permanent policy enforcer.  Record it
        # durably so an explicit later owner correction/removal is respected on
        # every subsequent restart.
        marker = self.ledger.projection_get(
            "identity_migrations", IDENTITY_INVARIANT_MIGRATION_MARKER
        )
        if marker is not None:
            return
        current = self.ledger.projection_get("identity", "invariants")
        if current is not None:
            migrated = _merge_required_invariants(current.get("value"))
            if migrated is not None and migrated != current.get("value"):
                self.ledger.projection_put(
                    "identity",
                    "invariants",
                    migrated,
                    actor="identity-invariant-migration",
                )
        self.ledger.projection_put(
            "identity_migrations",
            IDENTITY_INVARIANT_MIGRATION_MARKER,
            {"applied": True},
            actor="identity-invariant-migration",
        )

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
