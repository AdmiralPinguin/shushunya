# Coding Warband Deferred Roadmap

Status: deferred after the July 2026 Ceraxia/Skitarii production baseline.

## Decision

The current warband is sufficient for its intended job: Ceraxia makes the
leadership decision, Skitarii turn it into a detailed engineering plan, and the
native mission pipeline explores, implements, reviews, verifies, applies,
commits, pushes, and proves the published result.

Stop redesigning this subsystem for now. Operate it on real work and spend
engineering effort on the other unfinished parts of Shushunya. Items below are
future backlog and require a new explicit priority decision before
implementation.

## How to Measure the Gap

Do not equate tool breadth with the number of Linux commands available to a
fighter. A capability counts as supported only when the full workflow has:

- an explicit authority and input contract;
- scoped credentials and mutation boundaries;
- machine-checkable acceptance evidence;
- crash recovery or a safe terminal failure;
- truthful user-visible progress and completion states.

On this definition, the warband has broad low-level repository access and a
strong autonomous coding workflow, but it is not yet a general-purpose agent
platform. That is an intentional scope boundary, not a reason for another
rewrite.

Before major architectural expansion, run a blind benchmark of 50-100 real
repository tasks against current Claude Code and Codex releases. Record task
success, correctness after independent review, human interventions, elapsed
time, collateral changes, recovery failures, and publication truth. Use those
results to choose work instead of adding machinery from intuition alone.

## Priority 1: Complete the Software-Department Loop

These capabilities extend the current mission without changing the
Ceraxia/Skitarii authority boundary:

1. First-class branch, pull-request, review-comment, and CI workflows.
2. Multi-repository missions with explicit dependency and publication order.
3. Browser-driven UI acceptance with screenshots and behavioural evidence.
4. Deployment, migration, health-check, rollback, and post-deploy proof.
5. Mid-mission clarification and approval checkpoints that survive restarts.
6. Long-running campaign coordination across dependent missions and worktrees.

Each adapter must preserve the current rule: a mission is complete only after
the claimed external state has been independently verified.

## Priority 2: Improve Capability, Not Ceremony

After benchmark evidence identifies real failures:

1. Improve repository discovery and context selection for very large codebases.
2. Add dynamic replanning when implementation evidence invalidates the first
   approach.
3. Build reusable, versioned engineering skills and cross-mission technical
   memory with provenance and expiry.
4. Allow task-specific specialist teams and dependency graphs where the fixed
   fighter/reviewer topology is demonstrably insufficient.
5. Add model routing by task difficulty, latency, and evidence quality while
   keeping background coders isolated from interactive capacity.

Do not add roles, prompts, or orchestration layers without a measured failure
that they are expected to fix.

## Priority 3: Optional Platform Breadth

Only add these when another Shushunya subsystem has a concrete need:

- MCP or equivalent dynamic external-tool discovery;
- issue trackers, chat systems, cloud consoles, databases, and observability;
- image, document, notebook, mobile-device, and desktop-application workflows;
- reusable integrations for release management and incident response.

Email, calendars, office documents, and generic personal-assistant features are
not coding-warband goals by default. They belong elsewhere unless a coding
mission requires them.

## Invariants for Future Work

- Ceraxia remains the warband leader, not a detailed planner or code worker.
- Skitarii own exploration, detailed planning, implementation, tests, review,
  and repair.
- Abaddon routes and coordinates; it does not micromanage worker execution.
- No compatibility path may revive the retired paper-worker pipeline.
- A new integration must fail closed without reporting false success.
- Owner changes outside the mission scope remain untouched.
- Broader functionality must not weaken isolation, private acceptance,
  recoverability, or exact publication proof.
