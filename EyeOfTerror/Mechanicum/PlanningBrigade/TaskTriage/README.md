# TaskTriage

Classifies the incoming code task before any repository mutation.

Responsibilities:

- identify task kind and risk level
- detect whether clarification is required
- list required planning artifacts
- hand the task to `RepoSurveyor`

Authority: advisory only. TaskTriage does not mutate source files.
