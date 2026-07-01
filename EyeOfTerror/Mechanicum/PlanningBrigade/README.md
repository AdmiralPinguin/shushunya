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

`role_contracts.json` fixes each role's authority, inputs, outputs, quality
gates, handoff, and read-only boundary. The roles remain in-process until field
trials prove the contracts are stable enough to split into services.
`planning_packet_contract.py` owns strict packet validation so Ceraxia can
import the contract gate without coupling review logic to packet generation.

Current contract:

- `problem_statement` restates the user request, known constraints, path hints,
  unknowns, and definition of done.
- `dependency_map` preserves the planning critical path from task contract to
  implementation brief.
- `work_breakdown` orders the work phases, owners, dependencies, outputs, exit
  gates, parallel work opportunities, and stop conditions.
- `impact_analysis` records affected engineering surfaces such as source
  behavior, tests, public API contracts, security boundaries, runtime config,
  data compatibility, and internal architecture.
- `execution_forecast` estimates task complexity, expected CodeBrigade
  iterations, timeout budget, orchestration notes, and escalation triggers.
- `surface_verification_matrix` maps each impacted surface to planned evidence
  and blocks handoff when coverage is missing.
- `acceptance_contract` records what the final package must prove and what
  shortcuts are forbidden.
- `implementation_brief_blueprint` defines the CodeBrigade handoff sections and
  mutation preconditions.
- `planning_review_gate` scores the planning packet and blocks unclear or
  structurally unsafe plans before they reach CodeBrigade.
- `code_brigade_handoff` lists the ordered execution/review steps.

```bash
python3 EyeOfTerror/Mechanicum/PlanningBrigade/planning_brigade.py --task "почини failing unittest без изменения тестов" --repo-path /repo
python3 EyeOfTerror/Mechanicum/PlanningBrigade/planning_brigade.py --task "почини failing unittest без изменения тестов" --repo-path /repo --validate
```

The brigade writes one `ceraxia_planning_packet` containing all five planning
roles plus the handoff contracts above. Split these roles into separate services
only after the packet format is stable.

Field trials:

```bash
PYTHONPATH=EyeOfTerror/Mechanicum/PlanningBrigade python3 EyeOfTerror/Mechanicum/PlanningBrigade/field_trial_runner.py
```

The trials cover failing-test repair, security boundaries, migration/API
compatibility, architecture refactors, concurrency/cache failures, and unclear
tasks that must be blocked. The runner also emits a coverage summary for task
kinds, work phases, impacted surfaces, highest-risk surfaces, gate decisions,
negative tests, and planning scores.
