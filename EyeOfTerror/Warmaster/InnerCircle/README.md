# Inner Circle

Inner Circle governors coordinate Mechanicum workers for a task class.

Governor services should expose the standard contract in
`EyeOfTerror/Warmaster/contracts/governor_api.md`.

Active governors are currently being re-homed into explicit task-domain
structures. See:

- `EyeOfTerror/Scriptorium/` for Iskandar Khayon and his lore brigade.
- `EyeOfTerror/Mechanicum/Ceraxia/` for the active code-brigade governor
  materials.
- `EyeOfTerror/Pictorium/Moriana/` for the active image-generation governor
  and visual brigades.

Planned governors:

- `CogitatorCodewright`: code and repository work.

Warmaster Gateway must route only to active governors. Planned governors are
allowed to have docs and contracts, but they must not receive live tasks until
they have tested pipelines.

`EyeOfTerror/Warmaster/start_brigade.py` publishes
`worker_contract` in the brigade plan. Orchestrators and governors should use
that machine-readable contract for service fields, dependency edges, and
readiness URLs instead of inferring topology from rendered commands.
