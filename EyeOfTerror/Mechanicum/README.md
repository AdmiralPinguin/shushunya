# EyeOfTerror Mechanicum

This folder contains the active coding warband and its evaluation boundary.

## Current Architecture

- `Skitarii/` is the executable coding warband. Its service listens on port
  `7200` and owns clarification, repository exploration, planning, isolated VM
  execution, fighter/reviewer loops, behavioural acceptance, patch bundling,
  and async mission lifecycle.
- Ceraxia lives in `Warmaster/eye_of_terror/inner_circle/`. Her service on port
  `7104` makes one leadership decision and emits mission intent, priorities,
  success conditions, and quality gates.
- `native_code_run.py` persists exactly one `skitarii_mission` execution step.
  Native code contracts reject `worker_plan`, dispatch packets, file lists,
  commands, and detailed implementation steps at the Ceraxia boundary.
- `skitarii_bridge.py` is the only gateway from an Abaddon run into Skitarii.
  Skitarii rejects direct production missions without a valid persisted Ceraxia
  directive.

There is no compatibility code brigade. The former Ceraxia/PlanningBrigade/
CodeBrigade implementation and ports `7014-7020` are retired and absent from
the runtime registry.

## Safety and Acceptance

Patch missions receive an exact bounded inline snapshot of Git-visible files.
Clean tracked assets above the inline cap remain immutable and are represented
by path, size, mode, and SHA-256; dirty or untracked oversized files block the
mission. File modes, safe in-repository symlinks, binary content, and deleted
paths are carried as baseline data. Returned patches are built against the
original baseline, include new/deleted/binary changes, and must apply and pass
their checks in an isolated self-contained clone before Warmaster can report
success. If live auto-apply is disabled, Warmaster reports `ready_to_apply` and
publishes `work/skitarii.patch`; it does not claim that the repository changed.
The returned action is executable through
`POST /runs/{task_id}/apply_patch` and requires the recorded repository
fingerprint. The gateway rechecks the patch under a repository lock, reruns
tests after live apply, and rolls back only when the post-apply state is proven
unchanged. The native result and patch are readable through the standard
`/final`, `/artifacts`, and `/artifact_text` endpoints.

Production missions use two acceptance layers: working checks may guide the
fighter, while a separate verifier head creates private behavioural edge checks
before implementation. The fighter and its background descendants are stopped
before the candidate is frozen; private checks run against a disposable copy
and any verifier mutation blocks acceptance. Both check sets are then rerun
through bubblewrap with a clean environment, no network, a fresh tmpfs per
command, bounded output, and cgroup CPU/memory/PID limits. A mission with no
usable private behavioural check cannot be reported complete.

The capability smoke suite keeps its oracle checks private, independently
applies the actual returned binary patch (including greenfield tasks), protects
seed test fixtures, and executes candidate code only inside the sandbox VM.
It records verifier infrastructure failures as unverified rather than blaming
or accepting the candidate, and attests the loaded service source, instance,
held-out policy, and model endpoints at both ends of the run. It is a 30-task
smoke barrier, not a substitute for a real-repository benchmark.

Run the local required barrier:

```bash
EyeOfTerror/Mechanicum/check-mechanicum-local.sh
```

Run a clean complete capability smoke evaluation and atomically replace the raw
result file only after all tasks finish:

```bash
cd EyeOfTerror/Mechanicum/Skitarii
python3 eval_suite.py --n 0 --out eval_results.json
```

The repository-level integration gate remains:

```bash
EyeOfTerror/check-eye-mechanicum.sh
```
