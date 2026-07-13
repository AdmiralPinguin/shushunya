# ResearchWarband production architecture

Status: **active Abaddon execution backend** (cut over on 2026-07-13).

```text
Abaddon :7000
  -> IskandarKhayon :7101 (leadership decision only)
       -> ResearchWarband :7201 (native planning and execution)
```

Iskandar owns the research objective, depth, source-class policy, error
tolerance, priorities, success conditions, output requirements, and escalation
decision.  The strict `iskandar_research_directive` schema rejects worker
steps, search queries, URLs, files, commands, and tool calls.

ResearchWarband owns detailed decomposition, search, acquisition, reading,
evidence construction, analysis, writing, semantic review, and internal repair.
Scout, Reader, Analyst, Writer, and Verifier are isolated logical roles inside
this stateful backend; they are not Mechanicum services and do not form a fixed
ten-worker pipeline.

The authoritative handoff is one native run package containing the bounded
commander order, leadership directive, governor plan, receipt, and status.  The
Abaddon bridge validates the exact package identity before dispatch, persists
the remote mission identity before the first POST, adopts matching missions on
replay, and accepts a result only after terminal cleanup is proven.

Research truth comes from immutable source snapshots, typed spans, claim and
evidence ledgers, deterministic integrity gates, and a fresh context-isolated
Gemma semantic-review pass.  Same-model review explicitly does not claim
epistemic independence.  Independent evaluation lives under
`EyeOfTerror/Evaluation/ResearchWarband` and is not visible to the production
warband.

The removed `Scriptorium/IskandarKhayon` worker-plan implementation is not a
fallback.  Any historical Iskandar research package without the exact native
execution descriptor is quarantined as `legacy_iskandar_run_removed`; it must
be replaced by a fresh native mission.

Relevant executable contracts:

- `service.py` — bearer-protected loopback service and mission lifecycle;
- `schema.py` — research/evidence/result schemas;
- `pipeline.py` — role orchestration and repair loop;
- `verifier.py` and `semantic_review.py` — deterministic and semantic gates;
- `deployment_guard.py` — production identity and readiness attestation;
- `EyeOfTerror/Warmaster/eye_of_terror/native_research_run.py` — native handoff;
- `EyeOfTerror/Warmaster/eye_of_terror/research_warband_bridge.py` — Abaddon bridge.
