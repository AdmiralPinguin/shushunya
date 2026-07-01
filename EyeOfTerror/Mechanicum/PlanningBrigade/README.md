# Planning Brigade

Ceraxia's planning-side Mechanicum brigade.

The brigade is advisory. It prepares a structured planning packet for Ceraxia,
but Ceraxia remains the responsible code brigadier and must approve or revise
the plan before implementation.

Members:

- `TaskTriage`
- `RepoSurveyor`
- `DesignStrategos`
- `VerificationArchitect`
- `RiskScribe`

Current prototype:

```bash
python3 EyeOfTerror/Mechanicum/PlanningBrigade/planning_brigade.py --task "почини failing unittest без изменения тестов" --repo-path /repo
```

The prototype writes one `ceraxia_planning_packet` containing all five planning
sections. Split these roles into separate services only after the packet format
is stable.
