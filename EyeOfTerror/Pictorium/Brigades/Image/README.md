# Image Brigade

Owns still-image generation, editing, inpaint, upscale, model/asset readiness,
artifact verification, and final image delivery.

Workers live under `Workers/`:

- `Promptwright`
- `ModelQuartermaster`
- `ForgeDispatcher`
- `ImageVerifier`
- `ArtifactFinalis`

This brigade uses `Moriana/forge_runtime` for API, queue, storage, schemas, and
job lifecycle, and uses `DemonsForge/forge_service/engines` only as graphical
engine adapters.
