# EyeOfTerror

EyeOfTerror is the command layer above the Mechanicum workers.

It is not a worker and should not execute long specialist work directly. Its
job is to accept user chat/tasks, pick the right Inner Circle governor, track
the task state, and return status/results to the user.

## Ports

| Port | Service | Role |
| --- | --- | --- |
| 7000 | Warmaster Gateway | User-facing chat/orchestration entrypoint |
| 7101 | Iskandar Khayon | Lore, research, reconstruction task governor |

Mechanicum workers use ports `7001+`. Legacy backends may keep their existing
ports while relay workers adapt them to the common worker API.

## Routing Rule

The Warmaster Gateway should only do top-level routing:

1. Accept a user message.
2. Decide whether it is chat, status, cancellation, or a task.
3. For tasks, create a task contract.
4. Assign one Inner Circle governor.
5. Let the governor coordinate Mechanicum workers.

The gateway should not micromanage individual worker steps.

