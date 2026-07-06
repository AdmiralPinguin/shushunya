# Pictorium

Pictorium is the EyeOfTerror visual-generation department. It owns visual
intent planning, model/resource policy, Forge runtime API, queueing, storage,
verification, comics workflows, video workflows, and final visual-package
handoff.

`DemonsForge` is intentionally narrow: it keeps graphical engine adapters,
local model folders, and direct standalone model demo scripts. Anything that is
API, queue, schema, project orchestration, policy, reporting, testing, or worker
coordination belongs here.

## Topology

```text
EyeOfTerror/Pictorium/
  Brigades/
    Image/
      Workers/
        Promptwright/
        ModelQuartermaster/
        ForgeDispatcher/
        ImageVerifier/
        ArtifactFinalis/
    Comics/
    Video/
  Moriana/
    contracts/
    forge_runtime/
    forge_tests/
    moriana_core/
    scripts/
```

## Brigades

- `Image`: still images, edits, inpaint, upscale, LoRA/IP-Adapter readiness,
  verification, and delivery.
- `Comics`: active first-pass pipeline for scenarios, storyboards,
  character-sheet planning, panel packages, layout, and final comic manifests.
- `Video`: future video backends, GPU scheduling, clips, and image-to-video or
  text-to-video workflows.

## Activation State

Pictorium is active for still-image tasks through Moriana and the Image
Brigade. Comics is active for first-pass storyboard and panel-package
workflows, and reuses the Image Brigade execution layer instead of duplicating
Forge runtime ownership. Video remains planned.

## DemonsForge Boundary

Allowed under `DemonsForge`:

- graphical engine adapters;
- local models, LoRAs, embeddings, generated artifacts, and runtime data;
- direct standalone model demo/download scripts.

Not allowed under `DemonsForge`:

- API server/client;
- queue/storage/project orchestration;
- schemas/contracts;
- planner/thinker/policy/reporting;
- Pictorium tests or bench scenarios.
