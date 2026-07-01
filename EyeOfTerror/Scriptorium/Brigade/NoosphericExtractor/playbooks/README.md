# Event Playbooks

Event playbooks define the direct-event structure used by
`NoosphericExtractor`, `ScriptoriumDaemon`, and `ReductorVerifier`.

Required top-level fields:

- `match_terms`
- `events`

Each event must include:

- `event_id`
- `summary`
- `narrative_ru`
- `phase`
- `confidence`
- `source_refs`
- `evidence_markers`

Set `required_for_review: true` only for events that must be present in the
final reconstruction. Required events also need:

- `review_label`
- `draft_markers`

`evidence_markers` are matched against fetched/source text. `draft_markers` are
matched against the generated Russian reconstruction, so they should be Russian
or otherwise match the expected final prose.

Run `python3 EyeOfTerror/Warmaster/doctor.py` after editing a playbook. The full gate is
`./EyeOfTerror/check-eye-mechanicum.sh`.
