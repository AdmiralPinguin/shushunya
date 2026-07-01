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
`service_contracts.json` reserves planned read-only service interfaces for the
same roles on ports 7111-7115, without starting or requiring those services
yet. Self-tests and field trials compare those reservations with
`EyeOfTerror/registry/ports.json` and `Mechanicum/worker_services.json` so the
future split cannot silently collide with active workers.
`planning_packet_contract.py` owns strict packet validation so Ceraxia can
import the contract gate without coupling review logic to packet generation.
`planning_feedback_contract.py` owns the reverse intake from Ceraxia: it
validates `planning_feedback_request.json` and turns review findings into a
PlanningBrigade replan checklist plus a `replan_payload` suitable for rebuilding
the packet, then hands authority back to Ceraxia.
`planning_feedback_intake.schema.json` fixes that reverse-intake output shape
for orchestration and regression tests.

Current contract:

- `problem_statement` restates the user request, known constraints, path hints,
  unknowns, and definition of done.
- `dependency_map` preserves the planning critical path from task contract to
  implementation brief.
- `assumption_register` records task, repository, verification, and specialized
  risk assumptions with validation sources and replan/blocker triggers.
- `work_breakdown` orders the work phases, owners, dependencies, outputs, exit
  gates, parallel work opportunities, and stop conditions.
- `impact_analysis` records affected engineering surfaces such as source
  behavior, tests, public API contracts, security boundaries, runtime config,
  data compatibility, and internal architecture.
- `investigation_playbook` gives CodeBrigade an ordered investigation protocol:
  entrypoints, candidate source, callers/dependencies, tests/oracles, contract
  risk review, and specialized traces for security, compatibility, or
  concurrency work.
- `change_control_plan` defines allowed change intents, protected invariants,
  mutation requirements, diff-review questions, rollback triggers, and
  post-change proofs before source mutation reaches CodeBrigade.
- `execution_forecast` estimates task complexity, expected CodeBrigade
  iterations, timeout budget, orchestration notes, and escalation triggers.
- `diagnostic_repair_plan` defines the bounded repair loop for failed
  verification: required diagnostic inputs, read-before-edit evidence, stop
  conditions, max attempts, and required repair evidence.
- `surface_verification_matrix` maps each impacted surface to planned evidence
  and blocks handoff when coverage is missing. Each row also states the command
  output or diagnostic evidence that must be linked back to that surface.
- `acceptance_contract` records what the final package must prove and what
  shortcuts are forbidden.
- `acceptance_trace_matrix` maps every definition-of-done, quality-bar, and
  acceptance requirement to planned evidence and CodeBrigade package ids. It
  also reports separate `definition_of_done` coverage counts so incomplete
  original-task fulfillment blocks the packet and CodeBrigade handoff.
- `constraint_trace_matrix` maps every preserved user/task constraint to
  planned evidence and CodeBrigade package ids.
- `implementation_brief_blueprint` defines the CodeBrigade handoff sections and
  mutation preconditions.
- `implementation_work_packages` turns the plan into reviewable CodeBrigade
  work packages with read scope, edit scope, verification scope, risk controls,
  blocking policy, handoff criteria, and a package dependency graph with
  execution batches for safe future orchestration.
- `worker_output_contract` tells CodeBrigade which reports, package statuses,
  package-level acceptance requirements, evidence sources, and blocker fields
  must return for Ceraxia to accept the work.
- `planning_feedback_contract.py` validates Ceraxia feedback when review
  findings point back at the planning packet or handoff contracts, then creates
  a replan intake with required return artifacts.
- `surface_package_matrix` traces every impacted surface to planned
  verification evidence and concrete implementation package ids.
- `planning_review_gate` scores the planning packet and blocks unclear or
  structurally unsafe plans before they reach CodeBrigade.
- `code_brigade_handoff` lists the ordered execution/review steps, package
  review order, package dependency graph, global handoff criteria, and
  acceptance and definition-of-done trace requirements, including the
  diagnostic repair plan that
  CodeBrigade must preserve. It also carries the worker-output contract that
  the worker report and review gate must satisfy.

```bash
python3 EyeOfTerror/Mechanicum/PlanningBrigade/planning_brigade.py --task "почини failing unittest без изменения тестов" --repo-path /repo
python3 EyeOfTerror/Mechanicum/PlanningBrigade/planning_brigade.py --task "почини failing unittest без изменения тестов" --repo-path /repo --validate
python3 EyeOfTerror/Mechanicum/PlanningBrigade/planning_feedback_contract.py path/to/planning_feedback_request.json
python3 EyeOfTerror/Mechanicum/PlanningBrigade/planning_brigade.py --feedback-request path/to/planning_feedback_request.json --feedback-replan-packet --validate
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
negative tests, implementation work packages, acceptance trace packages,
assumption coverage, change-control invariants, change-control post-change
proofs, rollback triggers, surface-specific output evidence requirements, and
implementation-brief blueprint sections and mutation preconditions.
