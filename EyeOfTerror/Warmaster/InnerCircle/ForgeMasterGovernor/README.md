# ForgeMasterGovernor

Planned Inner Circle governor for image generation and DemonsForge work.

This governor should treat image engines as specialist workers behind stable
ports, not as random tools embedded into the general agent loop.

## Intended Scope

- Prompt planning for image generation tasks.
- Stable Diffusion and alternative engine parameter orchestration.
- LoRA/model discovery requests through dedicated workers.
- Render verification, artifact packaging, and delivery.
- Coordination with `ForgeRelay` for existing DemonsForge APIs.

## Intended Worker Chain

1. A planner converts the user request into a structured image task contract.
2. A model/resource worker checks required models, LoRAs, VAEs, ControlNet units,
   and engine availability.
3. `ForgeRelay` submits generation jobs to DemonsForge.
4. A verifier checks produced artifacts, metadata, and failure states.
5. A finalizer records image artifacts and delivery targets.

## Activation Requirements

- Image task contract builder exists.
- `ForgeRelay` is tested through the common Worker API.
- Generated artifacts are visible through Warmaster artifact endpoints.
- Failures report engine/model/resource causes instead of vague generation
  errors.
