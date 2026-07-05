# ImageVerifier

Planned Pictorium worker for generated image verification.

Owned modules:

- `EyeOfTerror/Pictorium/Moriana/moriana_core/image_evaluator.py`
- `EyeOfTerror/Pictorium/Moriana/benches/quality_bench.py`
- `EyeOfTerror/Pictorium/Moriana/benches/long_forge_api.py`

Expected output:

- `/work/pictorium/image_verification.json`

The first version should preserve deterministic checks: dimensions, metadata,
pixel statistics, edit deltas, inpaint localization, and missing artifact
errors.
