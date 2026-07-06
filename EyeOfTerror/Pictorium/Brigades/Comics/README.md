# Comics Brigade

Active first-pass brigade for long-form visual sequences: comics, storyboards,
character sheets, panel continuity, lettering constraints, page layout, and
multi-artifact packaging.

Comics owns sequence planning, panel dependencies, text bubble/layout rules,
and character consistency policy. It does not own Forge runtime. Panel and
character-sheet image execution is delegated through Image Brigade workers:
`Promptwright`, `ModelQuartermaster`, and `ForgeDispatcher`.

## Workers

- `ScenarioScribe`: scenario, cast, visual style, and ordered beats.
- `StoryboardArchitect`: ordered panels, composition, camera, continuity, and
  image requests.
- `CharacterSheetwright`: Image Brigade character-sheet planning.
- `Panelwright`: per-panel Image Brigade plans, resource checks, and dry-runs.
- `LayoutFinalis`: page layout, blockers, handoff, and final manifest.
