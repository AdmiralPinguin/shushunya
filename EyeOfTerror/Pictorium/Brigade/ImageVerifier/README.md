# ImageVerifier

Planned Pictorium worker for generated image verification.

Owned modules:

- `EyeOfTerror/Pictorium/Moriana/moriana_core/image_evaluator.py`
- `DemonsForge/tests/quality_bench.py`

Expected output:

- `/work/pictorium/image_verification.json`

The first version should preserve deterministic checks: dimensions, metadata,
pixel statistics, edit deltas, inpaint localization, and missing artifact
errors.
