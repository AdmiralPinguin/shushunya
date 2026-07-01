# Inner Circle

Inner Circle governors coordinate Mechanicum workers for a task class.

Governor services should expose the standard contract in
`EyeOfTerror/Warmaster/contracts/governor_api.md`.

Active governors are currently being re-homed into explicit task-domain
structures. See:

- `EyeOfTerror/Scriptorium/` for Iskandar Khayon and his lore brigade.
- `EyeOfTerror/Mechanicum/Ceraxia/` for the active code-brigade governor
  materials.

Planned governors:

- `CogitatorCodewright`: code and repository work.
- `ForgeMasterGovernor`: image generation and DemonsForge work.

Warmaster Gateway must route only to active governors. Planned governors are
allowed to have docs and contracts, but they must not receive live tasks until
they have tested pipelines.
