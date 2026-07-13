from __future__ import annotations

from typing import Any

from .ledger import Ledger, new_id, utc_now
from .schema import PreferenceEvidence


class Preferences:
    """Context-scoped autonomy, not a global keyword table.

    Repetition creates a candidate. Only an explicit future delegation creates
    an active ``auto`` rule; ordinary one-off approvals never silently broaden
    Shushunya's authority.
    """

    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def record(self, item: PreferenceEvidence) -> dict[str, Any]:
        now = utc_now()
        with self.ledger.write() as db:
            evidence_id = new_id("pref-evidence")
            db.execute(
                """
                INSERT INTO preference_evidence(id,action_kind,target_scope,context_scope,verdict,evidence,created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    evidence_id,
                    item.action_kind,
                    item.target_scope,
                    item.context_scope,
                    item.verdict,
                    item.evidence,
                    now,
                ),
            )
            count = int(
                db.execute(
                    """
                    SELECT COUNT(*) FROM preference_evidence
                    WHERE action_kind=? AND target_scope=? AND context_scope=? AND verdict='approved_once'
                    """,
                    (item.action_kind, item.target_scope, item.context_scope),
                ).fetchone()[0]
            )
            if item.verdict in {"delegate_future", "never_auto"}:
                verdict = "auto" if item.verdict == "delegate_future" else "never_auto"
                rule_id = new_id("pref-rule")
                db.execute(
                    """
                    INSERT INTO preference_rules(
                        id,action_kind,target_scope,context_scope,verdict,confidence,source,evidence_count,created_at,updated_at
                    ) VALUES (?,?,?,?,?,1.0,'explicit_owner',?,?,?)
                    ON CONFLICT(action_kind,target_scope,context_scope) DO UPDATE SET
                        verdict=excluded.verdict,confidence=1.0,source='explicit_owner',
                        evidence_count=excluded.evidence_count,updated_at=excluded.updated_at
                    """,
                    (rule_id, item.action_kind, item.target_scope, item.context_scope, verdict, max(1, count), now, now),
                )
                candidate = None
            elif item.verdict == "rejected":
                rule_id = new_id("pref-rule")
                db.execute(
                    """
                    INSERT INTO preference_rules(
                        id,action_kind,target_scope,context_scope,verdict,confidence,source,evidence_count,created_at,updated_at
                    ) VALUES (?,?,?,?,'ask',1.0,'explicit_owner',1,?,?)
                    ON CONFLICT(action_kind,target_scope,context_scope) DO UPDATE SET
                        verdict='ask',confidence=1.0,source='explicit_owner',updated_at=excluded.updated_at
                    """,
                    (rule_id, item.action_kind, item.target_scope, item.context_scope, now, now),
                )
                candidate = None
            elif count >= 3:
                candidate_id = new_id("pref-candidate")
                db.execute(
                    """
                    INSERT INTO preference_candidates(
                        id,action_kind,target_scope,context_scope,proposed_verdict,evidence_count,state,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,'proposed',?,?)
                    ON CONFLICT(action_kind,target_scope,context_scope,proposed_verdict) DO UPDATE SET
                        evidence_count=excluded.evidence_count,updated_at=excluded.updated_at
                    """,
                    (candidate_id, item.action_kind, item.target_scope, item.context_scope, "auto", count, now, now),
                )
                candidate = {"proposed_verdict": "auto", "evidence_count": count, "requires_owner_confirmation": True}
            else:
                candidate = None
        return {"recorded": True, "evidence_count": count, "candidate": candidate}

    def lookup(self, action_kind: str, target_scope: str = "*", context_scope: str = "*") -> dict[str, Any] | None:
        with self.ledger.connect() as db:
            row = db.execute(
                """
                SELECT * FROM preference_rules
                WHERE action_kind=?
                  AND target_scope IN (?, '*')
                  AND context_scope IN (?, '*')
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY
                  CASE verdict WHEN 'never_auto' THEN 3 WHEN 'ask' THEN 2 ELSE 1 END DESC,
                  updated_at DESC,
                  (target_scope=? ) DESC,
                  (context_scope=? ) DESC
                LIMIT 1
                """,
                (action_kind, target_scope, context_scope, utc_now(), target_scope, context_scope),
            ).fetchone()
        return dict(row) if row else None

    def restrictive(self, action_kind: str, context_scope: str = "*") -> dict[str, Any] | None:
        """Return the strongest live owner restriction for an external action.

        ``target_scope`` in a model decision is descriptive output, not an
        authority token. Looking across scopes prevents a model from turning a
        code-specific prohibition into an allowed action by labelling it
        ``mixed`` or ``unknown``.
        """
        with self.ledger.connect() as db:
            row = db.execute(
                """
                SELECT * FROM preference_rules
                WHERE action_kind=?
                  AND verdict IN ('never_auto','ask')
                  AND context_scope IN (?, '*')
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY
                  CASE verdict WHEN 'never_auto' THEN 2 ELSE 1 END DESC,
                  updated_at DESC,
                  (context_scope=? ) DESC
                LIMIT 1
                """,
                (action_kind, context_scope, utc_now(), context_scope),
            ).fetchone()
        return dict(row) if row else None

    def candidates(self) -> list[dict[str, Any]]:
        with self.ledger.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM preference_candidates WHERE state='proposed' ORDER BY updated_at DESC"
            )]
