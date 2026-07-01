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

Current contract:

- `problem_statement` restates the user request, known constraints, path hints,
  unknowns, and definition of done.
- `dependency_map` preserves the planning critical path from task contract to
  implementation brief.
- `acceptance_contract` records what the final package must prove and what
  shortcuts are forbidden.
- `implementation_brief_blueprint` defines the CodeBrigade handoff sections and
  mutation preconditions.
- `code_brigade_handoff` lists the ordered execution/review steps.

```bash
python3 EyeOfTerror/Mechanicum/PlanningBrigade/planning_brigade.py --task "почини failing unittest без изменения тестов" --repo-path /repo
```

The brigade writes one `ceraxia_planning_packet` containing all five planning
roles plus the handoff contracts above. Split these roles into separate services
only after the packet format is stable.
