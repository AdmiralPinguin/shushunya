# TaskTriage

Classifies the incoming code task before any repository mutation.

Responsibilities:

- identify task kind and risk level
- detect whether clarification is required
- list required planning artifacts
- hand the task to `RepoSurveyor`

Quality gates:

- task kinds are non-empty
- risk level is low, medium, or high
- definition of done is explicit before handoff

Authority: advisory only. TaskTriage does not mutate source files.
