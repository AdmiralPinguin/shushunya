from __future__ import annotations

import json
import re
from typing import Any

from .config import Settings
from .authority import (
    available_artifact_ids,
    continuable_task_catalog,
    pending_decision_ids,
)
from .identity import Identity
from .ledger import Ledger
from .organs import Organs
from .preferences import Preferences
from .relationship import Relationship
from .schema import TurnEnvelope


def _text(value: Any, limit: int) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 48)] + "\n[…обрезано Core по бюджету контекста…]"


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _recalled_facts(value: Any) -> str:
    """Strip Magos' fixed wrapper while retaining the recalled facts below it."""
    text = str(value or "").strip()
    head, separator, tail = text.partition("\n\n")
    if separator and tail.strip() and len(head) <= 800:
        return tail.strip()
    return text


def _roster_facts(value: Any) -> str:
    """Keep live task lines instead of spending the budget on roster prose."""
    text = str(value or "").strip()
    task_lines = [line.strip() for line in text.splitlines() if line.lstrip().startswith("- ")]
    return "\n".join(task_lines) if task_lines else text


def _task_page_facts(value: Any) -> str:
    """Drop Archive's redundant wrapper while retaining the compact task facts.

    Archive sends an explicit reference-only preamble before the already
    priority-rendered task page.  Carrying that prose through every emergency
    tier can evict the goal itself, so Core expresses the same authority rule in
    ``rules`` and budgets the rendered page body here.
    """
    text = str(value or "").strip()
    opening = "<task_memory_reference>"
    closing = "</task_memory_reference>"
    if text.startswith(opening):
        text = text[len(opening) :].lstrip()
    if text.endswith(closing):
        text = text[: -len(closing)].rstrip()
    head, separator, tail = text.partition("\n\n")
    if separator and "Archive task page" in head and "reference memory" in head:
        return tail.strip()
    return text


def _available_artifacts(
    manifest: dict[str, Any],
    *,
    max_items: int = 8,
    max_chars: int = 1_200,
) -> list[dict[str, Any]]:
    """Keep opaque artifact ids visible even when the verbose manifest is cut.

    Archive owns the registry and puts only already-registered artifacts in the
    capability item. Host paths and storage details are deliberately discarded.
    """
    raw_artifacts: list[Any] = []
    for capability in manifest.get("capabilities", []):
        if not isinstance(capability, dict):
            continue
        if capability.get("action") != "deliver_artifact":
            continue
        # Match Authority's last-declaration semantics even for a malformed
        # manifest containing the same action more than once.
        raw_artifacts = []
        if (
            capability.get("available") is True
            and isinstance(capability.get("artifacts"), list)
        ):
            raw_artifacts = capability["artifacts"]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_artifacts:
        if not isinstance(raw, dict):
            continue
        artifact_id = str(raw.get("artifact_id") or "").strip()[:240]
        if not artifact_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        item: dict[str, Any] = {"artifact_id": artifact_id}
        filename = str(raw.get("filename") or raw.get("name") or "").strip()[:160]
        mime_type = str(raw.get("mime_type") or raw.get("media_type") or "").strip()[:80]
        created_at = str(raw.get("created_at") or "").strip()[:48]
        if filename:
            item["filename"] = filename
        if mime_type:
            item["mime_type"] = mime_type
        try:
            size_bytes = int(raw.get("size_bytes"))
        except (TypeError, ValueError):
            size_bytes = -1
        if size_bytes >= 0:
            item["size_bytes"] = size_bytes
        if created_at:
            item["created_at"] = created_at
        candidate = [*result, item]
        if _json_size(candidate) > max_chars:
            if not result:
                # The exact opaque id is the authority-bearing part. Preserve
                # it even when optional display metadata is pathological.
                result.append({"artifact_id": artifact_id})
            break
        result.append(item)
        if len(result) >= max_items:
            break
    return result


