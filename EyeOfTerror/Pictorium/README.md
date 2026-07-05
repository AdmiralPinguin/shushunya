# Pictorium

Pictorium is the EyeOfTerror visual-generation department. It owns image
planning, model/resource selection, DemonsForge dispatch, generated artifact
verification, and final visual-package handoff.

The department is intentionally separate from `DemonsForge`:

- `DemonsForge` remains the image engine, job queue, runtime API, and artifact
  store.
- `Pictorium` is the governor/brigade layer that turns user intent into
  supervised image-generation runs.
- `Warmaster` routes image tasks to this department only after the governor
  service and worker chain are active.

## Planned Topology

```text
EyeOfTerror/Pictorium/
  Moriana/
    contracts/
  Brigade/
    Promptwright/
    ModelQuartermaster/
    ForgeDispatcher/
    ImageVerifier/
    ArtifactFinalis/
```

## Activation Rule

Pictorium is scaffolded but not active. Do not switch the image governor to
`active` until Moriana can prepare a valid Warmaster run package and the brigade
can pass the common worker API contract.

## DemonsForge Cleanup Rule

DemonsForge must stay a narrow runtime. The desired end state is:

- Pictorium owns visual intent, policy, verification, and final handoff.
- DemonsForge owns runtime execution, queueing, engine adapters, and raw
  artifact storage.

The cleanup pass moved planner, thinker, project-planning, deterministic
image-evaluator, character-profile, asset-catalog, asset-download, report, and
bench logic into `Pictorium/Moriana`. DemonsForge imports this logic directly
where its API still needs to expose it; it must not keep local compatibility
wrappers for agent-owned modules.