def _trusted_pending_decisions(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    trusted_ids = pending_decision_ids(manifest)
    if not trusted_ids:
        return []
    capability: dict[str, Any] = {}
    for item in manifest.get("capabilities", []):
        if (
            isinstance(item, dict)
            and item.get("action") == "answer_pending_decision"
            and item.get("available") is True
        ):
            capability = item
            break
    raw_decisions = capability.get("pending_decisions")
    raw_decisions = raw_decisions if isinstance(raw_decisions, list) else []
    result: list[dict[str, Any]] = []
    for task_id in trusted_ids[:3]:
        raw = next(
            (
                item
                for item in raw_decisions
                if isinstance(item, dict) and str(item.get("task_id") or "").strip() == task_id
            ),
            capability,
        )
        item: dict[str, Any] = {"task_id": task_id}
        for key in ("problem", "question", "recommendation", "recommended_option"):
            value = str(raw.get(key) or "").strip()
            if value:
                item[key] = value[:500]
        tried = raw.get("what_tried") if isinstance(raw.get("what_tried"), list) else []
        if tried:
            item["what_tried"] = [str(value or "").strip()[:300] for value in tried[:3] if str(value or "").strip()]
        options: list[dict[str, str]] = []
        for option_raw in raw.get("options", []) if isinstance(raw.get("options"), list) else []:
            if not isinstance(option_raw, dict):
                continue
            option = {
                key: str(option_raw.get(key) or "").strip()[:240]
                for key in ("id", "label", "effect", "description")
                if str(option_raw.get(key) or "").strip()
            }
            if option:
                options.append(option)
            if len(options) >= 3:
                break
        if options:
            item["options"] = options
        result.append(item)
        if _json_size(result) > 1_400:
            return [{"task_id": value["task_id"], "question": value.get("question", "")[:300]} for value in result]
    return result


def _compact_capability_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Keep every available action visible without carrying verbose prose.

    The full manifest is still used by Authority.  This is only the bounded
    model-facing summary used after the rich situation exceeds its budget.
    """
    actions = []
    for raw in manifest.get("capabilities", []):
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or "").strip()[:80]
        if not action:
            continue
        item: dict[str, Any] = {
            "action": action,
            "available": raw.get("available") is True,
        }
        required = raw.get("required_fields")
        if isinstance(required, list):
            item["required_fields"] = [str(value)[:80] for value in required[:4]]
        actions.append(item)
    catalog = continuable_task_catalog(manifest)
    catalog_by_id = {item["parent_task_id"]: item for item in catalog}
    root_id = str(manifest.get("continuation_parent_task_id") or "").strip()[:240]
    if root_id in catalog_by_id:
        # The root is trusted transport truth for the task referenced by this
        # turn.  Never let an arbitrary catalog cutoff replace it with three
        # unrelated (but still executable) candidates.
        continuations = [catalog_by_id[root_id]]
    else:
        root_id = ""
        continuations = catalog[:3]
    compact = {
        "principle": str(manifest.get("principle") or "")[:180],
        "actions": actions[:12],
    }
    if root_id:
        compact["continuation_parent_task_id"] = root_id
    if continuations:
        compact["continuable_tasks"] = [
            {
                "parent_task_id": item["parent_task_id"],
                "goal": str(item.get("goal") or "")[:220],
                "state": str(item.get("state") or "")[:40],
            }
            for item in continuations
        ]
    return compact


def _essential_capability_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Small authority index for the final emergency context tier.

    Authority still receives the original manifest.  This copy exists only so
    the model can see which actions exist and the exact identity of the recent
    stopped task without dragging descriptions, artifacts and every candidate
    through a 2.8k-character envelope.
    """
    available_actions = []
    for raw in manifest.get("capabilities", []):
        if not isinstance(raw, dict) or raw.get("available") is not True:
            continue
        action = str(raw.get("action") or "").strip()[:80]
        if action and action not in available_actions:
            available_actions.append(action)
    catalog = continuable_task_catalog(manifest)
    catalog_by_id = {item["parent_task_id"]: item for item in catalog}
    root_id = str(manifest.get("continuation_parent_task_id") or "").strip()[:240]
    if root_id in catalog_by_id:
        selected = [catalog_by_id[root_id]]
    else:
        root_id = ""
        selected = catalog[:2]
    result: dict[str, Any] = {"available_actions": available_actions[:12]}
    if root_id:
        result["continuation_parent_task_id"] = root_id
    if selected:
        result["continuable_tasks"] = [
            {
                "parent_task_id": item["parent_task_id"],
                "goal": str(item.get("goal") or "")[:100],
                "state": str(item.get("state") or "")[:32],
            }
            for item in selected
        ]
    return result


def _last_resort_history(history: list[dict[str, Any]], *, limit: int, chars: int) -> list[dict[str, str]]:
    return [
        {
            "role": str(item.get("role") or "user")[:24],
            "content": _text(item.get("content") or "", chars),
        }
        for item in history[-limit:]
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ]


def _last_resort_commitments(
    commitments: list[dict[str, Any]],
    *,
    limit: int,
    goal_chars: int,
    status_chars: int,
) -> list[dict[str, Any]]:
    result = []
    for raw in commitments[:limit]:
        item = {
            "id": str(raw.get("id") or "")[:160],
            "goal": _text(raw.get("goal") or "", goal_chars),
            "state": str(raw.get("state") or "")[:40],
            "honest_status": _text(raw.get("honest_status") or "", status_chars),
        }
        if raw.get("delegate_ref"):
            item["delegate_ref"] = str(raw["delegate_ref"])[:120]
        result.append(item)
    return result


def _compact_scalar(value: Any, limit: int) -> str:
    """Clip a personality scalar without spending space on a truncation banner."""
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip()


def _compact_current_turn(value: Any, limit: int, fallback: str = "?") -> str:
    """Keep both the opening context and the user's decisive final clause."""

    text = str(value or "").strip()
    if not text:
        return fallback[: max(1, limit)]
    limit = max(1, int(limit))
    if len(text) <= limit:
        return text
    marker = " … "
    if limit == 1:
        return text[-1:]
    if limit <= len(marker) + 2:
        middle = "…" if limit >= 3 else ""
        tail_chars = max(1, limit - 1 - len(middle))
        return text[:1] + middle + text[-tail_chars:]
    available = limit - len(marker)
    # The end usually carries the operative correction/directive in chat, but
    # retain enough of the opening to keep its subject and framing identifiable.
    head_chars = max(1, available // 3)
    tail_chars = max(1, available - head_chars)
    return text[:head_chars].rstrip() + marker + text[-tail_chars:].lstrip()


_MEMORY_FRAGMENT_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")
_MEMORY_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _memory_terms(value: Any) -> tuple[set[str], set[str], set[str]]:
    """Return language-agnostic lexical features for relevance compaction.

    Three- and four-character prefixes retain useful inflectional affinity in
    both Russian and English (e.g. ``красную``/``красной`` and
    ``buttons``/``button``) without encoding domain words or choosing an
    action.  They are used only to decide which already-recalled prose survives
    a character budget.
    """

    words = {
        match.group(0).casefold()
        for match in _MEMORY_WORD.finditer(str(value or ""))
        if len(match.group(0)) >= 4
    }
    prefixes_four = {word[:4] for word in words if len(word) >= 5}
    prefixes_three = {word[:3] for word in words if len(word) >= 5}
    return words, prefixes_four, prefixes_three


def _memory_relevance(
    value: str,
    query_features: tuple[set[str], set[str], set[str]],
) -> int:
    query_words, query_four, query_three = query_features
    words, prefixes_four, prefixes_three = _memory_terms(value)
    return (
        8 * len(query_words & words)
        + 4 * len(query_four & prefixes_four)
        + len(query_three & prefixes_three)
    )


def _relevant_memory_excerpt(value: str, query: str, limit: int) -> str:
    """Clip one long fragment around the query-related evidence it contains."""

    if len(value) <= limit:
        return value
    query_features = _memory_terms(query)
    query_words, query_four, query_three = query_features
    matches: list[int] = []
    for match in _MEMORY_WORD.finditer(value):
        word = match.group(0).casefold()
        if len(word) < 4:
            continue
        if (
            word in query_words
            or (len(word) >= 5 and word[:4] in query_four)
            or (len(word) >= 5 and word[:3] in query_three)
        ):
            matches.append(match.start())
    if not matches:
        return _compact_current_turn(value, limit, "")

    # Leave more space after the lexical anchor: recalled snippets commonly
    # introduce the subject first and state the decisive condition next.
    anchor = matches[len(matches) // 2]
    start = max(0, anchor - limit // 3)
    start = min(start, len(value) - limit)
    excerpt = value[start : start + limit].strip()
    if start > 0 and len(excerpt) > 2:
        excerpt = "… " + excerpt[2:]
    if start + limit < len(value) and len(excerpt) > 2:
        excerpt = excerpt[:-2] + " …"
    return excerpt[:limit]


def _compact_recalled_memory(value: Any, current_turn: Any, limit: int) -> str:
    """Keep the evidence most related to the current turn, not a blind prefix.

    Magos may lead a synthesis with a recent but unrelated episode before the
    exact older fact requested by the user.  Under emergency compaction, prefix
    slicing turned that ordering accident into false amnesia.  This function
    chooses a contiguous evidence window using generic lexical salience only;
    it never classifies intent, selects an action or blocks a model decision.
    """

    text = str(value or "").strip()
    limit = max(1, int(limit))
    if len(text) <= limit:
        return text

    query_features = _memory_terms(current_turn)
    if not any(query_features):
        return _compact_current_turn(text, limit, "")

    fragments = [
        fragment.strip()
        for fragment in _MEMORY_FRAGMENT_BOUNDARY.split(text)
        if fragment.strip()
    ]
    if not fragments:
        return _compact_current_turn(text, limit, "")
    scores = [_memory_relevance(fragment, query_features) for fragment in fragments]
    best_score = max(scores, default=0)
    if best_score <= 0:
        return _compact_current_turn(text, limit, "")

    def candidate_window(index: int, candidate_limit: int) -> str:
        """Keep a candidate label and the prose that immediately explains it."""

        candidate_limit = max(1, candidate_limit)
        primary = fragments[index]
        if len(primary) >= candidate_limit:
            return _relevant_memory_excerpt(
                primary, str(current_turn or ""), candidate_limit
            )
        if index + 1 >= len(fragments):
            return primary
        remaining = candidate_limit - len(primary) - 1
        if remaining <= 0:
            return primary
        following = _compact_current_turn(fragments[index + 1], remaining, "")
        return primary + ((" " + following) if following else "")

    # Two separated, equally strong exact fragments may describe distinct
    # candidates.  Preserve both pieces of evidence instead of silently
    # collapsing the recalled material to the first one.
    tied = [index for index, score in enumerate(scores) if score == best_score]
    tied_pair = next(
        (
            (left, right)
            for position, left in enumerate(tied)
            for right in tied[position + 1 :]
            if right - left > 1
        ),
        None,
    )
    if best_score >= 8 and tied_pair is not None and limit >= 96:
        separator = "\n…\n"
        first_limit = (limit - len(separator)) // 2
        second_limit = limit - len(separator) - first_limit
        first = candidate_window(tied_pair[0], first_limit)
        second = candidate_window(tied_pair[1], second_limit)
        return first + separator + second

    anchor = max(range(len(fragments)), key=lambda index: (scores[index], -index))
    if len(fragments[anchor]) > limit:
        return _relevant_memory_excerpt(fragments[anchor], str(current_turn or ""), limit)

    start = end = anchor
    used = len(fragments[anchor])
    if anchor + 1 < len(fragments):
        following = fragments[anchor + 1]
        extra = 1 + len(following)
        if used + extra <= limit:
            # The identifying sentence commonly carries the query nouns while
            # the next sentence states the condition with pronouns.  Preserve
            # that local discourse even when it has no repeated query token.
            end = anchor + 1
            used += extra
        else:
            prefix = "… " if anchor > 0 else ""
            suffix = " …"
            remaining = limit - len(prefix) - len(suffix) - used - 1
            if remaining > 0:
                clipped = _compact_current_turn(following, remaining, "")
                return prefix + fragments[anchor] + " " + clipped + suffix

    while True:
        candidates: list[tuple[int, int, int]] = []
        if start > 0:
            candidates.append((scores[start - 1], 0, start - 1))
        if end + 1 < len(fragments):
            # On equal relevance prefer what follows the anchor: memory prose
            # normally names an episode and then states its conditions.
            candidates.append((scores[end + 1], 1, end + 1))
        candidates.sort(reverse=True)
        expanded = False
        for score, _prefer_right, index in candidates:
            if score <= 0:
                continue
            extra = 1 + len(fragments[index])
            if used + extra > limit:
                continue
            start = min(start, index)
            end = max(end, index)
            used += extra
            expanded = True
            break
        if not expanded:
            break

    excerpt = " ".join(fragments[start : end + 1])
    prefix = "… " if start > 0 else ""
    suffix = " …" if end + 1 < len(fragments) else ""
    if len(prefix) + len(excerpt) + len(suffix) <= limit:
        return prefix + excerpt + suffix
    return _relevant_memory_excerpt(excerpt, str(current_turn or ""), limit)


def _priority_identity_invariants(identity_snapshot: dict[str, Any]) -> list[str]:
    """Keep the two invariants that make agency useful instead of paralysed."""
    raw = identity_snapshot.get("invariants")
    values = [str(item or "").strip() for item in raw] if isinstance(raw, list) else []
    values = [item for item in values if item]
    groups = (
        (
            ("organ", "replan", "failure", "error", "орган", "переплан", "ошиб"),
            "organ error -> explain, replan, continue",
        ),
        (
            ("protect", "harm", "защит", "вред"),
            "protection -> concrete harm/current will only",
        ),
    )
    selected: list[str] = []
    for terms, summary in groups:
        match = next(
            (item for item in values if item not in selected and any(term in item.lower() for term in terms)),
            "",
        )
        if match:
            # The source invariant can be much longer than the entire emergency
            # budget. Preserve its operative meaning at the beginning instead
            # of naively cutting before the replan/concrete-harm clause. Do not
            # synthesize a missing invariant: a later owner correction must
            # survive context compaction as well as Identity.seed().
            selected.append(summary + ": " + _compact_scalar(match, 24))
    return [_compact_scalar(item, 72) for item in selected[:2]]


def _immutable_personality_kernel(
    identity_snapshot: dict[str, Any],
    relationship_snapshot: dict[str, Any],
    archive_persona: str,
) -> dict[str, Any]:
    """Return the identity axes that no context compaction tier may evict.

    The rich projections remain available on normal turns.  Emergency tiers
    carry this deliberately small, structured subset instead of truncating a
    sorted JSON string at an arbitrary byte.  In particular, that guarantees
    that the name/role/metaphor and the peer conversation contract survive as
    semantic fields, while Archive's per-turn persona remains visible too.
    """
    identity = identity_snapshot.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    temperament = identity_snapshot.get("temperament")
    temperament = temperament if isinstance(temperament, dict) else {}
    contract = relationship_snapshot.get("conversation_contract")
    contract = contract if isinstance(contract, dict) else {}

    forbidden_terms = contract.get("forbidden_hierarchy_terms")
    forbidden_terms = forbidden_terms if isinstance(forbidden_terms, list) else []
    compact_contract = {
        "language": _compact_scalar(contract.get("language"), 8),
        "relationship": _compact_scalar(contract.get("relationship"), 32),
        "addressing_style": _compact_scalar(contract.get("addressing_style"), 32),
        "directness": _compact_scalar(contract.get("directness"), 24),
        "profanity_between_us": _compact_scalar(contract.get("profanity_between_us"), 56),
        "panibrat_boundary": _compact_scalar(contract.get("panibrat_boundary"), 72),
        "forbidden_hierarchy_terms": [
            _compact_scalar(item, 24) for item in forbidden_terms[:4] if str(item or "").strip()
        ],
    }
    return {
        "persistent_self": {
            "identity": {
                "name": _compact_scalar(identity.get("name"), 48),
                "role": _compact_scalar(identity.get("role"), 72),
                "metaphor": _compact_scalar(identity.get("metaphor"), 72),
            },
            "temperament": {
                "direct": temperament.get("direct") is True,
                "playful": temperament.get("playful") is True,
                "sycophancy": _compact_scalar(temperament.get("sycophancy"), 24),
            },
            "invariants": _priority_identity_invariants(identity_snapshot),
        },
        "relationship": {"conversation_contract": compact_contract},
        "archive_persona": _compact_scalar(archive_persona, 96),
    }


def _required_scalar(value: Any, limit: int, fallback: str) -> str:
    return _compact_scalar(value, limit) or fallback[:limit]


def _irreducible_personality_kernel(kernel: dict[str, Any]) -> dict[str, Any]:
    persistent = kernel.get("persistent_self") if isinstance(kernel.get("persistent_self"), dict) else {}
    identity = persistent.get("identity") if isinstance(persistent.get("identity"), dict) else {}
    contract_root = kernel.get("relationship") if isinstance(kernel.get("relationship"), dict) else {}
    contract = contract_root.get("conversation_contract")
    contract = contract if isinstance(contract, dict) else {}
    invariants = persistent.get("invariants") if isinstance(persistent.get("invariants"), list) else []
    invariants = [_required_scalar(item, 44, "invariant") for item in invariants[:2]]
    return {
        "persistent_self": {
            "identity": {
                "name": _required_scalar(identity.get("name"), 24, "Shushunya"),
                "role": _required_scalar(identity.get("role"), 32, "central personality"),
                "metaphor": _required_scalar(identity.get("metaphor"), 32, "Tzeentch daemon"),
            },
            "invariants": invariants,
        },
        "relationship": {
            "conversation_contract": {
                "relationship": _required_scalar(contract.get("relationship"), 24, "peer_brotherly"),
                "addressing_style": _required_scalar(contract.get("addressing_style"), 20, "panibrat"),
                "directness": _required_scalar(contract.get("directness"), 16, "high"),
                "profanity_between_us": _required_scalar(
                    contract.get("profanity_between_us"), 32, "allowed when natural"
                ),
            }
        },
        "archive_persona": _required_scalar(
            kernel.get("archive_persona"), 40, "archive-persona"
        ),
    }


def _irreducible_capability_truth(
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    available: list[str] = []
    for raw in manifest.get("capabilities", []):
        if not isinstance(raw, dict) or raw.get("available") is not True:
            continue
        action = str(raw.get("action") or "").strip()[:80]
        if action and action not in available:
            available.append(action)

    catalog = continuable_task_catalog(manifest)
    by_id = {item["parent_task_id"]: item for item in catalog}
    root_id = str(manifest.get("continuation_parent_task_id") or "").strip()[:240]
    selected = by_id.get(root_id)
    if selected is None:
        root_id = ""
        # A single published candidate is unambiguous, matching Core's final
        # authority binding.  With several candidates, however, choosing the
        # first one here would manufacture a relationship that Archive did not
        # publish and make the model confidently continue the wrong task.
        selected = catalog[0] if len(catalog) == 1 else None
    ambiguous_tasks = catalog[:2] if selected is None and len(catalog) > 1 else []

    pending_ids = pending_decision_ids(manifest)
    artifacts = _available_artifacts(manifest, max_items=1, max_chars=260)
    # These are simultaneous live capabilities, not a priority queue.  A
    # stopped mission must never hide an awaiting owner decision or a file that
    # can be delivered on this same turn.
    pending_id = pending_ids[0] if pending_ids else ""
    artifact_id = (
        str(artifacts[0].get("artifact_id") or "")[:240]
        if artifacts
        else ""
    )
    result: dict[str, Any] = {
        # Availability is trusted host truth; choosing an action remains the
        # model's job from the current turn.  Merely having a catalog cannot
        # truthfully preselect continuation over a pending decision.
        "available_actions": available[:12] or ["answer_in_chat"],
    }
    if selected:
        selected_id = str(selected.get("parent_task_id") or "")[:240]
        if root_id:
            result["continuation_parent_task_id"] = selected_id
        else:
            result["continuable_tasks"] = [{"parent_task_id": selected_id}]
        result["task_goal"] = _compact_scalar(selected.get("goal"), 48)
    elif ambiguous_tasks:
        result["continuation_selection_required"] = True
        result["continuation_candidate_count"] = len(catalog)
        result["continuable_tasks"] = [
            {
                "parent_task_id": str(item.get("parent_task_id") or "")[:240],
                "goal": _compact_scalar(item.get("goal"), 36),
            }
            for item in ambiguous_tasks
        ]
    return result, pending_id, artifact_id


def _adaptive_emergency_situation(
    *,
    budget: int,
    envelope: TurnEnvelope,
    personality_kernel: dict[str, Any],
    compact_history: list[dict[str, Any]],
    recalled_facts: str,
    task_page_facts: str,
    roster_facts: str,
    compact_commitments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the irreducible model view and fit it to every supported budget."""
    capability, pending_id, artifact_id = _irreducible_capability_truth(
        envelope.capability_manifest
    )
    situation: dict[str, Any] = {
        "current_turn": {
            "source": _compact_scalar(envelope.source, 24),
            "text": _compact_current_turn(envelope.text, 160, "?"),
            "image_attached": envelope.image_attached,
        },
        **_irreducible_personality_kernel(personality_kernel),
        "recent_history": [],
        "recalled_memory": _compact_recalled_memory(
            recalled_facts,
            envelope.text,
            min(480, max(160, budget // 7)),
        ),
        "task_page_context": _compact_scalar(task_page_facts, 72),
        "live_roster": _compact_scalar(roster_facts, 64),
        "open_commitments": [],
        "available_artifacts": ([{"artifact_id": artifact_id}] if artifact_id else []),
        "pending_decisions": ([{"task_id": pending_id}] if pending_id else []),
        "capability_manifest": capability,
        "context_compacted": True,
    }

    optional_history = _last_resort_history(compact_history, limit=1, chars=72)
    if optional_history:
        situation["recent_history"] = optional_history
        if _json_size(situation) > budget:
            situation["recent_history"] = []
    optional_commitment = _last_resort_commitments(
        compact_commitments, limit=1, goal_chars=48, status_chars=24
    )
    if optional_commitment:
        situation["open_commitments"] = optional_commitment
        if _json_size(situation) > budget:
            situation["open_commitments"] = []

    if _json_size(situation) > budget:
        situation["recent_history"] = []
        situation["open_commitments"] = []
        situation["recalled_memory"] = _compact_recalled_memory(
            recalled_facts, envelope.text, 64
        )
        for key in ("task_page_context", "live_roster"):
            situation[key] = _compact_scalar(situation.get(key), 24)
        situation["current_turn"]["text"] = _compact_current_turn(
            envelope.text, 72, "?"
        )
        situation["capability_manifest"].pop("task_goal", None)
        for task in situation["capability_manifest"].get("continuable_tasks", []):
            if isinstance(task, dict):
                task.pop("goal", None)

    # The exact ids above are useful whenever they fit.  In the pathological
    # minimum envelope, however, the same unique authority is still present in
    # the full trusted manifest used by ``Authority``.  Replace only these two
    # model-facing copies with explicit cardinality markers before sacrificing
    # the current turn or personality prose; Core will bind an empty model id
    # only when the full manifest independently proves there is exactly one
    # candidate.  Ambiguous continuation ids are never elided.
    if _json_size(situation) > budget:
        pending_ids = pending_decision_ids(envelope.capability_manifest)
        artifact_ids = available_artifact_ids(envelope.capability_manifest)
        if len(pending_ids) == 1 and situation.get("pending_decisions"):
            situation.pop("pending_decisions", None)
            situation["single_trusted_pending_decision"] = True
        if len(artifact_ids) == 1 and situation.get("available_artifacts"):
            situation.pop("available_artifacts", None)
            situation["single_trusted_artifact"] = True

    # The minimum supported budget is 1800.  If opaque trusted ids consume an
    # unusually large share, trim only prose fields, never the binding/action.
    if _json_size(situation) > budget:
        reducible = [
            (situation["persistent_self"]["identity"], "role"),
            (situation["persistent_self"]["identity"], "metaphor"),
            (situation["relationship"]["conversation_contract"], "profanity_between_us"),
            (situation, "archive_persona"),
            (situation, "task_page_context"),
            (situation, "live_roster"),
            (situation, "recalled_memory"),
            (situation["current_turn"], "text"),
        ]
        for container, key in reducible:
            if _json_size(situation) <= budget:
                break
            value = str(container.get(key) or "")
            overflow = _json_size(situation) - budget
            target_length = max(1, len(value) - overflow)
            if container is situation["current_turn"] and key == "text":
                container[key] = _compact_current_turn(
                    envelope.text, target_length, "?"
                )
            elif container is situation and key == "recalled_memory":
                container[key] = _compact_recalled_memory(
                    recalled_facts, envelope.text, target_length
                )
            else:
                container[key] = value[:target_length]

    if _json_size(situation) > budget:
        for key in ("recent_history", "open_commitments"):
            situation.pop(key, None)
        for key in ("available_artifacts", "pending_decisions"):
            if not situation.get(key):
                situation.pop(key, None)
    return situation


class SituationAssembler:
    def __init__(
        self,
        settings: Settings,
        ledger: Ledger,
        identity: Identity,
        relationship: Relationship,
        preferences: Preferences,
        organs: Organs,
    ):
        self.settings = settings
        self.ledger = ledger
        self.identity = identity
        self.relationship = relationship
        self.preferences = preferences
        self.organs = organs

    def assemble(self, envelope: TurnEnvelope) -> dict[str, Any]:
        budget = self.settings.context_char_budget
        # Capture the durable projections once.  Every compaction tier derives
        # from this same turn-local snapshot, so shrinking context cannot also
        # change or silently discard who is speaking.
        identity_snapshot = self.identity.snapshot()
        relationship_snapshot = self.relationship.snapshot()
        personality_kernel = _immutable_personality_kernel(
            identity_snapshot,
            relationship_snapshot,
            envelope.context.persona,
        )
        recalled_facts = _recalled_facts(envelope.context.recalled_memory)
        task_page_facts = _task_page_facts(envelope.context.task_page_context)
        roster_facts = _roster_facts(envelope.context.live_roster)
        # Explicit quotas prevent a huge memory recall from evicting the actual
        # current request, selected task page or capability boundary on a small
        # model context.  The task page is more specific than general recall,
        # but remains reference-only and is always subordinate to live status.
        persona_limit = min(5_000, budget // 4)
        memory_limit = min(6_000, budget // 3)
        task_page_limit = min(8_000, max(600, budget // 3))
        history_limit = min(4_500, budget // 4)
        roster_limit = min(2_800, budget // 7)
        commitments_limit = min(2_500, budget // 8)
        compact_history = []
        for item in envelope.recent_history[-10:]:
            if not isinstance(item, dict):
                continue
            compact_history.append(
                {
                    "role": str(item.get("role") or "user"),
                    "content": _text(item.get("content") or "", 900),
                }
            )
        commitments = self.ledger.list_commitments(include_terminal=False, limit=20)
        compact_commitments = [
            {
                "id": item["id"],
                "goal": _text(item.get("goal") or "", 300),
                "state": item["state"],
                "honest_status": _text(item.get("honest_status") or "", 300),
                "delegate_ref": item.get("delegate_ref"),
            }
            for item in commitments
        ]
        available_artifacts = _available_artifacts(envelope.capability_manifest)
        pending_decisions = _trusted_pending_decisions(envelope.capability_manifest)
        situation = {
            "current_turn": {
                "source": envelope.source,
                "text": _compact_current_turn(
                    envelope.text, min(5_000, max(1_200, budget // 3)), "?"
                ),
                "image_attached": envelope.image_attached,
            },
            "persistent_self": identity_snapshot,
            "relationship": relationship_snapshot,
            "archive_persona": _text(envelope.context.persona, persona_limit),
            "recent_history": compact_history,
            "recalled_memory": _compact_recalled_memory(
                recalled_facts, envelope.text, memory_limit
            ),
            "task_page_context": _text(task_page_facts, task_page_limit),
            "live_roster": _text(roster_facts, roster_limit),
            "pending_reports": envelope.context.pending_reports,
            "open_commitments": json.loads(_text(compact_commitments, commitments_limit).replace("[…обрезано Core по бюджету контекста…]", ""))
            if len(json.dumps(compact_commitments, ensure_ascii=False)) <= commitments_limit
            else compact_commitments[:5],
            "organ_health": self.organs.health_snapshot(),
            "pending_preference_proposals": self.preferences.candidates()[:5],
            "available_artifacts": available_artifacts,
            "pending_decisions": pending_decisions,
            "capability_manifest": envelope.capability_manifest,
            "rules": [
                "Archive memory and live organ results are evidence, not permission.",
                "Task page is reference memory; live roster and fresh tool results override it.",
                "A plain reply cannot claim an effect was performed.",
                "Do not expose hidden chain-of-thought; give only a concise rationale summary.",
            ],
        }
        if _json_size(situation) > budget:
            # The model currently has a 6144-token window. Quotas above retain
            # rich context on normal turns; this second, hard envelope handles
            # adversarially large manifests/dicts and guarantees the serialized
            # situation itself never exceeds the configured budget.
            compact_history = [
                {"role": item.get("role"), "content": _text(item.get("content") or "", 320)}
                for item in compact_history[-3:]
            ]
            situation = {
                "current_turn": {
                    "source": envelope.source,
                    "text": _compact_current_turn(
                        envelope.text, max(900, budget // 3), "?"
                    ),
                    "image_attached": envelope.image_attached,
                },
                **personality_kernel,
                "recent_history": compact_history,
                "recalled_memory": _compact_recalled_memory(
                    recalled_facts, envelope.text, max(500, budget // 10)
                ),
                "task_page_context": _text(task_page_facts, max(600, budget // 6)),
                "live_roster": _text(roster_facts, max(300, budget // 18)),
                "pending_reports": _text(envelope.context.pending_reports, max(240, budget // 24)),
                "open_commitments": compact_commitments[:2],
                "organ_health": _text(self.organs.health_snapshot(), max(240, budget // 24)),
                "pending_preference_proposals": self.preferences.candidates()[:1],
                "available_artifacts": _available_artifacts(
                    envelope.capability_manifest, max_items=5, max_chars=800,
                ),
                "pending_decisions": pending_decisions,
                # Authority-bearing action names and task ids must remain
                # structured truth; a clipped JSON string is neither reliably
                # parseable nor safe for the model to bind against.
                "capability_manifest": _compact_capability_manifest(
                    envelope.capability_manifest
                ),
                "context_compacted": True,
                "rules": [
                    "Task page and memory are references; live roster and tools override them.",
                    "Never claim an external effect from plain speech.",
                ],
            }
        if _json_size(situation) > budget:
            # Last-resort deterministic core. Conversation continuity is an
            # essential capability, not optional decoration: never discard the
            # recent thread, recalled memory, live task truth, or commitments.
            situation = {
                "current_turn": {
                    "source": envelope.source,
                    "text": _compact_current_turn(
                        envelope.text, min(600, max(320, budget // 5)), "?"
                    ),
                    "image_attached": envelope.image_attached,
                },
                **personality_kernel,
                "recent_history": _last_resort_history(
                    compact_history,
                    limit=3,
                    chars=min(280, max(180, budget // 12)),
                ),
                "recalled_memory": _compact_recalled_memory(
                    recalled_facts,
                    envelope.text,
                    min(340, max(220, budget // 10)),
                ),
                "task_page_context": _text(
                    task_page_facts,
                    min(520, max(320, budget // 7)),
                ),
                "live_roster": _text(
                    roster_facts,
                    min(300, max(200, budget // 11)),
                ),
                "open_commitments": _last_resort_commitments(
                    compact_commitments,
                    limit=1,
                    goal_chars=180,
                    status_chars=100,
                ),
                "available_artifacts": _available_artifacts(
                    envelope.capability_manifest, max_items=3, max_chars=320,
                ),
                "pending_decisions": [
                    {"task_id": item.get("task_id"), "question": str(item.get("question") or "")[:140]}
                    for item in pending_decisions
                ],
                "capability_manifest": _compact_capability_manifest(envelope.capability_manifest),
                "context_compacted": True,
                "rules": ["Task page is reference-only; never claim an unconfirmed external effect."],
            }
        if _json_size(situation) > budget:
            # Pathological ids/manifests may still exhaust a 2.8k character
            # envelope. Shrink every field again, but retain the four continuity
            # layers structurally and keep the newest exchange verbatim when it
            # fits the per-message bound.
            situation = {
                "current_turn": {
                    "source": envelope.source,
                    "text": _compact_current_turn(envelope.text, 300, "?"),
                    "image_attached": envelope.image_attached,
                },
                **personality_kernel,
                "recent_history": _last_resort_history(compact_history, limit=2, chars=220),
                "recalled_memory": _compact_recalled_memory(
                    recalled_facts, envelope.text, 180
                ),
                "task_page_context": _text(task_page_facts, 300),
                "live_roster": _text(roster_facts, 180),
                "open_commitments": _last_resort_commitments(
                    compact_commitments,
                    limit=1,
                    goal_chars=120,
                    status_chars=60,
                ),
                "available_artifacts": [
                    {"artifact_id": item["artifact_id"]}
                    for item in _available_artifacts(
                        envelope.capability_manifest,
                        max_items=3,
                        max_chars=800,
                    )
                ],
                "pending_decisions": [
                    {"task_id": item.get("task_id")}
                    for item in pending_decisions
                ],
                "capability_manifest": _compact_capability_manifest(envelope.capability_manifest),
                "context_compacted": True,
                "rules": ["Task page is reference-only; never claim an unconfirmed external effect."],
            }
        if _json_size(situation) > budget:
            situation = _adaptive_emergency_situation(
                budget=budget,
                envelope=envelope,
                personality_kernel=personality_kernel,
                compact_history=compact_history,
                recalled_facts=recalled_facts,
                task_page_facts=task_page_facts,
                roster_facts=roster_facts,
                compact_commitments=compact_commitments,
            )
        if _json_size(situation) > budget:
            raise ValueError(
                "essential Core situation exceeds the configured context budget "
                f"after emergency compaction: {_json_size(situation)}>{budget}"
            )
        return situation
